"""Números de um projeto, lidos do que já está no sistema (o orçamento nunca é alterado aqui): realizado, projeção do
gestor e orçado de referência pelos vínculos centro de custo × conta; orçamento do ciclo pelas solicitações de CAPEX
vinculadas (versão vigente). Também o farol (% gasto × % do prazo), os apontamentos, a figura e os lançamentos."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import plotly.graph_objects as go
from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    ActualEntry,
    BudgetSubmission,
    CapexProject,
    CostCenter,
    DatasetVersion,
    Project,
    ProjectFactKey,
    ReferenceBudgetEntry,
)
from app.services.analytics import _fig, _layout
from app.services.analytics_base import fmt_money
from app.services.opex import OpexError, context

ZERO = Decimal("0")

STATUS_LABELS = {
    "PLANNED": "Planejado",
    "IN_PROGRESS": "Em andamento",
    "PAUSED": "Pausado",
    "DONE": "Concluído",
    "CANCELLED": "Cancelado",
}


def _cc_label(cc: CostCenter | None) -> str | None:
    return f"{cc.code} · {cc.name}" if cc else None


@dataclass
class Numbers:
    ref_year: int
    target_year: int
    last_closed: int | None
    actual: dict[int, list[Decimal]]  # ano → 12 meses (sem a projeção)
    projection: dict[int, list[Decimal]]  # ano → 12 meses (projeção do gestor)
    ref_budget: dict[int, list[Decimal]]  # ano → 12 meses (orçado de referência carregado)
    cycle_budget: list[Decimal]  # 12 meses do orçamento do ciclo (CAPEX vinculado)
    cycle_unscheduled: Decimal  # CAPEX vinculado sem cronograma mensal
    capex_found: list[dict]
    capex_missing: list[dict]


def _months() -> list[Decimal]:
    return [ZERO] * 12


def _key_filter(model, keys: list[ProjectFactKey]):
    return or_(*(and_(model.cost_center_id == k.cost_center_id, model.account_id == k.account_id) for k in keys))


def _last_closed(db: Session, year: int) -> int | None:
    return db.scalar(
        select(func.max(DatasetVersion.last_closed_period)).where(
            DatasetVersion.dataset_type == "ACTUAL",
            DatasetVersion.is_current,
            DatasetVersion.scope_key.like(f"ACTUAL:{year}:%"),
        )
    )


def numbers(db: Session, p: Project) -> Numbers:
    try:
        ctx = context(db)
        ref_year, target_year, version_id = ctx.ref_year, ctx.target_year, ctx.version.id
    except OpexError:
        ref_year, target_year, version_id = date.today().year, date.today().year + 1, None
    actual: dict[int, list[Decimal]] = {}
    projection: dict[int, list[Decimal]] = {}
    ref_budget: dict[int, list[Decimal]] = {}
    keys = list(p.fact_keys)
    if keys:
        rows = db.execute(
            select(ActualEntry.fiscal_year, ActualEntry.period, ActualEntry.projected, func.sum(ActualEntry.amount))
            .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
            .where(DatasetVersion.is_current, _key_filter(ActualEntry, keys))
            .group_by(ActualEntry.fiscal_year, ActualEntry.period, ActualEntry.projected)
        )
        for year, period, projected, amount in rows:
            target = projection if projected else actual
            target.setdefault(int(year), _months())[int(period) - 1] += amount or ZERO
        rows = db.execute(
            select(ReferenceBudgetEntry.fiscal_year, ReferenceBudgetEntry.period, func.sum(ReferenceBudgetEntry.amount))
            .join(DatasetVersion, DatasetVersion.id == ReferenceBudgetEntry.dataset_version_id)
            .where(DatasetVersion.is_current, _key_filter(ReferenceBudgetEntry, keys))
            .group_by(ReferenceBudgetEntry.fiscal_year, ReferenceBudgetEntry.period)
        )
        for year, period, amount in rows:
            ref_budget.setdefault(int(year), _months())[int(period) - 1] += amount or ZERO
    cycle_budget, unscheduled = _months(), ZERO
    found, missing = [], []
    for link in p.capex_links:
        req = None
        if version_id is not None:
            req = db.scalar(
                select(CapexProject)
                .join(BudgetSubmission, BudgetSubmission.id == CapexProject.submission_id)
                .where(
                    BudgetSubmission.version_id == version_id,
                    BudgetSubmission.module == "CAPEX",
                    BudgetSubmission.cost_center_id == link.cost_center_id,
                    CapexProject.code == link.capex_code,
                )
            )
        cc = db.get(CostCenter, link.cost_center_id)
        info = {"cost_center_id": link.cost_center_id, "cost_center": _cc_label(cc), "capex_code": link.capex_code}
        if req is None:
            missing.append(info)
            continue
        total = ZERO
        for item in req.items:
            scheduled = ZERO
            for v in item.values:
                if 1 <= v.month <= 12:
                    cycle_budget[v.month - 1] += v.amount or ZERO
                    scheduled += v.amount or ZERO
            unscheduled += max(ZERO, (item.total_value or ZERO) - scheduled)
            total += item.total_value or ZERO
        found.append(info | {"title": req.title, "total": str(total), "submission_id": req.submission_id})
    return Numbers(
        ref_year,
        target_year,
        _last_closed(db, ref_year),
        actual,
        projection,
        ref_budget,
        cycle_budget,
        unscheduled,
        found,
        missing,
    )


def _sum(values: list[Decimal] | None) -> Decimal:
    return sum(values or [], ZERO)


def _time_pct(p: Project, today: date) -> float | None:
    start, end = p.start_actual or p.start_planned, p.end_planned
    if not start or not end or end <= start:
        return None
    return max(0.0, min(1.0, (today - start).days / (end - start).days))


def summary(db: Session, p: Project, n: Numbers | None = None, today: date | None = None) -> dict:
    """Totais do ano de referência, do ciclo e do acumulado + farol (% gasto × % do prazo) e apontamentos."""
    today = today or date.today()
    n = n or numbers(db, p)
    y = n.ref_year
    actual_y, proj_y, budget_y = _sum(n.actual.get(y)), _sum(n.projection.get(y)), _sum(n.ref_budget.get(y))
    actual_all = sum((_sum(v) for v in n.actual.values()), ZERO)
    cycle_total = _sum(n.cycle_budget) + n.cycle_unscheduled
    spent = float(actual_y / budget_y) if budget_y > 0 else None
    time_pct = _time_pct(p, today)
    if p.status in ("DONE", "CANCELLED"):
        light = ("closed", STATUS_LABELS[p.status])
    elif spent is None:
        light = ("none", f"Sem orçado {y} no sistema" if actual_y else "Sem números ainda")
    elif spent > 1:
        light = ("bad", "Realizado acima do orçado")
    elif time_pct is not None and spent > time_pct + 0.10:
        light = ("warn", "Gasto à frente do prazo")
    elif time_pct is None and spent > 0.9:
        light = ("warn", "Mais de 90% do orçado já realizado")
    else:
        light = ("good", "Dentro do orçado")
    warnings = []
    if not p.fact_keys:
        warnings.append("Sem vínculo de centro de custo × conta: o realizado do projeto não aparece.")
    if actual_y and not budget_y:
        warnings.append(f"Há realizado {y} ({fmt_money(actual_y)}), mas não há orçado {y} carregado no sistema.")
    if budget_y and actual_y > budget_y:
        warnings.append(f"Realizado {y} acima do orçado em {fmt_money(actual_y - budget_y)}.")
    for m in n.capex_missing:
        warnings.append(f"Solicitação {m['capex_code']} ({m['cost_center']}) não existe na versão vigente do CAPEX.")
    late = [m for m in p.milestones if m.due_date and m.due_date < today and not m.done_date and m.progress_pct < 100]
    if late:
        warnings.append(f"{len(late)} marco(s) atrasado(s).")
    if p.end_planned and p.end_planned < today and p.status not in ("DONE", "CANCELLED"):
        warnings.append("Data de término prevista já passou e o projeto não está concluído.")
    high = [r for r in p.risks if r.status == "OPEN" and r.impact == "HIGH"]
    if high:
        warnings.append(f"{len(high)} risco(s) de impacto alto em aberto.")
    done = [m.progress_pct or 0 for m in p.milestones]
    return {
        "ref_year": y,
        "target_year": n.target_year,
        "last_closed": n.last_closed,
        "actual_year": str(actual_y),
        "projection_year": str(proj_y),
        "forecast_year": str(actual_y + proj_y),
        "budget_year": str(budget_y),
        "available_year": str(budget_y - actual_y - proj_y) if budget_y else None,
        "spent_pct": spent,
        "time_pct": time_pct,
        "actual_all": str(actual_all),
        "actual_years": sorted(n.actual),
        "cycle_budget": str(cycle_total),
        "cycle_unscheduled": str(n.cycle_unscheduled),
        "capex": n.capex_found,
        "progress_pct": round(sum(done) / len(done)) if done else None,
        "light": light[0],
        "light_label": light[1],
        "warnings": warnings,
    }


def merge(items: list[Numbers]) -> Numbers:
    """Soma os números de vários projetos (gráfico "Todos os projetos" da carteira)."""
    first = items[0]
    out = Numbers(first.ref_year, first.target_year, first.last_closed, {}, {}, {}, _months(), ZERO, [], [])
    for n in items:
        for src, dst in ((n.actual, out.actual), (n.projection, out.projection), (n.ref_budget, out.ref_budget)):
            for year, values in src.items():
                acc = dst.setdefault(year, _months())
                for i, v in enumerate(values):
                    acc[i] += v
        out.cycle_budget = [a + b for a, b in zip(out.cycle_budget, n.cycle_budget, strict=True)]
        out.cycle_unscheduled += n.cycle_unscheduled
    return out


def chart_years(n: Numbers) -> list[int]:
    return sorted(set(n.actual) | set(n.projection) | set(n.ref_budget) | {n.ref_year, n.target_year})


def figure(db: Session, p: Project, year: int | None = None) -> dict:
    return figure_from(numbers(db, p), year)


def figure_from(n: Numbers, year: int | None = None, view: str = "month") -> dict:
    """Colunas como no Painel: realizado (azul, com a projeção em vermelho empilhada) × orçado (verde). `view="month"`:
    os 12 meses do ano (no ano do ciclo, o orçamento do CAPEX vinculado); `view="year"`: um grupo por ano, com o
    orçado de referência nos anos carregados e o orçamento do ciclo (inclusive o sem cronograma) no ano do ciclo."""
    from app.services.painel_figures import INK, LEGEND, MONTHS, ORCADO, PROJETADO, REALIZADO, _headroom, _note_unit
    from app.services.painel_figures import _short as short_label

    if view == "year":
        years = chart_years(n)
        x = [str(y) for y in years]
        actual = [float(_sum(n.actual.get(y))) for y in years]
        proj = [float(_sum(n.projection.get(y))) for y in years]
        budget = [
            float(_sum(n.cycle_budget) + n.cycle_unscheduled if y == n.target_year else _sum(n.ref_budget.get(y)))
            for y in years
        ]
        series = [
            ("ref", "Realizado", REALIZADO, actual, None),
            ("proj", "Projeção do gestor", PROJETADO, proj, actual),
            ("budget", "Orçado / orçamento", ORCADO, budget, None),
        ]
        angle = 0
    else:
        year = year or n.ref_year
        x = list(MONTHS)
        if year == n.target_year:
            series = [("budget", f"Orçamento {year}", ORCADO, [float(v) for v in n.cycle_budget], None)]
        else:
            actual = [float(v) for v in n.actual.get(year, _months())]
            proj = [float(v) for v in n.projection.get(year, _months())]
            series = [
                ("ref", f"Realizado {year}", REALIZADO, actual, None),
                ("proj", "Projeção do gestor", PROJETADO, proj, actual),
                ("budget", f"Orçado {year}", ORCADO, [float(v) for v in n.ref_budget.get(year, _months())], None),
            ]
        angle = -90
    series = [s for s in series if any(s[3])]
    data, everything = [], []
    stacked = next((s[3] for s in series if s[0] == "proj"), None)
    for key, name, color, values, base in series:
        everything += [v + (base[i] if base else 0) for i, v in enumerate(values)]
        # realizado com projeção empilhada por cima: o rótulo vai para dentro da coluna (fora, a projeção o cobriria)
        inside = [key == "ref" and bool(stacked and stacked[i]) for i in range(len(values))]
        ink = INK.get(color, color)
        data.append(
            go.Bar(
                x=x,
                y=values,
                name=name,
                offsetgroup="ref" if key == "proj" else key,
                base=base,
                marker={"color": color, "cornerradius": 4},
                text=[short_label(v) if v > 0 else "" for v in values],
                textposition=["inside" if i else "outside" for i in inside],
                insidetextanchor="end",
                textangle=angle,
                constraintext="none",
                cliponaxis=False,
                textfont={"size": 11, "color": ["#ffffff" if i else ink for i in inside]},
                customdata=[fmt_money(v) for v in values],
                hovertemplate=name + ": <b>%{customdata}</b><extra></extra>",
                **({"width": 0.22} if view == "year" else {}),  # por ano: colunas estreitas, como no mês
            )
        )
    layout = _layout(
        barmode="group",
        bargap=0.16 if view != "year" else 0.35,
        bargroupgap=0.04,
        hovermode="x unified",
        showlegend=True,
        legend=LEGEND,
        yaxis={"visible": False, "range": [0, (max(everything + [0.0]) or 1) * _headroom()], "fixedrange": True},
        xaxis={"showgrid": False, "automargin": True, "fixedrange": True, "type": "category"},
        margin={"l": 8, "r": 8, "t": 36, "b": 8},
    )
    fig = _fig(data, layout)
    _note_unit(fig)
    return fig


def documents(db: Session, p: Project, limit: int = 200) -> list[dict]:
    """Lançamentos do realizado (documento a documento quando o KSB1 traz), mais recentes primeiro."""
    if not p.fact_keys:
        return []
    rows = db.scalars(
        select(ActualEntry)
        .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
        .where(DatasetVersion.is_current, ~ActualEntry.projected, _key_filter(ActualEntry, list(p.fact_keys)))
        .order_by(
            ActualEntry.fiscal_year.desc(),
            ActualEntry.period.desc(),
            ActualEntry.posting_date.desc().nulls_last(),
            ActualEntry.id.desc(),
        )
        .limit(limit)
    )
    return [
        {
            "year": r.fiscal_year,
            "period": r.period,
            "date": r.posting_date.isoformat() if r.posting_date else None,
            "document": r.document_number,
            "vendor": r.vendor_name or r.vendor_code,
            "text": r.text,
            "amount": str(r.amount),
        }
        for r in rows
    ]
