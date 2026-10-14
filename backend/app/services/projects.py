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

from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import is_global
from app.models import (
    Account,
    BudgetSubmission,
    CapexProject,
    CostCenter,
    Project,
    ProjectCapexLink,
    ProjectFactKey,
    ProjectLink,
    ProjectMilestone,
    ProjectRisk,
    ProjectUpdate,
    User,
)
from app.models.project import PROJECT_STATUS, RISK_LEVELS, RISK_STATUS
from app.services import audit
from app.services.opex import OpexError, context
from app.services.project_numbers import (  # noqa: F401  (figure é usada pela rota)
    STATUS_LABELS,
    ZERO,
    Numbers,
    _cc_label,
    _sum,
    documents,
    figure,
    numbers,
    summary,
)

ENTITY = "project"
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


# ---------------------------------------------------------------- leitura


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
