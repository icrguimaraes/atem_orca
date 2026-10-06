"""Painel: realizado × orçamento de referência e checagens de qualidade dos dados.

Toda agregação considera apenas as versões vigentes (`dataset_versions.is_current`): versões
anteriores ficam no banco para auditoria, mas nunca somam no painel.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

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
    Company,
    ContractType,
    CostCenter,
    DatasetVersion,
    Employee,
    ImportBatch,
    MacroAssumption,
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

    def __init__(
        self, db: Session, user: User, company_id, cost_center_id, package_id, months: set[int] | None = None
    ) -> None:
        self.db = db
        self.visible = visible_cost_center_ids(db, user)
        self.company_id, self.cost_center_id, self.package_id = company_id, cost_center_id, package_id
        self.months = months  # filtro de meses do painel (vazio = todos)

    def _filtered(self, model, stmt: Select) -> Select:
        stmt = stmt.join(DatasetVersion, DatasetVersion.id == model.dataset_version_id).where(DatasetVersion.is_current)
        if self.visible is not None:
            stmt = stmt.where(model.cost_center_id.in_(self.visible or {-1}))
        if self.months:
            stmt = stmt.where(model.period.in_(self.months))
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


def _loaded_years(db: Session, dataset_type: str, position: int) -> list[int]:
    """Anos com versão vigente, lidos do escopo (ACTUAL:2026:1001 / REFERENCE_BUDGET:ORC:2026:1001)."""
    scopes = db.scalars(
        select(DatasetVersion.scope_key).where(DatasetVersion.dataset_type == dataset_type, DatasetVersion.is_current)
    )
    return sorted({int(s.split(":")[position]) for s in scopes})


def _last_closed(db: Session, year: int) -> int | None:
    return db.scalar(
        select(func.max(DatasetVersion.last_closed_period)).where(
            DatasetVersion.dataset_type == "ACTUAL",
            DatasetVersion.is_current,
            DatasetVersion.scope_key.like(f"ACTUAL:{year}:%"),
        )
    )


def _ints(csv: str | None) -> set[int]:
    return {int(x) for x in (csv or "").split(",") if x.strip().isdigit()}


@dataclass
class YearSelection:
    ref: int
    prev: int | None
    selected: list[int]
    actual_years: list[int]
    budget_years: list[int]
    available: list[int]


def _select_years(db: Session, years: str | None, year: int | None = None) -> YearSelection:
    """`years` (ex.: "2025" ou "2025,2026") escolhe os anos exibidos: um ano só mostra esse ano, sem
    comparação; dois ou mais comparam o maior (referência) com o segundo maior (anterior). Sem o
    parâmetro, vale o ano mais recente com realizado comparado ao anterior, quando carregado."""
    actual_years = _loaded_years(db, "ACTUAL", 1)
    budget_years = _loaded_years(db, "REFERENCE_BUDGET", 2)
    available = sorted(set(actual_years) | set(budget_years))  # crescente: 2025, 2026, …
    selected = sorted(_ints(years) & set(available), reverse=True)
    if selected:
        ref = selected[0]
        prev = selected[1] if len(selected) > 1 else None
    else:
        cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
        # ano exibido: o pedido, senão o mais recente com realizado, senão com orçamento, senão o do ciclo
        ref = year or (
            max(actual_years)
            if actual_years
            else max(budget_years)
            if budget_years
            else cycle.actual_reference_year
            if cycle
            else datetime.utcnow().year
        )
        prev = ref - 1
        selected = [ref] + ([prev] if prev in available else [])
    return YearSelection(ref, prev, sorted(selected), actual_years, budget_years, available)


@router.get("/overview", summary="KPIs, evolução mensal e rankings (realizado vigente × orçamento de referência)")
def overview(
    company_id: int | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    year: int | None = None,
    years: str | None = None,
    months: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Anos em `years` (ver `_select_years`); `months` (ex.: "1,2,3") restringe todos os números aos meses
    escolhidos — o mês fechado continua sendo o último com realizado no ano."""
    ys = _select_years(db, years, year)
    ref, prev, selected, actual_years, budget_years, available = (
        ys.ref,
        ys.prev,
        ys.selected,
        ys.actual_years,
        ys.budget_years,
        ys.available,
    )
    month_filter = {m for m in _ints(months) if 1 <= m <= 12}
    f = Facts(db, user, company_id, cost_center_id, package_id, month_filter or None)
    closed = _last_closed(db, ref)  # último mês com realizado no ano de referência

    # ---- KPIs
    prev_total = f.total(ActualEntry, prev) if prev else ZERO
    prev_ytd = f.total(ActualEntry, prev, closed) if (prev and closed) else ZERO
    ref_ytd = f.total(ActualEntry, ref)
    ref_annualized = (ref_ytd * 12 / closed) if closed else ZERO
    budget_total = f.total(ReferenceBudgetEntry, ref)
    budget_ytd = f.total(ReferenceBudgetEntry, ref, closed) if closed else ZERO

    # ---- série mensal
    monthly = {m: {"month": m, "prev": ZERO, "ref": ZERO, "budget": ZERO} for m in range(1, 13)}
    for period, amount in f.sums(ActualEntry, prev, ActualEntry.period) if prev else []:
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
    pkg_prev = by_package(ActualEntry, prev) if prev else {}
    pkg_prev_ytd = by_package(ActualEntry, prev, closed) if (prev and closed) else {}
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
        base = dict(f.sums(ActualEntry, prev, column, max_period=closed)) if (prev and closed) else {}
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

    return {
        "reference_year": ref,
        "previous_year": prev,
        "selected_years": selected,
        "selected_months": sorted(month_filter),
        "last_closed_period": closed,
        "years_loaded": actual_years,
        "budget_years": budget_years,
        "available_years": available,
        "has_actual": ref in actual_years,
        "has_prev": prev is not None and prev in actual_years,
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
        "heatmap": _heatmap(db, f, ref if ref in actual_years else (prev or ref)),
        "account_deltas": _account_deltas(db, f, ref, prev, closed)
        if (closed and prev and prev in actual_years)
        else [],
        "budget_progress": budget_progress(db, user, company_id, cost_center_id, package_id),
    }


