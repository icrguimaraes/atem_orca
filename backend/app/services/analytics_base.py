"""Análise — constantes, formatação pt-BR, filtros, base filtrada por request e agregações. Saiu de
`services/analytics.py` em 13/10/2026, sem mudar comportamento; as figuras e o `dashboard()` continuam lá."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.models import (
    Account,
    ActualEntry,
    BudgetPackage,
    BudgetSubmission,
    BudgetVersion,
    Company,
    CostCenter,
    DatasetVersion,
    Department,
    ReferenceBudgetEntry,
)
from app.services import consolidation as cons
from app.services.opex import Context

ZERO = Decimal("0")
MODULES = ("OPEX", "CAPEX", "PERSONNEL")
MODULE_LABELS = cons.MODULE_LABELS
MONTH_NAMES = (
    "Janeiro",
    "Fevereiro",
    "Março",
    "Abril",
    "Maio",
    "Junho",
    "Julho",
    "Agosto",
    "Setembro",
    "Outubro",
    "Novembro",
    "Dezembro",
)
DIMENSIONS = {
    "company": "Empresa",
    "department": "Diretoria",
    "cost_center": "Centro de custo",
    "account": "Conta contábil",
    "module": "OPEX / CAPEX / Pessoal",
    "month": "Mês",
}
DRILL_ORDER = ("company", "department", "cost_center", "account", "month")
# Paleta do app (styles.css, etapa 3 de 10/10/2026 — sem laranja): realizado/referência azul, orçamento verde-água,
# ano anterior roxo; COLOR_PREV (antigo laranja) passa a ser o roxo do ano anterior
COLOR_PREV, COLOR_REF, COLOR_TARGET = "#8a6bbf", "#4472c4", "#4fa894"
COLOR_PAST = "#8a6bbf"  # realizado do ano anterior: roxo, igual ao token --series-past do frontend
COLOR_UP, COLOR_DOWN, COLOR_FLAT = "#e34948", "#4472c4", "#9aa1ad"
# composição do orçamento por tipo: tudo é orçamento, então o mesmo verde-água (o rótulo diz o tipo)
MODULE_COLORS = {"OPEX": COLOR_TARGET, "CAPEX": COLOR_TARGET, "PERSONNEL": COLOR_TARGET}
# situação dos orçamentos: rampa neutra até o envio, vermelho só para devolvido, verde para aprovado/consolidado
STATUS_COLORS = {
    "DRAFT": "#c9ced6",
    "IN_PROGRESS": "#9aa5b3",
    "ADJUSTMENT_REQUESTED": "#e34948",
    "SUBMITTED": "#6b7a8f",
    "UNDER_REVIEW": "#46566b",
    "APPROVED": "#4fa894",
    "CONSOLIDATED": "#2b7a68",
}
STATUS_ORDER = ("DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW", "APPROVED", "CONSOLIDATED")
FLAT_BAND = Decimal("0.05")  # ±5%: "estável" na análise por CC


# ------------------------------------------------------------------ formatação pt-BR


def _br(number: Decimal | float, decimals: int) -> str:
    text = f"{float(number):,.{decimals}f}"
    return text.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_money(value: Decimal | float | None) -> str:
    return f"R$ {_br(value or 0, 2)}"


def fmt_compact(value: Decimal | float | None) -> str:
    """R$ 1,25 mi · R$ 850,00 mil · R$ 125,43 mil · R$ 980,00 — sempre duas casas decimais (pedido de 09/10/2026)."""
    n = float(value or 0)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e9:
        return f"{sign}R$ {_br(a / 1e9, 2)} bi"
    if a >= 1e6:
        return f"{sign}R$ {_br(a / 1e6, 2)} mi"
    if a >= 1e3:
        return f"{sign}R$ {_br(a / 1e3, 2)} mil"
    return f"{sign}R$ {_br(a, 2)}"


def fmt_pct(ratio: Decimal | float | None) -> str:
    if ratio is None:
        return "—"
    n = float(ratio) * 100
    return f"{'+' if n > 0 else ''}{_br(n, 1)}%"


def short(label: str, size: int = 34) -> str:
    """Rótulo de eixo encurtado (o nome completo vai no hover)."""
    return label if len(label) <= size else label[: size - 1].rstrip() + "…"


def pct(new: Decimal, base: Decimal) -> Decimal | None:
    return None if not base else ((new - base) / abs(base)).quantize(Decimal("0.0001"))


# ------------------------------------------------------------------ filtros e base


@dataclass(frozen=True)
class Filters:
    company_id: str | None = None  # uma ou mais empresas: "1" ou "1,2"
    department_id: str | None = None  # uma ou mais áreas: "1" ou "1,2" (-1 = sem área)
    cost_center_id: int | None = None
    account: str | None = None
    module: str | None = None
    package_id: int | None = None
    prev: str | None = None  # série "Realizado": actual:ANO | actual_ann:ANO | none (padrão: realizado do ano anterior)
    ref: str | None = None  # série "Referência": budget:ANO | actual:ANO | actual_ann:ANO | none (padrão: orçado ref.)
    # filtros no modelo do Painel (08/10/2026): ano principal, meses, comparar com o ano anterior e mesmo período
    year: int | None = None
    months: str | None = None
    compare: bool = True
    same_period: bool = True

    def key(self) -> str:
        return json.dumps(self.__dict__, sort_keys=True, default=str)


@dataclass
class Cell:
    company_id: int
    company: str
    department_id: int | None
    department: str
    cost_center_id: int
    cc_code: str
    cc_name: str
    account: str
    account_name: str
    module: str
    package: str
    month: int
    prev: Decimal = ZERO  # realizado ano anterior
    ref: Decimal = ZERO  # orçado ano de referência
    target: Decimal = ZERO  # orçamento ano alvo


@dataclass
class Base:
    ctx: Context
    version: BudgetVersion
    filters: Filters
    cells: list[Cell]
    cost_centers: dict[int, CostCenter]
    scope_ids: set[int]  # CCs do escopo (visibilidade ∩ filtros)
    submissions: dict[int, dict[str, str]] = field(default_factory=dict)  # cc → módulo → status
    labels: dict[str, str] = field(default_factory=dict)  # prev/ref/target → rótulo da série escolhida

    def total(self, attr: str) -> Decimal:
        return money(sum((getattr(c, attr) for c in self.cells), ZERO))


def _dims(db: Session):
    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    companies = {c.id: c for c in db.scalars(select(Company))}
    departments = {d.id: d.name for d in db.scalars(select(Department))}
    accounts = {a.code: a for a in db.scalars(select(Account))}
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}
    return ccs, companies, departments, accounts, packages


def _package_name(module: str, acc: Account | None, packages: dict[int, str]) -> str:
    if module == "PERSONNEL":
        return "Pessoas"
    if module == "CAPEX":
        return "Capex"
    return packages.get(acc.package_id, "Sem pacote") if acc and acc.package_id else "Sem pacote"


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


def _years_loaded(db: Session, dataset_type: str) -> set[int]:
    return {
        int(k.split(":")[1])
        for k in db.scalars(
            select(DatasetVersion.scope_key).where(
                DatasetVersion.dataset_type == dataset_type, DatasetVersion.is_current
            )
        )
        if k and k.count(":") >= 2 and k.split(":")[1].isdigit()
    }


def years_available(db: Session, ctx: Context) -> list[dict]:
    """Anos do cabeçalho da Análise (igual ao Painel): realizado carregado + ano do ciclo."""
    out = []
    for y in sorted(_years_loaded(db, "ACTUAL") | {ctx.target_year}):
        closed = None if y == ctx.target_year else _last_closed(db, y)
        out.append({"year": y, "kind": "target" if y == ctx.target_year else "actual", "closed": closed})
    return out


def series_options(db: Session, ctx: Context) -> list[dict]:
    """Séries que o usuário pode escolher na Análise (Painel escolhe anos; aqui escolhe o que comparar)."""
    years_actual = sorted(
        {
            int(k.split(":")[1])
            for k in db.scalars(
                select(DatasetVersion.scope_key).where(
                    DatasetVersion.dataset_type == "ACTUAL", DatasetVersion.is_current
                )
            )
            if k and k.count(":") >= 2 and k.split(":")[1].isdigit()
        }
    )
    years_budget = sorted(
        {
            int(k.split(":")[1])
            for k in db.scalars(
                select(DatasetVersion.scope_key).where(
                    DatasetVersion.dataset_type == "REFERENCE_BUDGET", DatasetVersion.is_current
                )
            )
            if k and k.count(":") >= 2 and k.split(":")[1].isdigit()
        }
    )
    out = []
    for y in years_actual:
        closed = _last_closed(db, y)
        if closed and closed < 12:
            out.append({"key": f"actual:{y}", "label": f"Realizado {y} (até {MONTH_NAMES[closed - 1]})"})
            out.append({"key": f"actual_ann:{y}", "label": f"Realizado {y} anualizado"})
        else:
            out.append({"key": f"actual:{y}", "label": f"Realizado {y}"})
    for y in years_budget:
        out.append({"key": f"budget:{y}", "label": f"Orçado {y}"})
    return out


def resolve_series(db: Session, ctx: Context, key: str | None, default: str) -> tuple | None:
    """(modelo, ano, fator, rótulo) da série pedida; `none` = sem série. Chave inválida cai no padrão."""
    key = (key or default).strip()
    if key == "none":
        return None
    kind, _, year_s = key.partition(":")
    if kind not in ("actual", "actual_ann", "budget") or not year_s.isdigit():
        key, kind, _, year_s = default, *default.partition(":")
    year = int(year_s)
    if kind == "budget":
        return ReferenceBudgetEntry, year, Decimal(1), f"Orçado {year}"
    closed = _last_closed(db, year)
    if kind == "actual_ann" and closed and closed < 12:
        return ActualEntry, year, Decimal(12) / Decimal(closed), f"Realizado {year} anualizado"
    note = f" (até {MONTH_NAMES[closed - 1]})" if closed and closed < 12 else ""
    return ActualEntry, year, Decimal(1), f"Realizado {year}{note}"


def build(db: Session, ctx: Context, version: BudgetVersion, visible: set[int] | None, f: Filters) -> Base:
    ccs, companies, departments, accounts, packages = _dims(db)
    # escopo de CCs = visibilidade do usuário ∩ filtros de empresa/diretoria/CC
    scope = set(ccs)
    if visible is not None:
        scope &= visible
    wanted_companies = {int(x) for x in (f.company_id or "").split(",") if x.strip().isdigit()}
    if wanted_companies:
        scope &= {c.id for c in ccs.values() if c.company_id in wanted_companies}
    wanted_depts = {int(x) for x in (f.department_id or "").split(",") if x.strip().lstrip("-").isdigit()}
    if wanted_depts:  # -1 = centros de custo sem área
        wanted = {None if d == -1 else d for d in wanted_depts}
        scope &= {c.id for c in ccs.values() if c.department_id in wanted}
    if f.cost_center_id:
        scope &= {f.cost_center_id}
    package_accounts = {a.code for a in accounts.values() if a.package_id == f.package_id} if f.package_id else None

    def account_ok(code: str, module: str | None) -> bool:
        if module is None:
            return False
        if f.module and module != f.module:
            return False
        if f.account and code != f.account:
            return False
        if package_accounts is not None and code not in package_accounts:
            # custo de pessoal da consolidação sai em contas sem pacote: entra quando o filtro é o pacote Pessoas
            return module == "PERSONNEL" and packages.get(f.package_id) == "Pessoas"
        return True

    cells: dict[tuple[int, str, int], Cell] = {}

    def cell(cc_id: int, code: str, month: int, module: str, account_name: str | None = None) -> Cell:
        k = (cc_id, code, month)
        c = cells.get(k)
        if c is None:
            cc = ccs[cc_id]
            acc = accounts.get(code)
            c = cells[k] = Cell(
                cc.company_id,
                companies[cc.company_id].code,
                cc.department_id,
                departments.get(cc.department_id, "Sem diretoria"),
                cc.id,
                cc.code,
                cc.name,
                code,
                acc.name if acc else (account_name or code),
                module,
                _package_name(module, acc, packages),
                month,
            )
        return c

    def facts(
        model,
        year: int,
        attr: str,
        factor: Decimal = Decimal(1),
        months: set[int] | None = None,
        cap: int | None = None,
    ) -> None:
        stmt = (
            select(model.cost_center_id, Account.code, Account.nature, model.period, func.sum(model.amount))
            .join(DatasetVersion, DatasetVersion.id == model.dataset_version_id)
            .join(Account, Account.id == model.account_id)
            .where(DatasetVersion.is_current, model.fiscal_year == year, model.cost_center_id.in_(scope or {-1}))
            .group_by(model.cost_center_id, Account.code, Account.nature, model.period)
        )
        for cc_id, code, nature, period, amount in db.execute(stmt):
            module = cons.module_of_nature(nature)
            if not account_ok(code, module) or not 1 <= int(period) <= 12:
                continue
            if (months is not None and int(period) not in months) or (cap is not None and int(period) > cap):
                continue
            c = cell(cc_id, code, int(period), module)
            setattr(c, attr, getattr(c, attr) + (amount or ZERO) * factor)

    months = {int(m) for m in (f.months or "").split(",") if m.strip().isdigit() and 1 <= int(m) <= 12} or None
    labels: dict[str, str] = {}
    if f.year is not None:
        # modelo do Painel: ano principal (orçamento do ciclo ou realizado), ano anterior como base e mesmo período
        principal = f.year
        is_target = principal == ctx.target_year
        closed_p = None if is_target else _last_closed(db, principal)
        suffix = f" (até {MONTH_NAMES[closed_p - 1]})" if closed_p and closed_p < 12 and not months else ""
        labels["target"] = f"Orçamento {principal}" if is_target else f"Realizado {principal}{suffix}"
        cap = closed_p if (f.same_period and closed_p and closed_p < 12 and not months) else None
        if not is_target:
            facts(ActualEntry, principal, "target", months=months)
        budget_years = _years_loaded(db, "REFERENCE_BUDGET")
        ref_year = principal if principal in budget_years else (ctx.ref_year if is_target else None)
        if ref_year is not None and ref_year in budget_years:
            facts(ReferenceBudgetEntry, ref_year, "ref", months=months, cap=cap)
            labels["ref"] = f"Orçado {ref_year}" + (f" (até {MONTH_NAMES[cap - 1]})" if cap else "")
        else:
            labels["ref"] = ""
        prev_year = principal - 1
        if f.compare and prev_year in _years_loaded(db, "ACTUAL"):
            closed_prev = _last_closed(db, prev_year)
            if is_target and closed_prev and closed_prev < 12 and not months:
                facts(ActualEntry, prev_year, "prev", Decimal(12) / Decimal(closed_prev))
                labels["prev"] = f"Realizado {prev_year} anualizado"
            else:
                facts(ActualEntry, prev_year, "prev", months=months, cap=cap)
                note = f" (até {MONTH_NAMES[cap - 1]})" if cap else ""
                labels["prev"] = f"Realizado {prev_year}{note}"
        else:
            labels["prev"] = ""
    else:
        is_target = True
        labels["target"] = f"Orçamento {ctx.target_year}"
        prev_s = resolve_series(db, ctx, f.prev, f"actual:{ctx.prev_year}")
        ref_s = resolve_series(db, ctx, f.ref, f"budget:{ctx.ref_year}")
        if prev_s:
            facts(prev_s[0], prev_s[1], "prev", prev_s[2], months=months)
            labels["prev"] = prev_s[3]
        else:
            labels["prev"] = ""
        if ref_s:
            facts(ref_s[0], ref_s[1], "ref", ref_s[2], months=months)
            labels["ref"] = ref_s[3]
        else:
            labels["ref"] = ""
    if is_target:
        for row in cons.rows_for(db, ctx, version, scope):
            if row.cost_center_id is None or not account_ok(row.account_code, row.module):
                continue
            for i, amount in enumerate(row.values, start=1):
                if amount and (months is None or i in months):
                    c = cell(row.cost_center_id, row.account_code, i, row.module, row.account_name)
                    c.target += amount

    subs: dict[int, dict[str, str]] = defaultdict(dict)
    for s in db.scalars(
        select(BudgetSubmission).where(
            BudgetSubmission.version_id == version.id, BudgetSubmission.cost_center_id.in_(scope or {-1})
        )
    ):
        subs[s.cost_center_id][s.module] = s.status
    active_scope = {i for i in scope if ccs[i].is_active}
    return Base(ctx, version, f, list(cells.values()), ccs, active_scope, subs, labels=labels)


# ------------------------------------------------------------------ agregações


def group(base: Base, dim: str) -> list[dict]:
    """Soma prev/ref/target por dimensão; devolve rótulo, id para drill-down e totais."""
    out: dict = {}
    for c in base.cells:
        if dim == "company":
            key, label = c.company_id, c.company
        elif dim == "department":
            key, label = c.department_id or -1, c.department
        elif dim == "cost_center":
            key, label = c.cost_center_id, f"{c.cc_name}"
        elif dim == "account":
            key, label = c.account, f"{c.account_name}"
        elif dim == "module":
            key, label = c.module, MODULE_LABELS[c.module]
        elif dim == "month":
            key, label = c.month, MONTH_NAMES[c.month - 1]
        elif dim == "package":
            key, label = c.package, c.package
        else:
            raise ValueError(dim)
        g = out.setdefault(key, {"id": key, "label": label, "prev": ZERO, "ref": ZERO, "target": ZERO})
        if dim == "cost_center":
            g["sub"] = c.cc_code
        elif dim == "account":
            g["sub"] = c.account
        g["prev"] += c.prev
        g["ref"] += c.ref
        g["target"] += c.target
    rows = list(out.values())
    for r in rows:
        for k in ("prev", "ref", "target"):
            r[k] = money(r[k])
    return rows


def status_summary(base: Base) -> dict:
    """CCs do escopo por situação do orçamento OPEX (fluxo principal) e por módulo."""
    per_module: dict[str, dict[str, int]] = {m: defaultdict(int) for m in MODULES}
    filled = set()
    for c in base.cells:
        if c.target:
            filled.add(c.cost_center_id)
    for cc_id in base.scope_ids:
        for m in MODULES:
            per_module[m][base.submissions.get(cc_id, {}).get(m, "DRAFT")] += 1
    opex = per_module["OPEX"]
    total = len(base.scope_ids)
    approved = opex["APPROVED"] + opex["CONSOLIDATED"]
    sent = opex["SUBMITTED"] + opex["UNDER_REVIEW"]
    return {
        "total": total,
        "filled": len(filled & base.scope_ids),
        "pending": total - len(filled & base.scope_ids),
        "submitted": sent,
        "returned": opex["ADJUSTMENT_REQUESTED"],
        "approved": approved,
        "filled_pct": str((Decimal(len(filled & base.scope_ids)) / total).quantize(Decimal("0.0001")))
        if total
        else None,
        "approved_pct": str((Decimal(approved) / total).quantize(Decimal("0.0001"))) if total else None,
        "by_module": {m: dict(v) for m, v in per_module.items()},
    }
