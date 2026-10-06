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
from app.services import consolidation as cons
from app.services import opex as opex_svc

router = APIRouter(prefix="/dashboard", tags=["painel"])
ZERO = Decimal("0")


def _money(v: Decimal | None) -> str:
    return str((v or ZERO).quantize(Decimal("0.01")))


def _pct(new: Decimal, base: Decimal) -> str | None:
    return None if not base else str(((new - base) / abs(base)).quantize(Decimal("0.0001")))


MONTH_ABBR = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]


def _ints(csv: str | None) -> set[int]:
    return {int(x) for x in (csv or "").split(",") if x.strip().isdigit()}


def _years_label(years: list[int]) -> str:
    ys = [str(y) for y in years]
    return ys[0] if len(ys) == 1 else f"{', '.join(ys[:-1])} e {ys[-1]}"


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


@dataclass
class Period:
    """Período do painel, no modelo de filtros do Power BI: os anos selecionados são **somados** e os meses
    filtram todos os números. A comparação com o ano anterior é opcional (só com um ano selecionado) e pode
    ser limitada ao mesmo período (até o último mês fechado do ano em foco)."""

    years: list[int]
    months: set[int] | None
    same_period: bool
    prev_years: list[int]
    closed: int | None  # último mês fechado do maior ano com realizado selecionado
    actual_years: list[int]
    budget_years: list[int]
    target_year: int | None  # ano do ciclo: orçamento proposto (consolidação)
    available: list[int]

    @property
    def compare(self) -> bool:
        return bool(self.prev_years)

    @property
    def compare_available(self) -> bool:
        return len(self.years) == 1 and (self.years[0] - 1) in self.actual_years

    @property
    def base_cap(self) -> int | None:
        """Mês limite da base: o mês fechado do ano em foco quando "mesmo período" está ligado."""
        return self.closed if (self.same_period and self.compare) else None

    @property
    def same_period_available(self) -> bool:
        return self.compare and self.closed is not None and self.closed < 12 and not self.months

    @property
    def kind_label(self) -> str:
        has_actual = any(y in self.actual_years for y in self.years)
        has_target = self.target_year in self.years
        if has_actual and has_target:
            return f"Realizado e orçamento {self.target_year}"
        return f"Orçamento {self.target_year}" if has_target else "Realizado"

    @property
    def base_label(self) -> str | None:
        if not self.compare:
            return None
        label = _years_label(self.prev_years)
        if self.months:
            return label
        if self.base_cap and self.base_cap < 12:
            return f"{label} até {MONTH_ABBR[self.base_cap - 1]}"
        return f"{label} (ano cheio)"

    def summary(self) -> dict:
        return {
            "years": self.years,
            "years_label": _years_label(self.years),
            "months": sorted(self.months or []),
            "kind_label": self.kind_label,
            "closed": self.closed,
            "closed_month": MONTH_ABBR[self.closed - 1] if self.closed else None,
            "compare": self.compare,
            "compare_available": self.compare_available,
            "same_period": self.same_period,
            "same_period_available": self.same_period_available,
            "prev_years": self.prev_years,
            "base_label": self.base_label,
            "target_year": self.target_year,
        }


def _period(
    db: Session, years: str | None, months: str | None, compare: bool, same_period: bool, year: int | None = None
) -> Period:
    actual_years = _loaded_years(db, "ACTUAL", 1)
    budget_years = _loaded_years(db, "REFERENCE_BUDGET", 2)
    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    target_year = cycle.fiscal_year if cycle else None
    available = sorted(set(actual_years) | set(budget_years) | ({target_year} if target_year else set()))
    selected = sorted(_ints(years) & set(available))
    if not selected:
        # padrão: o ano pedido, senão o mais recente com realizado, senão com orçamento, senão o do ciclo
        default = year or (max(actual_years) if actual_years else max(budget_years) if budget_years else target_year)
        selected = [default] if default in available else (available[-1:] or [default or datetime.utcnow().year])
    month_filter = {m for m in _ints(months) if 1 <= m <= 12} or None
    actual_selected = [y for y in selected if y in actual_years]
    closed = _last_closed(db, max(actual_selected)) if actual_selected else None
    prev_years = [selected[0] - 1] if (compare and len(selected) == 1 and (selected[0] - 1) in actual_years) else []
    return Period(
        selected, month_filter, same_period, prev_years, closed, actual_years, budget_years, target_year, available
    )


