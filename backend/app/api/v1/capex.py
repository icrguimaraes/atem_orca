"""Módulo CAPEX: solicitações de investimento por centro de custo, itens com cronograma e workflow."""

from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.v1.opex import Access, _require_edit
from app.core.deps import client_ip, get_current_user, visible_cost_center_ids
from app.db import get_db
from app.domain.rules.common import money
from app.domain.workflow import STATUS_LABELS, WorkflowError, available_actions, check_transition
from app.models import (
    Account,
    ActualEntry,
    BudgetSubmission,
    CapexItem,
    CapexProject,
    CostCenter,
    DatasetVersion,
    User,
    WorkflowEvent,
)
from app.services import audit, template_export
from app.services import capex as svc
from app.services import opex as opex_svc

router = APIRouter(prefix="/capex", tags=["CAPEX"])
ZERO = Decimal("0")


def _ctx(db: Session) -> opex_svc.Context:
    try:
        return opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


def _load(db: Session, user: User, submission_id: int) -> tuple[opex_svc.Context, BudgetSubmission, Access]:
    ctx = _ctx(db)
    sub = db.get(BudgetSubmission, submission_id)
    if sub is None or sub.module != svc.MODULE:
        raise HTTPException(404, "Orçamento CAPEX não encontrado")
    access = Access(db, ctx, user, sub)
    if not (access.global_ or access.owner or access.read_only_scope):
        raise HTTPException(403, "Sem acesso a este centro de custo")
    return ctx, sub, access


def _header(ctx: opex_svc.Context, sub: BudgetSubmission, access: Access) -> dict:
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
            "deadline": ctx.cycle.capex_deadline.isoformat() if ctx.cycle.capex_deadline else None,
        },
        "version": ctx.version.label,
        "years": {"prev": ctx.prev_year, "ref": ctx.ref_year, "target": ctx.target_year},
        "permissions": {
            "edit": access.edit,
            "owner": access.owner,
            "global": access.global_,
            "cycle_blocked": access.cycle_blocked,
            "frozen": access.frozen,
        },
        "actions": [] if access.frozen else available_actions(sub.status, roles=access.roles, is_owner=access.owner),
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
    }


# ------------------------------------------------------------------ visão geral


