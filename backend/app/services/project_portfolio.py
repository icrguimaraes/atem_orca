"""Resumo da carteira de projetos (14/10/2026, "essa página está muito simples"): indicadores somados, cronograma
(Gantt com marcos e a linha de hoje), realizado × orçado por projeto, curva S do realizado acumulado e os cartões.
Mesmos números da página de cada projeto (`project_numbers`), lidos do sistema — nada é alterado."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import plotly.graph_objects as go
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import is_global
from app.models import Project, User
from app.services.analytics import _fig, _layout
from app.services.analytics_base import fmt_money, short
from app.services.project_numbers import ZERO, Numbers, _sum, chart_years, figure_from, merge, numbers, summary

MONTHS_SHORT = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


def _label(p: Project) -> str:
    return f"{p.code} · {short(p.name, 30)}"


def _month_label(d: date) -> str:
    return f"{MONTHS_SHORT[d.month - 1]}/{d.strftime('%y')}"


def _month_ticks(first: date, last: date) -> dict:
    """Ticks mensais em pt-BR (o Plotly escreveria "Apr/26"): de 1, 2, 3 ou 6 em 6 meses, conforme o período."""
    months = (last.year - first.year) * 12 + last.month - first.month + 1
    step = next(s for s in (1, 2, 3, 6, 12) if months / s <= 9)
    vals, d = [], date(first.year, first.month, 1)
    while d <= last:
        vals.append(d)
        m = d.month - 1 + step
        d = date(d.year + m // 12, m % 12 + 1, 1)
    return {"tickvals": [v.isoformat() for v in vals], "ticktext": [_month_label(v) for v in vals]}


def _first_actual(n: Numbers) -> date | None:
    for y in sorted(n.actual):
        for m, v in enumerate(n.actual[y]):
            if v:
                return date(y, m + 1, 1)
    return None


def _gantt(projects: list[Project], nums: dict[int, Numbers], today: date) -> dict:
    """Uma barra por projeto (início → término previsto, preenchida até o % dos marcos) e os marcos em losango.
    Sem término previsto, a barra vai até hoje, clara, com o aviso; sem início, começa no primeiro mês com realizado.
    Sempre há a série de marcos: com ela o
    PlotlyChart não transforma o gráfico (a adaptação do celular é só para gráficos só de barras)."""
    from app.services.painel_figures import LEGEND, ORCADO, REALIZADO, _rgba

    labels, data = [_label(p) for p in projects], []
    spans = []
    for p in projects:
        start = p.start_actual or p.start_planned or _first_actual(nums[p.id])
        end = p.end_actual or p.end_planned
        open_end = end is None
        if start is None:
            spans.append(None)
            continue
        end = end or max(today, start + timedelta(days=30))
        spans.append((start, end, open_end))
    ms_day = 86_400_000
    for name, color, fill in (("Prazo", _rgba(REALIZADO, 0.22), False), ("Concluído", REALIZADO, True)):
        x, base, y, text, tips = [], [], [], [], []
        for p, label, span in zip(projects, labels, spans, strict=True):
            if span is None:
                continue
            start, end, open_end = span
            done = [m.progress_pct or 0 for m in p.milestones]
            pct = (sum(done) / len(done) / 100) if done else 0.0
            days = (end - start).days * (pct if fill else 1)
            if fill and not days:
                continue
            x.append(days * ms_day)
            base.append(start.isoformat())
            y.append(label)
            text.append("" if fill else ("sem término previsto" if open_end else ""))
            tips.append(
                f"{start.strftime('%d/%m/%Y')} a {'—' if open_end else end.strftime('%d/%m/%Y')}"
                + (f" · {round(pct * 100)}% dos marcos" if done else "")
            )
        data.append(
            go.Bar(
                x=x,
                base=base,
                y=y,
                orientation="h",
                name=name,
                marker={"color": color, "cornerradius": 4},
                text=text,
                textposition="inside",
                insidetextanchor="end",
                textfont={"size": 11},
                customdata=tips,
                hovertemplate="%{y}<br>%{customdata}<extra></extra>",
                width=0.5,
                showlegend=fill,
            )
        )
    groups = {"done": ([], [], [], ORCADO), "late": ([], [], [], "#e24b4a"), "todo": ([], [], [], "#9aa1ad")}
    for p, label in zip(projects, labels, strict=True):
        for m in p.milestones:
            if not m.due_date:
                continue
            state = (
                "done" if m.done_date or (m.progress_pct or 0) >= 100 else ("late" if m.due_date < today else "todo")
            )
            xs, ys, tip, _ = groups[state]
            xs.append(m.due_date.isoformat())
            ys.append(label)
            tip.append(f"{m.title} · {m.due_date.strftime('%d/%m/%Y')} · {m.progress_pct or 0}%")
    names = {"done": "Marco concluído", "late": "Marco atrasado", "todo": "Marco a fazer"}
    for state, (xs, ys, tip, color) in groups.items():
        data.append(
            go.Scatter(
                x=xs,
                y=ys,
                mode="markers",
                name=names[state],
                marker={"symbol": "diamond", "size": 13, "color": color, "line": {"width": 1, "color": "#ffffff"}},
                customdata=tip,
                hovertemplate="%{customdata}<extra></extra>",
                showlegend=bool(xs),
            )
        )
    edges = [d for span in spans if span for d in span[:2]] + [today]
    edges += [m.due_date for p in projects for m in p.milestones if m.due_date]
    layout = _layout(
        barmode="overlay",
        showlegend=True,
        legend=LEGEND,
        xaxis={
            "type": "date",
            "gridcolor": "rgba(128,128,128,0.18)",
            "automargin": True,
            **_month_ticks(min(edges), max(edges)),
        },
        yaxis={"autorange": "reversed", "automargin": True, "categoryorder": "array", "categoryarray": labels},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
        shapes=[
            {
                "type": "line",
                "x0": today.isoformat(),
                "x1": today.isoformat(),
                "yref": "paper",
                "y0": 0,
                "y1": 1,
                "line": {"color": "#e24b4a", "width": 1.5, "dash": "dot"},
            }
        ],
        annotations=[
            {
                "x": today.isoformat(),
                "y": 0,
                "yref": "paper",
                "text": "hoje",
                "showarrow": False,
                "yanchor": "bottom",
                "xanchor": "left",
                "font": {"size": 11, "color": "#a32d2d"},
            }
        ],
        separators=",.",
    )
    return _fig(data, layout)


def _compare(projects: list[Project], nums: dict[int, Numbers]) -> dict:
    """Realizado (com a projeção empilhada) × orçado do ano de referência × orçamento do ciclo, por projeto."""
    from app.services.painel_figures import INK, LEGEND, ORCADO, PROJETADO, REALIZADO, _headroom, _note_unit, _rgba
    from app.services.painel_figures import _short as short_label

    any_n = next(iter(nums.values()))
    ref, target = any_n.ref_year, any_n.target_year
    labels = [f"{p.code}<br>{short(p.name, 24)}" for p in projects]  # duas linhas: sem inclinar nem cortar
    actual = [float(_sum(nums[p.id].actual.get(ref))) for p in projects]
    proj = [float(_sum(nums[p.id].projection.get(ref))) for p in projects]
    budget = [float(_sum(nums[p.id].ref_budget.get(ref))) for p in projects]
    cycle = [float(_sum(nums[p.id].cycle_budget) + nums[p.id].cycle_unscheduled) for p in projects]
    series = [
        ("ref", f"Realizado {ref}", REALIZADO, actual, None),
        ("proj", "Projeção do gestor", PROJETADO, proj, actual),
        ("budget", f"Orçado {ref}", ORCADO, budget, None),
        ("cycle", f"Orçamento {target}", _rgba(ORCADO, 0.55), cycle, None),
    ]
    data, everything = [], []
    for key, name, color, values, base in series:
        if not any(values):
            continue
        everything += [v + (base[i] if base else 0) for i, v in enumerate(values)]
        data.append(
            go.Bar(
                x=labels,
                y=values,
                base=base,
                name=name,
                offsetgroup="ref" if key == "proj" else key,
                marker={"color": color, "cornerradius": 4},
                text=[short_label(v) if v > 0 else "" for v in values],
                textposition="outside",
                textangle=0,
                constraintext="none",
                cliponaxis=False,
                textfont={"size": 11, "color": INK.get(color, "#2b7a68")},
                customdata=[fmt_money(v) for v in values],
                hovertemplate=name + ": <b>%{customdata}</b><extra></extra>",
            )
        )
    layout = _layout(
        barmode="group",
        bargap=0.3,
        bargroupgap=0.06,
        hovermode="x unified",
        showlegend=True,
        legend=LEGEND,
        yaxis={"visible": False, "range": [0, (max(everything + [0.0]) or 1) * _headroom()], "fixedrange": True},
        xaxis={"showgrid": False, "automargin": True, "fixedrange": True, "tickangle": 0},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    fig = _fig(data, layout)
    _note_unit(fig)
    return fig


def _scurve(projects: list[Project], nums: dict[int, Numbers]) -> dict:
    """Curva S: realizado acumulado mês a mês por projeto (linha cheia) e a continuação com a projeção (tracejada)."""
    from app.services.painel_figures import LEGEND

    palette = ("#4472c4", "#8a6bbf", "#4fa894", "#c8ad5e", "#e24b4a", "#3a64b4")
    data, span = [], []
    for i, p in enumerate(projects):
        n = nums[p.id]
        years = sorted(set(n.actual) | set(n.projection))
        points = []
        for y in years:
            for m in range(12):
                a = n.actual.get(y, [ZERO] * 12)[m]
                pr = n.projection.get(y, [ZERO] * 12)[m]
                points.append((date(y, m + 1, 1), a, pr))
        while points and not (points[0][1] or points[0][2]):
            points.pop(0)
        while points and not (points[-1][1] or points[-1][2]):
            points.pop()
        if not points:
            continue
        color = palette[i % len(palette)]
        total, xs_a, ys_a, xs_p, ys_p = ZERO, [], [], [], []
        last_actual = max((k for k, pt in enumerate(points) if pt[1]), default=-1)
        for k, (d, a, pr) in enumerate(points):
            total += a + pr
            if k <= last_actual:
                xs_a.append(d.isoformat())
                ys_a.append(float(total))
            if k >= last_actual:
                xs_p.append(d.isoformat())
                ys_p.append(float(total))
            span.append(d)
        tip = "%{customdata}: <b>R$ %{y:,.2f}</b><extra>" + _label(p) + "</extra>"
        data.append(
            go.Scatter(
                x=xs_a,
                y=ys_a,
                mode="lines+markers",
                name=_label(p),
                line={"color": color, "width": 2.5},
                marker={"size": 6},
                customdata=[_month_label(date.fromisoformat(x)) for x in xs_a],
                hovertemplate=tip,
            )
        )
        if len(xs_p) > 1:
            data.append(
                go.Scatter(
                    x=xs_p,
                    y=ys_p,
                    mode="lines",
                    name=f"{p.code} · com projeção",
                    line={"color": color, "width": 2, "dash": "dash"},
                    customdata=[_month_label(date.fromisoformat(x)) for x in xs_p],
                    hovertemplate=tip,
                    showlegend=False,
                )
            )
    layout = _layout(
        showlegend=True,
        legend=LEGEND,
        hovermode="closest",
        xaxis={
            "type": "date",
            "showgrid": False,
            "automargin": True,
            **(_month_ticks(min(span), max(span)) if span else {}),
        },
        yaxis={"gridcolor": "rgba(128,128,128,0.18)", "automargin": True, "tickformat": ",.0f"},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    return _fig(data, layout)


def _next_milestone(p: Project) -> dict | None:
    pending = [m for m in p.milestones if not m.done_date and (m.progress_pct or 0) < 100]
    pending.sort(key=lambda m: (m.due_date or date.max, m.id))
    if not pending:
        return None
    m = pending[0]
    return {"title": m.title, "due_date": m.due_date, "progress_pct": m.progress_pct or 0}


def portfolio(db: Session, user: User, base_fn, can_view_fn, today: date | None = None) -> dict:
    """`base_fn`/`can_view_fn` vêm de `services.projects` (evita import circular)."""
    today = today or date.today()
    projects = [p for p in db.scalars(select(Project).order_by(Project.code)) if can_view_fn(db, user, p)]
    nums = {p.id: numbers(db, p) for p in projects}
    items = []
    for p in projects:
        n = nums[p.id]
        s = summary(db, p, n, today)
        monthly = [float(a) for a in n.actual.get(n.ref_year, [ZERO] * 12)]
        projected = [float(a) for a in n.projection.get(n.ref_year, [ZERO] * 12)]
        items.append(
            base_fn(db, user, p)
            | {
                "summary": s,
                "next_milestone": _next_milestone(p),
                "milestones": len(p.milestones),
                "late_milestones": sum(
                    1
                    for m in p.milestones
                    if m.due_date and m.due_date < today and not m.done_date and m.progress_pct < 100
                ),
                "open_high_risks": sum(1 for r in p.risks if r.status == "OPEN" and r.impact == "HIGH"),
                "monthly": monthly,
                "monthly_projection": projected,
            }
        )
    out: dict = {"items": items, "can_create": is_global(user), "kpis": None, "figures": None}
    if not projects:
        return out
    any_n = next(iter(nums.values()))

    def total(key: str) -> str:
        return str(sum((Decimal(i["summary"][key]) for i in items), ZERO))

    out["kpis"] = {
        "ref_year": any_n.ref_year,
        "target_year": any_n.target_year,
        "active": sum(1 for p in projects if p.status == "IN_PROGRESS"),
        "count": len(projects),
        "actual_all": total("actual_all"),
        "actual_year": total("actual_year"),
        "projection_year": total("projection_year"),
        "budget_year": total("budget_year"),
        "budget_all": total("budget_all"),
        "budget_projects": sum(1 for i in items if i["summary"]["budget_scope"] == "project"),
        "cycle_budget": total("cycle_budget"),
        "late_milestones": sum(i["late_milestones"] for i in items),
        "open_high_risks": sum(i["open_high_risks"] for i in items),
        "warnings": sum(len(i["summary"]["warnings"]) for i in items),
    }
    out["figures"] = {
        "gantt": _gantt(projects, nums, today),
        "compare": _compare(projects, nums),
        "scurve": _scurve(projects, nums),
    }
    out["months"] = list(MONTHS_SHORT)
    return out


def chart(db: Session, user: User, can_view_fn, project_id: int | None, year: int | None, view: str) -> dict:
    """Gráfico de colunas da carteira com o filtro de projeto: um projeto ou a soma de todos os visíveis."""
    projects = [p for p in db.scalars(select(Project).order_by(Project.code)) if can_view_fn(db, user, p)]
    if project_id:
        projects = [p for p in projects if p.id == project_id]
    if not projects:
        return {"figure": None, "years": [], "year": year}
    n = merge([numbers(db, p) for p in projects])
    year = year or n.ref_year
    return {"figure": figure_from(n, year, view), "years": chart_years(n), "year": year, "ref_year": n.ref_year}
