"""Integração com o SAP HANA (KSB1 direto da view). Todas as rotas exigem o acesso pessoal "Central de dados".

A busca não grava nada na base: cria um lote em Importações (mesma validação, prévia e confirmação dos arquivos)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, require_data_control
from app.db import get_db
from app.imports import pipeline
from app.imports.base import StructureError
from app.models import Company, CycleParameter, User
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


@router.get("/status", summary="Situação da integração com o SAP HANA (sem segredos)")
def status(_: User = Depends(require_data_control)):
    return hana.status()


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
        rows, names, mapping = hana.fetch_ksb1(payload.year, company, _override(db))
        content, count = hana.to_workbook(rows, names, mapping)
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
