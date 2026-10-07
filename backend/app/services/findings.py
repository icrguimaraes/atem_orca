"""Apontamentos: divergências e erros de preenchimento dos orçamentos em aberto, item a item, num só lugar.

Reúne o que cada tela de centro de custo já calcula (pendências do CAPEX, alertas das viagens, contas com
variação sem justificativa, movimentações de pessoal sem justificativa) para a Controladoria analisar e o
gestor corrigir. Crítico = bloqueia o envio; aviso = para a análise (não bloqueia)."""

from collections import defaultdict
from decimal import Decimal
from functools import partial

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules.capex import brl
from app.domain.rules.common import MONTH_LABELS, money
from app.models import BudgetLine, BudgetSubmission, CostCenter
from app.services import capex as capex_svc
from app.services import opex as opex_svc
from app.services import personnel as personnel_svc

# orçamentos iniciados e ainda não aprovados (aprovado = divergências já aceitas na análise)
OPEN = ("IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW")
ROUTES = {"OPEX": "/orcamento", "CAPEX": "/capex", "PERSONNEL": "/pessoal"}
MODULE_LABELS = {"OPEX": "OPEX", "CAPEX": "CAPEX", "PERSONNEL": "Pessoal"}
KIND_LABELS = {
    "OPEX_JUSTIFICATION": "Variação sem justificativa",
    "TRAVEL_NO_FARE": "Passagem zerada",
    "TRAVEL_RATE": "Viagem sem tarifa",
    "CAPEX_SCHEDULE_MISMATCH": "Cronograma diferente do total",
    "CAPEX_INVALID_VALUE": "Valor ou quantidade inválidos",
    "CAPEX_NO_PROJECT_TYPE": "Projeto sem tipo",
    "CAPEX_NO_JUSTIFICATION": "Sem justificativa",
    "CAPEX_NO_ITEMS": "Solicitação sem itens",
    "CAPEX_BELOW_MIN_VALUE": "Valor baixo (possível OPEX)",
    "CAPEX_SHORT_LIFE": "Vida útil curta",
    "CAPEX_ACCOUNT_MISMATCH": "Conta diferente do catálogo",
    "CAPEX_SOFTWARE": "Software no CAPEX",
    "PERSONNEL_JUSTIFICATION": "Movimentação sem justificativa",
}
FLAG_TEXT = {
    "NEW_ACCOUNT": "Conta nova, sem histórico",
    "NO_BUDGET": "Sem orçamento, com gasto na referência",
    "GROWTH_ABOVE": "Crescimento acima do limite",
    "REDUCTION_ABOVE": "Redução acima do limite",
}
SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1}


def _pct(value: str | None) -> str:
    if value is None:
        return ""
    n = Decimal(value) * 100
    return f" ({'+' if n > 0 else ''}{n:.1f}%)".replace(".", ",")


def collect(db: Session, ctx: opex_svc.Context, cc_ids: set[int] | None) -> list[dict]:
    """Apontamentos da versão em elaboração nos CCs visíveis (`cc_ids` None = todos)."""
    stmt = select(BudgetSubmission).where(
        BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.status.in_(OPEN)
    )
    if cc_ids is not None:
        stmt = stmt.where(BudgetSubmission.cost_center_id.in_(cc_ids or {-1}))
    subs = list(db.scalars(stmt))
    ccs = {
        c.id: c
        for c in db.scalars(select(CostCenter).where(CostCenter.id.in_({s.cost_center_id for s in subs} or {-1})))
    }
    items: list[dict] = []
    for sub in subs:
        add = partial(_add, items, sub, ccs[sub.cost_center_id])
        if sub.module == "OPEX":
            _opex(db, ctx, sub, add)
        elif sub.module == "CAPEX":
            _capex(db, ctx, sub, add)
        elif sub.module == "PERSONNEL":
            for text in personnel_svc.blockers(db, ctx, sub):
                add("CRITICAL", "PERSONNEL_JUSTIFICATION", "Movimentações do quadro", text[0].upper() + text[1:])
    items.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["cost_center"], i["module"], i["kind"], i["subject"]))
    return items


def _add(
    items: list[dict],
    sub: BudgetSubmission,
    cc: CostCenter,
    severity: str,
    kind: str,
    subject: str,
    message: str,
    amount=None,
    detail: str | None = None,
) -> None:
    items.append(
        {
            "severity": severity,
            "module": sub.module,
            "module_label": MODULE_LABELS.get(sub.module, sub.module),
            "kind": kind,
            "kind_label": KIND_LABELS.get(kind, kind),
            "cost_center_id": cc.id,
            "cost_center": f"{cc.code} · {cc.name}",
            "status": sub.status,
            "subject": subject,
            "detail": detail,
            "message": message,
            "amount": None if amount is None else str(money(amount)),
            "link": f"{ROUTES[sub.module]}/{cc.id}" if sub.module in ROUTES else None,
        }
    )


def _opex(db: Session, ctx: opex_svc.Context, sub: BudgetSubmission, add) -> None:
    # contas com alerta (variação acima do limite, conta nova, sem orçamento) sem justificativa: bloqueiam o envio
    for row in opex_svc.account_view(db, ctx, sub)["accounts"]:
        if not row["needs_justification"] or (row["justification"] or "").strip():
            continue
        flag = FLAG_TEXT.get(row["flags"][0], row["flags"][0])
        add(
            "CRITICAL",
            "OPEX_JUSTIFICATION",
            f"{row['code']} {row['name']}",
            f"{flag}: proposto {brl(Decimal(row['proposed']))} × referência {brl(Decimal(row['variation_base']))}"
            f"{_pct(row['variation_pct'])}. Justifique a conta para enviar",
            row["proposed"],
        )
    # alertas das viagens (passagem zerada, diária ou hospedagem sem tarifa): um por viagem
    trips: dict[str, list[BudgetLine]] = defaultdict(list)
    for line in db.scalars(
        select(BudgetLine).where(BudgetLine.submission_id == sub.id, BudgetLine.line_type == "TRAVEL")
    ):
        trips[line.group_ref or str(line.id)].append(line)
    for lines in trips.values():
        attrs = lines[0].attributes or {}
        warnings = attrs.get("warnings") or []
        if not warnings:
            continue
        month = attrs.get("departure_month")
        when = f" · {MONTH_LABELS[int(month) - 1]}" if month else ""
        subject = f"{lines[0].description or 'Viagem'} · {attrs.get('origin')} → {attrs.get('destination')}{when}"
        total = sum((line.total_amount for line in lines), Decimal(0))
        for warning in warnings:
            fare = warning.startswith(("Passagem", "Tarifa de passagem"))
            add("WARNING", "TRAVEL_NO_FARE" if fare else "TRAVEL_RATE", subject, warning, total)


def _capex(db: Session, ctx: opex_svc.Context, sub: BudgetSubmission, add) -> None:
    for project in capex_svc.projects(db, sub):
        for issue in capex_svc.project_issues(project):
            total = sum((i.total_value for i in project.items), Decimal(0))
            add(issue["severity"], issue["code"], f"{project.code} · {project.title}", issue["message"], total)
        for item in project.items:
            for issue in capex_svc.item_issues(ctx, item):
                add(
                    issue["severity"],
                    issue["code"],
                    f"{project.code} · {item.item_name}",
                    issue["message"],
                    item.total_value,
                    item.description,
                )
