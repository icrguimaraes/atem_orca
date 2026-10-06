"""Camada analítica (Plotly): base única filtrada → KPIs e figuras do dashboard FP&A.

Conceitos iguais aos do processo orçamentário (sem recalcular regras):
- Realizado {ano anterior}: `actual_entries` das versões vigentes.
- Orçado {ano de referência}: `reference_budget_entries` das versões vigentes.
- Orçamento {ano alvo}: `consolidation.rows_for` (OPEX + CAPEX + Pessoal da versão escolhida; congelada lê a
  fotografia).
- Variação = orçamento alvo − base; % sobre a base. Módulo da conta = natureza (consolidation.module_of_nature).

Tudo é montado numa só passada (3 consultas agregadas + o orçamento) e as figuras saem prontas em JSON Plotly;
o frontend só renderiza. Formatação pt-BR feita aqui para ser a mesma em eixos, rótulos e tooltips.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

import plotly.graph_objects as go
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.domain.workflow import STATUS_LABELS
from app.models import (
    Account,
    ActualEntry,
    AuditLog,
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
# Paleta validada do app (styles.css): ano anterior laranja, referência azul, orçamento verde-água
COLOR_PREV, COLOR_REF, COLOR_TARGET = "#eb6834", "#2a78d6", "#1baf7a"
COLOR_PAST = "#8b95a7"  # realizado do ano anterior: neutro, igual ao token --series-past do frontend
COLOR_UP, COLOR_DOWN, COLOR_FLAT = "#e34948", "#2a78d6", "#9aa1ad"
MODULE_COLORS = {"OPEX": COLOR_REF, "CAPEX": COLOR_PREV, "PERSONNEL": COLOR_TARGET}
STATUS_COLORS = {
    "DRAFT": "#b8bec9",
    "IN_PROGRESS": "#2a78d6",
    "ADJUSTMENT_REQUESTED": "#e34948",
    "SUBMITTED": "#e07912",
    "UNDER_REVIEW": "#c9a227",
    "APPROVED": "#1baf7a",
    "CONSOLIDATED": "#0e8a62",
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
    """R$ 1,2 mi · R$ 850 mil · R$ 125,4 mil · R$ 980."""
    n = float(value or 0)
    sign = "-" if n < 0 else ""
    a = abs(n)
    if a >= 1e9:
        return f"{sign}R$ {_br(a / 1e9, 1)} bi"
    if a >= 1e6:
        return f"{sign}R$ {_br(a / 1e6, 1)} mi"
    if a >= 1e3:
        return f"{sign}R$ {_br(a / 1e3, 1)} mil".replace(",0 mil", " mil")
    return f"{sign}R$ {_br(a, 0)}"


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
    company_id: int | None = None
    department_id: int | None = None  # Diretoria
    cost_center_id: int | None = None
    account: str | None = None
    module: str | None = None
    package_id: int | None = None

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


def build(db: Session, ctx: Context, version: BudgetVersion, visible: set[int] | None, f: Filters) -> Base:
    ccs, companies, departments, accounts, packages = _dims(db)
    # escopo de CCs = visibilidade do usuário ∩ filtros de empresa/diretoria/CC
    scope = set(ccs)
    if visible is not None:
        scope &= visible
    if f.company_id:
        scope &= {c.id for c in ccs.values() if c.company_id == f.company_id}
    if f.department_id:  # -1 = centros de custo sem diretoria
        wanted = None if f.department_id == -1 else f.department_id
        scope &= {c.id for c in ccs.values() if c.department_id == wanted}
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

    def facts(model, year: int, attr: str) -> None:
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
            c = cell(cc_id, code, int(period), module)
            setattr(c, attr, getattr(c, attr) + (amount or ZERO))

    facts(ActualEntry, ctx.prev_year, "prev")
    facts(ReferenceBudgetEntry, ctx.ref_year, "ref")
    for row in cons.rows_for(db, ctx, version, scope):
        if row.cost_center_id is None or not account_ok(row.account_code, row.module):
            continue
        for i, amount in enumerate(row.values, start=1):
            if amount:
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
    return Base(ctx, version, f, list(cells.values()), ccs, active_scope, subs)


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


# ------------------------------------------------------------------ figuras


def _layout(title: str | None = None, **kw) -> dict:
    layout = {
        "template": "none",
        "paper_bgcolor": "rgba(0,0,0,0)",
        "plot_bgcolor": "rgba(0,0,0,0)",
        "margin": {"l": 8, "r": 8, "t": 8 if not title else 36, "b": 8},
        "font": {"family": "DM Sans, system-ui, sans-serif", "size": 12},
        "hoverlabel": {"font": {"family": "DM Sans, system-ui, sans-serif", "size": 12}, "namelength": -1},
        "legend": {"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0, "font": {"size": 12}},
        "separators": ",.",
        "autosize": True,
    }
    if title:
        layout["title"] = {"text": title, "x": 0, "font": {"size": 13}}
    layout.update(kw)
    return layout


def _money_axis(values) -> dict:
    """Eixo em R$ compacto: ticks calculados aqui para a formatação ser pt-BR."""
    top = max([abs(float(v)) for v in values] + [0])
    if top <= 0:
        return {"tickvals": [0], "ticktext": ["R$ 0"], "zeroline": False, "automargin": True}
    exp = 10 ** math.floor(math.log10(top))
    step = next(s * exp for s in (1, 2, 2.5, 5, 10) if s * exp >= top / 4)
    ticks = [i * step for i in range(0, int(top // step) + 2)]
    return {
        "tickvals": ticks,
        "ticktext": [fmt_compact(t) for t in ticks],
        "zeroline": False,
        "gridcolor": "rgba(128,128,128,0.18)",
        "automargin": True,
    }


def _fig(data: list, layout: dict) -> dict:
    fig = go.Figure(data=data, layout=layout)
    return json.loads(fig.to_json())


def _extreme_labels(values: list[float]) -> tuple[list[str], list[str]]:
    """Rótulos só no maior e no menor ponto da série (regra do design: gráfico sem rótulo mostra os extremos).
    O mínimo considera só meses com valor; se máximo e mínimo coincidem, um rótulo só."""
    positives = [v for v in values if v > 0]
    if not positives:
        return [""] * len(values), ["top center"] * len(values)
    hi, lo = max(positives), min(positives)
    i_hi, i_lo = values.index(hi), values.index(lo)
    text = [""] * len(values)
    pos = ["top center"] * len(values)
    text[i_hi] = fmt_compact(hi)
    if i_lo != i_hi:
        text[i_lo] = fmt_compact(lo)
        pos[i_lo] = "bottom center"
    return text, pos


def fig_monthly(base: Base) -> dict:
    y = base.ctx
    rows = {r["id"]: r for r in group(base, "month")}
    series = [
        ("prev", f"Realizado {y.prev_year}", COLOR_PAST, "dot"),
        ("ref", f"Orçado {y.ref_year}", COLOR_REF, "dash"),
        ("target", f"Orçamento {y.target_year}", COLOR_TARGET, "solid"),
    ]
    data = []
    all_values = []
    for attr, name, color, dash in series:
        values = [float(rows.get(m, {}).get(attr, ZERO)) for m in range(1, 13)]
        if not any(values):
            continue
        all_values += values
        text, textposition = _extreme_labels(values)
        data.append(
            go.Scatter(
                x=list(MONTH_NAMES),
                y=values,
                name=name,
                mode="lines+markers+text",
                text=text,
                textposition=textposition,
                textfont={"size": 11, "color": color},
                cliponaxis=False,
                line={"color": color, "width": 2.5, "dash": dash},
                marker={"size": 7},
                customdata=[fmt_money(v) for v in values],
                hovertemplate="%{x} · " + name + ": <b>%{customdata}</b><extra></extra>",
            )
        )
    layout = _layout(
        hovermode="x unified", yaxis=_money_axis(all_values), xaxis={"showgrid": False, "automargin": True}
    )
    layout["dragmode"] = "zoom"
    return _fig(data, layout)


def fig_annual(base: Base, dim: str, limit: int = 12) -> dict:
    y = base.ctx
    rows = group(base, dim)
    if dim == "month":
        rows = sorted(rows, key=lambda r: r["id"])  # ordem do calendário, todos os meses
    else:
        rows = sorted(rows, key=lambda r: -max(r["target"], r["ref"], r["prev"]))[:limit]
    labels = [r["label"] for r in rows]
    data = []
    all_values = []
    for attr, name, color in (
        ("prev", f"Realizado {y.prev_year}", COLOR_PAST),
        ("ref", f"Orçado {y.ref_year}", COLOR_REF),
        ("target", f"Orçamento {y.target_year}", COLOR_TARGET),
    ):
        values = [float(r[attr]) for r in rows]
        if not any(values):
            continue
        all_values += values
        data.append(
            go.Bar(
                x=[short(lb, 26) for lb in labels],
                y=values,
                name=name,
                marker_color=color,
                customdata=[[fmt_money(v), str(r["id"]), r["label"]] for v, r in zip(values, rows, strict=True)],
                text=[fmt_compact(v) if v else "" for v in values],
                textposition="outside",
                textfont={"size": 10},
                hovertemplate="%{customdata[2]}<br>" + name + ": <b>%{customdata[0]}</b><extra></extra>",
            )
        )
    layout = _layout(barmode="group", yaxis=_money_axis(all_values), xaxis={"showgrid": False, "automargin": True})
    layout["uniformtext"] = {"mode": "hide", "minsize": 9}
    return _fig(data, layout)


def fig_variation(base: Base, by: str, mode: str, limit: int = 15) -> dict:
    """Maiores variações orçamento alvo × orçado referência (ou × realizado anterior quando não há orçado)."""
    y = base.ctx
    rows = group(base, by)
    has_ref = any(r["ref"] for r in rows)
    base_attr, base_label = ("ref", f"Orçado {y.ref_year}") if has_ref else ("prev", f"Realizado {y.prev_year}")
    min_relevant = y.param("alert.min_relevant_amount", 1000)
    items = []
    for r in rows:
        b, t = r[base_attr], r["target"]
        if max(abs(b), abs(t)) < min_relevant:
            continue
        diff = money(t - b)
        ratio = pct(t, b)
        if mode == "pct" and ratio is None:
            continue
        items.append((r, diff, ratio))
    items.sort(key=lambda it: -abs(it[2] if mode == "pct" else it[1]))
    items = items[:limit][::-1]  # maior no topo
    xs = [float(it[2]) * 100 if mode == "pct" else float(it[1]) for it in items]
    labels = [short(it[0]["label"]) for it in items]
    colors = [COLOR_UP if x > 0 else COLOR_DOWN for x in xs]
    custom = [
        [
            fmt_money(it[0][base_attr]),
            fmt_money(it[0]["target"]),
            fmt_money(it[1]),
            fmt_pct(it[2]),
            str(it[0]["id"]),
            it[0]["label"],
        ]
        for it in items
    ]
    text = [fmt_pct(it[2]) if mode == "pct" else ("+" if it[1] > 0 else "") + fmt_compact(it[1]) for it in items]
    data = [
        go.Bar(
            x=xs,
            y=labels,
            orientation="h",
            marker_color=colors,
            text=text,
            textposition="outside",
            customdata=custom,
            cliponaxis=False,
            hovertemplate=(
                "<b>%{customdata[5]}</b><br>"
                + base_label
                + ": %{customdata[0]}<br>Orçamento "
                + str(y.target_year)
                + ": %{customdata[1]}<br>Variação: %{customdata[2]} (%{customdata[3]})<extra></extra>"
            ),
        )
    ]
    span = max([abs(x) for x in xs] + [0])
    xaxis = {
        "zeroline": True,
        "zerolinecolor": "rgba(128,128,128,0.4)",
        "gridcolor": "rgba(128,128,128,0.18)",
        "automargin": True,
        "range": [-span * 1.45, span * 1.45] if span else None,  # texto fora da barra sem invadir os nomes
    }
    if mode == "pct":
        xaxis["ticksuffix"] = "%"
    else:
        xaxis.update(_money_axis(xs) | {"zeroline": True, "range": xaxis["range"]})
        if span:
            exp = 10 ** math.floor(math.log10(span))
            step = next(s * exp for s in (1, 2, 2.5, 5, 10) if s * exp >= span / 3)
            ticks = [i * step for i in range(-int(span // step) - 1, int(span // step) + 2)]
            xaxis.update({"tickvals": ticks, "ticktext": [("+" if t > 0 else "") + fmt_compact(t) for t in ticks]})
    layout = _layout(
        xaxis=xaxis,
        yaxis={"automargin": True, "tickfont": {"size": 11}},
        height=max(260, 28 * len(items) + 60),
        showlegend=False,
        margin={"l": 8, "r": 70, "t": 8, "b": 8},
    )
    return _fig(data, layout) | {"meta": {"base_label": base_label}}


def fig_ranking(base: Base, top: int) -> dict:
    y = base.ctx
    rows = sorted(group(base, "account"), key=lambda r: -r["target"])
    rows = [r for r in rows if r["target"] > 0]
    if top:
        rows = rows[:top]
    rows = rows[::-1]
    total = base.total("target") or Decimal(1)
    values = [float(r["target"]) for r in rows]
    data = [
        go.Bar(
            x=values,
            y=[short(r["label"]) for r in rows],
            orientation="h",
            marker_color=COLOR_TARGET,
            text=[fmt_compact(v) for v in values],
            textposition="outside",
            cliponaxis=False,
            customdata=[
                [fmt_money(r["target"]), fmt_pct(r["target"] / total), r["sub"], fmt_money(r["ref"]), r["label"]]
                for r in rows
            ],
            hovertemplate=(
                "<b>%{customdata[4]}</b> · %{customdata[2]}<br>Orçamento "
                + str(y.target_year)
                + ": %{customdata[0]} (%{customdata[1]} do total)<br>Orçado "
                + str(y.ref_year)
                + ": %{customdata[3]}<extra></extra>"
            ),
        )
    ]
    layout = _layout(
        xaxis=_money_axis(values),
        yaxis={"automargin": True, "tickfont": {"size": 11}},
        height=max(260, 26 * len(rows) + 60),
        showlegend=False,
        margin={"l": 8, "r": 70, "t": 8, "b": 8},
    )
    return _fig(data, layout)


def fig_composition(base: Base) -> dict:
    rows = {r["id"]: r for r in group(base, "module")}
    total = sum((r["target"] for r in rows.values()), ZERO)
    labels = [MODULE_LABELS[m] for m in MODULES]
    values = [float(rows.get(m, {}).get("target", ZERO)) for m in MODULES]
    shares = [fmt_pct(Decimal(v) / total) if total else "—" for v in values]
    bar = go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color=[MODULE_COLORS[m] for m in MODULES],
        text=[f"{fmt_compact(v)} · {s.lstrip('+')}" if v else "" for v, s in zip(values, shares, strict=True)],
        textposition="outside",
        cliponaxis=False,
        customdata=[[fmt_money(v), s.lstrip("+")] for v, s in zip(values, shares, strict=True)],
        hovertemplate="<b>%{y}</b><br>%{customdata[0]} · %{customdata[1]} do total<extra></extra>",
    )
    layout = _layout(
        xaxis=_money_axis(values),
        yaxis={"automargin": True, "autorange": "reversed"},
        height=200,
        showlegend=False,
        margin={"l": 8, "r": 110, "t": 8, "b": 8},
    )
    return _fig([bar], layout) | {"meta": {"total": fmt_money(total), "total_compact": fmt_compact(total)}}


def fig_cost_centers(base: Base, limit: int = 20) -> dict:
    """Ref × alvo por CC: barras pareadas ordenadas, cor pela classificação (aumento/redução/estável)."""
    y = base.ctx
    rows = group(base, "cost_center")
    has_ref = any(r["ref"] for r in rows)
    base_attr, base_label = ("ref", f"Orçado {y.ref_year}") if has_ref else ("prev", f"Realizado {y.prev_year}")
    rows = sorted(rows, key=lambda r: -max(r["target"], r[base_attr]))[:limit][::-1]
    classes = []
    for r in rows:
        ratio = pct(r["target"], r[base_attr])
        if ratio is None:
            classes.append(("Novo" if r["target"] else "Sem orçamento", COLOR_FLAT))
        elif ratio > FLAT_BAND:
            classes.append(("Aumento", COLOR_UP))
        elif ratio < -FLAT_BAND:
            classes.append(("Redução", COLOR_DOWN))
        else:
            classes.append(("Estável", COLOR_FLAT))
    labels = [short(r["label"]) for r in rows]
    data = [
        go.Bar(
            x=[float(r[base_attr]) for r in rows],
            y=labels,
            orientation="h",
            name=base_label,
            marker_color="rgba(128,128,128,0.35)",
            customdata=[fmt_money(r[base_attr]) for r in rows],
            hovertemplate=base_label + ": %{customdata}<extra></extra>",
        ),
        go.Bar(
            x=[float(r["target"]) for r in rows],
            y=labels,
            orientation="h",
            name=f"Orçamento {y.target_year}",
            marker_color=[c[1] for c in classes],
            text=[
                f"{c[0]} {fmt_pct(pct(r['target'], r[base_attr]))}"
                if pct(r["target"], r[base_attr]) is not None
                else c[0]
                for r, c in zip(rows, classes, strict=True)
            ],
            textposition="outside",
            cliponaxis=False,
            textfont={"size": 10},
            customdata=[[fmt_money(r["target"]), r["sub"], str(r["id"]), r["label"]] for r in rows],
            hovertemplate="<b>%{customdata[3]}</b> · %{customdata[1]}<br>Orçamento "
            + str(y.target_year)
            + ": %{customdata[0]}<extra></extra>",
        ),
    ]
    values = [float(r["target"]) for r in rows] + [float(r[base_attr]) for r in rows]
    layout = _layout(
        barmode="group",
        bargap=0.3,
        xaxis=_money_axis(values),
        yaxis={"automargin": True, "tickfont": {"size": 11}},
        height=max(280, 44 * len(rows) + 70),
        margin={"l": 8, "r": 120, "t": 8, "b": 8},
    )
    layout["legend"]["traceorder"] = "normal"
    return _fig(data, layout) | {"meta": {"base_label": base_label, "band": str(FLAT_BAND)}}


def fig_status(base: Base) -> dict:
    s = status_summary(base)
    data = []
    for status in STATUS_ORDER:
        counts = [s["by_module"][m].get(status, 0) for m in MODULES]
        if not any(counts):
            continue
        data.append(
            go.Bar(
                x=counts,
                y=[MODULE_LABELS[m] for m in MODULES],
                orientation="h",
                name=STATUS_LABELS[status],
                marker_color=STATUS_COLORS[status],
                text=[str(c) if c else "" for c in counts],
                textposition="inside",
                insidetextanchor="middle",
                hovertemplate="%{y} · " + STATUS_LABELS[status] + ": <b>%{x}</b> CC(s)<extra></extra>",
            )
        )
    layout = _layout(
        barmode="stack",
        xaxis={"tickformat": "d", "gridcolor": "rgba(128,128,128,0.18)", "dtick": 1 if s["total"] <= 12 else None},
        yaxis={"automargin": True, "autorange": "reversed"},
        height=220,
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    return _fig(data, layout)


# ------------------------------------------------------------------ dashboard completo (com cache curto)


_CACHE: dict[str, tuple[float, dict]] = {}
CACHE_TTL = 30.0


def _data_token(db: Session) -> str:
    """Muda a cada mutação registrada (todo lançamento/importação grava em audit_logs)."""
    return str(db.scalar(select(func.max(AuditLog.id))) or 0)


def dashboard(
    db: Session,
    ctx: Context,
    version: BudgetVersion,
    visible: set[int] | None,
    f: Filters,
    *,
    dimension: str = "cost_center",
    variation_by: str = "account",
    variation_mode: str = "abs",
    top: int = 10,
) -> dict:
    key_src = "|".join(
        [
            str(version.id),
            "all" if visible is None else ",".join(map(str, sorted(visible))),
            f.key(),
            dimension,
            variation_by,
            variation_mode,
            str(top),
            _data_token(db),
        ]
    )
    key = hashlib.sha1(key_src.encode()).hexdigest()
    now = time.time()
    hit = _CACHE.get(key)
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    if len(_CACHE) > 200:
        _CACHE.clear()

    base = build(db, ctx, version, visible, f)
    prev, ref, target = base.total("prev"), base.total("ref"), base.total("target")
    modules = {r["id"]: r for r in group(base, "module")}
    status = status_summary(base)
    y = ctx
    kpis = {
        "prev_actual": {"label": f"Realizado {y.prev_year}", "value": str(prev), "compact": fmt_compact(prev)},
        "ref_budget": {"label": f"Orçado {y.ref_year}", "value": str(ref), "compact": fmt_compact(ref)},
        "target": {"label": f"Orçamento {y.target_year}", "value": str(target), "compact": fmt_compact(target)},
        "var_ref": {
            "label": f"Variação {y.target_year} × {y.ref_year}",
            "value": str(money(target - ref)),
            "compact": fmt_compact(target - ref),
            "pct": None if pct(target, ref) is None else str(pct(target, ref)),
            "pct_label": fmt_pct(pct(target, ref)),
        },
        "var_prev": {
            "label": f"Variação {y.target_year} × {y.prev_year}",
            "value": str(money(target - prev)),
            "compact": fmt_compact(target - prev),
            "pct": None if pct(target, prev) is None else str(pct(target, prev)),
            "pct_label": fmt_pct(pct(target, prev)),
        },
        **{
            m.lower(): {
                "label": MODULE_LABELS[m],
                "value": str(modules.get(m, {}).get("target", ZERO)),
                "compact": fmt_compact(modules.get(m, {}).get("target", ZERO)),
                "share": fmt_pct(modules.get(m, {}).get("target", ZERO) / target).lstrip("+") if target else "—",
            }
            for m in MODULES
        },
        "filled_pct": {
            "label": "Orçamento preenchido",
            "value": status["filled_pct"],
            "compact": fmt_pct(Decimal(status["filled_pct"])).lstrip("+") if status["filled_pct"] else "—",
            "hint": f"{status['filled']} de {status['total']} CCs com valores em {y.target_year}",
        },
        "approved_pct": {
            "label": "Aprovado",
            "value": status["approved_pct"],
            "compact": fmt_pct(Decimal(status["approved_pct"])).lstrip("+") if status["approved_pct"] else "—",
            "hint": f"{status['approved']} de {status['total']} CCs com OPEX aprovado ou consolidado",
        },
    }
    result = {
        "years": {"prev": y.prev_year, "ref": y.ref_year, "target": y.target_year},
        "version": {"id": version.id, "label": version.label, "status": version.status},
        "kpis": kpis,
        "status": status,
        "has": {"prev": prev != 0, "ref": ref != 0, "target": target != 0},
        "dimension": dimension,
        "dimensions": DIMENSIONS,
        "drill_order": DRILL_ORDER,
        "figures": {
            "monthly": fig_monthly(base),
            "annual": fig_annual(base, dimension),
            "variation": fig_variation(base, variation_by, variation_mode),
            "ranking": fig_ranking(base, top),
            "composition": fig_composition(base),
            "cost_centers": fig_cost_centers(base),
            "status": fig_status(base),
        },
        "cells": len(base.cells),
    }
    _CACHE[key] = (now, result)
    return result
