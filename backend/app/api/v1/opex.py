"""Módulo OPEX: orçamento por centro de custo, linhas por pacote, justificativas e workflow."""

from collections import defaultdict
from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user, is_global, visible_cost_center_ids
from app.db import get_db
from app.domain.workflow import EDITABLE, STATUS_LABELS, WorkflowError, available_actions, check_transition
from app.models import (
    Account,
    AccountDetail,
    AccountJustification,
    ActualEntry,
    Branch,
    BudgetLine,
    BudgetPackage,
    BudgetSubmission,
    CostCenter,
    DatasetVersion,
    LookupValue,
    PackageManager,
    PackageReview,
    User,
    WorkflowEvent,
)
from app.services import audit
from app.services import opex as svc

router = APIRouter(prefix="/opex", tags=["OPEX"])
ZERO = Decimal("0")


def _ctx(db: Session) -> svc.Context:
    try:
        return svc.context(db)
    except svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


def _managed_packages(db: Session, ctx: svc.Context, user: User) -> dict[int, set[int | None]]:
    """Pacotes em que o usuário é gestor GMD → empresas (None = todas)."""
    out: dict[int, set[int | None]] = defaultdict(set)
    for pm in db.scalars(
        select(PackageManager).where(PackageManager.cycle_id == ctx.cycle.id, PackageManager.user_id == user.id)
    ):
        out[pm.package_id].add(pm.company_id)
    return out


class Access:
    def __init__(self, db: Session, ctx: svc.Context, user: User, sub: BudgetSubmission) -> None:
        self.cc = db.get(CostCenter, sub.cost_center_id)
        visible = visible_cost_center_ids(db, user)
        self.global_ = is_global(user)
        self.owner = self.cc.manager_user_id == user.id or (visible is not None and self.cc.id in visible)
        managed = _managed_packages(db, ctx, user)
        self.reviewable = {
            pkg for pkg, companies in managed.items() if None in companies or self.cc.company_id in companies
        }
        self.view = self.global_ or self.owner or bool(self.reviewable)
        cycle_ok = ctx.cycle.status == "OPEN" or self.global_
        self.edit = (self.global_ or self.owner) and sub.status in EDITABLE and cycle_ok
        self.cycle_blocked = not cycle_ok
        self.roles = set(user.role_codes)


def _load(db: Session, user: User, submission_id: int) -> tuple[svc.Context, BudgetSubmission, Access]:
    ctx = _ctx(db)
    sub = db.get(BudgetSubmission, submission_id)
    if sub is None or sub.module != "OPEX":
        raise HTTPException(404, "Orçamento não encontrado")
    access = Access(db, ctx, user, sub)
    if not access.view:
        raise HTTPException(403, "Sem acesso a este centro de custo")
    return ctx, sub, access


def _require_edit(access: Access) -> None:
    if not access.edit:
        if access.cycle_blocked:
            raise HTTPException(409, "O ciclo orçamentário ainda não está aberto para preenchimento")
        raise HTTPException(409, "Este orçamento não está em edição (envie de volta para ajuste para alterar)")


def _header(db: Session, ctx: svc.Context, sub: BudgetSubmission, access: Access) -> dict:
    reviews = db.scalars(select(PackageReview).where(PackageReview.submission_id == sub.id)).all()
    packages = {p.id: p for p in db.scalars(select(BudgetPackage))}
    users = {
        u.id: u.name
        for u in db.scalars(select(User).where(User.id.in_([r.reviewer_id for r in reviews if r.reviewer_id])))
    }
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
            "deadline": ctx.cycle.opex_deadline.isoformat() if ctx.cycle.opex_deadline else None,
        },
        "version": ctx.version.label,
        "years": {"prev": ctx.prev_year, "ref": ctx.ref_year, "target": ctx.target_year},
        "permissions": {
            "edit": access.edit,
            "owner": access.owner,
            "global": access.global_,
            "review_packages": sorted(access.reviewable),
            "cycle_blocked": access.cycle_blocked,
        },
        "actions": available_actions(sub.status, roles=access.roles, is_owner=access.owner),
        "package_reviews": [
            {
                "package_id": r.package_id,
                "package": packages[r.package_id].name,
                "status": r.status,
                "comment": r.comment,
                "reviewer": users.get(r.reviewer_id),
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
                "can_review": access.global_ or r.package_id in access.reviewable,
            }
            for r in reviews
        ],
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
    }