@router.get("/summary", summary="CAPEX do ciclo: centros de custo, totais por conta, tipo de projeto e mês")
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
                BudgetSubmission.module == svc.MODULE,
                BudgetSubmission.cost_center_id.in_(ids or {-1}),
            )
        )
    }
    by_sub: dict[int, list[CapexProject]] = defaultdict(list)
    for p in db.scalars(select(CapexProject).where(CapexProject.submission_id.in_([s.id for s in subs.values()]))):
        by_sub[p.submission_id].append(p)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.nature == svc.MODULE))}

    ref_actual_cc = dict(
        db.execute(
            select(ActualEntry.cost_center_id, func.sum(ActualEntry.amount))
            .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
            .where(
                DatasetVersion.is_current,
                ActualEntry.fiscal_year == ctx.ref_year,
                ActualEntry.account_id.in_(accounts or {-1}),
                ActualEntry.cost_center_id.in_(ids or {-1}),
            )
            .group_by(ActualEntry.cost_center_id)
        ).all()
    )

    monthly = [ZERO] * 12
    by_account: dict[int, Decimal] = defaultdict(lambda: ZERO)
    by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    rows = []
    counts: dict[str, int] = defaultdict(int)
    for cc in ccs:
        sub = subs.get(cc.id)
        found = by_sub.get(sub.id, []) if sub else []
        total = ZERO
        critical = 0
        n_items = 0
        for p in found:
            critical += sum(1 for i in svc.project_issues(p, ctx) if i["severity"] == "CRITICAL")
            kind = (p.project_type_code or "Projeto sem tipo") if p.is_project else "Aquisição avulsa"
            for item in p.items:
                n_items += 1
                total += item.total_value
                by_account[item.account_id] += item.total_value
                by_type[kind] += item.total_value
                for v in item.values:
                    monthly[v.month - 1] += v.amount
                critical += sum(1 for i in svc.item_issues(ctx, item) if i["severity"] == "CRITICAL")
        status = sub.status if sub else "DRAFT"
        counts[status] += 1
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
                "requests": len(found),
                "projects": sum(1 for p in found if p.is_project),
                "items": n_items,
                "total": str(money(total)),
                "ref_actual": str(money(ref_actual_cc.get(cc.id, ZERO))),
                "critical": critical,
            }
        )
    return {
        "cycle": {
            "name": ctx.cycle.name,
            "status": ctx.cycle.status,
            "deadline": ctx.cycle.capex_deadline.isoformat() if ctx.cycle.capex_deadline else None,
        },
        "years": {"prev": ctx.prev_year, "ref": ctx.ref_year, "target": ctx.target_year},
        "status_counts": dict(counts),
        "rows": rows,
        "monthly": [str(money(v)) for v in monthly],
        "by_account": [
            {"code": accounts[a].code, "label": accounts[a].name, "total": str(money(v))}
            for a, v in sorted(by_account.items(), key=lambda kv: -kv[1])
            if a in accounts
        ],
        "by_type": [{"label": k, "total": str(money(v))} for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
    }


# ------------------------------------------------------------------ orçamento do CC


@router.get("/cost-centers/{cost_center_id}", summary="Abre (ou cria) o orçamento CAPEX do centro de custo")
def open_cost_center(cost_center_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    cc = db.get(CostCenter, cost_center_id)
    if cc is None:
        raise HTTPException(404, "Centro de custo não encontrado")
    # versão congelada é só leitura: não cria orçamento fora da fotografia
    sub = opex_svc.get_submission(db, ctx, cc.id, module=svc.MODULE, create=not ctx.frozen)
    if sub is None:
        raise HTTPException(404, f"Este centro de custo não tem orçamento na versão congelada {ctx.version.label}")
    access = Access(db, ctx, user, sub)
    if not (access.global_ or access.owner or access.read_only_scope):
        db.rollback()
        raise HTTPException(403, "Sem acesso a este centro de custo")
    db.commit()
    return _header(ctx, sub, access)


@router.get("/submissions/{submission_id}")
def get_submission(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, access = _load(db, user, submission_id)
    return _header(ctx, sub, access)


@router.get(
    "/submissions/{submission_id}/template.xlsx",
    summary="Orçamento CAPEX do centro de custo no layout do template Excel (reimportável)",
)
def template_xlsx(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, access = _load(db, user, submission_id)
    content = template_export.capex_template_workbook(db, ctx, sub)
    name = f"Template_CAPEX_{ctx.target_year}_{access.cc.code}_{ctx.version.file_tag}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/submissions/{submission_id}/view", summary="Solicitações, itens, pendências e consolidado")
def view(submission_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, _ = _load(db, user, submission_id)
    return svc.submission_view(db, ctx, sub)


class ItemIn(BaseModel):
    account_id: int | None = None
    asset_item_id: int | None = None
    item_name: str | None = None
    description: str | None = None
    unit_value: Decimal | None = None
    quantity: Decimal | None = None
    useful_life_months: int | None = None
    values: dict[int, Decimal] | None = None


class ProjectIn(BaseModel):
    title: str
    is_project: bool = False
    project_type_code: str | None = None
    branch_id: int | None = None
    description: str | None = None
    justification: str | None = None
    expected_cost_reduction: Decimal | None = None
    expected_revenue: Decimal | None = None
    priority: str | None = None
    budget_prev_year: Decimal | None = None
    observations: str | None = None
    items: list[ItemIn] = Field(default_factory=list)


class ProjectUpdate(BaseModel):
    title: str | None = None
    is_project: bool | None = None
    project_type_code: str | None = None
    branch_id: int | None = None
    description: str | None = None
    justification: str | None = None
    expected_cost_reduction: Decimal | None = None
    expected_revenue: Decimal | None = None
    priority: str | None = None
    budget_prev_year: Decimal | None = None
    observations: str | None = None


def _fail(db: Session, exc: Exception) -> HTTPException:
    db.rollback()
    return HTTPException(422, str(exc))


def _accounts(db: Session) -> dict[int, Account]:
    return {a.id: a for a in db.scalars(select(Account).where(Account.nature == svc.MODULE))}


@router.post("/submissions/{submission_id}/projects", status_code=201)
def create_project(
    submission_id: int,
    payload: ProjectIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access = _load(db, user, submission_id)
    _require_edit(access)
    try:
        data = payload.model_dump()
        data["items"] = [i.model_dump(exclude_unset=True) for i in payload.items]
        project = svc.create_project(db, ctx, sub, data, user.id)
    except (svc.CapexError, opex_svc.OpexError, ValueError) as exc:
        raise _fail(db, exc) from exc
    out = svc.project_out(ctx, project, _accounts(db))
    audit.record(
        db,
        user_id=user.id,
        action="CREATE",
        entity_type="capex_project",
        entity_id=project.id,
        after=out,
        ip=client_ip(request),
    )
    db.commit()
    return out


def _project(db: Session, user: User, project_id: int):
    project = db.get(CapexProject, project_id)
    if project is None:
        raise HTTPException(404, "Solicitação não encontrada")
    ctx, sub, access = _load(db, user, project.submission_id)
    return ctx, sub, access, project


@router.patch("/projects/{project_id}")
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access, project = _project(db, user, project_id)
    _require_edit(access)
    accounts = _accounts(db)
    before = svc.project_out(ctx, project, accounts)
    try:
        svc.update_project(db, project, payload.model_dump(exclude_unset=True), user.id)
    except (svc.CapexError, ValueError) as exc:
        raise _fail(db, exc) from exc
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    after = svc.project_out(ctx, project, accounts)
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type="capex_project",
        entity_id=project.id,
        before={k: v for k, v in before.items() if k != "items"},
        after={k: v for k, v in after.items() if k != "items"},
        ip=client_ip(request),
    )
    db.commit()
    return after


@router.delete("/projects/{project_id}")
def delete_project(
    project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    ctx, sub, access, project = _project(db, user, project_id)
    _require_edit(access)
    audit.record(
        db,
        user_id=user.id,
        action="DELETE",
        entity_type="capex_project",
        entity_id=project.id,
        before=svc.project_out(ctx, project, _accounts(db)),
        ip=client_ip(request),
    )
    db.delete(project)
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    db.commit()
    return {"deleted": project_id}


@router.post("/projects/{project_id}/items", status_code=201)
def add_item(
    project_id: int,
    payload: ItemIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access, project = _project(db, user, project_id)
    _require_edit(access)
    try:
        item = svc.add_item(db, ctx, project, payload.model_dump(exclude_unset=True))
    except (svc.CapexError, ValueError) as exc:
        raise _fail(db, exc) from exc
    project.updated_by = user.id
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    out = svc.item_out(ctx, item, _accounts(db))
    audit.record(
        db,
        user_id=user.id,
        action="CREATE",
        entity_type="capex_item",
        entity_id=item.id,
        after=out,
        ip=client_ip(request),
    )
    db.commit()
    return out


def _item(db: Session, user: User, item_id: int):
    item = db.get(CapexItem, item_id)
    if item is None:
        raise HTTPException(404, "Item não encontrado")
    ctx, sub, access, project = _project(db, user, item.project_id)
    return ctx, sub, access, item


@router.patch("/items/{item_id}")
def update_item(
    item_id: int,
    payload: ItemIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx, sub, access, item = _item(db, user, item_id)
    _require_edit(access)
    accounts = _accounts(db)
    before = svc.item_out(ctx, item, accounts)
    try:
        svc.update_item(db, item, payload.model_dump(exclude_unset=True))
    except (svc.CapexError, ValueError) as exc:
        raise _fail(db, exc) from exc
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    db.refresh(item)
    after = svc.item_out(ctx, item, accounts)
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type="capex_item",
        entity_id=item.id,
        before=before,
        after=after,
        ip=client_ip(request),
    )
    db.commit()
    return after


@router.delete("/items/{item_id}")
def delete_item(item_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx, sub, access, item = _item(db, user, item_id)
    _require_edit(access)
    audit.record(
        db,
        user_id=user.id,
        action="DELETE",
        entity_type="capex_item",
        entity_id=item.id,
        before=svc.item_out(ctx, item, _accounts(db)),
        ip=client_ip(request),
    )
    db.delete(item)
    opex_svc.mark_in_progress(db, sub, ctx, user.id)
    db.commit()
    return {"deleted": item_id}


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
    if ctx.frozen:
        raise HTTPException(409, f"A versão {ctx.version.label} está congelada; o fluxo continua na revisão")
    blockers: list[str] = []
    if action == "submit":
        if ctx.cycle.status != "OPEN" and not access.global_:
            blockers.append("ciclo não está aberto")
        blockers += svc.blockers(db, ctx, sub)
    if action == "approve":
        blockers += svc.blockers(db, ctx, sub)
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
    return _header(ctx, sub, Access(db, ctx, user, sub))


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


@router.get("/options", summary="Contas de ativo, catálogo de itens, tipos de projeto e filiais")
def options(company_id: int | None = None, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return svc.options(db, _ctx(db), company_id)
