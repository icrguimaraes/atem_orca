from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.v1 import opex as opex_api
from app.api.v1 import personnel as personnel_api
from app.core.deps import client_ip, get_current_user, is_global, require_roles, visible_cost_center_ids
from app.db import get_db
from app.models import BudgetSubmission, FindingReview, User
from app.models.base import Role
from app.services import audit
from app.services import findings as svc
from app.services import opex as opex_svc

router = APIRouter(prefix="/findings", tags=["apontamentos"])
controller = require_roles(Role.CONTROLLER)


def _ctx(db: Session) -> opex_svc.Context:
    try:
        return opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


def _access(db: Session, ctx: opex_svc.Context, user: User, sub: BudgetSubmission):
    """Mesma regra de edição das telas do CC (OPEX/CAPEX ou Pessoal)."""
    if sub.module == "PERSONNEL":
        return personnel_api.Access(db, ctx, user, sub), personnel_api._require_edit
    return opex_api.Access(db, ctx, user, sub), opex_api._require_edit


def _find(db: Session, ctx: opex_svc.Context, user: User, key: str) -> tuple[BudgetSubmission, dict]:
    try:
        sub = db.get(BudgetSubmission, int(key.split(":", 1)[0]))
    except ValueError:
        sub = None
    if sub is None or sub.version_id != ctx.version.id:
        raise HTTPException(404, "Apontamento não encontrado")
    scope = visible_cost_center_ids(db, user)
    if scope is not None and sub.cost_center_id not in scope:
        raise HTTPException(403, "Sem acesso a este centro de custo")
    found = svc.collect(db, ctx, {sub.cost_center_id}, svc.kept_keys(db, ctx))
    finding = next((f for f in found if f["key"] == key), None)
    if finding is None:
        raise HTTPException(404, "Apontamento não encontrado ou já resolvido")
    return sub, finding


@router.get("", summary="Apontamentos: divergências e erros de preenchimento dos orçamentos em aberto, item a item")
def findings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    scope = visible_cost_center_ids(db, user)
    items = svc.collect(db, ctx, scope, svc.kept_keys(db, ctx))
    editable: dict[int, bool] = {}
    for i in items:
        if i["submission_id"] not in editable:
            access, _ = _access(db, ctx, user, db.get(BudgetSubmission, i["submission_id"]))
            editable[i["submission_id"]] = access.edit
        i["editable"] = editable[i["submission_id"]]
    logged = svc.reviews(db, ctx, scope)
    return {
        "version": ctx.version.label,
        "target_year": ctx.target_year,
        "items": items,
        "counts": {
            "critical": sum(1 for i in items if i["severity"] == "CRITICAL"),
            "warning": sum(1 for i in items if i["severity"] == "WARNING"),
            "cost_centers": len({i["cost_center_id"] for i in items}),
        },
        "reviews": logged,
        "cost_centers": svc.cost_centers(
            db, ctx, {i["cost_center_id"] for i in items} | {r["cost_center_id"] for r in logged}
        ),
        "can_review": is_global(user),
        "project_types": svc.project_types(db),
    }


class FixIn(BaseModel):
    key: str
    text: str | None = None
    values: dict[int, Decimal | None] | None = None
    ticket_amount: Decimal | None = None
    project_type_code: str | None = None
    amount: Decimal | None = None  # novo salário (promoção pendente)
    month: int | None = None  # mês da ação (importada sem mês)


@router.post(
    "/fix", summary="Corrige o apontamento na própria página (cronograma, conta, justificativa, passagem, salário, mês)"
)
def fix(payload: FixIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    sub, finding = _find(db, ctx, user, payload.key)
    access, require_edit = _access(db, ctx, user, sub)
    require_edit(access)
    try:
        note, before, after = svc.apply_fix(db, sub, finding, payload.model_dump(), user.id)
    except (svc.FindingError, opex_svc.OpexError, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    svc.log(db, sub, finding, "CORRECTED", note, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="FINDING_FIX",
        entity_type="finding",
        entity_id=payload.key,
        before=before,
        after=after,
        reason=note,
        ip=client_ip(request),
    )
    db.commit()
    return {"key": payload.key, "note": note}


class KeepIn(BaseModel):
    key: str
    note: str


@router.post("/keep", summary="Mantém o aviso como veio (Controladoria), com justificativa")
def keep(payload: KeepIn, request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    note = payload.note.strip()
    if not note:
        raise HTTPException(422, "Informe por que o item fica como está")
    ctx = _ctx(db)
    sub, finding = _find(db, ctx, user, payload.key)
    if finding["severity"] != "WARNING":
        raise HTTPException(409, "Só avisos podem ser mantidos; pendências críticas precisam ser corrigidas")
    svc.log(db, sub, finding, "KEPT", note, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="FINDING_KEEP",
        entity_type="finding",
        entity_id=payload.key,
        after={"kind": finding["kind"], "subject": finding["subject"], "note": note},
        reason=note,
        ip=client_ip(request),
    )
    db.commit()
    return {"key": payload.key, "note": note}


@router.delete("/reviews/{review_id}", summary="Reabre um aviso mantido (volta para a lista de pendentes)")
def reopen(review_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    ctx = _ctx(db)
    review = db.get(FindingReview, review_id)
    sub = db.get(BudgetSubmission, review.submission_id) if review else None
    if review is None or sub is None or sub.version_id != ctx.version.id or review.action != "KEPT":
        raise HTTPException(404, "Aviso mantido não encontrado")
    audit.record(
        db,
        user_id=user.id,
        action="FINDING_REOPEN",
        entity_type="finding",
        entity_id=review.finding_key,
        before={"kind": review.kind, "subject": review.subject, "note": review.note},
        ip=client_ip(request),
    )
    db.delete(review)
    db.commit()
    return {"ok": True}


@router.get("/export.xlsx", summary="Relatório dos apontamentos: pendentes, corrigidos e mantidos")
def export_xlsx(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    scope = visible_cost_center_ids(db, user)
    content = svc.workbook(ctx, svc.collect(db, ctx, scope, svc.kept_keys(db, ctx)), svc.reviews(db, ctx, scope))
    name = f"Apontamentos_{ctx.target_year}_v{ctx.version.label}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
