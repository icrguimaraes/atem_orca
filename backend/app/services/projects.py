"""Projetos (14/10/2026): cadastro completo de cada projeto e os números dele **lidos do sistema** — nada é digitado
nem importado à parte, e o orçamento nunca é alterado ("não altere em nada o orçamento, já foi validado pela área").

- Realizado, projeção do gestor e orçado de referência (ex.: Orçado 2026): lançamentos das versões vigentes nos
  vínculos centro de custo × conta do projeto (`ProjectFactKey`; ex.: TDC = VP de Controladoria × 6090201002
  Intangível em andamento — o realizado bate com a planilha "Capex Acum" da área).
- Orçamento do ciclo (ex.: 2027): solicitações de CAPEX vinculadas (`ProjectCapexLink`, CC + "CPX-001") na versão
  vigente; valor sem cronograma mensal entra no total do ano.

Acesso: Controladoria/Administrador (todos; só eles criam e excluem) e o responsável pelo projeto ou o gestor do CC
responsável (veem e editam o próprio). Toda mutação grava auditoria (`entity_type="project"`)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

import plotly.graph_objects as go
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import is_global
from app.models import (
    Account,
    ActualEntry,
    BudgetSubmission,
    CapexProject,
    CostCenter,
    DatasetVersion,
    Project,
    ProjectCapexLink,
    ProjectFactKey,
    ProjectLink,
    ProjectMilestone,
    ProjectRisk,
    ProjectUpdate,
    ReferenceBudgetEntry,
    User,
)
from app.models.project import PROJECT_STATUS, RISK_LEVELS, RISK_STATUS
from app.services import audit
from app.services.analytics import _fig, _layout
from app.services.analytics_base import fmt_money
from app.services.opex import OpexError, context

ENTITY = "project"
ZERO = Decimal("0")
STATUS_LABELS = {
    "PLANNED": "Planejado",
    "IN_PROGRESS": "Em andamento",
    "PAUSED": "Pausado",
    "DONE": "Concluído",
    "CANCELLED": "Cancelado",
}
LEVEL_LABELS = {"LOW": "Baixo", "MEDIUM": "Médio", "HIGH": "Alto"}
RISK_STATUS_LABELS = {"OPEN": "Aberto", "MITIGATED": "Mitigado", "CLOSED": "Encerrado"}
FIELDS = (
    "code",
    "name",
    "status",
    "cost_center_id",
    "owner_user_id",
    "owner_name",
    "sponsor",
    "start_planned",
    "end_planned",
    "start_actual",
    "end_actual",
    "objective",
    "scope",
    "benefits",
    "notes",
)
# filhos editáveis na página: modelo + campos aceitos
CHILDREN: dict[str, tuple[type, tuple[str, ...]]] = {
    "milestones": (ProjectMilestone, ("title", "due_date", "done_date", "progress_pct", "owner_name", "notes")),
    "risks": (ProjectRisk, ("description", "impact", "probability", "mitigation", "status")),
    "links": (ProjectLink, ("title", "url")),
    "updates": (ProjectUpdate, ("text",)),
}
CHILD_LABELS = {"milestones": "marco", "risks": "risco", "links": "link", "updates": "atualização"}


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class Invalid(Exception):
    pass


# ---------------------------------------------------------------- acesso


def can_view(db: Session, user: User, p: Project) -> bool:
    if is_global(user) or p.owner_user_id == user.id:
        return True
    cc = db.get(CostCenter, p.cost_center_id) if p.cost_center_id else None
    return bool(cc and cc.manager_user_id == user.id)


def _get(db: Session, user: User, project_id: int) -> Project:
    p = db.get(Project, project_id)
    if p is None:
        raise NotFound("Projeto não encontrado")
    if not can_view(db, user, p):
        raise Forbidden("Sem acesso a este projeto")
    return p


def _require_global(user: User) -> None:
    if not is_global(user):
        raise Forbidden("Só a Controladoria cria ou exclui projetos")


# ---------------------------------------------------------------- números (lidos do sistema)


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
    from sqlalchemy import and_, or_

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


def figure(db: Session, p: Project, year: int | None = None) -> dict:
    """Colunas mensais como no Painel: realizado (azul, com a projeção em vermelho empilhada), orçado (verde)."""
    from app.services.painel_figures import INK, LEGEND, MONTHS, ORCADO, PROJETADO, REALIZADO, _headroom, _note_unit
    from app.services.painel_figures import _short as short_label

    n = numbers(db, p)
    year = year or n.ref_year
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
    series = [s for s in series if any(s[3])]
    data, everything = [], []
    for key, name, color, values, base in series:
        everything += [v + (base[i] if base else 0) for i, v in enumerate(values)]
        data.append(
            go.Bar(
                x=list(MONTHS),
                y=values,
                name=name,
                offsetgroup="ref" if key == "proj" else key,
                base=base,
                marker={"color": color, "cornerradius": 4},
                text=[short_label(v) if v > 0 else "" for v in values],
                textposition="outside",
                textangle=-90,
                constraintext="none",
                cliponaxis=False,
                textfont={"size": 11, "color": INK.get(color, color)},
                customdata=[fmt_money(v) for v in values],
                hovertemplate=name + ": <b>%{customdata}</b><extra></extra>",
            )
        )
    layout = _layout(
        barmode="group",
        bargap=0.16,
        bargroupgap=0.04,
        hovermode="x unified",
        showlegend=True,
        legend=LEGEND,
        yaxis={"visible": False, "range": [0, (max(everything + [0.0]) or 1) * _headroom()], "fixedrange": True},
        xaxis={"showgrid": False, "automargin": True, "fixedrange": True},
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


# ---------------------------------------------------------------- leitura


def _cc_label(cc: CostCenter | None) -> str | None:
    return f"{cc.code} · {cc.name}" if cc else None


def _user_name(db: Session, user_id: int | None) -> str | None:
    u = db.get(User, user_id) if user_id else None
    return u.name if u else None


def _base(db: Session, user: User, p: Project) -> dict:
    cc = db.get(CostCenter, p.cost_center_id) if p.cost_center_id else None
    return {
        "id": p.id,
        "code": p.code,
        "name": p.name,
        "status": p.status,
        "status_label": STATUS_LABELS.get(p.status, p.status),
        "cost_center_id": p.cost_center_id,
        "cost_center": _cc_label(cc),
        "owner_user_id": p.owner_user_id,
        "owner_name": p.owner_name,
        "owner": p.owner_name or _user_name(db, p.owner_user_id),
        "sponsor": p.sponsor,
        "start_planned": p.start_planned,
        "end_planned": p.end_planned,
        "start_actual": p.start_actual,
        "end_actual": p.end_actual,
        "can_edit": True,
        "can_delete": is_global(user),
    }


def list_projects(db: Session, user: User) -> dict:
    items = []
    for p in db.scalars(select(Project).order_by(Project.code)):
        if can_view(db, user, p):
            items.append(_base(db, user, p) | {"summary": summary(db, p)})
    return {"items": items, "can_create": is_global(user)}


def detail(db: Session, user: User, project_id: int) -> dict:
    p = _get(db, user, project_id)
    n = numbers(db, p)
    keys = []
    for k in p.fact_keys:
        cc, acc = db.get(CostCenter, k.cost_center_id), db.get(Account, k.account_id)
        keys.append(
            {
                "id": k.id,
                "cost_center_id": k.cost_center_id,
                "account_id": k.account_id,
                "cost_center": _cc_label(cc),
                "account": f"{acc.code} · {acc.name}" if acc else None,
            }
        )
    links = [
        {"id": c.id, "cost_center_id": c.cost_center_id, "capex_code": c.capex_code} | _capex_info(n, c)
        for c in p.capex_links
    ]
    years = sorted(set(n.actual) | set(n.projection) | set(n.ref_budget) | {n.ref_year})
    return _base(db, user, p) | {
        "objective": p.objective,
        "scope": p.scope,
        "benefits": p.benefits,
        "notes": p.notes,
        "fact_keys": keys,
        "capex_links": links,
        "summary": summary(db, p, n),
        "by_year": [
            {
                "year": y,
                "actual": str(_sum(n.actual.get(y))),
                "projection": str(_sum(n.projection.get(y))),
                "budget": str(_sum(n.ref_budget.get(y))),
            }
            for y in years
        ]
        + [
            {
                "year": n.target_year,
                "actual": "0",
                "projection": "0",
                "budget": str(_sum(n.cycle_budget) + n.cycle_unscheduled),
            }
        ],
        "figure_years": [y for y in years if y != n.target_year] + [n.target_year],
        "milestones": [
            {
                "id": m.id,
                "title": m.title,
                "due_date": m.due_date,
                "done_date": m.done_date,
                "progress_pct": m.progress_pct,
                "owner_name": m.owner_name,
                "notes": m.notes,
                "late": bool(m.due_date and m.due_date < date.today() and not m.done_date and m.progress_pct < 100),
            }
            for m in sorted(p.milestones, key=lambda m: (m.due_date or date.max, m.id))
        ],
        "risks": [
            {
                "id": r.id,
                "description": r.description,
                "impact": r.impact,
                "impact_label": LEVEL_LABELS.get(r.impact),
                "probability": r.probability,
                "probability_label": LEVEL_LABELS.get(r.probability),
                "mitigation": r.mitigation,
                "status": r.status,
                "status_label": RISK_STATUS_LABELS.get(r.status),
            }
            for r in p.risks
        ],
        "links": [{"id": x.id, "title": x.title, "url": x.url} for x in p.links],
        "updates": [
            {
                "id": u.id,
                "text": u.text,
                "author": _user_name(db, u.user_id),
                "created_at": u.created_at,
                "can_delete": is_global(user) or u.user_id == user.id,
            }
            for u in sorted(p.updates, key=lambda u: u.id, reverse=True)
        ],
        "documents": documents(db, p),
    }


def _capex_info(n: Numbers, link: ProjectCapexLink) -> dict:
    match = next(
        (f for f in n.capex_found if f["cost_center_id"] == link.cost_center_id and f["capex_code"] == link.capex_code),
        None,
    )
    cc = next(
        (f["cost_center"] for f in n.capex_found + n.capex_missing if f["cost_center_id"] == link.cost_center_id), None
    )
    base = {"cost_center": cc}
    return base | ({"title": match["title"], "total": match["total"], "found": True} if match else {"found": False})


def options(db: Session, user: User) -> dict:
    """Listas do formulário: CCs, contas com realizado de CAPEX, solicitações de CAPEX da versão vigente e usuários."""
    ccs = db.scalars(select(CostCenter).where(CostCenter.is_active).order_by(CostCenter.code))
    accounts = db.scalars(select(Account).where(Account.nature == "CAPEX").order_by(Account.code))
    requests = []
    try:
        ctx = context(db)
        rows = db.execute(
            select(CapexProject, CostCenter)
            .join(BudgetSubmission, BudgetSubmission.id == CapexProject.submission_id)
            .join(CostCenter, CostCenter.id == BudgetSubmission.cost_center_id)
            .where(BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.module == "CAPEX")
            .order_by(CostCenter.code, CapexProject.code)
        )
        for req, cc in rows:
            requests.append(
                {
                    "cost_center_id": cc.id,
                    "capex_code": req.code,
                    "label": f"{cc.code} · {req.code} · {req.title}",
                    "is_project": req.is_project,
                    "total": str(sum((i.total_value or ZERO for i in req.items), ZERO)),
                }
            )
    except OpexError:
        pass
    users = db.scalars(select(User).where(User.is_active).order_by(User.name)) if is_global(user) else []
    return {
        "cost_centers": [{"id": c.id, "label": f"{c.code} · {c.name} ({c.company.code})"} for c in ccs],
        "accounts": [{"id": a.id, "label": f"{a.code} · {a.name}"} for a in accounts],
        "capex_requests": requests,
        "users": [{"id": u.id, "label": u.name} for u in users],
        "status": [{"value": k, "label": v} for k, v in STATUS_LABELS.items()],
    }


# ---------------------------------------------------------------- escrita


def _snapshot(p: Project) -> dict[str, Any]:
    out = {f: getattr(p, f) for f in FIELDS}
    out["fact_keys"] = sorted([k.cost_center_id, k.account_id] for k in p.fact_keys)
    out["capex_links"] = sorted([c.cost_center_id, c.capex_code] for c in p.capex_links)
    return out


def _apply(db: Session, p: Project, data: dict, user: User) -> None:
    if "status" in data and data["status"] not in PROJECT_STATUS:
        raise Invalid("Situação inválida")
    for f in FIELDS:
        if f in data:
            value = data[f]
            if isinstance(value, str):
                value = value.strip() or None
            if f in ("code", "name") and not value:
                raise Invalid("Código e nome são obrigatórios")
            if f == "owner_user_id" and value is not None and not is_global(user) and value != p.owner_user_id:
                raise Forbidden("Só a Controladoria troca o responsável")
            setattr(p, f, value)
    if p.code:
        clash = db.scalar(select(Project.id).where(Project.code == p.code, Project.id != (p.id or 0)))
        if clash:
            raise Invalid(f"Já existe um projeto com o código {p.code}")
    if "fact_keys" in data and data["fact_keys"] is not None:
        wanted = {(int(k["cost_center_id"]), int(k["account_id"])) for k in data["fact_keys"]}
        p.fact_keys = [k for k in p.fact_keys if (k.cost_center_id, k.account_id) in wanted]
        have = {(k.cost_center_id, k.account_id) for k in p.fact_keys}
        for cc_id, acc_id in sorted(wanted - have):
            if db.get(CostCenter, cc_id) is None or db.get(Account, acc_id) is None:
                raise Invalid("Centro de custo ou conta inexistente no vínculo")
            p.fact_keys.append(ProjectFactKey(cost_center_id=cc_id, account_id=acc_id))
    if "capex_links" in data and data["capex_links"] is not None:
        wanted = {(int(c["cost_center_id"]), str(c["capex_code"]).strip().upper()) for c in data["capex_links"]}
        p.capex_links = [c for c in p.capex_links if (c.cost_center_id, c.capex_code) in wanted]
        have = {(c.cost_center_id, c.capex_code) for c in p.capex_links}
        for cc_id, code in sorted(wanted - have):
            if db.get(CostCenter, cc_id) is None or not code:
                raise Invalid("Solicitação de CAPEX inválida no vínculo")
            p.capex_links.append(ProjectCapexLink(cost_center_id=cc_id, capex_code=code))


def create(db: Session, user: User, data: dict, ip: str | None = None) -> dict:
    _require_global(user)
    p = Project(status="IN_PROGRESS", created_by=user.id, updated_by=user.id)
    _apply(db, p, data, user)
    if not p.code or not p.name:
        raise Invalid("Código e nome são obrigatórios")
    db.add(p)
    db.flush()
    audit.record(db, user_id=user.id, action="CREATE", entity_type=ENTITY, entity_id=p.id, after=_snapshot(p), ip=ip)
    db.commit()
    return detail(db, user, p.id)


def update(db: Session, user: User, project_id: int, data: dict, ip: str | None = None) -> dict:
    p = _get(db, user, project_id)
    before = _snapshot(p)
    _apply(db, p, data, user)
    p.updated_by = user.id
    db.flush()
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=p.id,
        before=before,
        after=_snapshot(p),
        ip=ip,
    )
    db.commit()
    return detail(db, user, p.id)


def delete(db: Session, user: User, project_id: int, ip: str | None = None) -> None:
    _require_global(user)
    p = _get(db, user, project_id)
    audit.record(db, user_id=user.id, action="DELETE", entity_type=ENTITY, entity_id=p.id, before=_snapshot(p), ip=ip)
    db.delete(p)
    db.commit()


def _child_values(kind: str, data: dict, partial: bool) -> dict:
    model, fields = CHILDREN[kind]
    values = {}
    for f in fields:
        if f not in data:
            continue
        v = data[f]
        if isinstance(v, str):
            v = v.strip() or None
        values[f] = v
    required = {"milestones": "title", "risks": "description", "links": "title", "updates": "text"}[kind]
    if (not partial or required in values) and not values.get(required):
        raise Invalid("Preencha o campo principal")
    if kind == "links" and (not partial or "url" in values):
        url = values.get("url") or ""
        if not url.lower().startswith(("http://", "https://")):
            raise Invalid("O link precisa começar com http:// ou https://")
    if kind == "milestones" and "progress_pct" in values:
        values["progress_pct"] = max(0, min(100, int(values["progress_pct"] or 0)))
    if kind == "risks":
        for f, allowed in (("impact", RISK_LEVELS), ("probability", RISK_LEVELS), ("status", RISK_STATUS)):
            if f in values and values[f] not in allowed:
                raise Invalid("Valor inválido no risco")
    return values


def _child_dict(obj: Any, kind: str) -> dict:
    return {f: getattr(obj, f) for f in CHILDREN[kind][1]}


def add_child(db: Session, user: User, project_id: int, kind: str, data: dict, ip: str | None = None) -> dict:
    p = _get(db, user, project_id)
    model, _ = CHILDREN[kind]
    values = _child_values(kind, data, partial=False)
    if kind == "milestones":
        values.setdefault("progress_pct", 0)
    if kind == "updates":
        values["user_id"] = user.id
    obj = model(project_id=p.id, **values)
    db.add(obj)
    db.flush()
    audit.record(
        db,
        user_id=user.id,
        action="CREATE",
        entity_type=ENTITY,
        entity_id=p.id,
        after={"item": CHILD_LABELS[kind], "id": obj.id} | _child_dict(obj, kind),
        ip=ip,
    )
    db.commit()
    db.refresh(p)
    return detail(db, user, p.id)


def _child(db: Session, p: Project, kind: str, child_id: int) -> Any:
    model, _ = CHILDREN[kind]
    obj = db.get(model, child_id)
    if obj is None or obj.project_id != p.id:
        raise NotFound("Item não encontrado")
    return obj


def update_child(
    db: Session, user: User, project_id: int, kind: str, child_id: int, data: dict, ip: str | None = None
) -> dict:
    if kind == "updates":
        raise Invalid("Atualizações do diário não são editadas; exclua e escreva de novo")
    p = _get(db, user, project_id)
    obj = _child(db, p, kind, child_id)
    before = _child_dict(obj, kind)
    for f, v in _child_values(kind, data, partial=True).items():
        setattr(obj, f, v)
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=p.id,
        before={"item": CHILD_LABELS[kind], "id": obj.id} | before,
        after={"item": CHILD_LABELS[kind], "id": obj.id} | _child_dict(obj, kind),
        ip=ip,
    )
    db.commit()
    db.refresh(p)
    return detail(db, user, p.id)


def delete_child(db: Session, user: User, project_id: int, kind: str, child_id: int, ip: str | None = None) -> dict:
    p = _get(db, user, project_id)
    obj = _child(db, p, kind, child_id)
    if kind == "updates" and not (is_global(user) or obj.user_id == user.id):
        raise Forbidden("Só quem escreveu (ou a Controladoria) exclui a atualização")
    audit.record(
        db,
        user_id=user.id,
        action="DELETE",
        entity_type=ENTITY,
        entity_id=p.id,
        before={"item": CHILD_LABELS[kind], "id": obj.id} | _child_dict(obj, kind),
        ip=ip,
    )
    db.delete(obj)
    db.commit()
    db.refresh(p)
    return detail(db, user, p.id)