class Facts:
    """Somas sobre os fatos vigentes com os filtros, o escopo do usuário e o período do painel.

    Realizado e orçamento de referência vêm do banco; o ano-alvo do ciclo (orçamento proposto) vem da
    consolidação (`consolidation.rows_for`) e entra nas mesmas somas — assim o painel soma e compara anos
    de fontes diferentes por um único caminho."""

    def __init__(
        self,
        db: Session,
        user: User,
        company_id,
        cost_center_id,
        package_id,
        period: Period,
        parent: dict | None = None,
    ) -> None:
        self.db = db
        self.visible = visible_cost_center_ids(db, user)
        self.company_id, self.cost_center_id, self.package_id = company_id, cost_center_id, package_id
        self.months = period.months
        self.target_year = period.target_year
        self.parent = parent or {}  # filtros do drill-down (filhos de um pacote ou de uma conta)
        self._target: list[tuple] | None = None

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
        if self.months:
            stmt = stmt.where(model.period.in_(self.months))
        if self.parent.get("package_id") is not None:
            stmt = stmt.where(
                model.account_id.in_(select(Account.id).where(Account.package_id == self.parent["package_id"]))
            )
        elif self.parent.get("no_package"):
            stmt = stmt.where(model.account_id.in_(select(Account.id).where(Account.package_id.is_(None))))
        if self.parent.get("account_id") is not None:
            stmt = stmt.where(model.account_id == self.parent["account_id"])
        return stmt

    def _target_rows(self) -> list[tuple]:
        """Linhas do orçamento proposto (OPEX + CAPEX + Pessoal) no escopo e nos filtros: (linha, conta, pacote)."""
        if self._target is None:
            try:
                ctx = opex_svc.context(self.db)
                rows = cons.rows_for(self.db, ctx, ctx.version, self.visible)
            except opex_svc.OpexError:
                rows = []
            companies = {c.id: c.code for c in self.db.scalars(select(Company))}
            accounts = {a.code: a.id for a in self.db.scalars(select(Account))}
            packages = {p.name.upper(): p.id for p in self.db.scalars(select(BudgetPackage))}
            company_code = companies.get(self.company_id) if self.company_id else None
            out = []
            for r in rows:
                if r.cost_center_id is None or (company_code and r.company_code != company_code):
                    continue
                if self.cost_center_id and r.cost_center_id != self.cost_center_id:
                    continue
                account_id = accounts.get(r.account_code)
                pkg_id = packages.get((r.package or "").upper())
                if self.package_id and pkg_id != self.package_id:
                    continue
                if self.parent.get("package_id") is not None and pkg_id != self.parent["package_id"]:
                    continue
                if self.parent.get("no_package") and pkg_id is not None:
                    continue
                if self.parent.get("account_id") is not None and account_id != self.parent["account_id"]:
                    continue
                out.append((r, account_id, pkg_id))
            self._target = out
        return self._target

    def _target_sums(self, keys: tuple[str, ...], max_period: int | None) -> dict[tuple, Decimal]:
        out: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        for r, account_id, pkg_id in self._target_rows():
            attrs = {"cost_center_id": r.cost_center_id, "account_id": account_id, "package_id": pkg_id}
            for m, amount in enumerate(r.values, start=1):
                if not amount or (self.months and m not in self.months) or (max_period and m > max_period):
                    continue
                out[tuple(m if k == "period" else attrs[k] for k in keys)] += amount
        return out

    def sums(self, model, years, *group_cols, max_period: int | None = None) -> list[tuple]:
        """Soma por grupo nos anos informados (somados). Chaves de grupo: colunas do modelo ou `Account.package_id`."""
        years = list(years)
        sql_years = [y for y in years if y != self.target_year]
        totals: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        if sql_years:
            stmt = (
                select(*group_cols, func.sum(model.amount)).select_from(model).where(model.fiscal_year.in_(sql_years))
            )
            if any(c.key == "package_id" for c in group_cols):
                stmt = stmt.join(Account, Account.id == model.account_id)
            if max_period:
                stmt = stmt.where(model.period <= max_period)
            stmt = self._filtered(model, stmt)
            if group_cols:
                stmt = stmt.group_by(*group_cols)
            for *key, amount in self.db.execute(stmt):
                totals[tuple(key)] += amount or ZERO
        if model is ActualEntry and self.target_year in years:
            for key, amount in self._target_sums(tuple(c.key for c in group_cols), max_period).items():
                totals[key] += amount
        return [(*k, v) for k, v in totals.items()]

    def total(self, model, years, max_period: int | None = None) -> Decimal:
        rows = self.sums(model, years, max_period=max_period)
        return rows[0][0] if rows else ZERO


