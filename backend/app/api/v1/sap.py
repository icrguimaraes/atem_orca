"""Integração com o SAP HANA (KSB1 direto da view). Todas as rotas exigem o acesso pessoal "Central de dados".

A busca não grava nada na base: cria um lote em Importações (mesma validação, prévia e confirmação dos arquivos)."""

import hmac
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1 import auth as auth_api
from app.config import get_settings
from app.core.deps import client_ip, require_data_control
from app.db import get_db
from app.imports import pipeline
from app.imports.base import StructureError
from app.models import Company, CycleParameter, ImportBatch, User
from app.schemas.common import ImportBatchOut
from app.services import audit, hana
from app.services import opex as opex_svc
from app.services.storage import LocalStorage, get_storage

router = APIRouter(prefix="/sap", tags=["sap"])


def _override(db: Session) -> dict:
    try:
        ctx = opex_svc.context(db)
    except opex_svc.OpexError:
        return {}
    value = db.get(CycleParameter, (ctx.cycle.id, "sap.ksb1_columns"))
    return value.value if value is not None and isinstance(value.value, dict) else {}


def _recent_batches(db: Session, limit: int = 5) -> list[dict]:
    rows = db.scalars(
        select(ImportBatch)
        .where(ImportBatch.file_name.like("SAP_HANA_KSB1_%"))
        .order_by(ImportBatch.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": b.id,
            "file_name": b.file_name,
            "status": b.status,
            "created_at": b.created_at,
            "total_rows": b.total_rows,
            "via": (b.options or {}).get("via", "tela"),
        }
        for b in rows
    ]


@router.get("/status", summary="Situação da integração com o SAP HANA (sem segredos)")
def status(db: Session = Depends(get_db), _: User = Depends(require_data_control)):
    return hana.status() | {"push_enabled": bool(get_settings().sap_push_token), "recent": _recent_batches(db)}


@router.post("/test", summary="Testa a conexão e mostra as colunas da view, o mapeamento e uma amostra")
def test(db: Session = Depends(get_db), actor: User = Depends(require_data_control)):
    try:
        return hana.inspect(_override(db))
    except hana.HanaError as exc:
        raise HTTPException(502, str(exc)) from exc


class FetchIn(BaseModel):
    year: int = Field(ge=2000, le=2100)
    company_code: str | None = Field(default=None, max_length=10)
    mode: Literal["MERGE", "REPLACE"] = "MERGE"


@router.post("/ksb1/fetch", status_code=202, response_model=ImportBatchOut, summary="Busca o KSB1 do ano no SAP HANA")
def fetch(
    payload: FetchIn,
    request: Request,
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
    actor: User = Depends(require_data_control),
):
    company = (payload.company_code or "").strip() or None
    if company and not db.scalar(select(Company.id).where(Company.code == company)):
        raise HTTPException(422, f"Empresa {company} não cadastrada")
    try:
        content, count = hana.ksb1_workbook(payload.year, company, _override(db))
    except hana.HanaError as exc:
        raise HTTPException(502, str(exc)) from exc
    if not count:
        raise HTTPException(
            404, f"A view não trouxe lançamentos de {payload.year}" + (f" para a empresa {company}" if company else "")
        )
    stamp = datetime.now().strftime("%d-%m-%Y_%H%M")
    name = f"SAP_HANA_KSB1_{payload.year}{'_' + company if company else ''}_{stamp}.xlsx"
    options = {"reference_year": payload.year, "mode": payload.mode, "source": "SAP_HANA"}
    if company:
        options["company_code"] = company
    try:
        batch = pipeline.create_batch(
            db, storage, content=content, file_name=name, user_id=actor.id, dataset_type="ACTUAL", options=options
        )
    except StructureError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        user_id=actor.id,
        action="SAP_FETCH",
        entity_type="import_batch",
        entity_id=batch.id,
        after={
            "year": payload.year,
            "company": company,
            "mode": payload.mode,
            "rows": count,
            "view": hana.status()["view"],
        },
        ip=client_ip(request),
    )
    db.commit()
    return batch


# ---------------------------------------------------------------- envio pelo script local (token, sem login)

PUSH_FAIL_KEY = "puship:{}"


def require_push_token(request: Request, x_push_token: str | None = Header(default=None)) -> None:
    """Autentica o script local pelo token da variável SAP_PUSH_TOKEN (comparação em tempo constante). Sem token
    configurado a rota não existe; 5 falhas por IP em 15 min bloqueiam o IP (mesmo limite do login)."""
    token = get_settings().sap_push_token
    if not token:
        raise HTTPException(404, "Rota não encontrada")
    key = PUSH_FAIL_KEY.format(client_ip(request) or "-")
    if auth_api._blocked(key):
        raise HTTPException(429, "Muitas tentativas; aguarde 15 minutos")
    if not x_push_token or not hmac.compare_digest(x_push_token.encode(), token.encode()):
        auth_api._register_failure(key)
        raise HTTPException(401, "Token inválido")
    auth_api._clear_failures(key)


@router.get("/ksb1/config", summary="Mapeamento de colunas (parâmetro sap.ksb1_columns) para o script local")
def push_config(db: Session = Depends(get_db), _: None = Depends(require_push_token)):
    return {"columns": _override(db)}


@router.post("/ksb1/push", status_code=202, summary="Recebe o KSB1 montado pelo script local e cria o lote")
async def push(
    request: Request,
    file: Annotated[UploadFile, File(description="Planilha .xlsx do KSB1 montada pelo script")],
    year: Annotated[int, Form(ge=2000, le=2100)],
    company_code: Annotated[str | None, Form()] = None,
    mode: Annotated[Literal["MERGE", "REPLACE"], Form()] = "MERGE",
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
    _: None = Depends(require_push_token),
):
    content = await file.read()
    limit = get_settings().max_upload_mb * 1024 * 1024
    if not content:
        raise HTTPException(422, "Arquivo vazio")
    if len(content) > limit:
        raise HTTPException(413, f"Arquivo excede {get_settings().max_upload_mb} MB: envie por empresa")
    company = (company_code or "").strip() or None
    if company and not db.scalar(select(Company.id).where(Company.code == company)):
        raise HTTPException(422, f"Empresa {company} não cadastrada")
    stamp = datetime.now().strftime("%d-%m-%Y_%H%M")
    name = f"SAP_HANA_KSB1_{year}{'_' + company if company else ''}_{stamp}.xlsx"
    options = {"reference_year": year, "mode": mode, "source": "SAP_HANA", "via": "script"}
    if company:
        options["company_code"] = company
    try:
        batch = pipeline.create_batch(
            db, storage, content=content, file_name=name, user_id=None, dataset_type="ACTUAL", options=options
        )
    except StructureError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        user_id=None,
        action="SAP_PUSH",
        entity_type="import_batch",
        entity_id=batch.id,
        after={"year": year, "company": company, "mode": mode, "bytes": len(content)},
        ip=client_ip(request),
    )
    db.commit()
    return {"id": batch.id, "file_name": batch.file_name, "status": batch.status}
