"""Módulo Pessoal: quadro por CC, movimentações, contratações, workflow, validação GMD (Pessoas) e what-if."""

from collections import defaultdict
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user, require_roles, visible_cost_center_ids
from app.db import get_db
from app.domain.rules.common import money
from app.domain.workflow import EDITABLE, STATUS_LABELS, WorkflowError, available_actions, check_transition
from app.models import (
    BudgetPackage,
    BudgetSubmission,
    CostCenter,
    Employee,
    JobPosition,
    PackageManager,
    PackageReview,
    PersonnelMovement,
    PersonnelScenario,
    ScenarioMultiplier,
    User,
    WorkflowEvent,
)
from app.models.base import Role
from app.services import audit
from app.services import opex as opex_svc
from app.services import personnel as svc

router = APIRouter(prefix="/personnel", tags=["Pessoal"])
ZERO = Decimal("0")
GLOBAL_ROLES = {Role.ADMIN, Role.CONTROLLER, Role.HR}
PEOPLE_PACKAGE = "PESSOAS"
planner = require_roles(Role.CONTROLLER, Role.HR)


def _ctx(db: Session) -> opex_svc.Context:
    try:
        return opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


def _is_global(user: User) -> bool:
    return bool(user.role_codes & GLOBAL_ROLES)


def _visible(db: Session, user: User) -> set[int] | None:
    return None if _is_global(user) else visible_cost_center_ids(db, user)


def _people_package(db: Session) -> BudgetPackage | None:
    return db.scalar(select(BudgetPackage).where(BudgetPackage.code == PEOPLE_PACKAGE))


class Access:
    def __init__(self, db: Session, ctx: opex_svc.Context, user: User, sub: BudgetSubmission) -> None:
        self.cc = db.get(CostCenter, sub.cost_center_id)
        visible = _visible(db, user)
        self.global_ = _is_global(user)
        self.owner = self.cc.manager_user_id == user.id or (visible is not None and self.cc.id in visible)
        pkg = _people_package(db)
        self.reviewer = pkg is not None and any(
            pm.company_id in (None, self.cc.company_id)
            for pm in db.scalars(
                select(PackageManager).where(
                    PackageManager.cycle_id == ctx.cycle.id,
                    PackageManager.package_id == pkg.id,
                    PackageManager.user_id == user.id,
                )
            )
        )
        self.view = self.global_ or self.owner or self.reviewer
        cycle_ok = ctx.cycle.status == "OPEN" or self.global_
        self.edit = (self.global_ or self.owner) and sub.status in EDITABLE and cycle_ok
        self.cycle_blocked = not cycle_ok
        self.roles = set(user.role_codes)


def _load(db: Session, user: User, submission_id: int):
    ctx = _ctx(db)
    sub = db.get(BudgetSubmission, submission_id)
    if sub is None or sub.module != svc.MODULE:
        raise HTTPException(404, "Orçamento de pessoal não encontrado")
    access = Access(db, ctx, user, sub)
    if not access.view:
        raise HTTPException(403, "Sem acesso a este centro de custo")
    return ctx, sub, access


def _require_edit(access: Access) -> None:
    if not access.edit:
        if access.cycle_blocked:
            raise HTTPException(409, "O ciclo orçamentário ainda não está aberto para preenchimento")
        raise HTTPException(409, "Este orçamento não está em edição (envie de volta para ajuste para alterar)")


def _header(db: Session, ctx: opex_svc.Context, sub: BudgetSubmission, access: Access) -> dict:
    pkg = _people_package(db)
    review = (
        db.scalar(
            select(PackageReview).where(PackageReview.submission_id == sub.id, PackageReview.package_id == pkg.id)
        )
        if pkg
        else None
    )
    reviewer = db.get(User, review.reviewer_id) if review and review.reviewer_id else None
    return {
        "submission_id": sub.id,
        "status": sub.status,
        "status_label": STATUS_LABELS.get(sub.status, sub.status),
        "cost_center": {
            "id": access.cc.id,
            "code": access.cc.code,
            "name": access.cc.name,
            "company_id": access.cc.company_id,
            "company_code": access.cc.company.code,
            "manager_name": access.cc.manager_name,
        },
        "cycle": {
            "id": ctx.cycle.id,
            "name": ctx.cycle.name,
            "status": ctx.cycle.status,
            "deadline": ctx.cycle.personnel_deadline.isoformat() if ctx.cycle.personnel_deadline else None,
        },
        "version": ctx.version.label,
        "years": {"prev": ctx.prev_year, "ref": ctx.ref_year, "target": ctx.target_year},
        "permissions": {
            "edit": access.edit,
            "owner": access.owner,
            "global": access.global_,
            "reviewer": access.reviewer,
            "cycle_blocked": access.cycle_blocked,
        },
        "actions": available_actions(sub.status, roles=access.roles, is_owner=access.owner),
        "package_review": None
        if review is None
        else {
            "package": pkg.name,
            "status": review.status,
            "comment": review.comment,
            "reviewer": reviewer.name if reviewer else None,
            "updated_at": review.updated_at.isoformat() if review.updated_at else None,
            "can_review": access.global_ or access.reviewer,
        },
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
    }


