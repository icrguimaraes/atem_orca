"""Regras do módulo CAPEX: solicitações (projeto ou aquisição avulsa), itens com cronograma e pendências.

Uma solicitação (`CapexProject`) agrupa itens. Se marcada como projeto, exige tipo e justificativa
quantificada. Cada item tem valor unitário × quantidade e o cronograma mensal de desembolso, que
precisa fechar com o valor total (no Excel era só um "Check" textual; aqui bloqueia envio e aprovação).
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules import capex as rules
from app.domain.rules.common import MONTHS, money
from app.models import (
    Account,
    ActualEntry,
    AssetClass,
    AssetItem,
    Branch,
    BudgetSubmission,
    CapexItem,
    CapexItemValue,
    CapexProject,
    CostCenter,
    LookupValue,
    ReferenceBudgetEntry,
)
from app.services.opex import Context, OpexError, _current_sum, mark_in_progress

ZERO = Decimal("0")
MODULE = "CAPEX"


class CapexError(OpexError):
    """Erro de regra de negócio do CAPEX (mensagem pronta para o usuário)."""


# ------------------------------------------------------------------ validação


def _limits(ctx: Context) -> tuple[Decimal, int]:
    return ctx.param("capex.min_unit_value", 1200), int(ctx.param("capex.min_useful_life_months", 12))


def item_issues(ctx: Context, item: CapexItem) -> list[dict]:
    min_value, min_life = _limits(ctx)
    schedule = {v.month: v.amount for v in item.values}
    check = rules.check_item(
        item.unit_value,
        item.quantity,
        schedule,
        min_unit_value=min_value,
        useful_life_months=item.useful_life_months,
        min_useful_life_months=min_life,
    )
    return [{"code": i.code, "severity": i.severity, "message": i.message} for i in check.issues]


def project_issues(project: CapexProject) -> list[dict]:
    issues = [
        {"code": i.code, "severity": i.severity, "message": i.message}
        for i in rules.check_project(project.is_project, project.project_type_code, project.justification)
    ]
    if not project.items:
        issues.append({"code": "CAPEX_NO_ITEMS", "severity": "CRITICAL", "message": "Solicitação sem itens"})
    return issues


# ------------------------------------------------------------------ saída


def _plain(value: Decimal | None) -> str:
    """Quantidade sem zeros à direita e sem notação científica (Decimal('100.00') → '100')."""
    if value is None:
        return "0"
    normalized = value.normalize()
    return f"{normalized:f}" if normalized == normalized.to_integral() else str(normalized)


def item_out(ctx: Context, item: CapexItem, accounts: dict[int, Account] | None = None) -> dict:
    values = {m: ZERO for m in MONTHS}
    for v in item.values:
        values[v.month] = v.amount
    scheduled = sum(values.values(), ZERO)
    acc = (accounts or {}).get(item.account_id)
    return {
        "id": item.id,
        "project_id": item.project_id,
        "account_id": item.account_id,
        "account_code": acc.code if acc else None,
        "account_name": acc.name if acc else None,
        "asset_item_id": item.asset_item_id,
        "item_name": item.item_name,
        "description": item.description,
        "unit_value": str(money(item.unit_value)),
        "quantity": _plain(item.quantity),
        "total_value": str(money(item.total_value)),
        "useful_life_months": item.useful_life_months,
        "values": {m: str(money(v)) for m, v in values.items()},
        "scheduled": str(money(scheduled)),
        "difference": str(money(scheduled - item.total_value)),
        "issues": item_issues(ctx, item),
    }


def project_out(ctx: Context, project: CapexProject, accounts: dict[int, Account] | None = None) -> dict:
    items = [item_out(ctx, i, accounts) for i in project.items]
    return {
        "id": project.id,
        "code": project.code,
        "branch_id": project.branch_id,
        "is_project": project.is_project,
        "project_type_code": project.project_type_code,
        "title": project.title,
        "description": project.description,
        "justification": project.justification,
        "expected_cost_reduction": None
        if project.expected_cost_reduction is None
        else str(project.expected_cost_reduction),
        "expected_revenue": None if project.expected_revenue is None else str(project.expected_revenue),
        "priority": project.priority,
        "budget_prev_year": None if project.budget_prev_year is None else str(project.budget_prev_year),
        "observations": project.observations,
        "source": (project.attributes or {}).get("source", "SYSTEM"),
        "items": items,
        "total": str(money(sum((i.total_value for i in project.items), ZERO))),
        "issues": project_issues(project),
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
    }


def projects(db: Session, sub: BudgetSubmission) -> list[CapexProject]:
    return list(db.scalars(select(CapexProject).where(CapexProject.submission_id == sub.id).order_by(CapexProject.id)))


def submission_view(db: Session, ctx: Context, sub: BudgetSubmission) -> dict:
    """Solicitações + consolidado por conta (com realizado CAPEX dos anos anteriores), mês e tipo de projeto."""
    items_by_project = projects(db, sub)
    acc_ids = {i.account_id for p in items_by_project for i in p.items}

    def hist(model, year):
        return {acc: amount for acc, amount in _current_sum(db, model, sub.cost_center_id, year, False)}

    prev, ref, ref_budget = (
        hist(ActualEntry, ctx.prev_year),
        hist(ActualEntry, ctx.ref_year),
        hist(ReferenceBudgetEntry, ctx.ref_year),
    )
    acc_ids |= set(prev) | set(ref) | set(ref_budget)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(acc_ids or {-1})))}
    capex_ids = {a for a, acc in accounts.items() if acc.nature == MODULE}

    out_projects = [project_out(ctx, p, accounts) for p in items_by_project]
    monthly = [ZERO] * 12
    proposed: dict[int, Decimal] = defaultdict(lambda: ZERO)
    by_type: dict[str, Decimal] = defaultdict(lambda: ZERO)
    critical = warnings = 0
    for p in out_projects:
        for issue in p["issues"]:
            critical += issue["severity"] == "CRITICAL"
            warnings += issue["severity"] == "WARNING"
        kind = (p["project_type_code"] or "Projeto sem tipo") if p["is_project"] else "Aquisição avulsa"
        for item in p["items"]:
            for m, v in item["values"].items():
                monthly[int(m) - 1] += Decimal(v)
            proposed[item["account_id"]] += Decimal(item["total_value"])
            by_type[kind] += Decimal(item["total_value"])
            for issue in item["issues"]:
                critical += issue["severity"] == "CRITICAL"
                warnings += issue["severity"] == "WARNING"
    rows = []
    for acc_id in sorted(capex_ids | set(proposed), key=lambda a: accounts[a].code if a in accounts else ""):
        acc = accounts.get(acc_id)
        if acc is None:
            continue
        rows.append(
            {
                "account_id": acc_id,
                "code": acc.code,
                "name": acc.name,
                "prev_actual": str(money(prev.get(acc_id, ZERO))),
                "ref_actual": str(money(ref.get(acc_id, ZERO))),
                "ref_budget": str(money(ref_budget.get(acc_id, ZERO))),
                "proposed": str(money(proposed.get(acc_id, ZERO))),
            }
        )
    total = sum(proposed.values(), ZERO)
    return {
        "prev_year": ctx.prev_year,
        "ref_year": ctx.ref_year,
        "target_year": ctx.target_year,
        "projects": out_projects,
        "accounts": rows,
        "monthly": [str(money(v)) for v in monthly],
        "by_type": [{"label": k, "total": str(money(v))} for k, v in sorted(by_type.items(), key=lambda kv: -kv[1])],
        "totals": {
            "proposed": str(money(total)),
            "scheduled": str(money(sum(monthly, ZERO))),  # soma do cronograma: é o que entra na consolidação
            "projects_total": str(money(sum((Decimal(p["total"]) for p in out_projects if p["is_project"]), ZERO))),
            "requests": len(out_projects),
            "projects": sum(1 for p in out_projects if p["is_project"]),
            "items": sum(len(p["items"]) for p in out_projects),
            "prev_actual": str(money(sum((prev.get(a, ZERO) for a in capex_ids), ZERO))),
            "ref_actual": str(money(sum((ref.get(a, ZERO) for a in capex_ids), ZERO))),
            "ref_budget": str(money(sum((ref_budget.get(a, ZERO) for a in capex_ids), ZERO))),
        },
        "issues": {"critical": critical, "warning": warnings},
    }


def _lower_first(text: str) -> str:
    return text[:1].lower() + text[1:]


def blockers(db: Session, ctx: Context, sub: BudgetSubmission) -> list[str]:
    """Pendências críticas: impedem envio e aprovação."""
    found = projects(db, sub)
    if not any(p.items for p in found):
        return ["nenhum item de CAPEX lançado"]
    out = []
    for p in found:
        for issue in project_issues(p):
            if issue["severity"] == "CRITICAL":
                out.append(f"{p.code} {p.title}: {_lower_first(issue['message'])}")
        for item in p.items:
            for issue in item_issues(ctx, item):
                if issue["severity"] == "CRITICAL":
                    out.append(f"{p.code} · {item.item_name}: {_lower_first(issue['message'])}")
    if len(out) > 6:
        out = out[:6] + [f"e mais {len(out) - 6} pendência(s)"]
    return out


# ------------------------------------------------------------------ edição


def _validate_account(db: Session, account_id: int | None) -> Account:
    acc = db.get(Account, account_id) if account_id else None
    if acc is None or not acc.is_active:
        raise CapexError("Conta contábil inválida ou inativa")
    if acc.nature != MODULE:
        raise CapexError(f"A conta {acc.code} é de {acc.nature}; no CAPEX use contas de ativo")
    return acc


def _branch(db: Session, cc: CostCenter, branch_id: int | None) -> int | None:
    if branch_id is None:
        return None
    branch = db.get(Branch, branch_id)
    if branch is None or branch.company_id != cc.company_id:
        raise CapexError("Filial inválida para a empresa do centro de custo")
    return branch.id


def _project_type(db: Session, code: str | None) -> str | None:
    if not code:
        return None
    exists = db.scalar(
        select(LookupValue.id).where(LookupValue.domain == "CAPEX_PROJECT_TYPE", LookupValue.code == code)
    )
    if exists is None:
        raise CapexError(f"Tipo de projeto inválido: {code}")
    return code


def _next_code(db: Session, sub: BudgetSubmission) -> str:
    count = db.scalar(select(func.count()).select_from(CapexProject).where(CapexProject.submission_id == sub.id))
    used = set(db.scalars(select(CapexProject.code).where(CapexProject.submission_id == sub.id)))
    n = (count or 0) + 1
    while f"CPX-{n:03d}" in used:
        n += 1
    return f"CPX-{n:03d}"


PROJECT_FIELDS = (
    "title",
    "description",
    "justification",
    "expected_cost_reduction",
    "expected_revenue",
    "priority",
    "budget_prev_year",
    "observations",
)


def _apply_project(db: Session, project: CapexProject, cc: CostCenter, data: dict) -> None:
    for key in PROJECT_FIELDS:
        if key in data:
            value = data[key]
            setattr(project, key, value.strip() if isinstance(value, str) else value)
    if "is_project" in data:
        project.is_project = bool(data["is_project"])
    if "project_type_code" in data:
        project.project_type_code = _project_type(db, data["project_type_code"])
    if not project.is_project:
        project.project_type_code = None
    if "branch_id" in data:
        project.branch_id = _branch(db, cc, data["branch_id"])
    if not (project.title or "").strip():
        raise CapexError("Informe o título da solicitação")


def create_project(
    db: Session, ctx: Context, sub: BudgetSubmission, data: dict, user_id: int | None, *, attributes: dict | None = None
) -> CapexProject:
    cc = db.get(CostCenter, sub.cost_center_id)
    project = CapexProject(
        submission_id=sub.id,
        code=_next_code(db, sub),
        company_id=cc.company_id,
        cost_center_id=cc.id,
        is_project=False,
        title="",
        attributes=attributes or {"source": "SYSTEM"},
        created_by=user_id,
        updated_by=user_id,
    )
    _apply_project(db, project, cc, data)
    db.add(project)
    db.flush()
    for item in data.get("items") or []:
        add_item(db, ctx, project, item)
    mark_in_progress(db, sub, ctx, user_id)
    db.flush()
    return project


def update_project(db: Session, project: CapexProject, data: dict, user_id: int | None) -> CapexProject:
    cc = db.get(CostCenter, project.cost_center_id)
    _apply_project(db, project, cc, data)
    project.updated_by = user_id
    db.flush()
    return project


def _schedule(values: dict | None) -> dict[int, Decimal]:
    out = {}
    for month, amount in (values or {}).items():
        m = int(month)
        if m not in MONTHS:
            raise CapexError("Mês inválido no cronograma")
        v = money(amount)
        if v < 0:
            raise CapexError("O cronograma não aceita valores negativos")
        out[m] = v
    return out


def _set_schedule(item: CapexItem, values: dict[int, Decimal]) -> None:
    existing = {v.month: v for v in item.values}
    for month, amount in values.items():
        if month in existing:
            if amount:
                existing[month].amount = amount
            else:
                item.values.remove(existing[month])
        elif amount:
            item.values.append(CapexItemValue(month=month, amount=amount))


def _apply_item(db: Session, item: CapexItem, data: dict) -> None:
    if "asset_item_id" in data:
        asset = db.get(AssetItem, data["asset_item_id"]) if data["asset_item_id"] else None
        if data["asset_item_id"] and asset is None:
            raise CapexError("Item do catálogo de ativos não encontrado")
        item.asset_item_id = asset.id if asset else None
        if asset and not data.get("account_id"):  # o catálogo sugere a conta pela classe do ativo
            klass = db.get(AssetClass, asset.asset_class_id)
            if klass and klass.account_id:
                data = data | {"account_id": klass.account_id}
        if asset and not (data.get("item_name") or item.item_name):
            data = data | {"item_name": asset.name}
    if data.get("account_id") is not None:
        item.account_id = _validate_account(db, data["account_id"]).id
    if not item.account_id:
        raise CapexError("Informe a conta do ativo (ou escolha um item do catálogo)")
    if "item_name" in data:
        item.item_name = (data["item_name"] or "").strip()
    if not item.item_name:
        raise CapexError("Informe o item")
    if "description" in data:
        item.description = data["description"]
    if "useful_life_months" in data:
        life = data["useful_life_months"]
        item.useful_life_months = int(life) if life not in (None, "") else None
    for key in ("unit_value", "quantity"):
        if key in data and data[key] is not None:
            setattr(item, key, Decimal(str(data[key])))
    if item.unit_value is None or item.quantity is None:
        raise CapexError("Informe valor unitário e quantidade")
    if item.unit_value < 0 or item.quantity < 0:
        raise CapexError("Valor unitário e quantidade não podem ser negativos")
    item.total_value = rules.item_total(item.unit_value, item.quantity)
    if "values" in data and data["values"] is not None:
        current = {v.month: v.amount for v in item.values}
        incoming = _schedule(data["values"])
        _set_schedule(item, {m: incoming.get(m, current.get(m, ZERO)) for m in MONTHS})


def add_item(db: Session, ctx: Context, project: CapexProject, data: dict) -> CapexItem:
    item = CapexItem(project_id=project.id, account_id=None, item_name="", unit_value=None, quantity=None)
    _apply_item(db, item, dict(data))
    project.items.append(item)
    db.flush()
    return item


def update_item(db: Session, item: CapexItem, data: dict) -> CapexItem:
    _apply_item(db, item, dict(data))
    db.flush()
    return item


# ------------------------------------------------------------------ opções


def options(db: Session, ctx: Context, company_id: int | None) -> dict:
    accounts = db.scalars(select(Account).where(Account.is_active, Account.nature == MODULE).order_by(Account.code))
    classes = {c.id: c for c in db.scalars(select(AssetClass))}
    branches = select(Branch).where(Branch.is_active).order_by(Branch.name)
    if company_id:
        branches = branches.where(Branch.company_id == company_id)
    lookups = defaultdict(list)
    for lv in db.scalars(
        select(LookupValue)
        .where(LookupValue.is_active, LookupValue.domain.in_(("CAPEX_PROJECT_TYPE", "PRIORITY")))
        .order_by(LookupValue.sort_order)
    ):
        lookups[lv.domain].append({"code": lv.code, "label": lv.label})
    min_value, min_life = _limits(ctx)
    return {
        "accounts": [{"id": a.id, "code": a.code, "name": a.name} for a in accounts],
        "asset_items": [
            {
                "id": i.id,
                "name": i.name,
                "asset_class": classes[i.asset_class_id].name if i.asset_class_id in classes else None,
                "account_id": classes[i.asset_class_id].account_id if i.asset_class_id in classes else None,
            }
            for i in db.scalars(select(AssetItem).order_by(AssetItem.name))
        ],
        "branches": [
            {"id": b.id, "code": b.code, "name": b.name, "company_id": b.company_id} for b in db.scalars(branches)
        ],
        "lookups": lookups,
        "params": {"min_unit_value": str(min_value), "min_useful_life_months": min_life},
    }