# ------------------------------------------------------------------ listas


@router.get("/summary", summary="Centros de custo do usuário com status e totais do orçamento OPEX")
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    visible = visible_cost_center_ids(db, user)
    stmt = select(CostCenter).where(CostCenter.is_active).order_by(CostCenter.code)
    if visible is not None:
        stmt = stmt.where(CostCenter.id.in_(visible or {-1}))
    ccs = db.scalars(stmt).all()
    ids = [c.id for c in ccs]
    subs = {
        s.cost_center_id: s
        for s in db.scalars(
            select(BudgetSubmission).where(
                BudgetSubmission.version_id == ctx.version.id,
                BudgetSubmission.module == "OPEX",
                BudgetSubmission.cost_center_id.in_(ids),
            )
        )
    }
    proposed = dict(
        db.execute(
            select(BudgetLine.cost_center_id, func.sum(BudgetLine.total_amount))
            .join(BudgetSubmission, BudgetSubmission.id == BudgetLine.submission_id)
            .where(BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.module == "OPEX")
            .group_by(BudgetLine.cost_center_id)
        ).all()
    )

    def actual(year: int) -> dict[int, Decimal]:
        return dict(
            db.execute(
                select(ActualEntry.cost_center_id, func.sum(ActualEntry.amount))
                .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
                .join(Account, Account.id == ActualEntry.account_id)
                .where(
                    DatasetVersion.is_current,
                    ActualEntry.fiscal_year == year,
                    Account.nature.in_(svc.OPEX_NATURES),
                    ActualEntry.cost_center_id.in_(ids),
                )
                .group_by(ActualEntry.cost_center_id)
            ).all()
        )

    prev, ref = actual(ctx.prev_year), actual(ctx.ref_year)
    closed_by_company = {}
    rows = []
    for cc in ccs:
        code = cc.company.code
        if code not in closed_by_company:
            closed_by_company[code] = svc.closed_period(db, ctx.ref_year, code)
        closed = closed_by_company[code]
        r = ref.get(cc.id, ZERO)
        annualized = r * 12 / closed if closed else ZERO
        p = proposed.get(cc.id, ZERO)
        sub = subs.get(cc.id)
        rows.append(
            {
                "cost_center_id": cc.id,
                "code": cc.code,
                "name": cc.name,
                "company_code": code,
                "manager_name": cc.manager_name,
                "has_manager_user": cc.manager_user_id is not None,
                "submission_id": sub.id if sub else None,
                "status": sub.status if sub else "DRAFT",
                "status_label": STATUS_LABELS[sub.status if sub else "DRAFT"],
                "submitted_at": sub.submitted_at.isoformat() if sub and sub.submitted_at else None,
                "prev_actual": str(prev.get(cc.id, ZERO)),
                "ref_annualized": str(annualized.quantize(Decimal("0.01"))),
                "proposed": str(p),
                "variation_pct": str(((p - annualized) / annualized).quantize(Decimal("0.0001")))
                if annualized and p
                else None,
            }
        )
    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
    return {
        "cycle": {
            "name": ctx.cycle.name,
            "status": ctx.cycle.status,
            "deadline": ctx.cycle.opex_deadline.isoformat() if ctx.cycle.opex_deadline else None,
        },
        "years": {"prev": ctx.prev_year, "ref": ctx.ref_year, "target": ctx.target_year},
        "status_counts": dict(counts),
        "rows": rows,
    }