# ------------------------------------------------------------------ visão geral


@router.get("/summary", summary="Pessoal do ciclo: CCs, headcount, custo 2027, admissões e desligamentos")
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    visible = _visible(db, user)
    stmt = select(CostCenter).where(CostCenter.is_active).order_by(CostCenter.code)
    if visible is not None:
        stmt = stmt.where(CostCenter.id.in_(visible or {-1}))
    ccs = db.scalars(stmt).all()
    ids = {c.id for c in ccs}
    scenario = svc.baseline(db, ctx)
    positions = svc.build_positions(db, ctx, ids)
    subs = svc.submissions_by_cc(db, ctx)
    actual = svc.personnel_actual(db, ctx, ids)
    overall = svc.Totals()
    rows = []
    counts: dict[str, int] = defaultdict(int)
    terminations_by_month = [0] * 12
    hires_by_month = [0] * 12
    by_position: dict[str, int] = defaultdict(int)
    for cc in ccs:
        pos = positions.get(cc.id, [])
        t = svc.totals_for(pos, scenario)
        overall.add(t)
        for p in pos:
            mv = p.movement
            if p.kind == "HIRE":
                hires_by_month[p.plan.effective_month - 1] += p.plan.quantity
                by_position[p.extra.get("position") or "Sem cargo"] += p.plan.quantity
            elif p.kind == "EMPLOYEE" and mv is not None and mv.movement_type == "TERMINATION":
                terminations_by_month[mv.effective_month - 1] += 1
        sub = subs.get(cc.id)
        status = sub.status if sub else "DRAFT"
        counts[status] += 1
        a = actual.get(cc.id) or {"prev": ZERO, "ref_ytd": ZERO, "ref_annualized": ZERO}
        if not pos and not a["ref_annualized"] and sub is None:
            continue  # CC sem quadro, sem realizado de pessoal e não iniciado
        rows.append(
            {
                "cost_center_id": cc.id,
                "code": cc.code,
                "name": cc.name,
                "company_code": cc.company.code,
                "manager_name": cc.manager_name,
                "submission_id": sub.id if sub else None,
                "status": status,
                "status_label": STATUS_LABELS[status],
                "headcount_start": t.headcount[0],
                "headcount_end": t.headcount[11],
                "hires": t.hires,
                "terminations": t.terminations,
                "annual": str(t.annual),
                "ref_annualized": str(money(a["ref_annualized"])),
                "variation_pct": str(
                    ((t.annual - a["ref_annualized"]) / a["ref_annualized"]).quantize(Decimal("0.0001"))
                )
                if a["ref_annualized"] and t.annual
                else None,
            }
        )
    return {
        "cycle": {
            "name": ctx.cycle.name,
            "status": ctx.cycle.status,
            "deadline": ctx.cycle.personnel_deadline.isoformat() if ctx.cycle.personnel_deadline else None,
        },
        "years": {"prev": ctx.prev_year, "ref": ctx.ref_year, "target": ctx.target_year},
        "scenario": svc.scenario_out(scenario),
        "status_counts": dict(counts),
        "rows": rows,
        "totals": svc.totals_out(overall),
        "ref_annualized": str(money(sum((v["ref_annualized"] for v in actual.values()), ZERO))),
        "terminations_by_month": terminations_by_month,
        "hires_by_month": hires_by_month,
        "hires_by_position": [{"label": k, "count": v} for k, v in sorted(by_position.items(), key=lambda kv: -kv[1])],
    }


# ------------------------------------------------------------------ orçamento do CC


