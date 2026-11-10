"""Defesa do orçamento (10/11/2026): base (Revisão 1 · VP) × proposta (versão em elaboração), cortes com argumento,
o que pedir de volta e o que está em aberto. Só a Controladoria (o VP entra com esse perfil)."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import client_ip, require_roles
from app.db import get_db
from app.models import User
from app.models.base import Role
from app.services import budget_defense as svc
from app.services import opex as opex_svc

router = APIRouter(prefix="/defense", tags=["defesa do orçamento"])
controller = require_roles(Role.CONTROLLER)


def _ctx(db: Session) -> opex_svc.Context:
    try:
        return opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


def _data(db: Session, base_version_id: int | None, version_id: int | None) -> dict:
    ctx = _ctx(db)
    try:
        base, proposal = svc.pick_versions(db, ctx, base_version_id, version_id)
    except svc.DefenseError as exc:
        raise HTTPException(422, str(exc)) from exc
    return svc.build(db, ctx, base, proposal)


@router.get("", summary="Defesa do orçamento: base × proposta, cortes, pedir de volta e pontos em aberto")
def defense(
    base_version_id: int | None = None,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(controller),
):
    return _data(db, base_version_id, version_id)


class ItemIn(BaseModel):
    base_version_id: int | None = None
    version_id: int | None = None
    cost_center_id: int
    module: str = Field(max_length=20)
    account: str = Field(max_length=20)
    priority: str | None = None
    argument: str | None = Field(None, max_length=4000)
    request_amount: str | None = None
    reset: bool = False


@router.put("/items", summary="Ajuste da Controladoria num corte: prioridade, argumento e valor a pedir (auditoria)")
def save_item(payload: ItemIn, request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    try:
        return svc.save(db, _ctx(db), payload.model_dump(), user.id, client_ip(request))
    except svc.DefenseError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc


@router.get("/export.xlsx", summary="Excel da defesa do orçamento")
def export_xlsx(
    base_version_id: int | None = None,
    version_id: int | None = None,
    db: Session = Depends(get_db),
    _: User = Depends(controller),
):
    data = _data(db, base_version_id, version_id)
    content = svc.workbook(data)
    name = f"Defesa_Orcamento_{data['target_year']}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