@router.get("/overview", summary="KPIs, evolução mensal e rankings do período selecionado (anos somados × meses)")
def overview(
    company_id: int | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    year: int | None = None,
    years: str | None = None,
    months: str | None = None,
    compare: bool = True,
    same_period: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """`years` (ex.: "2025,2026") são somados; `months` (ex.: "1,2,3") filtra tudo. `compare` liga a comparação
    com o ano anterior (só com um ano selecionado); `same_period` limita a base ao último mês fechado do ano em
    foco. O ano do ciclo (orçamento proposto) é selecionável como qualquer outro."""
    P = _period(db, years, months, compare, same_period, year)
    f = Facts(db, user, company_id, cost_center_id, package_id, P)
    cap = P.base_cap
    cur_total = f.total(ActualEntry, P.years)
    prev_full = f.total(ActualEntry, P.prev_years) if P.compare else ZERO
    prev_base = f.total(ActualEntry, P.prev_years, cap) if P.compare else ZERO
    budget_total = f.total(ReferenceBudgetEntry, P.years)
    budget_ytd = f.total(ReferenceBudgetEntry, P.years, P.closed) if P.closed else budget_total
    annualizable = (
        len(P.years) == 1 and P.years[0] in P.actual_years and bool(P.closed) and P.closed < 12 and not P.months
    )
    annualized = (cur_total * 12 / P.closed) if annualizable else None

    # ---- série mensal (anos selecionados somados mês a mês)
    monthly = {m: {"month": m, "prev": ZERO, "ref": ZERO, "budget": ZERO} for m in range(1, 13)}
    if P.compare:
        for period, amount in f.sums(ActualEntry, P.prev_years, ActualEntry.period, max_period=cap):
            monthly[int(period)]["prev"] = amount
    for period, amount in f.sums(ActualEntry, P.years, ActualEntry.period):
        monthly[int(period)]["ref"] = amount
    for period, amount in f.sums(ReferenceBudgetEntry, P.years, ReferenceBudgetEntry.period):
        monthly[int(period)]["budget"] = amount

    # ---- por pacote
    packages = {p.id: p for p in db.scalars(select(BudgetPackage))}
    pkg_cur = dict(f.sums(ActualEntry, P.years, Account.package_id))
    pkg_prev_full = dict(f.sums(ActualEntry, P.prev_years, Account.package_id)) if P.compare else {}
    pkg_prev = dict(f.sums(ActualEntry, P.prev_years, Account.package_id, max_period=cap)) if P.compare else {}
    pkg_budget = dict(f.sums(ReferenceBudgetEntry, P.years, Account.package_id))
    package_rows = []
    for pkg_id in set(pkg_cur) | set(pkg_prev) | set(pkg_budget):
        p = packages.get(pkg_id)
        cur, base = pkg_cur.get(pkg_id, ZERO), pkg_prev.get(pkg_id, ZERO)
        package_rows.append(
            {
                "package_id": pkg_id,
                "package": p.name if p else "Sem pacote",
                "package_type": p.package_type if p else None,
                "prev_total": _money(pkg_prev_full.get(pkg_id)),
                "prev_ytd": _money(base),
                "ref_ytd": _money(cur),
                "ref_annualized": _money(cur * 12 / P.closed if annualizable else ZERO),
                "budget": _money(pkg_budget.get(pkg_id)),
                "ytd_var_pct": _pct(cur, base),
            }
        )
    package_rows.sort(key=lambda r: Decimal(r["ref_ytd"]) + Decimal(r["prev_ytd"]), reverse=True)

    # ---- rankings (CC e conta) no período, com a base de comparação quando ligada
    def ranking(column, label_model, label_attrs):
        cur = dict(f.sums(ActualEntry, P.years, column))
        base = dict(f.sums(ActualEntry, P.prev_years, column, max_period=cap)) if P.compare else {}
        ids = sorted((k for k in cur if k is not None), key=lambda k: cur[k], reverse=True)[:10]
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
                    "ytd_var_pct": _pct(cur[i], base.get(i, ZERO)) if P.compare else None,
                }
            )
        return rows

    has_series = any(y in P.actual_years for y in P.years) or P.target_year in P.years
    return {
        "reference_year": max(P.years),
        "previous_year": P.prev_years[0] if P.compare else None,
        "selected_years": P.years,
        "selected_months": sorted(P.months or []),
        "last_closed_period": P.closed,
        "years_loaded": P.actual_years,
        "budget_years": P.budget_years,
        "available_years": P.available,
        "target_year": P.target_year,
        "period": P.summary(),
        "has_actual": has_series,
        "has_prev": P.compare,
        "has_budget": budget_total != ZERO,
        "kpis": {
            "prev_total": _money(prev_full),
            "prev_ytd": _money(prev_base),
            "ref_ytd": _money(cur_total),
            "ytd_var_pct": _pct(cur_total, prev_base) if P.compare else None,
            "ref_annualized": _money(annualized),
            "annualized_vs_prev_pct": _pct(annualized, prev_full) if (annualized is not None and P.compare) else None,
            "budget_total": _money(budget_total),
            "budget_ytd": _money(budget_ytd),
            "budget_consumption_pct": _pct(cur_total, budget_ytd) if budget_ytd else None,
        },
        "monthly": [{k: (_money(v) if k != "month" else v) for k, v in row.items()} for row in monthly.values()],
        "by_package": package_rows,
        "top_cost_centers": ranking(ActualEntry.cost_center_id, CostCenter, ("code", "name")),
        "top_accounts": ranking(ActualEntry.account_id, Account, ("code", "name")),
        "heatmap": _heatmap(db, f, P.years),
        "account_deltas": _account_deltas(db, f, P.years, P.prev_years, cap) if P.compare else [],
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
    compare: bool = True,
    same_period: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Uma linha por grupo com o valor do período (anos somados × meses), a base de comparação (ano anterior,
    quando `compare` está ligado e há um só ano; senão o orçado do período até o mês fechado), participação
    (AV %), variação e variação %. `parent_*` restringem aos filhos de uma linha expandida (drill-down).
    `thresholds` traz os limiares do ciclo usados no semáforo (alert.growth_pct / alert.reduction_pct)."""
    P = _period(db, years, months, compare, same_period)
    parent = {"package_id": parent_package_id, "no_package": parent_no_package, "account_id": parent_account_id}
    f = Facts(db, user, company_id, cost_center_id, package_id, P, parent)
    col = {"package": Account.package_id, "account": ActualEntry.account_id, "cost_center": ActualEntry.cost_center_id}
    bcol = {
        "package": Account.package_id,
        "account": ReferenceBudgetEntry.account_id,
        "cost_center": ReferenceBudgetEntry.cost_center_id,
    }

    cur = dict(f.sums(ActualEntry, P.years, col[group_by]))
    base_kind: str | None = None
    base: dict = {}
    base_label: str | None = None
    if P.compare:
        base_kind, base, base_label = (
            "prev",
            dict(f.sums(ActualEntry, P.prev_years, col[group_by], max_period=P.base_cap)),
            P.base_label,
        )
    elif any(y in P.budget_years for y in P.years):
        base_kind = "budget"
        base = dict(f.sums(ReferenceBudgetEntry, P.years, bcol[group_by], max_period=P.closed))
        suffix = f" até {MONTH_ABBR[P.closed - 1]}" if (P.closed and P.closed < 12 and not P.months) else ""
        base_label = f"Orçado {_years_label(P.years)}{suffix}"

    ids = [k for k in set(cur) | set(base) if k is not None]
    if group_by == "package":
        objs = {
            p.id: (None, p.name) for p in db.scalars(select(BudgetPackage).where(BudgetPackage.id.in_(ids or [-1])))
        }
        missing = (None, "Sem pacote")
    elif group_by == "account":
        objs = {a.id: (a.code, a.name) for a in db.scalars(select(Account).where(Account.id.in_(ids or [-1])))}
        missing = (None, "Conta não cadastrada")
    else:
        objs = {c.id: (c.code, c.name) for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(ids or [-1])))}
        missing = (None, "—")
    total_cur = sum(cur.values(), ZERO)
    total_base = sum(base.values(), ZERO)
    rows = []
    for key in set(cur) | set(base):
        c, b = cur.get(key, ZERO), base.get(key, ZERO)
        if c == 0 and b == 0:
            continue
        code, name = objs.get(key, missing)
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
                "var_pct": _pct(c, b) if base_kind else None,
                "has_children": group_by != "cost_center" and key is not None,
            }
        )
    rows.sort(key=lambda r: Decimal(r["ref"]), reverse=True)
    try:
        ctx = opex_svc.context(db)
        growth, reduction = float(ctx.param("alert.growth_pct", 0.2)), float(ctx.param("alert.reduction_pct", 0.3))
    except opex_svc.OpexError:
        growth, reduction = 0.2, 0.3
    return {
        "group_by": group_by,
        "reference_year": max(P.years),
        "previous_year": P.prev_years[0] if P.compare else None,
        "last_closed_period": P.closed,
        "period": P.summary(),
        "base": base_kind,
        "base_label": base_label,
        "thresholds": {"growth": growth, "reduction": reduction},
        "rows": rows,
        "total": {
            "ref": _money(total_cur),
            "base": _money(total_base),
            "var": _money(total_cur - total_base),
            "var_pct": _pct(total_cur, total_base) if base_kind else None,
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


def _heatmap(db: Session, f: Facts, years: list[int], limit: int = 12) -> dict:
    """Centro de custo × mês (maiores CCs do período, anos somados), para o mapa de calor."""
    rows: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for cc_id, period, amount in f.sums(ActualEntry, years, ActualEntry.cost_center_id, ActualEntry.period):
        if cc_id is not None:
            rows[cc_id][int(period)] += amount
    top = sorted(rows, key=lambda k: sum(rows[k].values()), reverse=True)[:limit]
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(top)))} if top else {}
    return {
        "year": max(years),
        "years": years,
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


def _account_deltas(
    db: Session, f: Facts, years: list[int], prev_years: list[int], cap: int | None, limit: int = 8
) -> list[dict]:
    """Maiores aumentos e reduções por conta: período selecionado × base (ano anterior, até `cap` quando informado)."""
    cur = dict(f.sums(ActualEntry, years, ActualEntry.account_id))
    base = dict(f.sums(ActualEntry, prev_years, ActualEntry.account_id, max_period=cap))
    deltas = {k: cur.get(k, ZERO) - base.get(k, ZERO) for k in (set(cur) | set(base)) if k is not None}
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
