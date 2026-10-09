from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
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


class _Finder:
    """Acha o apontamento pela chave, recalculando os apontamentos uma vez por CC (correção em lote)."""

    def __init__(self, db: Session, ctx: opex_svc.Context, user: User) -> None:
        self.db, self.ctx, self.user = db, ctx, user
        self.scope = visible_cost_center_ids(db, user)
        self.kept = svc.kept_keys(db, ctx)
        self.cache: dict[int, list[dict]] = {}

    def get(self, key: str) -> tuple[BudgetSubmission | None, dict]:
        if key.startswith("0:"):  # sem orçamento de CC (colaborador sem CC): só a Controladoria
            if not is_global(self.user):
                raise HTTPException(403, "Só a Controladoria corrige este apontamento")
            if 0 not in self.cache:  # + apontamentos do ciclo (premissas)
                self.cache[0] = svc.employees_without_cost_center(self.db) + svc.cycle_findings(self.db, self.ctx)
            finding = next((f for f in self.cache[0] if f["key"] == key), None)
            if finding is None:
                raise HTTPException(404, "Apontamento não encontrado ou já resolvido")
            return None, finding
        try:
            sub = self.db.get(BudgetSubmission, int(key.split(":", 1)[0]))
        except ValueError:
            sub = None
        if sub is None or sub.version_id != self.ctx.version.id:
            raise HTTPException(404, "Apontamento não encontrado")
        if self.scope is not None and sub.cost_center_id not in self.scope:
            raise HTTPException(403, "Sem acesso a este centro de custo")
        if sub.cost_center_id not in self.cache:
            self.cache[sub.cost_center_id] = svc.collect(
                self.db, self.ctx, {sub.cost_center_id}, self.kept, structure=is_global(self.user)
            )
        finding = next((f for f in self.cache[sub.cost_center_id] if f["key"] == key), None)
        if finding is None:
            raise HTTPException(404, "Apontamento não encontrado ou já resolvido")
        return sub, finding


def _find(db: Session, ctx: opex_svc.Context, user: User, key: str) -> tuple[BudgetSubmission, dict]:
    return _Finder(db, ctx, user).get(key)


@router.get("", summary="Apontamentos: divergências e erros de preenchimento dos orçamentos em aberto, item a item")
def findings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    scope = visible_cost_center_ids(db, user)
    items = svc.collect(db, ctx, scope, svc.kept_keys(db, ctx), structure=is_global(user))
    editable: dict[int, bool] = {}
    for i in items:
        if i["submission_id"] is None or i["kind"] == "STRUCTURE_NO_SECTOR":
            i["editable"] = is_global(user)  # estrutura, CC do colaborador e premissas do ciclo: Controladoria
            continue
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
        "sectors": svc.sector_options(db) if is_global(user) else [],
    }


class FixFields(BaseModel):
    text: str | None = None
    values: dict[int, Decimal | None] | None = None
    ticket_amount: Decimal | None = None
    project_type_code: str | None = None
    amount: Decimal | None = None  # novo salário (promoção pendente)
    month: int | None = None  # mês da ação (importada sem mês)
    area_id: int | None = None  # setor do CC (CC sem área e setor)
    cost_center_id: int | None = None  # CC do colaborador sem centro de custo


class FixIn(FixFields):
    key: str


class FixManyIn(FixFields):
    keys: list[str] = Field(min_length=1, max_length=500)


def _fix_one(
    db: Session,
    ctx: opex_svc.Context,
    user: User,
    sub: BudgetSubmission | None,
    finding: dict,
    data: dict,
    ip: str | None,
) -> str:
    """Aplica uma correção (mesma regra de edição das telas do CC) e registra na análise e na auditoria."""
    if sub is None and finding["kind"] != "PERSONNEL_NO_CC":  # premissas do ciclo: corrige na página do link
        raise HTTPException(409, "Este apontamento não tem correção direta: abra a página indicada")
    if finding["kind"] == "PERSONNEL_NO_CC":
        if not is_global(user):
            raise HTTPException(403, "Só a Controladoria define o centro de custo do colaborador")
        note, before, after = svc.apply_fix(db, None, finding, data, user.id)
        # registro no orçamento de pessoal do CC escolhido (que passa a ter o colaborador)
        sub = opex_svc.get_submission(db, ctx, after["cost_center_id"], module="PERSONNEL")
        opex_svc.mark_in_progress(db, sub, ctx, user.id)
        svc.log(db, sub, finding, "CORRECTED", note, user.id)
        audit.record(
            db,
            user_id=user.id,
            action="FINDING_FIX",
            entity_type="finding",
            entity_id=finding["key"],
            before=before,
            after=after,
            reason=note,
            ip=ip,
        )
        return note
    if finding["kind"] == "STRUCTURE_NO_SECTOR":
        if not is_global(user):
            raise HTTPException(403, "Só a Controladoria define a área e o setor do centro de custo")
    else:
        access, require_edit = _access(db, ctx, user, sub)
        require_edit(access)
    note, before, after = svc.apply_fix(db, sub, finding, data, user.id)
    if finding["kind"] != "STRUCTURE_NO_SECTOR":
        opex_svc.mark_in_progress(db, sub, ctx, user.id)
    svc.log(db, sub, finding, "CORRECTED", note, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="FINDING_FIX",
        entity_type="finding",
        entity_id=finding["key"],
        before=before,
        after=after,
        reason=note,
        ip=ip,
    )
    return note


