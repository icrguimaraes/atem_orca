import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.v1 import audit_logs, auth, cycles, imports, master, rules, users
from app.config import get_settings
from app.db import SessionLocal, engine
from app.imports.pipeline import ImportWorker
from app.services.storage import get_storage

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
settings = get_settings()
if settings.environment == "production" and (settings.secret_key == "change-me" or len(settings.secret_key) < 32):
    raise RuntimeError("Defina SECRET_KEY (>= 32 caracteres) nas variáveis do serviço")
FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend_dist"


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
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_methods=["*"],
    allow_headers=["*"],
)

api = APIRouter(prefix="/api/v1")
for module in (auth, users, master, cycles, imports, audit_logs, rules):
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
        candidate = FRONTEND_DIST / path
        return FileResponse(candidate if candidate.is_file() else FRONTEND_DIST / "index.html")
