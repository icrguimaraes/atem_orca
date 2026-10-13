"""Painel — período e fatos: o recorte (anos somados × meses × tipos) e as somas sobre as versões vigentes.

Saiu de `api/v1/dashboard.py` em 13/10/2026 (arquivo com 1.700 linhas); nada mudou de comportamento. Usado pelas
rotas do Painel, pela defesa do orçamento (`defense.py`) e pelo rastro (`services/trace.py`).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from app.core.deps import visible_cost_center_ids
from app.models import (
    Account,
    ActualEntry,
    BudgetCycle,
    BudgetPackage,
    BudgetVersion,
    Company,
    CostCenter,
    DatasetVersion,
    ReferenceBudgetEntry,
    User,
)
from app.services import consolidation as cons
from app.services import opex as opex_svc

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
    # comparação entre revisões (13/10/2026): base = foto de uma versão congelada do ciclo ("Revisão 0")
    base_version: BudgetVersion | None = None
    frozen_versions: list[dict] = field(default_factory=list)

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
    def version_compare_available(self) -> bool:
        """Comparar com uma revisão: só com o ano do ciclo no filtro e alguma versão congelada."""
        return self.target_year in self.years and bool(self.frozen_versions)

    @property
    def base_kind(self) -> str | None:
        if self.base_version is not None:
            return "version"
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
        if self.base_kind == "version":
            return f"{self.base_version.label} (congelada)"
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
            "base_version_id": self.base_version.id if self.base_version else None,
            "version_compare_available": self.version_compare_available,
            "versions": self.frozen_versions,
        }


def _period(
    db: Session,
    years: str | None,
    months: str | None,
    modules: str | None,
    compare: bool,
    same_period: bool,
    year: int | None = None,
    base_version_id: int | None = None,
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
    # revisões congeladas do ciclo (base alternativa, só com o ano do ciclo no filtro)
    frozen = (
        list(
            db.scalars(
                select(BudgetVersion)
                .where(BudgetVersion.cycle_id == cycle.id, BudgetVersion.status != "WORKING")
                .order_by(BudgetVersion.major, BudgetVersion.minor)
            )
        )
        if cycle
        else []
    )
    base_version = next((v for v in frozen if v.id == base_version_id), None) if target_year in selected else None
    prev = selected[0] - 1
    prev_years = [prev] if (compare and base_version is None and len(selected) == 1 and prev in actual_years) else []
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
        base_version,
        [{"id": v.id, "label": v.label} for v in frozen],
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
        self._rows: dict[int | None, list[tuple]] = {}  # linhas por versão (None = versão em elaboração)
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
        return self._version_rows(None)

    def _version_rows(self, version: BudgetVersion | None) -> list[tuple]:
        """Linhas da versão (None = a em elaboração; congelada = a foto), filtradas como o orçamento proposto."""
        key = version.id if version is not None else None
        if key not in self._rows:
            try:
                ctx = opex_svc.context(self.db)
                v = version or ctx.version
                rows = cons.rows_for(self.db, cons.version_context(ctx, v), v, self.visible)
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
            self._rows[key] = out
        return self._rows[key]

    def _target_sums(
        self, keys: tuple[str, ...], max_period: int | None, version: BudgetVersion | None = None
    ) -> dict[tuple, Decimal]:
        out: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        for r, account_id, pkg_id in self._version_rows(version):
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

    # ---- versão congelada como base (comparar Revisão 1 × Revisão 0)

    def version_sums(self, version: BudgetVersion, *keys: str, max_period: int | None = None) -> list[tuple]:
        return [(*k, v) for k, v in self._target_sums(keys, max_period, version).items()]

    def version_total(self, version: BudgetVersion) -> Decimal:
        rows = self.version_sums(version)
        return rows[0][0] if rows else ZERO

    def by_module_version(self, version: BudgetVersion) -> dict[str, Decimal]:
        out: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for r, _, _ in self._version_rows(version):
            for m, amount in enumerate(r.values, start=1):
                if amount and not (self.months and m not in self.months):
                    out[r.module] += amount
            if r.unscheduled and not self.months:
                out[r.module] += r.unscheduled
        return out


def _main_model(P: Period):
    return ActualEntry if P.main == "actual" else ReferenceBudgetEntry


def _base_total(f: Facts, P: Period) -> Decimal:
    if P.base_kind == "version":
        return f.version_total(P.base_version)
    if P.base_kind == "prev":
        return f.total(ActualEntry, P.prev_years, P.base_cap) * P.base_scale
    if P.base_kind == "budget":
        return f.total(ReferenceBudgetEntry, P.years, P.base_cap)
    if P.base_kind == "actual":
        return f.total(ActualEntry, P.actual_selected) * P.base_scale
    return ZERO


def _base_sums(f: Facts, P: Period, key: str) -> dict:
    """Base de comparação por grupo: ano anterior (no mesmo período ou anualizado), orçado do período ou a foto
    de uma revisão congelada."""
    if P.base_kind == "version":
        return dict(f.version_sums(P.base_version, key))
    if P.base_kind == "prev":
        return {k: v * P.base_scale for k, v in f.sums(ActualEntry, P.prev_years, key, max_period=P.base_cap)}
    if P.base_kind == "budget":
        return dict(f.sums(ReferenceBudgetEntry, P.years, key, max_period=P.base_cap))
    if P.base_kind == "actual":
        return {k: v * P.base_scale for k, v in f.sums(ActualEntry, P.actual_selected, key)}
    return {}


def _closed_by_company(db: Session, dataset_type: str, year: int) -> dict[str, int]:
    """Último mês de cada empresa no conjunto vigente (escopo ACTUAL:2026:1001 / PROJECTION:2026:1001)."""
    out: dict[str, int] = {}
    rows = db.execute(
        select(DatasetVersion.scope_key, DatasetVersion.last_closed_period).where(
            DatasetVersion.dataset_type == dataset_type,
            DatasetVersion.is_current,
            DatasetVersion.scope_key.like(f"{dataset_type}:{year}:%"),
        )
    )
    for scope, closed in rows:
        if closed:
            company = scope.split(":")[2]
            out[company] = max(out.get(company, 0), int(closed))
    return out


def _actual_only(f: Facts, years: list[int], keys: tuple[str, ...], cap: int | None) -> dict[tuple, Decimal]:
    """Realizado contábil (sem as linhas da projeção do gestor), por grupo, até o mês `cap`."""
    out: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    for *key, projected, amount in f.sums(ActualEntry, years, *keys, "projected", max_period=cap):
        if not projected:
            out[tuple(key)] += amount or ZERO
    return out