FIX_ERRORS = (svc.FindingError, opex_svc.OpexError, ValueError)


@router.post(
    "/fix",
    summary="Corrige o apontamento na própria página (cronograma, conta, justificativa, passagem, salário, mês, setor)",
)
def fix(payload: FixIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    sub, finding = _find(db, ctx, user, payload.key)
    try:
        note = _fix_one(db, ctx, user, sub, finding, payload.model_dump(), client_ip(request))
    except FIX_ERRORS as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.commit()
    return {"key": payload.key, "note": note}


@router.post("/fix-many", summary="Mesma correção em vários apontamentos (ex.: confirmar todos os CCs pelo cargo)")
def fix_many(
    payload: FixManyIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ctx = _ctx(db)
    finder = _Finder(db, ctx, user)
    data = payload.model_dump(exclude={"keys"})
    done, failed = [], []
    for key in dict.fromkeys(payload.keys):
        try:
            with db.begin_nested():
                sub, finding = finder.get(key)
                note = _fix_one(db, ctx, user, sub, finding, data | {"key": key}, client_ip(request))
            done.append({"key": key, "note": note})
        except HTTPException as exc:
            failed.append({"key": key, "error": exc.detail})
        except FIX_ERRORS as exc:
            failed.append({"key": key, "error": str(exc)})
    db.commit()
    return {"done": done, "failed": failed}


class KeepIn(BaseModel):
    key: str
    note: str


class KeepManyIn(BaseModel):
    keys: list[str] = Field(min_length=1, max_length=500)
    note: str


def _keep_one(db: Session, user: User, sub: BudgetSubmission, finding: dict, note: str, ip: str | None) -> None:
    if not finding["can_keep"]:
        raise HTTPException(409, "Só avisos podem ser mantidos; pendências críticas precisam ser corrigidas")
    svc.log(db, sub, finding, "KEPT", note, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="FINDING_KEEP",
        entity_type="finding",
        entity_id=finding["key"],
        after={"kind": finding["kind"], "subject": finding["subject"], "note": note},
        reason=note,
        ip=ip,
    )


@router.post("/keep", summary="Mantém o aviso como veio (Controladoria), com justificativa")
def keep(payload: KeepIn, request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    note = payload.note.strip()
    if not note:
        raise HTTPException(422, "Informe por que o item fica como está")
    ctx = _ctx(db)
    sub, finding = _find(db, ctx, user, payload.key)
    _keep_one(db, user, sub, finding, note, client_ip(request))
    db.commit()
    return {"key": payload.key, "note": note}


@router.post("/keep-many", summary="Mantém vários avisos com o mesmo motivo (Controladoria)")
def keep_many(payload: KeepManyIn, request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    note = payload.note.strip()
    if not note:
        raise HTTPException(422, "Informe por que os itens ficam como estão")
    ctx = _ctx(db)
    finder = _Finder(db, ctx, user)
    done, failed = [], []
    for key in dict.fromkeys(payload.keys):
        try:
            sub, finding = finder.get(key)
            _keep_one(db, user, sub, finding, note, client_ip(request))
            done.append({"key": key, "note": note})
        except HTTPException as exc:
            failed.append({"key": key, "error": exc.detail})
    db.commit()
    return {"done": done, "failed": failed}


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
    found = svc.collect(db, ctx, scope, svc.kept_keys(db, ctx), structure=is_global(user))
    content = svc.workbook(ctx, found, svc.reviews(db, ctx, scope))
    name = f"Apontamentos_{ctx.target_year}_v{ctx.version.label}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
