import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1 import (
    analytics,
    audit_logs,
    auth,
    capex,
    consolidation,
    cycles,
    dashboard,
    datasets,
    findings,
    imports,
    integrations,
    justifications,
    master,
    opex,
    personnel,
    rules,
    users,
    validation,
)
from app.config import get_settings
from app.db import SessionLocal, engine
from app.imports.pipeline import ImportWorker
from app.services.storage import get_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()
if settings.environment == "production" and (settings.secret_key == "change-me" or len(settings.secret_key) < 32):
    raise RuntimeError("Defina SECRET_KEY (>= 32 caracteres) nas variáveis do serviço")
FRONTEND_DIST = Path(os.getenv("FRONTEND_DIST", Path(__file__).resolve().parent.parent / "frontend_dist"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker = None
    if settings.run_import_worker:
        worker = ImportWorker(SessionLocal, get_storage(), settings.worker_poll_seconds)
        worker.start()
    yield
    if worker:
        worker.stop()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description="Sistema de Planejamento e Orçamento ATEM — API (Fase 1: fundação)",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return response


app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api/v1")
for module in (
    auth,
    users,
    master,
    cycles,
    imports,
    audit_logs,
    rules,
    dashboard,
    datasets,
    opex,
    capex,
    personnel,
    consolidation,
    analytics,
    findings,
    justifications,
    validation,
    integrations,
):
    api.include_router(module.router)
app.include_router(api)


@app.get("/api/health", tags=["infra"])
def health() -> dict:
    with engine.connect() as conn:
        conn.execute(text("select 1"))
    return {"status": "ok"}


if FRONTEND_DIST.exists():  # SPA servida pelo mesmo container (Fase 2)
    app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404, "Rota não encontrada")
        candidate = (FRONTEND_DIST / path).resolve()
        if candidate.is_file() and candidate.is_relative_to(FRONTEND_DIST.resolve()):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIST / "index.html")
