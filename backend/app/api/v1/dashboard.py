"""Painel: realizado × orçamento de referência e checagens de qualidade dos dados.

Toda agregação considera apenas as versões vigentes (`dataset_versions.is_current`): versões
anteriores ficam no banco para auditoria, mas nunca somam no painel.
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, require_roles, visible_cost_center_ids
from app.db import get_db
from app.models import (
    Account,
    ActualEntry,
    Area,
    BudgetCycle,
    BudgetPackage,
    Company,
    ContractType,
    CostCenter,
    DatasetVersion,
    Department,
    Employee,
    ImportBatch,
    MacroAssumption,
    ReferenceBudgetEntry,
    User,
)
from app.models.base import Role
from app.services import consolidation as cons
from app.services import opex as opex_svc
from app.services import painel_figures

router = APIRouter(prefix="/dashboard", tags=["painel"])
ZERO = Decimal("0")


def _money(v: Decimal | None) -> str:
    return str((v or ZERO).quantize(Decimal("0.01")))


def _pct(new: Decimal, base: Decimal) -> str | None:
    return None if not base else str(((new - base) / abs(base)).quantize(Decimal("0.0001")))


def _share(part: Decimal, total: Decimal) -> str | None:
    """Participação no total (análise vertical): 0,523 = 52,3%."""
    return None if not total else str((part / total).quantize(Decimal("0.0001")))


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
    """Último mês do realizado do ano. Com projeção carregada (meses sem KSB1), o ano vale completo."""
    projected = db.scalar(
        select(func.max(DatasetVersion.last_closed_period)).where(
            DatasetVersion.dataset_type == "PROJECTION",
            DatasetVersion.is_current,
            DatasetVersion.scope_key.like(f"PROJECTION:{year}:%"),
        )
    )
    if projected:
        return max(12, int(projected)) if projected == 12 else int(projected)
    return db.scalar(
        select(func.max(DatasetVersion.last_closed_period)).where(
            DatasetVersion.dataset_type == "ACTUAL",
            DatasetVersion.is_current,
            DatasetVersion.scope_key.like(f"ACTUAL:{year}:%"),
        )
    )


@dataclass
class Period:
    """Período do painel no modelo de filtros do Power BI: anos somados × meses × tipos de orçamento.

    Duas séries: **realizado** (azul) e **orçado** (verde: orçamento de referência importado e, no ano do
    ciclo, o orçamento proposto pelos gestores). A série principal é o realizado quando algum ano escolhido
    tem realizado; senão, o orçado. A base de comparação é uma só: o realizado do ano anterior (opção, só
    com um ano escolhido) ou, sem ela, o orçado do período quando a série principal é o realizado."""

    years: list[int]
    months: set[int] | None
    modules: set[str] | None
    same_period: bool
    prev_years: list[int]
    closed: int | None  # último mês fechado do maior ano com realizado escolhido
    prev_closed: int | None
    actual_years: list[int]
    budget_years: list[int]
    target_year: int | None  # ano do ciclo: orçamento proposto (consolidação)
    available: list[int]

    @property
    def actual_selected(self) -> list[int]:
        return [y for y in self.years if y in self.actual_years]

    @property
    def budget_selected(self) -> list[int]:
        return [y for y in self.years if y in self.budget_years or y == self.target_year]

    @property
    def main(self) -> str:
        # com o ano do ciclo no filtro, a série principal é o orçamento: "orçado 2027 − realizado 2026" (08/10/2026)
        if self.target_year in self.years and self.target_year not in self.actual_years:
            return "budget"
        return "actual" if self.actual_selected else "budget"

    @property
    def main_years(self) -> list[int]:
        """Anos da série principal: com o orçamento contra o realizado, os anos de realizado são a base."""
        if self.base_kind == "actual":
            return [y for y in self.years if y not in self.actual_years]
        return self.years

    @property
    def compare(self) -> bool:
        return bool(self.prev_years)

    @property
    def compare_available(self) -> bool:
        return len(self.years) == 1 and (self.years[0] - 1) in self.actual_years

    @property
    def base_kind(self) -> str | None:
        if self.compare:
            return "prev"
        if self.main == "actual" and self.budget_selected:
            return "budget"
        if self.main == "budget" and self.actual_selected:
            return "actual"
        return None

    @property
    def annualize_base(self) -> bool:
        """Orçamento (ano sem realizado) contra um ano anterior incompleto: base anualizada (régua da consolidação)."""
        if self.base_kind == "actual":
            return not self.months and len(self.actual_selected) == 1 and bool(self.closed) and self.closed < 12
        return (
            self.compare
            and self.main == "budget"
            and not self.months
            and bool(self.prev_closed)
            and self.prev_closed < 12
        )

    @property
    def base_scale(self) -> Decimal:
        if not self.annualize_base:
            return Decimal(1)
        return Decimal(12) / Decimal(self.closed if self.base_kind == "actual" else self.prev_closed)

    @property
    def same_period_available(self) -> bool:
        return (
            bool(self.base_kind)
            and self.main == "actual"
            and bool(self.closed)
            and self.closed < 12
            and not self.months
        )

    @property
    def base_cap(self) -> int | None:
        """ "Mesmo período": a base vai até o último mês fechado do realizado em foco."""
        return self.closed if (self.same_period and self.same_period_available) else None

    @property
    def _closed_suffix(self) -> str:
        return f" até {MONTH_ABBR[self.closed - 1]}" if (self.closed and self.closed < 12 and not self.months) else ""

    @property
    def actual_label(self) -> str | None:
        ys = self.actual_selected
        if not ys:
            return None
        suffix = self._closed_suffix
        if len(ys) == 1:
            return f"Realizado {ys[0]}{suffix}"
        return f"Realizado {_years_label(ys)}" + (f" ({max(ys)}{suffix})" if suffix else "")

    @property
    def budget_label(self) -> str | None:
        ys = self.budget_selected
        if self.base_kind == "actual":
            ys = [y for y in ys if y not in self.actual_years]
        if not ys:
            return None
        return f"{'Orçamento' if ys == [self.target_year] else 'Orçado'} {_years_label(ys)}"

    @property
    def main_label(self) -> str:
        label = self.actual_label if self.main == "actual" else self.budget_label
        return label or f"Realizado {_years_label(self.years)}"

    @property
    def base_label(self) -> str | None:
        if self.base_kind == "prev":
            label = f"Realizado {_years_label(self.prev_years)}"
            if self.annualize_base:
                return f"{label} anualizado"
            if self.base_cap:
                return f"{label} até {MONTH_ABBR[self.base_cap - 1]}"
            return label if self.months else f"{label} (ano cheio)"
        if self.base_kind == "budget":
            return f"{self.budget_label} até {MONTH_ABBR[self.base_cap - 1]}" if self.base_cap else self.budget_label
        if self.base_kind == "actual":
            return f"Realizado {self.actual_selected[0]} anualizado" if self.annualize_base else self.actual_label
        return None

    def summary(self) -> dict:
        return {
            "years": self.years,
            "years_label": _years_label(self.years),
            "months": sorted(self.months or []),
            "modules": sorted(self.modules or []),
            "main": self.main,
            "main_label": self.main_label,
            "actual_label": self.actual_label,
            "budget_label": self.budget_label,
            "closed": self.closed,
            "closed_month": MONTH_ABBR[self.closed - 1] if self.closed else None,
            "compare": self.compare,
            "compare_available": self.compare_available,
            "same_period": self.same_period,
            "same_period_available": self.same_period_available,
            "prev_years": self.prev_years,
            "base_kind": self.base_kind,
            "base_label": self.base_label,
            "annualized_base": self.annualize_base,
            "target_year": self.target_year,
        }


def _period(
    db: Session,
    years: str | None,
    months: str | None,
    modules: str | None,
    compare: bool,
    same_period: bool,
    year: int | None = None,
) -> Period:
    actual_years = _loaded_years(db, "ACTUAL", 1)
    budget_years = _loaded_years(db, "REFERENCE_BUDGET", 2)
    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    target_year = cycle.fiscal_year if cycle else None
    available = sorted(set(actual_years) | set(budget_years) | ({target_year} if target_year else set()))
    selected = sorted(_ints(years) & set(available))
    if not selected:
        # o ano pedido (mesmo sem base carregada), senão o mais recente com realizado, com orçado ou o do ciclo
        default = year or (max(actual_years) if actual_years else max(budget_years) if budget_years else target_year)
        selected = [default or datetime.utcnow().year]
    month_filter = {m for m in _ints(months) if 1 <= m <= 12} or None
    module_filter = {m.strip() for m in (modules or "").upper().split(",") if m.strip() in cons.MODULE_NATURES} or None
    actual_selected = [y for y in selected if y in actual_years]
    closed = _last_closed(db, max(actual_selected)) if actual_selected else None
    prev = selected[0] - 1
    prev_years = [prev] if (compare and len(selected) == 1 and prev in actual_years) else []
    prev_closed = _last_closed(db, prev) if prev_years else None
    return Period(
        selected,
        month_filter,
        module_filter,
        same_period,
        prev_years,
        closed,
        prev_closed,
        actual_years,
        budget_years,
        target_year,
        available,
    )


class Facts:
    """Somas sobre os fatos vigentes com os filtros, o escopo do usuário e o período do painel.

    Realizado e orçado de referência vêm do banco; o orçado do ano do ciclo (orçamento proposto) vem da
    consolidação (`consolidation.rows_for`) — o painel soma e compara anos de fontes diferentes por um único
    caminho. Chaves de agrupamento: "package_id", "cost_center_id", "account_id", "period", "department_id"
    (área do CC) e "area_id" (setor do CC)."""

    def __init__(
        self,
        db: Session,
        user: User,
        company_id,
        cost_center_id,
        package_id,
        period: Period,
        parent: dict | None = None,
        account_id: int | None = None,
        department_id=None,  # inteiro ou lista "1,2"
    ) -> None:
        self.db = db
        self.account_id = account_id  # filtro por conta (clique nos visuais do Painel 2)
        self.department_ids = {department_id} if isinstance(department_id, int) else _ints(department_id)  # áreas do CC
        self.visible = visible_cost_center_ids(db, user)
        # empresa(s): inteiro ou lista "1,2" (filtro com mais de uma empresa, 08/10/2026)
        self.company_ids = {company_id} if isinstance(company_id, int) else _ints(company_id)
        self.company_id = next(iter(self.company_ids)) if len(self.company_ids) == 1 else None
        self.cost_center_id, self.package_id = cost_center_id, package_id
        self.months = period.months
        self.modules = period.modules
        self.target_year = period.target_year
        self.parent = parent or {}  # filtros do drill-down (filhos de um pacote ou de uma conta)
        self._target: list[tuple] | None = None
        self._structure: dict[int, tuple[int | None, int | None]] = {}  # CC → (área, setor)

    def _filtered(self, model, stmt: Select) -> Select:
        stmt = stmt.join(DatasetVersion, DatasetVersion.id == model.dataset_version_id).where(DatasetVersion.is_current)
        if self.visible is not None:
            stmt = stmt.where(model.cost_center_id.in_(self.visible or {-1}))
        if self.company_ids:
            stmt = stmt.where(model.company_id.in_(self.company_ids))
        if self.cost_center_id:
            stmt = stmt.where(model.cost_center_id == self.cost_center_id)
        if self.package_id:
            stmt = stmt.where(model.account_id.in_(select(Account.id).where(Account.package_id == self.package_id)))
        if self.account_id:
            stmt = stmt.where(model.account_id == self.account_id)
        if self.department_ids:
            stmt = stmt.where(
                model.cost_center_id.in_(select(CostCenter.id).where(CostCenter.department_id.in_(self.department_ids)))
            )
        if self.modules:
            natures = [n for m in self.modules for n in cons.MODULE_NATURES[m]]
            stmt = stmt.where(model.account_id.in_(select(Account.id).where(Account.nature.in_(natures))))
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
        for key in ("department_id", "area_id"):  # área e setor do centro de custo (drill-down)
            column = getattr(CostCenter, key)
            if self.parent.get(key) is not None:
                stmt = stmt.where(model.cost_center_id.in_(select(CostCenter.id).where(column == self.parent[key])))
            elif self.parent.get(f"no_{key}"):
                stmt = stmt.where(
                    or_(
                        model.cost_center_id.is_(None),
                        model.cost_center_id.in_(select(CostCenter.id).where(column.is_(None))),
                    )
                )
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
            self._structure = {c.id: (c.department_id, c.area_id) for c in self.db.scalars(select(CostCenter))}
            company_codes = {companies[i] for i in self.company_ids if i in companies}
            out = []
            for r in rows:
                if r.cost_center_id is None or (company_codes and r.company_code not in company_codes):
                    continue
                if self.cost_center_id and r.cost_center_id != self.cost_center_id:
                    continue
                if self.modules and r.module not in self.modules:
                    continue
                account_id = accounts.get(r.account_code)
                pkg_id = packages.get((r.package or "").upper())
                if self.package_id and pkg_id != self.package_id:
                    continue
                if self.account_id and account_id != self.account_id:
                    continue
                if self.parent.get("package_id") is not None and pkg_id != self.parent["package_id"]:
                    continue
                if self.parent.get("no_package") and pkg_id is not None:
                    continue
                if self.parent.get("account_id") is not None and account_id != self.parent["account_id"]:
                    continue
                dept_id, area_id = self._structure.get(r.cost_center_id, (None, None))
                if self.department_ids and dept_id not in self.department_ids:
                    continue
                structure = {"department_id": dept_id, "area_id": area_id}
                if any(
                    (self.parent.get(k) is not None and structure[k] != self.parent[k])
                    or (self.parent.get(f"no_{k}") and structure[k] is not None)
                    for k in structure
                ):
                    continue
                out.append((r, account_id, pkg_id))
            self._target = out
        return self._target

    def _target_sums(self, keys: tuple[str, ...], max_period: int | None) -> dict[tuple, Decimal]:
        out: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        for r, account_id, pkg_id in self._target_rows():
            dept_id, area_id = self._structure.get(r.cost_center_id, (None, None))
            attrs = {
                "cost_center_id": r.cost_center_id,
                "account_id": account_id,
                "package_id": pkg_id,
                "department_id": dept_id,
                "area_id": area_id,
            }
            for m, amount in enumerate(r.values, start=1):
                if not amount or (self.months and m not in self.months) or (max_period and m > max_period):
                    continue
                out[tuple(m if k == "period" else attrs[k] for k in keys)] += amount
            # CAPEX sem cronograma: conta no total do ano, não nos cortes por mês nem no "mesmo período"
            if r.unscheduled and "period" not in keys and not self.months and max_period is None:
                out[tuple(attrs[k] for k in keys)] += r.unscheduled
        return out

    def by_module(self, model, years, max_period: int | None = None) -> dict[str, Decimal]:
        """Soma por tipo de orçamento (OPEX, CAPEX, Pessoal) nos mesmos filtros: realizado/orçado pela natureza da
        conta; orçamento proposto do ano do ciclo pelo módulo da linha (inclui o CAPEX sem cronograma no total)."""
        years = list(years)
        from_target = model is ReferenceBudgetEntry and self.target_year in years
        sql_years = [y for y in years if not (from_target and y == self.target_year)]
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        if sql_years:
            stmt = (
                select(Account.nature, func.sum(model.amount))
                .select_from(model)
                .join(Account, Account.id == model.account_id)
                .where(model.fiscal_year.in_(sql_years))
            )
            if max_period:
                stmt = stmt.where(model.period <= max_period)
            stmt = self._filtered(model, stmt).group_by(Account.nature)
            for nature, amount in self.db.execute(stmt):
                module = cons.module_of_nature(nature)
                if module:
                    out[module] += amount or ZERO
        if from_target:
            for r, _, _ in self._target_rows():
                for m, amount in enumerate(r.values, start=1):
                    if amount and not (self.months and m not in self.months) and not (max_period and m > max_period):
                        out[r.module] += amount
                if r.unscheduled and not self.months and max_period is None:
                    out[r.module] += r.unscheduled
        return out

    def unscheduled_by_module(self) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        if not self.months:
            for r, _, _ in self._target_rows():
                out[r.module] += r.unscheduled
        return out

    def unscheduled_total(self) -> Decimal:
        """Orçamento proposto sem mês (CAPEX sem cronograma) no escopo e nos filtros."""
        if self.months:
            return ZERO
        return sum((r.unscheduled for r, _, _ in self._target_rows()), ZERO)

    @staticmethod
    def _column(model, key: str):
        if key == "package_id":
            return Account.package_id
        if key in ("department_id", "area_id"):  # área e setor vêm do cadastro do centro de custo
            return getattr(CostCenter, key)
        return getattr(model, key)

    def sums(self, model, years, *keys: str, max_period: int | None = None) -> list[tuple]:
        """Soma por grupo nos anos informados (somados). O orçado do ano do ciclo vem do orçamento proposto."""
        years = list(years)
        from_target = model is ReferenceBudgetEntry and self.target_year in years
        sql_years = [y for y in years if not (from_target and y == self.target_year)]
        totals: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        if sql_years:
            cols = [self._column(model, k) for k in keys]
            stmt = select(*cols, func.sum(model.amount)).select_from(model).where(model.fiscal_year.in_(sql_years))
            if "package_id" in keys:
                stmt = stmt.join(Account, Account.id == model.account_id)
            if {"department_id", "area_id"} & set(keys):
                stmt = stmt.outerjoin(CostCenter, CostCenter.id == model.cost_center_id)
            if max_period:
                stmt = stmt.where(model.period <= max_period)
            stmt = self._filtered(model, stmt)
            if cols:
                stmt = stmt.group_by(*cols)
            for *key, amount in self.db.execute(stmt):
                totals[tuple(key)] += amount or ZERO
        if from_target:
            for key, amount in self._target_sums(keys, max_period).items():
                totals[key] += amount
        return [(*k, v) for k, v in totals.items()]

    def total(self, model, years, max_period: int | None = None) -> Decimal:
        rows = self.sums(model, years, max_period=max_period)
        return rows[0][0] if rows else ZERO


def _main_model(P: Period):
    return ActualEntry if P.main == "actual" else ReferenceBudgetEntry


def _base_total(f: Facts, P: Period) -> Decimal:
    if P.base_kind == "prev":
        return f.total(ActualEntry, P.prev_years, P.base_cap) * P.base_scale
    if P.base_kind == "budget":
        return f.total(ReferenceBudgetEntry, P.years, P.base_cap)
    if P.base_kind == "actual":
        return f.total(ActualEntry, P.actual_selected) * P.base_scale
    return ZERO


def _base_sums(f: Facts, P: Period, key: str) -> dict:
    """Base de comparação por grupo: ano anterior (no mesmo período ou anualizado) ou orçado do período."""
    if P.base_kind == "prev":
        return {k: v * P.base_scale for k, v in f.sums(ActualEntry, P.prev_years, key, max_period=P.base_cap)}
    if P.base_kind == "budget":
        return dict(f.sums(ReferenceBudgetEntry, P.years, key, max_period=P.base_cap))
    if P.base_kind == "actual":
        return {k: v * P.base_scale for k, v in f.sums(ActualEntry, P.actual_selected, key)}
    return {}


@router.get("/overview", summary="KPIs, evolução mensal e rankings do período (anos somados × meses × tipos)")
def overview(
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    year: int | None = None,
    years: str | None = None,
    months: str | None = None,
    modules: str | None = None,
    compare: bool = True,
    same_period: bool = True,
    figures: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """`figures=true` acrescenta as figuras Plotly do Painel 2 (mesmos números, desenhados em Plotly).
    `years` (ex.: "2025,2026") são somados; `months` ("1,2,3") e `modules` ("OPEX,CAPEX,PERSONNEL") filtram
    tudo. `compare` liga a comparação com o ano anterior (só com um ano escolhido); `same_period` limita a base
    ao último mês fechado do realizado em foco. O ano do ciclo traz o orçamento proposto como orçado."""
    P = _period(db, years, months, modules, compare, same_period, year)
    f = Facts(db, user, company_id, cost_center_id, package_id, P, account_id=account_id, department_id=department_id)
    main_model = _main_model(P)
    actual_total = f.total(ActualEntry, P.years)
    budget_total = f.total(ReferenceBudgetEntry, P.main_years)
    main_total = actual_total if P.main == "actual" else budget_total
    base_total = _base_total(f, P)
    prev_full = f.total(ActualEntry, P.prev_years) if P.compare else ZERO
    ytd_cap = P.closed if (P.main == "actual" and P.closed and P.closed < 12 and not P.months) else None
    budget_cmp = f.total(ReferenceBudgetEntry, P.years, ytd_cap) if P.main == "actual" else budget_total
    annualizable = P.main == "actual" and len(P.years) == 1 and bool(ytd_cap)
    annualized = (actual_total * 12 / P.closed) if annualizable else None

    data = {
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
        "has_actual": bool(P.actual_selected),
        "has_prev": P.compare,
        "has_budget": budget_total != ZERO,
        "kpis": {
            "prev_total": _money(prev_full),
            "prev_ytd": _money(base_total),  # base de comparação (ano anterior ou orçado)
            "ref_ytd": _money(main_total),  # série principal (realizado ou orçado)
            "actual_total": _money(actual_total),
            "ytd_var_pct": _pct(main_total, base_total) if P.base_kind else None,
            "ref_annualized": _money(annualized),
            "annualized_vs_prev_pct": _pct(annualized, prev_full) if (annualized is not None and P.compare) else None,
            "budget_total": _money(budget_total),
            "budget_ytd": _money(budget_cmp),
            "budget_consumption_pct": _pct(actual_total, budget_cmp) if (P.main == "actual" and budget_cmp) else None,
            # parcela do orçamento do ano do ciclo sem mês (CAPEX sem cronograma): está no total, não no mensal
            "budget_unscheduled": _money(f.unscheduled_total() if P.target_year in P.years else ZERO),
        },
        "by_module": _by_module(f, P),
        "monthly": _monthly(f, P),
        "top_cost_centers": _ranking(db, f, P, "cost_center_id", CostCenter),
        "top_accounts": _ranking(db, f, P, "account_id", Account),
        "heatmap": _heatmap(db, f, main_model, P.main_years),
        "budget_progress": budget_progress(db, user, company_id, cost_center_id, package_id),
    }
    if data["budget_progress"]:
        # proposto × base por pacote com todos os tipos (OPEX, Pessoal, CAPEX) e os filtros do Painel (09/10/2026:
        # "em Todos não aparecem as contas de pessoal"); o andamento por status continua o do fluxo OPEX
        f_all = Facts(
            db,
            user,
            company_id,
            cost_center_id,
            package_id,
            replace(P, months=None),
            account_id=account_id,
            department_id=department_id,
        )
        data["budget_progress"] |= _progress_by_package(db, f_all, data["budget_progress"]["ref_year"])
    if figures:
        # Painel 2, como o Power BI: o visual em que se clicou não é filtrado pela própria seleção — mostra todos
        # os itens, com o escolhido em destaque (e clicável de novo para desmarcar); os outros visuais filtram.
        all_months = replace(P, months=None)
        f_months = Facts(
            db,
            user,
            company_id,
            cost_center_id,
            package_id,
            all_months,
            account_id=account_id,
            department_id=department_id,
        )
        f_ccs = Facts(db, user, company_id, None, package_id, P, account_id=account_id, department_id=department_id)
        f_accounts = Facts(db, user, company_id, cost_center_id, package_id, P, department_id=department_id)
        f_heat = Facts(
            db, user, company_id, None, package_id, all_months, account_id=account_id, department_id=department_id
        )
        data["heatmap_all"] = _heatmap(db, f_heat, main_model, P.main_years)
        # total de todos os CCs (o ranking mostra só os maiores), sem o filtro de CC, como o próprio visual
        top_main, top_base = f_ccs.total(main_model, P.main_years), _base_total(f_ccs, P)
        data["top_cost_centers_total"] = {
            "main": _money(top_main),
            "base": _money(top_base) if P.base_kind else None,
            "var_pct": _pct(top_main, top_base) if P.base_kind else None,
        }
        # evolução do orçamento: sem o filtro de meses (f_months), com todos os demais filtros
        data["evolution"], options = _evolution(db, f_months, P)
        data["evolution_options"] = [
            {"key": x["key"], "label": x["label"], "kind": x["kind"], "value": x["value"]} for x in options
        ]
        data["figures"] = painel_figures.build(
            data,
            monthly=_monthly(f_months, all_months),
            top_cost_centers=_ranking(db, f_ccs, P, "cost_center_id", CostCenter),
            top_accounts=_ranking(db, f_accounts, P, "account_id", Account),
            selected={"months": sorted(P.months or []), "cost_center_id": cost_center_id, "account_id": account_id},
            evolution=data["evolution"],
        )
    return data


def _evolution_options(db: Session, f: Facts, P: Period) -> list[dict]:
    """Todos os marcos possíveis da "Evolução do orçamento", em ordem cronológica (no mesmo ano, o orçado antes do
    realizado, como no slide da diretoria): Realizado de cada ano carregado (ano cheio ou "até MÊS"; o ano em curso
    também anualizado), Orçado de cada ano com orçamento de referência e o Orçamento do ciclo (proposto). Os filtros de
    ano e mês não se aplicam (`f` vem sem meses); empresa, área, CC, pacote, conta e tipo valem. Marco zerado fica de
    fora. Chave: "actual:2025", "actual_ann:2026", "budget:2026", "target:2027"; os orçados também "até MÊS"
    ("budget_ytd:2026", "target_ytd:2027"), somados até o último mês fechado do realizado — comparação com o mesmo
    período do realizado em curso (sem o CAPEX sem cronograma, que não tem mês)."""
    target = P.target_year or (max(P.actual_years) + 1 if P.actual_years else None)
    out: list[dict] = []
    # último mês fechado do realizado mais recente: corte do "mesmo período" para os orçados
    cut = _last_closed(db, max(P.actual_years)) if P.actual_years else None
    cut = cut if cut and cut < 12 else None

    def add(key: str, kind: str, word: str, year: int, value: Decimal, note: str | None = None, **extra) -> None:
        if value:
            label = f"{word} {year}" + (f" ({note})" if note else "")
            milestone = {"key": key, "kind": kind, "word": word, "year": year, "note": note, "label": label}
            out.append(milestone | {"value": _money(value)} | extra)

    budget_years = {y for y in P.budget_years if y != target}
    for year in sorted(set(P.actual_years) | budget_years):
        if year in budget_years:
            if cut:
                note = f"até {MONTH_ABBR[cut - 1]}"
                add(f"budget_ytd:{year}", "budget", "Orçado", year, f.total(ReferenceBudgetEntry, [year], cut), note)
            add(f"budget:{year}", "budget", "Orçado", year, f.total(ReferenceBudgetEntry, [year]))
        if year in P.actual_years:
            closed = _last_closed(db, year)
            ytd = f.total(ActualEntry, [year])
            if closed and closed < 12:
                add(f"actual:{year}", "actual", "Realizado", year, ytd, f"até {MONTH_ABBR[closed - 1]}")
                add(
                    f"actual_ann:{year}",
                    "actual",
                    "Realizado",
                    year,
                    ytd * 12 / closed,
                    "anualizado",
                    ytd=_money(ytd),
                    closed=closed,
                    closed_month=MONTH_ABBR[closed - 1],
                )
            else:
                add(f"actual:{year}", "actual", "Realizado", year, ytd)
    if target is not None and P.target_year == target:
        if cut:
            add(
                f"target_ytd:{target}",
                "budget",
                "Orçamento",
                target,
                f.total(ReferenceBudgetEntry, [target], cut),
                f"proposto até {MONTH_ABBR[cut - 1]}",
            )
        add(
            f"target:{target}",
            "budget",
            "Orçamento",
            target,
            f.total(ReferenceBudgetEntry, [target]),
            "proposto",
            unscheduled=_money(f.unscheduled_total()),
        )
    return out


def _evolution_default(options: list[dict], target: int | None) -> list[str]:
    """Marcos padrão: Realizado do ano retrasado → Orçado do ano anterior → Realizado do ano anterior (anualizado,
    se o ano não fechou) → Orçamento do ciclo."""
    if target is None:
        return []
    keys = {o["key"] for o in options}
    before, ref = target - 2, target - 1
    wanted = [f"actual:{before}", f"budget:{ref}"]
    wanted.append(f"actual_ann:{ref}" if f"actual_ann:{ref}" in keys else f"actual:{ref}")
    wanted.append(f"target:{target}")
    return [k for k in wanted if k in keys]


def _evolution(db: Session, f: Facts, P: Period, marks: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """(marcos escolhidos, todas as opções). Sem escolha (ou escolha inválida), os marcos padrão."""
    options = _evolution_options(db, f, P)
    target = P.target_year or (max(P.actual_years) + 1 if P.actual_years else None)
    valid = [k for k in (marks or []) if any(o["key"] == k for o in options)]
    chosen = set(valid if len(valid) >= 2 else _evolution_default(options, target))
    return [o for o in options if o["key"] in chosen], options


@router.get("/evolution", summary="Evolução do orçamento (cascata) com os marcos escolhidos no próprio visual")
def evolution(
    marks: str | None = Query(None, description='Marcos, ex.: "actual:2024,actual:2025,target:2027"'),
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    modules: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    P = _period(db, None, None, modules, True, True)
    f = Facts(db, user, company_id, cost_center_id, package_id, P, account_id=account_id, department_id=department_id)
    selected, options = _evolution(db, f, P, [k.strip() for k in (marks or "").split(",") if k.strip()])
    return {
        "options": [{"key": o["key"], "label": o["label"], "kind": o["kind"], "value": o["value"]} for o in options],
        "selected": [o["key"] for o in selected],
        "milestones": selected,
        "figure": painel_figures.fig_evolution(selected),
    }


def _by_module(f: Facts, P: Period) -> list[dict]:
    """Cards por tipo de orçamento: série principal e base de comparação por módulo. Com o filtro de tipo
    ativo, o card do total já é o do tipo: a lista vem vazia."""
    if P.modules:
        return []
    main = f.by_module(_main_model(P), P.main_years)
    if P.base_kind == "prev":
        base = {k: v * P.base_scale for k, v in f.by_module(ActualEntry, P.prev_years, P.base_cap).items()}
    elif P.base_kind == "budget":
        base = f.by_module(ReferenceBudgetEntry, P.years, P.base_cap)
    elif P.base_kind == "actual":
        base = {k: v * P.base_scale for k, v in f.by_module(ActualEntry, P.actual_selected).items()}
    else:
        base = {}
    unscheduled = f.unscheduled_by_module() if (P.main == "budget" and P.target_year in P.years) else {}
    return [
        {
            "module": m,
            "label": cons.MODULE_LABELS[m],
            "main": _money(main.get(m, ZERO)),
            "base": _money(base.get(m, ZERO)),
            "var_pct": _pct(main.get(m, ZERO), base.get(m, ZERO)) if P.base_kind else None,
            "unscheduled": _money(unscheduled.get(m, ZERO)),
        }
        for m in cons.MODULES
    ]


def _monthly(f: Facts, P: Period) -> list[dict]:
    """Série mensal (anos escolhidos somados mês a mês): ano anterior (base), realizado e orçado."""
    monthly = {m: {"month": m, "prev": ZERO, "ref": ZERO, "budget": ZERO, "proj": ZERO} for m in range(1, 13)}
    if P.compare:
        for period, amount in f.sums(ActualEntry, P.prev_years, "period", max_period=P.base_cap):
            monthly[int(period)]["prev"] = amount
    for period, amount in f.sums(ActualEntry, P.years, "period"):
        monthly[int(period)]["ref"] = amount
    for period, projected, amount in f.sums(ActualEntry, P.years, "period", "projected"):
        if projected:
            monthly[int(period)]["proj"] = amount
    for period, amount in f.sums(ReferenceBudgetEntry, P.main_years, "period"):
        monthly[int(period)]["budget"] = amount
    return [{k: (_money(v) if k != "month" else v) for k, v in row.items()} for row in monthly.values()]


def _ranking(db: Session, f: Facts, P: Period, key: str, label_model, limit: int = 10) -> list[dict]:
    """Maiores itens (CC ou conta) na série principal, com a base de comparação quando houver."""
    cur = dict(f.sums(_main_model(P), P.main_years, key))
    base = _base_sums(f, P, key)
    ids = sorted((k for k in cur if k is not None), key=lambda k: cur[k], reverse=True)[:limit]
    objs = {o.id: o for o in db.scalars(select(label_model).where(label_model.id.in_(ids)))} if ids else {}
    return [
        {
            "id": i,
            "code": getattr(objs.get(i), "code", None),
            "name": getattr(objs.get(i), "name", None),
            "ref_ytd": _money(cur[i]),
            "prev_ytd": _money(base.get(i)),
            "ytd_var_pct": _pct(cur[i], base.get(i, ZERO)) if P.base_kind else None,
        }
        for i in ids
    ]


@router.get("/breakdown", summary="Tabela do painel com drill-down: pacote GMD → conta → centro de custo")
def breakdown(
    group_by: Literal["department", "area", "package", "account", "cost_center"] = "package",
    parent_department_id: int | None = None,
    parent_no_department: bool = False,
    parent_area_id: int | None = None,
    parent_no_area: bool = False,
    parent_package_id: int | None = None,
    parent_no_package: bool = False,
    parent_account_id: int | None = None,
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    years: str | None = None,
    months: str | None = None,
    modules: str | None = None,
    compare: bool = True,
    same_period: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Uma linha por grupo com a série principal do período, a base de comparação (ano anterior ou orçado),
    participação (AV %), variação e variação %. `parent_*` restringem aos filhos de uma linha expandida
    (drill-down). `thresholds` traz os limiares do ciclo usados no semáforo (alert.growth_pct / reduction_pct)."""
    P = _period(db, years, months, modules, compare, same_period)
    parent = {
        "package_id": parent_package_id,
        "no_package": parent_no_package,
        "account_id": parent_account_id,
        "department_id": parent_department_id,
        "no_department_id": parent_no_department,
        "area_id": parent_area_id,
        "no_area_id": parent_no_area,
    }
    f = Facts(
        db, user, company_id, cost_center_id, package_id, P, parent, account_id=account_id, department_id=department_id
    )
    key = {
        "department": "department_id",
        "area": "area_id",
        "package": "package_id",
        "account": "account_id",
        "cost_center": "cost_center_id",
    }[group_by]
    cur = dict(f.sums(_main_model(P), P.main_years, key))
    base = _base_sums(f, P, key)

    ids = [k for k in set(cur) | set(base) if k is not None]
    if group_by == "department":  # Área (Controladoria, Tributos…)
        objs = {d.id: (None, d.name) for d in db.scalars(select(Department).where(Department.id.in_(ids or [-1])))}
        missing = (None, "Sem área")
    elif group_by == "area":  # Setor (Fiscal, Contabilidade…)
        objs = {a.id: (None, a.name) for a in db.scalars(select(Area).where(Area.id.in_(ids or [-1])))}
        missing = (None, "Sem setor")
    elif group_by == "package":
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
    for k in set(cur) | set(base):
        c, b = cur.get(k, ZERO), base.get(k, ZERO)
        if c == 0 and b == 0:
            continue
        code, name = objs.get(k, missing)
        rows.append(
            {
                "id": k,
                "code": code,
                "name": name,
                "ref": _money(c),
                "base": _money(b),
                # AV %: participação no total da coluna (não é variação)
                "share_ref": _share(c, total_cur),
                "share_base": _share(b, total_base),
                "var": _money(c - b),
                "var_pct": _pct(c, b) if P.base_kind else None,
                # "Sem pacote" (id nulo) também detalha: os filhos vêm com parent_no_package; já uma conta
                # sem cadastro (só no orçamento proposto) não tem como ser filtrada
                "has_children": group_by in ("department", "area", "package")
                or (group_by == "account" and k is not None),
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
        "base": P.base_kind,
        "base_label": P.base_label,
        "main_label": P.main_label,
        "thresholds": {"growth": growth, "reduction": reduction},
        "rows": rows,
        "total": {
            "ref": _money(total_cur),
            "base": _money(total_base),
            "var": _money(total_cur - total_base),
            "var_pct": _pct(total_cur, total_base) if P.base_kind else None,
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
        "figures": {"people_by_cost_center": painel_figures.fig_people(personnel["by_cost_center"])},
    }


def _heatmap(db: Session, f: Facts, model, years: list[int], limit: int = 12) -> dict:
    """Centro de custo × mês (maiores CCs do período, anos somados) na série principal, para o mapa de calor."""
    rows: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for cc_id, period, amount in f.sums(model, years, "cost_center_id", "period"):
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


def budget_progress(
    db: Session,
    user: User,
    company_id: str | None = None,
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
    if _ints(company_id):
        cc_stmt = cc_stmt.where(CostCenter.company_id.in_(_ints(company_id)))
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
    # pacote da linha; linha importada antes de a conta ter pacote usa o pacote atual da conta
    line_pkg = func.coalesce(BudgetLine.package_id, Account.package_id)
    line_stmt = (
        select(line_pkg, BudgetLine.cost_center_id, func.sum(BudgetLine.total_amount))
        .join(BudgetSubmission, BudgetSubmission.id == BudgetLine.submission_id)
        .join(Account, Account.id == BudgetLine.account_id)
        .where(
            BudgetSubmission.version_id == version.id,
            BudgetSubmission.module == "OPEX",
            BudgetLine.cost_center_id.in_(cc_ids or {-1}),
        )
        .group_by(line_pkg, BudgetLine.cost_center_id)
    )
    if package_id:
        line_stmt = line_stmt.where(line_pkg == package_id)
    proposed: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    started: set[int] = set()
    for pkg_id, cc_id, amount in db.execute(line_stmt):
        proposed[pkg_id] += amount or ZERO
        if amount:
            started.add(cc_id)
    # só os CCs que têm OPEX (lançamento ou orçamento já enviado): CC sem despesa (ex.: só CAPEX) ou zerado depois
    # de uma correção não aparece como "não iniciado" (decisão de 07/10/2026)
    sent = {"SUBMITTED", "UNDER_REVIEW", "ADJUSTMENT_REQUESTED", "APPROVED", "CONSOLIDATED"}
    counted = {cc for cc in cc_ids if cc in started or statuses.get(cc) in sent}
    counts: dict[str, int] = defaultdict(int)
    for cc in counted:
        counts[statuses.get(cc, "DRAFT")] += 1

    # 2026 anualizado só dos CCs que já lançaram 2027 (comparação justa durante o preenchimento)
    ref = cycle.actual_reference_year
    annualized: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    closed_by_company: dict[str | None, int | None] = {}
    if started:
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
        "total_cost_centers": len(counted),
        "started_cost_centers": len(started),
        "status_counts": dict(counts),
        "proposed_total": _money(sum(proposed.values(), ZERO)),
        "annualized_started_total": _money(sum(annualized.values(), ZERO)),
        # com a projeção do gestor o ano está completo: "Realizado 2026" em vez de "2026 anualizado"
        "ref_label": f"Realizado {ref}"
        if closed_by_company and all(c == 12 for c in closed_by_company.values())
        else f"{ref} anualizado",
        "by_package": by_package,
    }


def _progress_by_package(db: Session, f: Facts, ref: int) -> dict:
    """Orçamento do ciclo em construção, todos os tipos: proposto (consolidação) × realizado do ano de referência por
    pacote, só dos CCs que já têm valor proposto. Realizado anualizado se o ano ainda não fechou (sem projeção)."""
    target = f.target_year
    if target is None:
        return {}
    proposed_rows = f.sums(ReferenceBudgetEntry, [target], "package_id", "cost_center_id")
    started = {cc for _pkg, cc, amount in proposed_rows if cc is not None and amount}
    proposed: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    for pkg, cc, amount in proposed_rows:
        if cc in started:
            proposed[pkg] += amount
    closed = _last_closed(db, ref)
    scale = Decimal(12) / Decimal(closed) if closed and closed < 12 else Decimal(1)
    base: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    for pkg, cc, amount in f.sums(ActualEntry, [ref], "package_id", "cost_center_id"):
        if cc in started:
            base[pkg] += amount * scale
    packages = {pk.id: pk for pk in db.scalars(select(BudgetPackage))}
    by_package = sorted(
        (
            {
                "package_id": pid,
                "package": packages[pid].name if pid in packages else "Sem pacote",
                "proposed": _money(proposed.get(pid)),
                "ref_annualized": _money(base.get(pid)),
            }
            for pid in set(proposed) | set(base)
            if proposed.get(pid) or base.get(pid)
        ),
        key=lambda r: -max(Decimal(r["proposed"]), Decimal(r["ref_annualized"])),
    )
    return {
        "started_cost_centers": len(started),
        "proposed_total": _money(sum(proposed.values(), ZERO)),
        "annualized_started_total": _money(sum(base.values(), ZERO)),
        "ref_label": f"Realizado {ref}" if scale == 1 else f"{ref} anualizado",
        "by_package": by_package,
        "all_modules": True,
    }


def _last_closed_company(db: Session, year: int, company_code: str | None) -> int | None:
    return opex_svc.closed_period(db, year, company_code) if company_code else None