@router.get("/breakdown", summary="Tabela do painel com drill-down: pacote GMD → conta → centro de custo")
def breakdown(
    group_by: Literal["package", "account", "cost_center"] = "package",
    parent_package_id: int | None = None,
    parent_no_package: bool = False,
    parent_account_id: int | None = None,
    company_id: int | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    years: str | None = None,
    months: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Uma linha por grupo com realizado do ano em foco, a base de comparação (realizado do ano anterior
    no mesmo período quando há dois anos; senão o orçado do ano até o mês fechado), participação (AV %),
    variação e variação %. `parent_*` restringem aos filhos de uma linha expandida (drill-down)."""
    ys = _select_years(db, years)
    ref, prev = ys.ref, ys.prev
    month_filter = {m for m in _ints(months) if 1 <= m <= 12}
    f = Facts(db, user, company_id, cost_center_id, package_id, month_filter or None)
    closed = _last_closed(db, ref)

    def grouped(model, year: int, max_period: int | None) -> dict:
        col = {"package": Account.package_id, "account": model.account_id, "cost_center": model.cost_center_id}[
            group_by
        ]
        stmt = select(col, func.sum(model.amount)).select_from(model).where(model.fiscal_year == year)
        if group_by == "package":
            stmt = stmt.join(Account, Account.id == model.account_id)
        if parent_package_id is not None:
            stmt = stmt.where(model.account_id.in_(select(Account.id).where(Account.package_id == parent_package_id)))
        elif parent_no_package:
            stmt = stmt.where(model.account_id.in_(select(Account.id).where(Account.package_id.is_(None))))
        if parent_account_id is not None:
            stmt = stmt.where(model.account_id == parent_account_id)
        if max_period:
            stmt = stmt.where(model.period <= max_period)
        return {k: v or ZERO for k, v in db.execute(f._filtered(model, stmt).group_by(col))}

    cur = grouped(ActualEntry, ref, None)
    base_kind: str | None = None
    base: dict = {}
    if prev is not None and prev in ys.actual_years:
        base_kind, base = "prev", grouped(ActualEntry, prev, closed)
    elif ref in ys.budget_years:
        base_kind, base = "budget", grouped(ReferenceBudgetEntry, ref, closed)

    ids = [k for k in set(cur) | set(base) if k is not None]
    if group_by == "package":
        objs = {
            p.id: (None, p.name) for p in db.scalars(select(BudgetPackage).where(BudgetPackage.id.in_(ids or [-1])))
        }
    elif group_by == "account":
        objs = {a.id: (a.code, a.name) for a in db.scalars(select(Account).where(Account.id.in_(ids or [-1])))}
    else:
        objs = {c.id: (c.code, c.name) for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(ids or [-1])))}
    total_cur = sum(cur.values(), ZERO)
    total_base = sum(base.values(), ZERO)
    rows = []
    for key in set(cur) | set(base):
        c, b = cur.get(key, ZERO), base.get(key, ZERO)
        if c == 0 and b == 0:
            continue
        code, name = objs.get(key, (None, "Sem pacote" if group_by == "package" else "—"))
        rows.append(
            {
                "id": key,
                "code": code,
                "name": name,
                "ref": _money(c),
                "base": _money(b),
                "share_ref": _pct(c, total_cur) if total_cur else None,
                "share_base": _pct(b, total_base) if total_base else None,
                "var": _money(c - b),
                "var_pct": _pct(c, b),
                "has_children": group_by != "cost_center",
            }
        )
    rows.sort(key=lambda r: Decimal(r["ref"]), reverse=True)
    base_label = (
        f"Realizado {prev} até {closed or 12}"
        if base_kind == "prev"
        else f"Orçado {ref} até {closed or 12}"
        if base_kind
        else None
    )
    return {
        "group_by": group_by,
        "reference_year": ref,
        "previous_year": prev,
        "last_closed_period": closed,
        "base": base_kind,
        "base_label": base_label,
        "rows": rows,
        "total": {
            "ref": _money(total_cur),
            "base": _money(total_base),
            "var": _money(total_cur - total_base),
            "var_pct": _pct(total_cur, total_base),
        },
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
            f"Realizado {year} carregado" if has else f"Realizado {year} ainda não carregado",
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


@router.get("/inventory", summary="O que existe na base: cadastros, quadro de pessoal e premissas")
def inventory(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    visible = visible_cost_center_ids(db, user)

    # ---- cadastros
    cc_stmt = select(CostCenter).where(CostCenter.is_active)
    if visible is not None:
        cc_stmt = cc_stmt.where(CostCenter.id.in_(visible or {-1}))
    ccs = db.scalars(cc_stmt).all()
    accounts_by_nature = dict(
        db.execute(select(Account.nature, func.count()).where(Account.is_active).group_by(Account.nature)).all()
    )
    package_counts = [
        {"package": name, "package_type": ptype, "accounts": n}
        for name, ptype, n in db.execute(
            select(BudgetPackage.name, BudgetPackage.package_type, func.count(Account.id))
            .join(Account, Account.package_id == BudgetPackage.id, isouter=True)
            .group_by(BudgetPackage.id)
            .order_by(BudgetPackage.sort_order)
        )
    ]

    # ---- pessoal (quadro vigente; custo estimado = salário × multiplicador do tipo de contrato)
    emp_stmt = select(Employee).where(Employee.is_active)
    if visible is not None:
        emp_stmt = emp_stmt.where(Employee.cost_center_id.in_(visible or {-1}))
    employees = db.scalars(emp_stmt).all()
    contracts = {c.code: c for c in db.scalars(select(ContractType))}
    by_contract: dict[str, dict] = {}
    by_cc: dict[int | None, dict] = {}
    payroll = cost = ZERO
    for e in employees:
        ct = contracts.get(e.contract_type_code)
        mult = Decimal(ct.default_multiplier) if ct and ct.apply_multiplier else Decimal(1)
        salary = Decimal(e.base_salary or 0)
        payroll += salary
        cost += salary * mult
        c = by_contract.setdefault(
            e.contract_type_code,
            {"contract": e.contract_type_code, "headcount": 0, "payroll": ZERO, "multiplier": str(mult)},
        )
        c["headcount"] += 1
        c["payroll"] += salary
        k = by_cc.setdefault(e.cost_center_id, {"cost_center_id": e.cost_center_id, "headcount": 0, "payroll": ZERO})
        k["headcount"] += 1
        k["payroll"] += salary
    cc_names = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_([k for k in by_cc if k])))}
    personnel = {
        "headcount": len(employees),
        "monthly_payroll": _money(payroll),
        "monthly_estimated_cost": _money(cost),
        "annual_estimated_cost": _money(cost * 12),
        "by_contract": [c | {"payroll": _money(c["payroll"])} for c in by_contract.values()],
        "by_cost_center": sorted(
            (
                {
                    "code": cc_names[k["cost_center_id"]].code if k["cost_center_id"] in cc_names else None,
                    "name": cc_names[k["cost_center_id"]].name if k["cost_center_id"] in cc_names else "Sem CC",
                    "headcount": k["headcount"],
                    "payroll": _money(k["payroll"]),
                }
                for k in by_cc.values()
            ),
            key=lambda r: -r["headcount"],
        )[:10],
    }

    # ---- premissas macroeconômicas (versão vigente), uma linha por indicador × fonte
    macro_version = db.scalar(
        select(DatasetVersion.id).where(DatasetVersion.dataset_type == "MACRO_ASSUMPTIONS", DatasetVersion.is_current)
    )
    macro_rows: dict[tuple, dict] = {}
    years: set[int] = set()
    if macro_version:
        for m in db.scalars(
            select(MacroAssumption)
            .where(MacroAssumption.dataset_version_id == macro_version)
            .order_by(MacroAssumption.id)
        ):
            key = (m.category, m.indicator, m.segment, m.source)
            row = macro_rows.setdefault(
                key,
                {
                    "category": m.category,
                    "indicator": m.indicator,
                    "segment": m.segment,
                    "source": m.source,
                    "reference_date": m.reference_date.isoformat() if m.reference_date else None,
                    "values": {},
                },
            )
            row["values"][m.year] = str(m.value)
            years.add(m.year)

    return {
        "master": {
            "cost_centers": len(ccs),
            "cost_centers_without_user": sum(1 for c in ccs if c.manager_user_id is None),
            "accounts_by_nature": accounts_by_nature,
            "packages": package_counts,
        },
        "personnel": personnel,
        "macro": {"years": sorted(years), "rows": list(macro_rows.values())},
    }


def _heatmap(db: Session, f: Facts, year: int, limit: int = 12) -> dict:
    """Centro de custo × mês (maiores CCs do ano), para o mapa de calor."""
    rows: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for cc_id, period, amount in f.sums(ActualEntry, year, ActualEntry.cost_center_id, ActualEntry.period):
        rows[cc_id][int(period)] += amount
    top = sorted(rows, key=lambda k: sum(rows[k].values()), reverse=True)[:limit]
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(top)))} if top else {}
    return {
        "year": year,
        "rows": [
            {
                "id": cc_id,
                "code": ccs[cc_id].code if cc_id in ccs else None,
                "name": ccs[cc_id].name if cc_id in ccs else "—",
                "values": [_money(rows[cc_id].get(m)) for m in range(1, 13)],
                "total": _money(sum(rows[cc_id].values(), ZERO)),
            }
            for cc_id in top
        ],
    }


def _account_deltas(db: Session, f: Facts, ref: int, prev: int, closed: int, limit: int = 8) -> list[dict]:
    """Maiores aumentos e reduções por conta no acumulado do ano (mesmo período nos dois anos)."""
    cur = dict(f.sums(ActualEntry, ref, ActualEntry.account_id))
    base = dict(f.sums(ActualEntry, prev, ActualEntry.account_id, max_period=closed))
    deltas = {k: cur.get(k, ZERO) - base.get(k, ZERO) for k in set(cur) | set(base)}
    ups = sorted((k for k in deltas if deltas[k] > 0), key=lambda k: deltas[k], reverse=True)[:limit]
    downs = sorted((k for k in deltas if deltas[k] < 0), key=lambda k: deltas[k])[:limit]
    ids = ups + downs
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
    return [
        {
            "id": k,
            "code": accounts[k].code if k in accounts else None,
            "name": accounts[k].name if k in accounts else "—",
            "prev_ytd": _money(base.get(k)),
            "ref_ytd": _money(cur.get(k)),
            "delta": _money(deltas[k]),
        }
        for k in ids
    ]


def budget_progress(
    db: Session,
    user: User,
    company_id: int | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
) -> dict | None:
    """Andamento do orçamento OPEX do ciclo: CCs por status e proposto × anualizado por pacote."""
    from app.models import BudgetLine, BudgetSubmission, BudgetVersion

    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    if cycle is None:
        return None
    version = db.scalar(
        select(BudgetVersion)
        .where(BudgetVersion.cycle_id == cycle.id, BudgetVersion.status == "WORKING")
        .order_by(BudgetVersion.major.desc(), BudgetVersion.minor.desc())
    )
    if version is None:
        return None
    visible = visible_cost_center_ids(db, user)
    cc_stmt = select(CostCenter.id).where(CostCenter.is_active)
    if visible is not None:
        cc_stmt = cc_stmt.where(CostCenter.id.in_(visible or {-1}))
    if company_id:
        cc_stmt = cc_stmt.where(CostCenter.company_id == company_id)
    if cost_center_id:
        cc_stmt = cc_stmt.where(CostCenter.id == cost_center_id)
    cc_ids = set(db.scalars(cc_stmt))
    statuses = dict(
        db.execute(
            select(BudgetSubmission.cost_center_id, BudgetSubmission.status).where(
                BudgetSubmission.version_id == version.id,
                BudgetSubmission.module == "OPEX",
                BudgetSubmission.cost_center_id.in_(cc_ids or {-1}),
            )
        ).all()
    )
    counts: dict[str, int] = defaultdict(int)
    for cc in cc_ids:
        counts[statuses.get(cc, "DRAFT")] += 1

    line_stmt = (
        select(BudgetLine.package_id, BudgetLine.cost_center_id, func.sum(BudgetLine.total_amount))
        .join(BudgetSubmission, BudgetSubmission.id == BudgetLine.submission_id)
        .where(
            BudgetSubmission.version_id == version.id,
            BudgetSubmission.module == "OPEX",
            BudgetLine.cost_center_id.in_(cc_ids or {-1}),
        )
        .group_by(BudgetLine.package_id, BudgetLine.cost_center_id)
    )
    if package_id:
        line_stmt = line_stmt.where(BudgetLine.package_id == package_id)
    proposed: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    started: set[int] = set()
    for pkg_id, cc_id, amount in db.execute(line_stmt):
        proposed[pkg_id] += amount or ZERO
        if amount:
            started.add(cc_id)

    # 2026 anualizado só dos CCs que já lançaram 2027 (comparação justa durante o preenchimento)
    ref = cycle.actual_reference_year
    annualized: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    if started:
        closed_by_company = {}
        stmt = (
            select(Account.package_id, CostCenter.company_id, func.sum(ActualEntry.amount))
            .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
            .join(Account, Account.id == ActualEntry.account_id)
            .join(CostCenter, CostCenter.id == ActualEntry.cost_center_id)
            .where(
                DatasetVersion.is_current,
                ActualEntry.fiscal_year == ref,
                ActualEntry.cost_center_id.in_(started),
                Account.nature.in_(("OPEX", "FINANCEIRO")),
            )
            .group_by(Account.package_id, CostCenter.company_id)
        )
        if package_id:
            stmt = stmt.where(Account.package_id == package_id)
        companies = {c.id: c.code for c in db.scalars(select(Company))}
        for pkg_id, comp_id, amount in db.execute(stmt):
            code = companies.get(comp_id)
            if code not in closed_by_company:
                closed_by_company[code] = _last_closed_company(db, ref, code)
            closed = closed_by_company[code]
            if closed:
                annualized[pkg_id] += amount * 12 / closed
    packages = {p.id: p for p in db.scalars(select(BudgetPackage))}
    by_package = sorted(
        (
            {
                "package_id": pid,
                "package": packages[pid].name if pid in packages else "Sem pacote",
                "proposed": _money(proposed.get(pid)),
                "ref_annualized": _money(annualized.get(pid)),
            }
            for pid in set(proposed) | set(annualized)
        ),
        key=lambda r: -max(Decimal(r["proposed"]), Decimal(r["ref_annualized"])),
    )
    return {
        "target_year": cycle.fiscal_year,
        "ref_year": ref,
        "cycle_status": cycle.status,
        "deadline": cycle.opex_deadline.isoformat() if cycle.opex_deadline else None,
        "total_cost_centers": len(cc_ids),
        "started_cost_centers": len(started),
        "status_counts": dict(counts),
        "proposed_total": _money(sum(proposed.values(), ZERO)),
        "annualized_started_total": _money(sum(annualized.values(), ZERO)),
        "by_package": by_package,
    }


def _last_closed_company(db: Session, year: int, company_code: str | None) -> int | None:
    return db.scalar(
        select(DatasetVersion.last_closed_period).where(
            DatasetVersion.dataset_type == "ACTUAL",
            DatasetVersion.is_current,
            DatasetVersion.scope_key == f"ACTUAL:{year}:{company_code}",
        )
    )
