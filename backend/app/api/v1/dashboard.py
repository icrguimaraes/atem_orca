"""Painel: realizado × orçamento de referência e checagens de qualidade dos dados.

Toda agregação considera apenas as versões vigentes (`dataset_versions.is_current`): versões
anteriores ficam no banco para auditoria, mas nunca somam no painel.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles, visible_cost_center_ids
from app.db import get_db
from app.models import (
    Account,
    ActualEntry,
    BudgetCycle,
    BudgetPackage,
    CostCenter,
    DatasetVersion,
    Employee,
    ImportBatch,
    ReferenceBudgetEntry,
    User,
)
from app.models.base import Role

router = APIRouter(prefix="/dashboard", tags=["painel"])
ZERO = Decimal("0")


def _money(v: Decimal | None) -> str:
    return str((v or ZERO).quantize(Decimal("0.01")))


def _pct(new: Decimal, base: Decimal) -> str | None:
    return None if not base else str(((new - base) / abs(base)).quantize(Decimal("0.0001")))


class Facts:
    """Consultas agregadas sobre os fatos vigentes com os filtros e o escopo do usuário."""

    def __init__(self, db: Session, user: User, company_id, cost_center_id, package_id) -> None:
        self.db = db
        self.visible = visible_cost_center_ids(db, user)
        self.company_id, self.cost_center_id, self.package_id = company_id, cost_center_id, package_id

    def _filtered(self, model, stmt: Select) -> Select:
        stmt = stmt.join(DatasetVersion, DatasetVersion.id == model.dataset_version_id).where(DatasetVersion.is_current)
        if self.visible is not None:
            stmt = stmt.where(model.cost_center_id.in_(self.visible or {-1}))
        if self.company_id:
            stmt = stmt.where(model.company_id == self.company_id)
        if self.cost_center_id:
            stmt = stmt.where(model.cost_center_id == self.cost_center_id)
        if self.package_id:
            stmt = stmt.where(model.account_id.in_(select(Account.id).where(Account.package_id == self.package_id)))
        return stmt

    def sums(self, model, year: int, *group_cols, max_period: int | None = None) -> list:
        stmt = select(*group_cols, func.sum(model.amount)).select_from(model).where(model.fiscal_year == year)
        if max_period:
            stmt = stmt.where(model.period <= max_period)
        stmt = self._filtered(model, stmt)
        if group_cols:
            stmt = stmt.group_by(*group_cols)
        return list(self.db.execute(stmt))

    def total(self, model, year: int, max_period: int | None = None) -> Decimal:
        rows = self.sums(model, year, max_period=max_period)
        return (rows[0][0] if rows else None) or ZERO


def _last_closed(db: Session, year: int) -> int | None:
    return db.scalar(
        select(func.max(DatasetVersion.last_closed_period)).where(
            DatasetVersion.dataset_type == "ACTUAL",
            DatasetVersion.is_current,
            DatasetVersion.scope_key.like(f"ACTUAL:{year}:%"),
        )
    )


@router.get("/overview", summary="KPIs, evolução mensal e rankings (realizado vigente × orçamento de referência)")
def overview(
    company_id: int | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    ref = cycle.actual_reference_year if cycle else datetime.utcnow().year
    prev = ref - 1
    f = Facts(db, user, company_id, cost_center_id, package_id)
    closed = _last_closed(db, ref)  # último mês com realizado no ano de referência

    # ---- KPIs
    prev_total = f.total(ActualEntry, prev)
    prev_ytd = f.total(ActualEntry, prev, closed) if closed else ZERO
    ref_ytd = f.total(ActualEntry, ref)
    ref_annualized = (ref_ytd * 12 / closed) if closed else ZERO
    budget_total = f.total(ReferenceBudgetEntry, ref)
    budget_ytd = f.total(ReferenceBudgetEntry, ref, closed) if closed else ZERO

    # ---- série mensal
    monthly = {m: {"month": m, "prev": ZERO, "ref": ZERO, "budget": ZERO} for m in range(1, 13)}
    for period, amount in f.sums(ActualEntry, prev, ActualEntry.period):
        monthly[period]["prev"] = amount
    for period, amount in f.sums(ActualEntry, ref, ActualEntry.period):
        monthly[period]["ref"] = amount
    for period, amount in f.sums(ReferenceBudgetEntry, ref, ReferenceBudgetEntry.period):
        monthly[period]["budget"] = amount

    # ---- por pacote
    def by_package(model, year, max_period=None):
        out: dict = defaultdict(lambda: ZERO)
        stmt = (
            select(Account.package_id, func.sum(model.amount))
            .select_from(model)
            .join(Account, Account.id == model.account_id)
            .where(model.fiscal_year == year)
        )
        if max_period:
            stmt = stmt.where(model.period <= max_period)
        for pkg_id, amount in db.execute(f._filtered(model, stmt).group_by(Account.package_id)):
            out[pkg_id] += amount
        return out

    packages = {p.id: p for p in db.scalars(select(BudgetPackage))}
    pkg_prev = by_package(ActualEntry, prev)
    pkg_prev_ytd = by_package(ActualEntry, prev, closed) if closed else {}
    pkg_ref = by_package(ActualEntry, ref)
    pkg_budget = by_package(ReferenceBudgetEntry, ref)
    package_rows = []
    for pkg_id in set(pkg_prev) | set(pkg_ref) | set(pkg_budget):
        p = packages.get(pkg_id)
        ytd = pkg_ref.get(pkg_id, ZERO)
        prev_ytd_p = pkg_prev_ytd.get(pkg_id, ZERO)
        package_rows.append(
            {
                "package_id": pkg_id,
                "package": p.name if p else "Sem pacote",
                "package_type": p.package_type if p else None,
                "prev_total": _money(pkg_prev.get(pkg_id)),
                "prev_ytd": _money(prev_ytd_p),
                "ref_ytd": _money(ytd),
                "ref_annualized": _money(ytd * 12 / closed if closed else ZERO),
                "budget": _money(pkg_budget.get(pkg_id)),
                "ytd_var_pct": _pct(ytd, prev_ytd_p),
            }
        )
    package_rows.sort(key=lambda r: Decimal(r["ref_ytd"]) + Decimal(r["prev_total"]), reverse=True)

    # ---- rankings (CC e conta) no acumulado do ano de referência vs mesmo período do ano anterior
    def ranking(column, label_model, label_attrs):
        cur = dict(f.sums(ActualEntry, ref, column))
        base = dict(f.sums(ActualEntry, prev, column, max_period=closed)) if closed else {}
        ids = sorted(cur, key=lambda k: cur[k], reverse=True)[:10]
        objs = {o.id: o for o in db.scalars(select(label_model).where(label_model.id.in_(ids)))} if ids else {}
        rows = []
        for i in ids:
            o = objs.get(i)
            rows.append(
                {
                    "id": i,
                    "code": getattr(o, label_attrs[0], None),
                    "name": getattr(o, label_attrs[1], None),
                    "ref_ytd": _money(cur[i]),
                    "prev_ytd": _money(base.get(i)),
                    "ytd_var_pct": _pct(cur[i], base.get(i, ZERO)),
                }
            )
        return rows

    years_loaded = sorted(
        {
            int(s.split(":")[1])
            for s in db.scalars(
                select(DatasetVersion.scope_key).where(
                    DatasetVersion.dataset_type == "ACTUAL", DatasetVersion.is_current
                )
            )
        }
    )

    return {
        "reference_year": ref,
        "previous_year": prev,
        "last_closed_period": closed,
        "years_loaded": years_loaded,
        "has_budget": budget_total != ZERO,
        "kpis": {
            "prev_total": _money(prev_total),
            "prev_ytd": _money(prev_ytd),
            "ref_ytd": _money(ref_ytd),
            "ytd_var_pct": _pct(ref_ytd, prev_ytd),
            "ref_annualized": _money(ref_annualized),
            "annualized_vs_prev_pct": _pct(ref_annualized, prev_total),
            "budget_total": _money(budget_total),
            "budget_ytd": _money(budget_ytd),
            "budget_consumption_pct": _pct(ref_ytd, budget_ytd) if budget_ytd else None,
        },
        "monthly": [{k: (_money(v) if k != "month" else v) for k, v in row.items()} for row in monthly.values()],
        "by_package": package_rows,
        "top_cost_centers": ranking(ActualEntry.cost_center_id, CostCenter, ("code", "name")),
        "top_accounts": ranking(ActualEntry.account_id, Account, ("code", "name")),
    }


@router.get("/data-quality", summary="Checagens de consistência da base após as importações")
def data_quality(db: Session = Depends(get_db), _: User = Depends(require_roles(Role.CONTROLLER))):
    checks = []

    def add(code, title, severity, count, detail=None, samples=None):
        checks.append(
            {
                "code": code,
                "title": title,
                "severity": severity if count else "OK",
                "count": count,
                "detail": detail,
                "samples": samples or [],
            }
        )

    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    ref = cycle.actual_reference_year if cycle else datetime.utcnow().year
    loaded = {
        s
        for s in db.scalars(
            select(DatasetVersion.scope_key).where(DatasetVersion.dataset_type == "ACTUAL", DatasetVersion.is_current)
        )
    }
    for year in (ref - 1, ref):
        has = any(s.startswith(f"ACTUAL:{year}:") for s in loaded)
        add(
            f"NO_ACTUAL_{year}",
            f"Realizado {year} carregado",
            "WARNING",
            0 if has else 1,
            None if has else f"Importe o realizado {year} para habilitar a comparação histórica.",
        )

    multi = list(
        db.execute(
            select(DatasetVersion.scope_key, func.count())
            .where(DatasetVersion.is_current)
            .group_by(DatasetVersion.dataset_type, DatasetVersion.scope_key)
            .having(func.count() > 1)
        )
    )
    add(
        "MULTIPLE_CURRENT",
        "Bases com mais de uma versão vigente (risco de soma duplicada)",
        "ERROR",
        len(multi),
        samples=[s for s, _ in multi],
    )

    no_manager = db.scalars(
        select(CostCenter.code)
        .where(CostCenter.is_active, CostCenter.manager_user_id.is_(None))
        .order_by(CostCenter.code)
    ).all()
    add(
        "CC_NO_USER",
        "Centros de custo sem usuário gestor vinculado",
        "WARNING",
        len(no_manager),
        "Sem usuário vinculado, nenhum gestor enxerga o CC. Cadastre o usuário com o mesmo nome do gestor do CC "
        "ou ajuste o cadastro.",
        no_manager[:15],
    )

    no_pkg = db.scalars(select(Account.code).where(Account.is_active, Account.package_id.is_(None))).all()
    add("ACCOUNT_NO_PACKAGE", "Contas sem pacote GMD", "WARNING", len(no_pkg), samples=no_pkg[:15])

    orphan_amount = db.scalar(
        select(func.sum(ActualEntry.amount))
        .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
        .join(Account, Account.id == ActualEntry.account_id)
        .where(DatasetVersion.is_current, Account.package_id.is_(None))
    )
    add(
        "ACTUAL_NO_PACKAGE",
        "Realizado vigente em contas sem pacote (fica fora dos totais por pacote)",
        "WARNING",
        1 if orphan_amount else 0,
        f"R$ {_money(orphan_amount)}" if orphan_amount else None,
    )

    pending = db.scalars(
        select(ImportBatch.id).where(
            ImportBatch.status == "VALIDATED", ImportBatch.validated_at < datetime.utcnow() - timedelta(hours=24)
        )
    ).all()
    add(
        "PENDING_IMPORTS",
        "Importações validadas aguardando confirmação há mais de 24h",
        "INFO",
        len(pending),
        samples=[f"#{i}" for i in pending],
    )

    repeated = db.execute(
        select(ImportBatch.file_name, func.count())
        .where(ImportBatch.status == "COMPLETED")
        .group_by(ImportBatch.file_sha256, ImportBatch.file_name)
        .having(func.count() > 1)
    ).all()
    add(
        "REPEATED_FILES",
        "Arquivos idênticos carregados mais de uma vez",
        "INFO",
        len(repeated),
        "Não duplica valores (só a versão vigente soma), mas gera versões redundantes.",
        [f"{n} ({c}×)" for n, c in repeated],
    )

    emp_no_cc = db.scalar(
        select(func.count()).select_from(Employee).where(Employee.is_active, Employee.cost_center_id.is_(None))
    )
    add("EMPLOYEE_NO_CC", "Colaboradores ativos sem centro de custo", "WARNING", emp_no_cc or 0)

    order = {"ERROR": 0, "WARNING": 1, "INFO": 2, "OK": 3}
    checks.sort(key=lambda c: order[c["severity"]])
    return {"checks": checks, "issues": sum(1 for c in checks if c["severity"] in ("ERROR", "WARNING"))}
