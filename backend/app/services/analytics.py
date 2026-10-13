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
from decimal import Decimal

import plotly.graph_objects as go
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.domain.workflow import STATUS_LABELS
from app.models import (
    AuditLog,
    BudgetVersion,
)
from app.services.analytics_base import (
    COLOR_DOWN,
    COLOR_FLAT,
    COLOR_PAST,
    COLOR_REF,
    COLOR_TARGET,
    COLOR_UP,
    DIMENSIONS,
    DRILL_ORDER,
    FLAT_BAND,
    MODULE_COLORS,
    MODULE_LABELS,
    MODULES,
    MONTH_NAMES,
    STATUS_COLORS,
    STATUS_ORDER,
    ZERO,
    Base,
    Filters,
    build,
    fmt_compact,
    fmt_money,
    fmt_pct,
    group,
    pct,
    short,
    status_summary,
)
from app.services.opex import Context

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
    rows = {r["id"]: r for r in group(base, "month")}
    series = [
        s
        for s in (
            ("prev", base.labels["prev"], COLOR_PAST, "dot"),
            ("ref", base.labels["ref"], COLOR_REF, "dash"),
            ("target", base.labels["target"], COLOR_TARGET, "solid"),
        )
        if s[1]
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
    rows = group(base, dim)
    if dim == "month":
        rows = sorted(rows, key=lambda r: r["id"])  # ordem do calendário, todos os meses
    else:
        rows = sorted(rows, key=lambda r: -max(r["target"], r["ref"], r["prev"]))[:limit]
    labels = [r["label"] for r in rows]
    data = []
    all_values = []
    for attr, name, color in (
        s
        for s in (
            ("prev", base.labels["prev"], COLOR_PAST),
            ("ref", base.labels["ref"], COLOR_REF),
            ("target", base.labels["target"], COLOR_TARGET),
        )
        if s[1]
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
    base_attr, base_label = ("ref", base.labels["ref"]) if has_ref else ("prev", base.labels["prev"])
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
                + ": %{customdata[0]}<br>"
                + base.labels["target"]
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


def ranking_base_key(base: Base) -> str | None:
    """Base das maiores contas: realizado do ano anterior se comparado, senão o orçado de referência."""
    return "prev" if base.labels.get("prev") else ("ref" if base.labels.get("ref") else None)


def fig_ranking(base: Base, top: int) -> dict:
    """Maiores contas no mesmo visual dos maiores CCs do Painel (09/10/2026): série principal e base do mesmo
    tamanho, rótulo nas duas e variação; o total de todas as contas vai em meta.totals (cartões) e na coluna Total."""
    from app.services import painel_figures as pf  # painel_figures importa daqui

    key = ranking_base_key(base)
    rows = sorted(group(base, "account"), key=lambda r: -r["target"])
    rows = [r for r in rows if r["target"] > 0]
    if top:
        rows = rows[:top]
    ranked = [
        {
            "id": r["id"],
            "code": r.get("sub"),
            "name": r["label"],
            "value": str(r["target"]),
            "base": str(r[key]) if key else None,
            "var_pct": str((r["target"] - r[key]) / r[key]) if key and r[key] else None,
        }
        for r in rows
    ]
    is_budget = base.labels["target"].startswith("Orçamento")
    lb = {
        "main": base.labels["target"],
        "base": base.labels[key] if key else "",
        "main_color": pf.ORCADO if is_budget else pf.REALIZADO,
        "base_color": (pf.REALIZADO if key == "prev" else pf.ORCADO) if key else pf.ANTERIOR,
    }
    fig = pf.fig_rank(ranked, lb, bool(key), show_code=False)
    main_total = base.total("target")
    base_total = base.total(key) if key else None
    totals = {
        "main": str(main_total),
        "base": str(base_total) if base_total is not None else None,
        "var_pct": str((main_total - base_total) / base_total) if base_total else None,
        "main_label": lb["main"],
        "base_label": lb["base"] or None,
    }
    fig["meta"] = (fig.get("meta") or {}) | {"totals": totals, "total_figure": pf.fig_rank_total(totals, lb)}
    return fig


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
    rows = group(base, "cost_center")
    has_ref = any(r["ref"] for r in rows)
    base_attr, base_label = ("ref", base.labels["ref"]) if has_ref else ("prev", base.labels["prev"])
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
            name=base.labels["target"],
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
            hovertemplate="<b>%{customdata[3]}</b> · %{customdata[1]}<br>"
            + base.labels["target"]
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
        "prev_actual": {"label": base.labels["prev"] or "Realizado", "value": str(prev), "compact": fmt_compact(prev)},
        "ref_budget": {"label": base.labels["ref"] or "Referência", "value": str(ref), "compact": fmt_compact(ref)},
        "target": {"label": base.labels["target"], "value": str(target), "compact": fmt_compact(target)},
        "var_ref": {
            "label": f"{base.labels['target']} × {base.labels['ref'] or 'referência'}",
            "value": str(money(target - ref)),
            "compact": fmt_compact(target - ref),
            "pct": None if pct(target, ref) is None else str(pct(target, ref)),
            "pct_label": fmt_pct(pct(target, ref)),
        },
        "var_prev": {
            "label": f"{base.labels['target']} × {base.labels['prev'] or 'realizado'}",
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
            "hint": f"{status['filled']} de {status['total']} CCs com valores em {base.labels['target']}",
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
        "series": {
            "prev": f.prev or f"actual:{y.prev_year}",
            "ref": f.ref or f"budget:{y.ref_year}",
            "labels": base.labels,
        },
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