@router.get("/cost-centers/{cost_center_id}", summary="Abre (ou cria) o orçamento de pessoal do CC")
def open_cost_center(cost_center_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    cc = db.get(CostCenter, cost_center_id)
    if cc is None:
        raise HTTPException(404, "Centro de custo não encontrado")
    sub = opex_svc.get_submission(db, ctx, cc.id, module=svc.MODULE)
    access = Access(db, ctx, user, sub)
    if not access.view:
        db.rollback()
        raise HTTPException(403, "Sem acesso a este centro de custo")
    db.commit()
    return _header(db, ctx, sub, access)


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, access = _load(db, user, submission_id)
    return _header(db, ctx, sub, access)


@router.get("/submissions/{submission_id}/view", summary="Quadro, vagas, projeção mensal e totais")
def view(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, _ = _load(db, user, submission_id)
    return svc.submission_view(db, ctx, sub)


class MovementIn(BaseModel):
    type: Literal["KEEP", "PROMOTION", "SALARY_ADJUSTMENT", "TERMINATION", "TRANSFER", "HIRE"]
    month: int | None = None
    new_salary: Decimal | None = None
    new_position: str | None = None
    target_cost_center_id: int | None = None
    severance_cost: Decimal | None = None
    reason: str | None = None


def _movement_snapshot(mv: PersonnelMovement | None) -> dict | None:
    if mv is None:
        return None
    return {
        "type": mv.movement_type,
        "month": mv.effective_month,
        "new_salary": None if mv.new_salary is None else str(mv.new_salary),
        "quantity": mv.quantity,
        "target_cost_center_id": mv.target_cost_center_id,
        "severance_cost": None if mv.severance_cost is None else str(mv.severance_cost),
        "reason": mv.reason,
        "attributes": mv.attributes,
    }


@router.put("/submissions/{submission_id}/employees/{employee_id}/movement", summary="Ação do colaborador no ano")
def set_movement(
    submission_id: int,
    employee_id: int,
    payload: MovementIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    _require_edit(access)
    emp = db.get(Employee, employee_id)
    if emp is None:
        raise HTTPException(404, "Colaborador não encontrado")
    before = _movement_snapshot(
        db.scalar(
            select(PersonnelMovement).where(
                PersonnelMovement.submission_id == sub.id, PersonnelMovement.employee_id == emp.id
            )
        )
    )
    try:
        mv = svc.set_employee_movement(db, sub, emp, payload.model_dump(), user.id)
    except svc.PersonnelError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="PERSONNEL_MOVEMENT",
        entity_type="employee",
        entity_id=emp.id,
        before=before,
        after=_movement_snapshot(mv) or {"type": "KEEP"},
        ip=client_ip(request),
    )
    db.commit()
    return svc.submission_view(db, ctx, sub)


class HireIn(BaseModel):
    position_name: str
    quantity: int = Field(1, ge=1)
    month: int
    new_salary: Decimal
    contract_type_code: str = "CLT"
    reason: str | None = None


@router.post("/submissions/{submission_id}/hires", status_code=201, summary="Nova vaga (contratação planejada)")
def create_hire(
    submission_id: int,
    payload: HireIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    _require_edit(access)
    try:
        mv = svc.save_hire(db, sub, payload.model_dump(), user.id)
    except svc.PersonnelError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="CREATE",
        entity_type="personnel_movement",
        entity_id=mv.id,
        after=_movement_snapshot(mv),
        ip=client_ip(request),
    )
    db.commit()
    return svc.submission_view(db, ctx, sub)


def _movement(db: Session, user: User, movement_id: int):
    mv = db.get(PersonnelMovement, movement_id)
    if mv is None:
        raise HTTPException(404, "Movimentação não encontrada")
    ctx, sub, access = _load(db, user, mv.submission_id)
    return ctx, sub, access, mv


@router.patch("/movements/{movement_id}", summary="Altera uma vaga")
def update_hire(
    movement_id: int,
    payload: HireIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access, mv = _movement(db, user, movement_id)
    _require_edit(access)
    if mv.movement_type != "HIRE":
        raise HTTPException(409, "Use a ação do colaborador para alterar esta movimentação")
    before = _movement_snapshot(mv)
    try:
        svc.save_hire(db, sub, payload.model_dump(), user.id, mv)
    except svc.PersonnelError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type="personnel_movement",
        entity_id=mv.id,
        before=before,
        after=_movement_snapshot(mv),
        ip=client_ip(request),
    )
    db.commit()
    return svc.submission_view(db, ctx, sub)


@router.delete("/movements/{movement_id}", summary="Exclui uma vaga ou volta o colaborador para 'Manter'")
def delete_movement(
    movement_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ctx, sub, access, mv = _movement(db, user, movement_id)
    _require_edit(access)
    audit.record(
        db,
        user_id=user.id,
        action="DELETE",
        entity_type="personnel_movement",
        entity_id=mv.id,
        before=_movement_snapshot(mv),
        ip=client_ip(request),
    )
    db.delete(mv)
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    db.commit()
    return svc.submission_view(db, ctx, sub)


# ------------------------------------------------------------------ workflow


class ActionIn(BaseModel):
    comment: str | None = None


@router.post("/submissions/{submission_id}/actions/{action}")
def do_action(
    submission_id: int,
    action: str,
    payload: ActionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    blockers: list[str] = []
    pkg = _people_package(db)
    if action == "submit":
        if ctx.cycle.status != "OPEN" and not access.global_:
            blockers.append("ciclo não está aberto")
        blockers += svc.blockers(db, ctx, sub)
    if action == "approve" and pkg is not None and pkg.package_type == 1:
        review = db.scalar(
            select(PackageReview).where(PackageReview.submission_id == sub.id, PackageReview.package_id == pkg.id)
        )
        if review is not None and review.status != "APPROVED":
            blockers.append(f"validação GMD obrigatória pendente: {pkg.name}")
    try:
        t = check_transition(
            action, sub.status, roles=access.roles, is_owner=access.owner, comment=payload.comment, blockers=blockers
        )
    except WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    before = sub.status
    opex_svc.log_event(
        db,
        sub,
        action=action,
        to_status=t.target,
        user_id=user.id,
        comment=payload.comment,
        version_label=ctx.version.label,
    )
    if action == "submit" and pkg is not None and pkg.package_type == 1:
        review = db.scalar(
            select(PackageReview).where(PackageReview.submission_id == sub.id, PackageReview.package_id == pkg.id)
        )
        if review is None:
            db.add(PackageReview(submission_id=sub.id, package_id=pkg.id, status="PENDING"))
        else:
            review.status, review.comment, review.reviewer_id = "PENDING", None, None
    audit.record(
        db,
        user_id=user.id,
        action=f"WORKFLOW_{action.upper()}",
        entity_type="budget_submission",
        entity_id=sub.id,
        before={"status": before},
        after={"status": t.target, "module": svc.MODULE},
        reason=payload.comment,
        ip=client_ip(request),
    )
    db.commit()
    return _header(db, ctx, sub, Access(db, ctx, user, sub))


class ReviewIn(BaseModel):
    status: Literal["APPROVED", "ADJUST_REQUESTED"]
    comment: str | None = None


@router.post("/submissions/{submission_id}/package-review", summary="Validação GMD do pacote Pessoas")
def review_package(
    submission_id: int,
    payload: ReviewIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    pkg = _people_package(db)
    if pkg is None or not (access.global_ or access.reviewer):
        raise HTTPException(403, "Você não é gestor do pacote Pessoas")
    if sub.status not in ("SUBMITTED", "UNDER_REVIEW"):
        raise HTTPException(409, "A validação GMD ocorre com o orçamento enviado ou em análise")
    if payload.status != "APPROVED" and not (payload.comment or "").strip():
        raise HTTPException(422, "Explique no comentário o ajuste necessário")
    review = db.scalar(
        select(PackageReview).where(PackageReview.submission_id == sub.id, PackageReview.package_id == pkg.id)
    )
    if review is None:
        review = PackageReview(submission_id=sub.id, package_id=pkg.id)
        db.add(review)
    review.status, review.comment, review.reviewer_id = payload.status, payload.comment, user.id
    if payload.status == "ADJUST_REQUESTED":
        opex_svc.log_event(
            db,
            sub,
            action="package_adjustment",
            to_status="ADJUSTMENT_REQUESTED",
            user_id=user.id,
            comment=f"[{pkg.name}] {payload.comment}",
            version_label=ctx.version.label,
        )
    audit.record(
        db,
        user_id=user.id,
        action="PACKAGE_REVIEW",
        entity_type="package_review",
        entity_id=f"{sub.id}:{pkg.id}",
        after={"status": payload.status, "comment": payload.comment},
        ip=client_ip(request),
    )
    db.commit()
    return _header(db, ctx, sub, Access(db, ctx, user, sub))


@router.get("/submissions/{submission_id}/events")
def events(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _load(db, user, submission_id)
    rows = db.execute(
        select(WorkflowEvent, User.name)
        .join(User, User.id == WorkflowEvent.user_id, isouter=True)
        .where(WorkflowEvent.submission_id == submission_id)
        .order_by(WorkflowEvent.id.desc())
    )
    return [
        {
            "action": e.action,
            "from_status": e.from_status,
            "to_status": e.to_status,
            "to_label": STATUS_LABELS.get(e.to_status, e.to_status),
            "comment": e.comment,
            "user": name,
            "version": e.version_label,
            "created_at": e.created_at.isoformat(),
        }
        for e, name in rows
    ]


@router.get("/options", summary="Contratos, cargos, centros de custo (transferência) e cenário base")
def options(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    ctx = _ctx(db)
    return {
        "contract_types": [
            {"code": c.code, "name": c.name, "apply_multiplier": c.apply_multiplier}
            for c in svc.contract_types(db).values()
        ],
        "positions": [p.name for p in db.scalars(select(JobPosition).order_by(JobPosition.name))],
        "cost_centers": [
            {"id": c.id, "code": c.code, "name": c.name, "company_id": c.company_id}
            for c in db.scalars(select(CostCenter).where(CostCenter.is_active).order_by(CostCenter.name))
        ],
        "scenario": svc.scenario_out(svc.baseline(db, ctx)),
    }


# ------------------------------------------------------------------ cenários e what-if


class ScenarioIn(BaseModel):
    name: str | None = None
    multipliers: dict[str, Decimal] = Field(default_factory=dict, description="Ex.: {'CLT': 2.0}")
    salary_adjustment_pct: Decimal | None = None
    adjustment_month: int | None = Field(None, ge=1, le=12)


class WhatIfIn(ScenarioIn):
    cost_center_ids: list[int] | None = None


@router.post("/what-if", summary="Cenário base × simulado (multiplicador por contrato, reajuste e data-base)")
def what_if(payload: WhatIfIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    visible = _visible(db, user)
    ids = set(payload.cost_center_ids or []) or None
    if visible is not None:
        ids = (ids or visible) & visible
    base = svc.baseline(db, ctx)
    base_row = db.get(PersonnelScenario, base.id) if base.id else None
    try:
        sim = svc.scenario_info(
            db,
            base_row,
            multipliers=payload.multipliers,
            salary_adjustment_pct=payload.salary_adjustment_pct,
            adjustment_month=payload.adjustment_month,
            name=payload.name or "Simulação",
        )
    except svc.PersonnelError as exc:
        raise HTTPException(422, str(exc)) from exc
    positions = svc.build_positions(db, ctx, ids)
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(list(positions) or [-1])))}
    total_base, total_sim = svc.Totals(), svc.Totals()
    by_cc = []
    by_contract: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"base": ZERO, "sim": ZERO, "people": ZERO})
    for cc_id, pos in positions.items():
        tb, ts = svc.totals_for(pos, base), svc.totals_for(pos, sim)
        total_base.add(tb)
        total_sim.add(ts)
        for p in pos:
            c = by_contract[p.plan.contract_type]
            c["base"] += sum(svc.position_cost(p, base)[0], ZERO)
            c["sim"] += sum(svc.position_cost(p, sim)[0], ZERO)
            c["people"] += p.plan.quantity
        cc = ccs.get(cc_id)
        by_cc.append(
            {
                "cost_center_id": cc_id,
                "code": cc.code if cc else None,
                "name": cc.name if cc else "—",
                "base": str(tb.annual),
                "simulated": str(ts.annual),
                "difference": str(money(ts.annual - tb.annual)),
            }
        )
    by_cc.sort(key=lambda r: -abs(Decimal(r["difference"])))
    diff = money(total_sim.annual - total_base.annual)
    return {
        "baseline": svc.scenario_out(base),
        "simulated": svc.scenario_out(sim),
        "base": svc.totals_out(total_base),
        "simulation": svc.totals_out(total_sim),
        "difference": str(diff),
        "difference_pct": str((diff / total_base.annual).quantize(Decimal("0.0001"))) if total_base.annual else None,
        "monthly_impact": [str(money(total_sim.monthly[i] - total_base.monthly[i])) for i in range(12)],
        "by_cost_center": by_cc,
        "by_contract": [
            {
                "contract": k,
                "people": int(v["people"]),
                "base": str(money(v["base"])),
                "simulated": str(money(v["sim"])),
                "difference": str(money(v["sim"] - v["base"])),
            }
            for k, v in sorted(by_contract.items())
        ],
    }


def _scenario_row(db: Session, row: PersonnelScenario) -> dict:
    info = svc.scenario_info(db, row)
    return svc.scenario_out(info) | {"is_baseline": row.is_baseline}


@router.get("/scenarios")
def list_scenarios(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    ctx = _ctx(db)
    rows = db.scalars(
        select(PersonnelScenario).where(PersonnelScenario.cycle_id == ctx.cycle.id).order_by(PersonnelScenario.id)
    )
    return [_scenario_row(db, r) for r in rows]


def _apply_scenario(db: Session, row: PersonnelScenario, payload: ScenarioIn) -> None:
    info = svc.scenario_info(
        db,
        row if row.id else None,
        multipliers=payload.multipliers,
        salary_adjustment_pct=payload.salary_adjustment_pct,
        adjustment_month=payload.adjustment_month,
    )
    row.salary_adjustment_pct = info.salary_adjustment_pct
    row.adjustment_month = info.adjustment_month
    if payload.name:
        row.name = payload.name.strip()
    if row.id is None:
        db.add(row)
        db.flush()
    db.execute(ScenarioMultiplier.__table__.delete().where(ScenarioMultiplier.scenario_id == row.id))
    for code, mult in info.multipliers.items():
        db.add(ScenarioMultiplier(scenario_id=row.id, contract_type_code=code, multiplier=mult))


@router.post("/scenarios", status_code=201, summary="Salvar cenário (não altera o base)")
def create_scenario(
    payload: ScenarioIn, request: Request, db: Session = Depends(get_db), user: User = Depends(planner)
):
    ctx = _ctx(db)
    if not (payload.name or "").strip():
        raise HTTPException(422, "Informe o nome do cenário")
    row = PersonnelScenario(cycle_id=ctx.cycle.id, name=payload.name.strip(), is_baseline=False, created_by=user.id)
    base = svc.baseline(db, ctx)
    payload = payload.model_copy(
        update={
            "multipliers": base.multipliers | payload.multipliers,
            "salary_adjustment_pct": base.salary_adjustment_pct
            if payload.salary_adjustment_pct is None
            else payload.salary_adjustment_pct,
            "adjustment_month": payload.adjustment_month or base.adjustment_month,
        }
    )
    try:
        _apply_scenario(db, row, payload)
    except svc.PersonnelError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        user_id=user.id,
        action="CREATE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        after=_scenario_row(db, row),
        ip=client_ip(request),
    )
    db.commit()
    return _scenario_row(db, row)


@router.patch("/scenarios/{scenario_id}", summary="Alterar cenário (o base afeta todos os cálculos)")
def update_scenario(
    scenario_id: int,
    payload: ScenarioIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(planner),
):
    row = db.get(PersonnelScenario, scenario_id)
    if row is None:
        raise HTTPException(404, "Cenário não encontrado")
    before = _scenario_row(db, row)
    try:
        _apply_scenario(db, row, payload)
    except svc.PersonnelError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.flush()
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        before=before,
        after=_scenario_row(db, row),
        ip=client_ip(request),
    )
    db.commit()
    return _scenario_row(db, row)


@router.post("/scenarios/{scenario_id}/baseline", summary="Tornar o cenário a base do orçamento de pessoal")
def set_baseline(scenario_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(planner)):
    ctx = _ctx(db)
    row = db.get(PersonnelScenario, scenario_id)
    if row is None or row.cycle_id != ctx.cycle.id:
        raise HTTPException(404, "Cenário não encontrado")
    for other in db.scalars(select(PersonnelScenario).where(PersonnelScenario.cycle_id == ctx.cycle.id)):
        other.is_baseline = other.id == row.id
    audit.record(
        db,
        user_id=user.id,
        action="SET_BASELINE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        after={"name": row.name},
        ip=client_ip(request),
    )
    db.commit()
    return _scenario_row(db, row)


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(planner)):
    row = db.get(PersonnelScenario, scenario_id)
    if row is None:
        raise HTTPException(404, "Cenário não encontrado")
    if row.is_baseline:
        raise HTTPException(409, "O cenário base não pode ser excluído; defina outro como base antes")
    audit.record(
        db,
        user_id=user.id,
        action="DELETE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        before=_scenario_row(db, row),
        ip=client_ip(request),
    )
    db.delete(row)
    db.commit()
    return {"deleted": scenario_id}