@router.get("/review-queue", summary="Orçamentos aguardando validação GMD dos pacotes do usuário")
def review_queue(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    managed = _managed_packages(db, ctx, user)
    stmt = (
        select(PackageReview, BudgetSubmission, CostCenter, BudgetPackage)
        .join(BudgetSubmission, BudgetSubmission.id == PackageReview.submission_id)
        .join(CostCenter, CostCenter.id == BudgetSubmission.cost_center_id)
        .join(BudgetPackage, BudgetPackage.id == PackageReview.package_id)
        .where(
            BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.status.in_(("SUBMITTED", "UNDER_REVIEW"))
        )
        .order_by(BudgetSubmission.submitted_at)
    )
    out = []
    for review, sub, cc, pkg in db.execute(stmt):
        companies = managed.get(pkg.id)
        if not is_global(user) and (companies is None or (None not in companies and cc.company_id not in companies)):
            continue
        total = db.scalar(
            select(func.sum(BudgetLine.total_amount)).where(
                BudgetLine.submission_id == sub.id, BudgetLine.package_id == pkg.id
            )
        )
        out.append(
            {
                "submission_id": sub.id,
                "cost_center_id": cc.id,
                "cost_center": f"{cc.code} · {cc.name}",
                "package_id": pkg.id,
                "package": pkg.name,
                "review_status": review.status,
                "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
                "package_total": str(total or ZERO),
            }
        )
    return out


# ------------------------------------------------------------------ orçamento do CC


@router.get("/cost-centers/{cost_center_id}", summary="Abre (ou cria) o orçamento OPEX do centro de custo")
def open_cost_center(cost_center_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    cc = db.get(CostCenter, cost_center_id)
    if cc is None:
        raise HTTPException(404, "Centro de custo não encontrado")
    sub = svc.get_submission(db, ctx, cc.id)
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


@router.get("/submissions/{submission_id}/accounts", summary="Histórico × proposta por conta, com alertas")
def accounts(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, _ = _load(db, user, submission_id)
    return svc.account_view(db, ctx, sub)


@router.get("/submissions/{submission_id}/accounts/{account_id}/monthly")
def account_monthly(
    submission_id: int, account_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ctx, sub, _ = _load(db, user, submission_id)
    return svc.account_monthly(db, ctx, sub, account_id)


@router.get("/submissions/{submission_id}/lines")
def list_lines(
    submission_id: int,
    package_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    _, sub, access = _load(db, user, submission_id)
    stmt = select(BudgetLine).where(BudgetLine.submission_id == sub.id)
    if package_id:
        stmt = stmt.where(BudgetLine.package_id == package_id)
    elif not (access.global_ or access.owner):
        stmt = stmt.where(BudgetLine.package_id.in_(access.reviewable or {-1}))
    return [svc.line_out(line) for line in db.scalars(stmt.order_by(BudgetLine.id))]


class LineIn(BaseModel):
    line_type: Literal["GENERIC", "TRAVEL", "EVENT"] = "GENERIC"
    account_id: int | None = None
    package_id: int | None = None
    branch_id: int | None = None
    account_detail_id: int | None = None
    description: str | None = None
    justification: str | None = None
    supplier: str | None = None
    contract_manager: str | None = None
    attributes: dict[str, Any] | None = None
    values: dict[int, Decimal] = Field(default_factory=dict)
    travel: dict[str, Any] | None = None
    event: dict[str, Any] | None = None


@router.post("/submissions/{submission_id}/lines", status_code=201)
def create_line(
    submission_id: int,
    payload: LineIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    _require_edit(access)
    if payload.line_type != "TRAVEL" and payload.account_id is None:
        raise HTTPException(422, "Informe a conta contábil")
    try:
        lines = svc.create_line(db, ctx, sub, payload.model_dump(), user.id)
    except (svc.OpexError, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    for line in lines:
        audit.record(
            db,
            user_id=user.id,
            action="CREATE",
            entity_type="budget_line",
            entity_id=line.id,
            after=svc.line_out(line),
            ip=client_ip(request),
        )
    db.commit()
    return [svc.line_out(line) for line in lines]


class LineUpdate(BaseModel):
    account_id: int | None = None
    branch_id: int | None = None
    account_detail_id: int | None = None
    description: str | None = None
    justification: str | None = None
    supplier: str | None = None
    contract_manager: str | None = None
    attributes: dict[str, Any] | None = None
    values: dict[int, Decimal] | None = None


def _line(db: Session, user: User, line_id: int):
    line = db.get(BudgetLine, line_id)
    if line is None:
        raise HTTPException(404, "Linha não encontrada")
    ctx, sub, access = _load(db, user, line.submission_id)
    return ctx, sub, access, line


@router.patch("/lines/{line_id}")
def update_line(
    line_id: int,
    payload: LineUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access, line = _line(db, user, line_id)
    _require_edit(access)
    before = svc.line_out(line)
    try:
        svc.update_line(db, ctx, line, payload.model_dump(exclude_unset=True), user.id)
    except (svc.OpexError, ValueError) as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    svc.mark_in_progress(db, sub, ctx, user.id)
    after = svc.line_out(line)
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type="budget_line",
        entity_id=line.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    db.commit()
    return after


@router.delete("/lines/{line_id}", summary="Exclui a linha (viagem: exclui as 3 contas geradas)")
def delete_line(line_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, access, line = _line(db, user, line_id)
    _require_edit(access)
    removed = svc.group_lines(db, line)
    for item in removed:
        audit.record(
            db,
            user_id=user.id,
            action="DELETE",
            entity_type="budget_line",
            entity_id=item.id,
            before=svc.line_out(item),
            ip=client_ip(request),
        )
        db.delete(item)
    svc.mark_in_progress(db, sub, ctx, user.id)
    db.commit()
    return {"deleted": [i.id for i in removed]}


class JustificationIn(BaseModel):
    text: str


@router.put("/submissions/{submission_id}/justifications/{account_id}")
def set_justification(
    submission_id: int,
    account_id: int,
    payload: JustificationIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    _require_edit(access)
    if db.get(Account, account_id) is None:
        raise HTTPException(404, "Conta não encontrada")
    just = db.get(AccountJustification, (sub.id, account_id))
    before = {"text": just.text} if just else None
    text = payload.text.strip()
    if not text:
        if just:
            db.delete(just)
    elif just:
        just.text, just.updated_by = text, user.id
    else:
        db.add(AccountJustification(submission_id=sub.id, account_id=account_id, text=text, updated_by=user.id))
    svc.mark_in_progress(db, sub, ctx, user.id)
    audit.record(
        db,
        user_id=user.id,
        action="JUSTIFY",
        entity_type="account_justification",
        entity_id=f"{sub.id}:{account_id}",
        before=before,
        after={"text": text},
        ip=client_ip(request),
    )
    db.commit()
    return {"account_id": account_id, "text": text or None}


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
    if action == "submit":
        if ctx.cycle.status != "OPEN" and not access.global_:
            blockers.append("ciclo não está aberto")
        blockers += svc.submit_blockers(db, ctx, sub)
    if action == "approve":
        pending = db.scalars(
            select(BudgetPackage.name)
            .join(PackageReview, PackageReview.package_id == BudgetPackage.id)
            .where(PackageReview.submission_id == sub.id, PackageReview.status != "APPROVED")
        ).all()
        if pending:
            blockers.append("validação GMD obrigatória pendente: " + ", ".join(pending))
    try:
        t = check_transition(
            action, sub.status, roles=access.roles, is_owner=access.owner, comment=payload.comment, blockers=blockers
        )
    except WorkflowError as exc:
        raise HTTPException(409, str(exc)) from exc
    before = sub.status
    svc.log_event(
        db,
        sub,
        action=action,
        to_status=t.target,
        user_id=user.id,
        comment=payload.comment,
        version_label=ctx.version.label,
    )
    if action == "submit":
        _open_package_reviews(db, sub)
    audit.record(
        db,
        user_id=user.id,
        action=f"WORKFLOW_{action.upper()}",
        entity_type="budget_submission",
        entity_id=sub.id,
        before={"status": before},
        after={"status": t.target},
        reason=payload.comment,
        ip=client_ip(request),
    )
    db.commit()
    return _header(db, ctx, sub, Access(db, ctx, user, sub))


def _open_package_reviews(db: Session, sub: BudgetSubmission) -> None:
    """A cada envio, pacotes Tipo 1 presentes no orçamento voltam a exigir validação do gestor do pacote."""
    type1 = set(
        db.scalars(
            select(BudgetLine.package_id)
            .join(BudgetPackage, BudgetPackage.id == BudgetLine.package_id)
            .where(BudgetLine.submission_id == sub.id, BudgetPackage.package_type == 1)
            .distinct()
        )
    )
    existing = {r.package_id: r for r in db.scalars(select(PackageReview).where(PackageReview.submission_id == sub.id))}
    for pkg_id, review in existing.items():
        if pkg_id not in type1:
            db.delete(review)
    for pkg_id in type1:
        review = existing.get(pkg_id)
        if review is None:
            db.add(PackageReview(submission_id=sub.id, package_id=pkg_id, status="PENDING"))
        else:
            review.status, review.comment, review.reviewer_id = "PENDING", None, None


class ReviewIn(BaseModel):
    status: Literal["APPROVED", "ADJUST_REQUESTED", "COMMENTED"]
    comment: str | None = None


@router.post("/submissions/{submission_id}/package-reviews/{package_id}", summary="Validação GMD do gestor de pacote")
def review_package(
    submission_id: int,
    package_id: int,
    payload: ReviewIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    if not (access.global_ or package_id in access.reviewable):
        raise HTTPException(403, "Você não é gestor deste pacote")
    if sub.status not in ("SUBMITTED", "UNDER_REVIEW"):
        raise HTTPException(409, "A validação GMD ocorre com o orçamento enviado ou em análise")
    if payload.status != "APPROVED" and not (payload.comment or "").strip():
        raise HTTPException(422, "Explique no comentário o ajuste necessário")
    review = db.scalar(
        select(PackageReview).where(PackageReview.submission_id == sub.id, PackageReview.package_id == package_id)
    )
    if review is None:
        review = PackageReview(submission_id=sub.id, package_id=package_id)
        db.add(review)
    review.status, review.comment, review.reviewer_id = payload.status, payload.comment, user.id
    if payload.status == "ADJUST_REQUESTED":
        pkg = db.get(BudgetPackage, package_id)
        svc.log_event(
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
        entity_id=f"{sub.id}:{package_id}",
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


# ------------------------------------------------------------------ opções de formulário


@router.get("/options", summary="Pacotes, contas, detalhamentos, filiais e listas para os formulários")
def options(company_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    ctx = _ctx(db)
    packages = db.scalars(
        select(BudgetPackage)
        .where(BudgetPackage.is_active, BudgetPackage.nature.in_(svc.OPEX_NATURES))
        .order_by(BudgetPackage.sort_order)
    ).all()
    accounts = db.scalars(
        select(Account).where(Account.is_active, Account.nature.in_(svc.OPEX_NATURES)).order_by(Account.code)
    ).all()
    details = defaultdict(list)
    for d in db.scalars(select(AccountDetail).order_by(AccountDetail.name)):
        details[d.account_id].append({"id": d.id, "name": d.name})
    branches = select(Branch).where(Branch.is_active).order_by(Branch.name)
    if company_id:
        branches = branches.where(Branch.company_id == company_id)
    lookups = defaultdict(list)
    for lv in db.scalars(
        select(LookupValue)
        .where(
            LookupValue.is_active,
            LookupValue.domain.in_(("TRIP_TYPE", "JOB_LEVEL", "TRAVEL_ORIGIN", "TRAVEL_DESTINATION", "EVENT_TYPE")),
        )
        .order_by(LookupValue.sort_order)
    ):
        lookups[lv.domain].append({"code": lv.code, "label": lv.label, "extra": lv.extra})
    return {
        "packages": [
            {"id": p.id, "name": p.name, "roman": p.roman, "package_type": p.package_type, "form_type": p.form_type}
            for p in packages
        ],
        "accounts": [
            {"id": a.id, "code": a.code, "name": a.name, "package_id": a.package_id, "details": details.get(a.id, [])}
            for a in accounts
        ],
        "branches": [
            {"id": b.id, "code": b.code, "name": b.name, "company_id": b.company_id} for b in db.scalars(branches)
        ],
        "lookups": lookups,
        "params": {"one_way_factor": str(ctx.param("travel.one_way_factor", 0.5))},
    }
