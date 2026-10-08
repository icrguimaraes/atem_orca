"""Dashboard analítico (Plotly): um endpoint agregado com filtros globais, figuras prontas e KPIs."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, visible_cost_center_ids
from app.db import get_db
from app.models import Account, BudgetVersion, CostCenter, Department, User
from app.services import analytics as svc
from app.services import opex as opex_svc

router = APIRouter(prefix="/analytics", tags=["Análise"])


def _ctx(db: Session) -> opex_svc.Context:
    try:
        return opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/dashboard", summary="KPIs e figuras Plotly do orçamento, já filtrados e formatados (pt-BR)")
def dashboard(
    version_id: int | None = None,
    company_id: int | None = None,
    department_id: int | None = Query(None, description="Diretoria"),
    cost_center_id: int | None = None,
    account: str | None = Query(None, description="Código da conta contábil"),
    module: Literal["OPEX", "CAPEX", "PERSONNEL"] | None = None,
    package_id: int | None = None,
    prev: str | None = Query(None, description="Série Realizado: actual:ANO | actual_ann:ANO | none"),
    ref: str | None = Query(None, description="Série Referência: budget:ANO | actual:ANO | actual_ann:ANO | none"),
    year: int | None = Query(None, description="Ano principal (modelo do Painel): ano do ciclo ou ano com realizado"),
    months: str | None = Query(None, description="Meses, ex.: 1,2,3"),
    compare: bool = True,
    same_period: bool = True,
    dimension: Literal["company", "department", "cost_center", "account", "module", "month"] = "cost_center",
    variation_by: Literal["account", "cost_center"] = "account",
    variation_mode: Literal["abs", "pct"] = "abs",
    top: int = Query(10, ge=0, le=500, description="0 = todas"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = _ctx(db)
    version = ctx.version
    if version_id is not None:
        version = db.get(BudgetVersion, version_id)
        if version is None or version.cycle_id != ctx.cycle.id:
            raise HTTPException(404, "Versão não encontrada")
    f = svc.Filters(
        company_id,
        department_id,
        cost_center_id,
        account,
        module,
        package_id,
        prev,
        ref,
        year,
        months,
        compare,
        same_period,
    )
    return svc.dashboard(
        db,
        ctx,
        version,
        visible_cost_center_ids(db, user),
        f,
        dimension=dimension,
        variation_by=variation_by,
        variation_mode=variation_mode,
        top=top,
    )


@router.get("/options", summary="Opções dos filtros (empresas, diretorias, CCs e contas do escopo do usuário)")
def options(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    visible = visible_cost_center_ids(db, user)
    stmt = select(CostCenter).where(CostCenter.is_active).order_by(CostCenter.code)
    if visible is not None:
        stmt = stmt.where(CostCenter.id.in_(visible or {-1}))
    ccs = db.scalars(stmt).all()
    dept_ids = {c.department_id for c in ccs if c.department_id}
    departments = db.scalars(select(Department).where(Department.id.in_(dept_ids or {-1})).order_by(Department.name))
    accounts = db.scalars(select(Account).where(Account.is_active).order_by(Account.code))
    ctx = _ctx(db)
    return {
        "cost_centers": [
            {"id": c.id, "code": c.code, "name": c.name, "company_id": c.company_id, "department_id": c.department_id}
            for c in ccs
        ],
        "departments": [{"id": d.id, "name": d.name} for d in departments],
        "series": svc.series_options(db, ctx),
        "years": svc.years_available(db, ctx),
        "target_year": ctx.target_year,
        "default_series": {"prev": f"actual:{ctx.prev_year}", "ref": f"budget:{ctx.ref_year}"},
        "accounts": [
            {"code": a.code, "name": a.name, "nature": a.nature, "package_id": a.package_id} for a in accounts
        ],
        "versions": [
            {"id": v.id, "label": v.label, "status": v.status, "current": v.id == ctx.version.id}
            for v in db.scalars(
                select(BudgetVersion)
                .where(BudgetVersion.cycle_id == ctx.cycle.id)
                .order_by(BudgetVersion.major, BudgetVersion.minor)
            )
        ],
    }
