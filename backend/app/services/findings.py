"""Apontamentos: divergências e erros de preenchimento dos orçamentos em aberto, item a item, num só lugar.

Reúne o que cada tela de centro de custo já calcula (pendências do CAPEX, alertas das viagens, contas com
variação sem justificativa, movimentações de pessoal sem justificativa) para a Controladoria analisar e o
gestor corrigir. Crítico = bloqueia o envio; aviso = para a análise (não bloqueia).

Cada apontamento tem uma chave estável (`{orçamento}:{entidade}:{id}:{tipo}`) e, quando dá, a correção direta
(`fix`) feita na própria página; avisos podem ser mantidos com justificativa. Correções e avisos mantidos ficam
em `finding_reviews` (registro da análise, exportado no relatório)."""

import io
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from functools import partial

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules.capex import brl
from app.domain.rules.common import MONTH_LABELS, MONTHS, money
from app.domain.workflow import STATUS_LABELS
from app.models import (
    Account,
    Area,
    BudgetLine,
    BudgetSubmission,
    CapexItem,
    CapexProject,
    CostCenter,
    Department,
    Employee,
    FindingReview,
    LookupValue,
    PersonnelMovement,
    User,
)
from app.services import capex as capex_svc
from app.services import opex as opex_svc
from app.services import personnel as personnel_svc
from app.services.exports import _sheet

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
    "PERSONNEL_NO_SALARY": "Sem novo salário",
    "PERSONNEL_NO_MONTH": "Ação sem mês",
    "PERSONNEL_CC_GUESSED": "Centro de custo pelo cargo",
    "STRUCTURE_NO_SECTOR": "CC sem área e setor",
    "OPEX_MOVED_CC": "Lançamento em CC de outra área",
}
FLAG_TEXT = {
    "NEW_ACCOUNT": "Conta nova, sem histórico",
    "NO_BUDGET": "Sem orçamento, com gasto na referência",
    "GROWTH_ABOVE": "Crescimento acima do limite",
    "REDUCTION_ABOVE": "Redução acima do limite",
}
ACTION_LABELS = {"CORRECTED": "Corrigido", "KEPT": "Mantido"}
SEVERITY_LABELS = {"CRITICAL": "Crítico", "WARNING": "Aviso"}
SEVERITY_ORDER = {"CRITICAL": 0, "WARNING": 1}
MANAUS = timezone(timedelta(hours=-4))  # horário de Manaus (sem horário de verão)
ZERO = Decimal("0")


class FindingError(Exception):
    """Correção recusada (mensagem pronta para o usuário)."""


def _pct(value: str | None) -> str:
    if value is None:
        return ""
    n = Decimal(value) * 100
    return f" ({'+' if n > 0 else ''}{n:.1f}%)".replace(".", ",")


# ------------------------------------------------------------------ apontamentos


def collect(
    db: Session,
    ctx: opex_svc.Context,
    cc_ids: set[int] | None,
    kept: set[str] | frozenset = frozenset(),
    *,
    structure: bool = False,
) -> list[dict]:
    """Apontamentos da versão em elaboração nos CCs visíveis (`cc_ids` None = todos), sem os avisos mantidos.

    `structure` (Controladoria): inclui os CCs sem área e setor, que caem em "Sem setor" no Painel."""
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
            for mv, label in personnel_svc.missing_reasons(db, sub):
                add(
                    "CRITICAL",
                    "PERSONNEL_JUSTIFICATION",
                    ("PERSONNEL_MOVEMENT", mv.id),
                    label[0].upper() + label[1:],
                    "Justificativa obrigatória para enviar (análise da Controladoria e do RH)",
                    fix={"type": "text", "label": "Justificativa da movimentação"},
                )
            for mv, label in personnel_svc.pending_salaries(db, sub):
                add(
                    "CRITICAL",
                    "PERSONNEL_NO_SALARY",
                    ("PERSONNEL_MOVEMENT", mv.id),
                    label[0].upper() + label[1:],
                    "Salário não veio na planilha: fica sem aumento (vaga: sem custo) até você informar o valor",
                    fix={"type": "money", "label": "Novo salário mensal (R$)"},
                )
            for mv, label in personnel_svc.pending_months(db, sub):
                add(
                    "CRITICAL",
                    "PERSONNEL_NO_MONTH",
                    ("PERSONNEL_MOVEMENT", mv.id),
                    label[0].upper() + label[1:],
                    "Mês não veio na planilha: a ação ainda não entra no custo (salário atual o ano todo) até você "
                    "informar o mês",
                    fix={"type": "month", "label": "Mês da ação"},
                )
            for emp in personnel_svc.pending_cost_centers(db, sub):
                info = (emp.attributes or {}).get("pending_cc") or {}
                add(
                    "WARNING",
                    "PERSONNEL_CC_GUESSED",
                    ("EMPLOYEE", emp.id),
                    f"{emp.name} · {info.get('position') or 'cargo não informado'}",
                    f"Centro de custo vazio na planilha: definido pelo cargo (setor {info.get('sector')}). Confirme; "
                    "se estiver errado, corrija o centro de custo na planilha e importe de novo",
                    fix={"type": "confirm", "label": "Confirmar centro de custo"},
                )
    if structure:  # um por CC (no primeiro orçamento aberto dele), corrigido escolhendo o setor
        seen: set[int] = set()
        for sub in sorted(subs, key=lambda s: (s.cost_center_id, ("OPEX", "CAPEX", "PERSONNEL").index(s.module))):
            cc = ccs[sub.cost_center_id]
            if cc.id in seen or cc.area_id is not None:
                continue
            seen.add(cc.id)
            _add(
                items,
                sub,
                cc,
                "WARNING",
                "STRUCTURE_NO_SECTOR",
                ("COST_CENTER", cc.id),
                f"{cc.code} · {cc.name}",
                'Centro de custo sem área e setor: no Painel ele aparece em "Sem setor". Escolha o setor (a área vem '
                "junto)",
                fix={"type": "sector", "label": "Setor do centro de custo"},
            )
            items[-1]["can_keep"] = False
    sectors = {o["id"]: o["label"] for o in sector_options(db)}
    for i in items:
        i["sector"] = sectors.get(ccs[i["cost_center_id"]].area_id)
    items = [i for i in items if i["key"] not in kept]
    items.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["cost_center"], i["module"], i["kind"], i["subject"]))
    return items


def sector_options(db: Session) -> list[dict]:
    """Setores (com a área) para corrigir CC sem estrutura direto no apontamento."""
    departments = {d.id: d.name for d in db.scalars(select(Department))}
    out = [
        {"id": a.id, "label": f"{departments[a.department_id]} › {a.name}" if a.department_id else a.name}
        for a in db.scalars(select(Area))
    ]
    return sorted(out, key=lambda o: o["label"])


def _add(
    items: list[dict],
    sub: BudgetSubmission,
    cc: CostCenter,
    severity: str,
    kind: str,
    entity: tuple[str, object],
    subject: str,
    message: str,
    amount=None,
    detail: str | None = None,
    fix: dict | None = None,
    suffix: str = "",
) -> None:
    items.append(
        {
            "key": f"{sub.id}:{entity[0]}:{entity[1]}:{kind}{suffix}",
            "entity": entity[0],
            "entity_id": str(entity[1]),
            "submission_id": sub.id,
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
            "fix": fix,
            "can_keep": severity == "WARNING",
        }
    )


def _opex(db: Session, ctx: opex_svc.Context, sub: BudgetSubmission, add) -> None:
    # contas com alerta (variação acima do limite, conta nova, sem orçamento) sem justificativa: aviso (recomendada,
    # não bloqueia o envio — o template OPEX só recomenda justificar)
    for row in opex_svc.account_view(db, ctx, sub)["accounts"]:
        if not row["needs_justification"] or (row["justification"] or "").strip():
            continue
        flag = FLAG_TEXT.get(row["flags"][0], row["flags"][0])
        add(
            "WARNING",
            "OPEX_JUSTIFICATION",
            ("OPEX_ACCOUNT", row["account_id"]),
            f"{row['code']} {row['name']}",
            f"{flag}: proposto {brl(Decimal(row['proposed']))} × referência {brl(Decimal(row['variation_base']))}"
            f"{_pct(row['variation_pct'])}. Justifique a conta (recomendado; não bloqueia o envio)",
            row["proposed"],
            fix={"type": "text", "label": "Justificativa da conta"},
        )
    # alertas das viagens (passagem zerada, diária ou hospedagem sem tarifa): um por alerta da viagem
    trips: dict[str, list[BudgetLine]] = defaultdict(list)
    for line in db.scalars(
        select(BudgetLine).where(BudgetLine.submission_id == sub.id, BudgetLine.line_type == "TRAVEL")
    ):
        trips[line.group_ref or f"L{line.id}"].append(line)
    for group, lines in trips.items():
        attrs = lines[0].attributes or {}
        month = attrs.get("departure_month")
        when = f" · {MONTH_LABELS[int(month) - 1]}" if month else ""
        route = f"{attrs.get('origin')} → {attrs.get('destination')}"
        subject = f"{lines[0].description or 'Viagem'} · {route}{when}"
        total = sum((line.total_amount for line in lines), ZERO)
        for n, warning in enumerate(attrs.get("warnings") or []):
            fare = warning.startswith(("Passagem", "Tarifa de passagem"))
            add(
                "WARNING",
                "TRAVEL_NO_FARE" if fare else "TRAVEL_RATE",
                ("TRAVEL", group),
                subject,
                warning,
                total,
                fix={"type": "ticket", "route": route} if fare and lines[0].group_ref else None,
                suffix=f":{n}" if n else "",
            )


def _capex(db: Session, ctx: opex_svc.Context, sub: BudgetSubmission, add) -> None:
    for project in capex_svc.projects(db, sub):
        for issue in capex_svc.project_issues(project):
            fix = None
            if issue["code"] == "CAPEX_NO_JUSTIFICATION":
                fix = {"type": "text", "label": "Justificativa da solicitação"}
            elif issue["code"] == "CAPEX_NO_PROJECT_TYPE":
                fix = {"type": "project_type"}
            add(
                issue["severity"],
                issue["code"],
                ("CAPEX_PROJECT", project.id),
                f"{project.code} · {project.title}",
                issue["message"],
                sum((i.total_value for i in project.items), ZERO),
                fix=fix,
            )
        for item in project.items:
            for issue in capex_svc.item_issues(ctx, item):
                fix = None
                if issue["code"] == "CAPEX_SCHEDULE_MISMATCH":
                    current = {v.month: v.amount for v in item.values}
                    fix = {
                        "type": "schedule",
                        "total": str(money(item.total_value)),
                        "values": {str(m): str(money(current.get(m, ZERO))) for m in MONTHS},
                    }
                elif issue["code"] == "CAPEX_ACCOUNT_MISMATCH":
                    catalog = capex_svc.catalog_account(db, item)
                    if catalog is not None:
                        fix = {
                            "type": "account",
                            "account_id": catalog.id,
                            "account": f"{catalog.code} · {catalog.name}",
                        }
                add(
                    issue["severity"],
                    issue["code"],
                    ("CAPEX_ITEM", item.id),
                    f"{project.code} · {item.item_name}",
                    issue["message"],
                    item.total_value,
                    item.description,
                    fix=fix,
                )


# ------------------------------------------------------------------ correção na página


def apply_fix(
    db: Session, sub: BudgetSubmission, finding: dict, data: dict, user_id: int | None
) -> tuple[str, dict | None, dict | None]:
    """Aplica a correção do apontamento. Devolve (descrição da correção, antes, depois) para o registro."""
    kind, ident = finding["kind"], finding["entity_id"]
    text = (data.get("text") or "").strip()

    def need_text() -> str:
        if not text:
            raise FindingError("Escreva a justificativa")
        return text

    if kind == "CAPEX_SCHEDULE_MISMATCH":
        item = db.get(CapexItem, int(ident))
        values = {int(m): money(v) for m, v in (data.get("values") or {}).items() if v not in (None, "")}
        if any(m not in MONTHS for m in values) or any(v < 0 for v in values.values()):
            raise FindingError("Cronograma inválido: use valores positivos de JAN a DEZ")
        scheduled = sum(values.values(), ZERO)
        if abs(scheduled - item.total_value) > Decimal("0.01"):
            raise FindingError(
                f"A soma dos meses ({brl(scheduled)}) precisa fechar com o total do item ({brl(item.total_value)})"
            )
        before = {"values": {str(v.month): str(v.amount) for v in item.values}}
        capex_svc.update_item(db, item, {"values": {str(m): values.get(m, ZERO) for m in MONTHS}})
        note = "Cronograma: " + ", ".join(f"{MONTH_LABELS[m - 1]} {brl(v)}" for m, v in sorted(values.items()) if v)
        return note, before, {"values": {str(m): str(v) for m, v in values.items() if v}}
    if kind == "CAPEX_ACCOUNT_MISMATCH":
        item = db.get(CapexItem, int(ident))
        old = db.get(Account, item.account_id)
        new = capex_svc.catalog_account(db, item)
        if new is None:
            raise FindingError("O item não está no catálogo de ativos")
        capex_svc.update_item(db, item, {"account_id": new.id})
        note = f"Conta {old.code} ({old.name}) → {new.code} ({new.name}), como no catálogo de ativos"
        return note, {"account": old.code}, {"account": new.code}
    if kind == "CAPEX_NO_JUSTIFICATION":
        project = db.get(CapexProject, int(ident))
        before = {"justification": project.justification}
        capex_svc.update_project(db, project, {"justification": need_text()}, user_id)
        return f"Justificativa: {text}", before, {"justification": text}
    if kind == "CAPEX_NO_PROJECT_TYPE":
        project = db.get(CapexProject, int(ident))
        code = (data.get("project_type_code") or "").strip()
        if not code:
            raise FindingError("Escolha o tipo de projeto")
        capex_svc.update_project(db, project, {"project_type_code": code}, user_id)
        return f"Tipo de projeto: {project.project_type_code}", None, {"project_type": project.project_type_code}
    if kind == "OPEX_JUSTIFICATION":
        out = opex_svc.set_justification(db, sub, int(ident), need_text(), user_id)
        return f"Justificativa: {text}", out["before"], out["after"]
    if kind == "TRAVEL_NO_FARE":
        raw = data.get("ticket_amount")
        amount = money(raw) if raw not in (None, "") else ZERO
        opex_svc.set_trip_ticket(db, sub, ident, amount, user_id)
        return f"Passagem incluída: {brl(amount)}", None, {"ticket_amount": str(amount)}
    if kind == "PERSONNEL_NO_SALARY":
        mv = db.get(PersonnelMovement, int(ident))
        raw = data.get("amount")
        amount = money(raw) if raw not in (None, "") else ZERO
        if amount <= 0:
            raise FindingError("Informe o novo salário")
        emp = db.get(Employee, mv.employee_id) if mv.employee_id else None
        if emp is not None and amount == money(emp.base_salary):
            raise FindingError("O novo salário é igual ao atual")
        mv.new_salary, mv.updated_by = amount, user_id
        _clear_pending(mv, "new_salary")
        db.flush()
        return f"Novo salário: {brl(amount)}", {"new_salary": None}, {"new_salary": str(amount)}
    if kind == "PERSONNEL_NO_MONTH":
        mv = db.get(PersonnelMovement, int(ident))
        try:
            month = int(data.get("month"))
        except (TypeError, ValueError):
            raise FindingError("Informe o mês da ação") from None
        if month not in MONTHS:
            raise FindingError("Mês da ação deve estar entre JAN e DEZ")
        mv.effective_month, mv.updated_by = month, user_id
        _clear_pending(mv, "month")
        db.flush()
        return f"Mês da ação: {MONTH_LABELS[month - 1]}", {"month": None}, {"month": month}
    if kind == "PERSONNEL_CC_GUESSED":
        emp = db.get(Employee, int(ident))
        attrs = dict(emp.attributes or {})
        info = attrs.pop("pending_cc", None)
        emp.attributes = attrs or None
        db.flush()
        cc = db.get(CostCenter, emp.cost_center_id)
        return f"Centro de custo confirmado: {cc.code} · {cc.name}", {"pending_cc": info}, None
    if kind == "STRUCTURE_NO_SECTOR":
        cc = db.get(CostCenter, int(ident))
        area = db.get(Area, int(data.get("area_id") or 0)) if data.get("area_id") else None
        if area is None:
            raise FindingError("Escolha o setor")
        before = {"department_id": cc.department_id, "area_id": cc.area_id}
        cc.area_id, cc.department_id = area.id, area.department_id
        db.flush()
        label = next(o["label"] for o in sector_options(db) if o["id"] == area.id)
        return f"Setor: {label}", before, {"department_id": cc.department_id, "area_id": cc.area_id}
    if kind == "PERSONNEL_JUSTIFICATION":
        mv = db.get(PersonnelMovement, int(ident))
        before = {"reason": mv.reason}
        mv.reason, mv.updated_by = need_text(), user_id
        db.flush()
        return f"Justificativa: {text}", before, {"reason": text}
    raise FindingError("Este apontamento não tem correção direta: abra o orçamento do centro de custo")


def _clear_pending(mv: PersonnelMovement, item: str) -> None:
    attrs = dict(mv.attributes or {})
    pending = [p for p in attrs.pop("pending", None) or [] if p != item]
    if pending:
        attrs["pending"] = pending
    mv.attributes = attrs


def log(db: Session, sub: BudgetSubmission, finding: dict, action: str, note: str, user_id: int | None) -> None:
    db.add(
        FindingReview(
            submission_id=sub.id,
            finding_key=finding["key"],
            kind=finding["kind"],
            action=action,
            severity=finding["severity"],
            subject=finding["subject"][:300],
            message=finding["message"],
            note=note,
            user_id=user_id,
        )
    )


def kept_keys(db: Session, ctx: opex_svc.Context) -> set[str]:
    return set(
        db.scalars(
            select(FindingReview.finding_key)
            .join(BudgetSubmission, BudgetSubmission.id == FindingReview.submission_id)
            .where(BudgetSubmission.version_id == ctx.version.id, FindingReview.action == "KEPT")
        )
    )


def reviews(db: Session, ctx: opex_svc.Context, cc_ids: set[int] | None) -> list[dict]:
    """Registro da análise na versão em elaboração: correções feitas na página e avisos mantidos."""
    stmt = (
        select(FindingReview, BudgetSubmission)
        .join(BudgetSubmission, BudgetSubmission.id == FindingReview.submission_id)
        .where(BudgetSubmission.version_id == ctx.version.id)
        .order_by(FindingReview.created_at.desc(), FindingReview.id.desc())
    )
    if cc_ids is not None:
        stmt = stmt.where(BudgetSubmission.cost_center_id.in_(cc_ids or {-1}))
    rows = db.execute(stmt).all()
    ccs = {
        c.id: c
        for c in db.scalars(select(CostCenter).where(CostCenter.id.in_({s.cost_center_id for _, s in rows} or {-1})))
    }
    users = {u.id: u.name for u in db.scalars(select(User).where(User.id.in_({r.user_id for r, _ in rows} or {-1})))}
    out = []
    for review, sub in rows:
        cc = ccs[sub.cost_center_id]
        created = review.created_at if review.created_at.tzinfo else review.created_at.replace(tzinfo=UTC)
        out.append(
            {
                "id": review.id,
                "action": review.action,
                "action_label": ACTION_LABELS.get(review.action, review.action),
                "kind": review.kind,
                "kind_label": KIND_LABELS.get(review.kind, review.kind),
                "severity": review.severity,
                "module": sub.module,
                "module_label": MODULE_LABELS.get(sub.module, sub.module),
                "cost_center_id": cc.id,
                "cost_center": f"{cc.code} · {cc.name}",
                "subject": review.subject,
                "message": review.message,
                "note": review.note,
                "user": users.get(review.user_id),
                "created_at": created.isoformat(),
                "link": f"{ROUTES[sub.module]}/{cc.id}" if sub.module in ROUTES else None,
            }
        )
    return out


def cost_centers(db: Session, ctx: opex_svc.Context, cc_ids: set[int]) -> list[dict]:
    """CCs com apontamento ou correção, com os orçamentos OPEX e CAPEX para baixar o template corrigido."""
    subs: dict[int, dict[str, int]] = defaultdict(dict)
    for sub in db.scalars(
        select(BudgetSubmission).where(
            BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.cost_center_id.in_(cc_ids or {-1})
        )
    ):
        subs[sub.cost_center_id][sub.module] = sub.id
    out = []
    for cc in db.scalars(select(CostCenter).where(CostCenter.id.in_(cc_ids or {-1})).order_by(CostCenter.code)):
        out.append(
            {
                "id": cc.id,
                "code": cc.code,
                "label": f"{cc.code} · {cc.name}",
                "opex_submission_id": subs[cc.id].get("OPEX"),
                "capex_submission_id": subs[cc.id].get("CAPEX"),
            }
        )
    return out


def project_types(db: Session) -> list[dict]:
    return [
        {"value": lv.code, "label": lv.label}
        for lv in db.scalars(
            select(LookupValue)
            .where(LookupValue.domain == "CAPEX_PROJECT_TYPE", LookupValue.is_active)
            .order_by(LookupValue.sort_order, LookupValue.label)
        )
    ]


# ------------------------------------------------------------------ relatório Excel


def workbook(ctx: opex_svc.Context, items: list[dict], logged: list[dict]) -> bytes:
    """Relatório dos apontamentos: resumo por CC, pendentes e o que foi corrigido ou mantido (por quem e quando)."""
    wb = Workbook()
    wb.remove(wb.active)
    summary: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for i in items:
        summary[i["cost_center"]][0 if i["severity"] == "CRITICAL" else 1] += 1
    for r in logged:
        summary[r["cost_center"]][2 if r["action"] == "CORRECTED" else 3] += 1
    _sheet(
        wb,
        "Resumo",
        ["Centro de custo", "Críticos pendentes", "Avisos pendentes", "Corrigidos", "Mantidos"],
        [[cc, *counts] for cc, counts in sorted(summary.items())],
        widths={1: 46, 2: 18, 3: 18, 4: 14, 5: 14},
    )
    _sheet(
        wb,
        "Pendentes",
        ["Gravidade", "Centro de custo", "Módulo", "Situação", "Tipo", "Item", "Detalhe", "Apontamento", "Valor"],
        [
            [
                SEVERITY_LABELS[i["severity"]],
                i["cost_center"],
                i["module_label"],
                STATUS_LABELS.get(i["status"], i["status"]),
                i["kind_label"],
                i["subject"],
                i["detail"],
                i["message"],
                float(i["amount"]) if i["amount"] is not None else None,
            ]
            for i in items
        ],
        money_cols={9},
        widths={2: 40, 4: 20, 5: 28, 6: 40, 7: 30, 8: 70},
    )

    def when(iso: str) -> str:
        return datetime.fromisoformat(iso).astimezone(MANAUS).strftime("%d/%m/%Y %H:%M")

    _sheet(
        wb,
        "Corrigidos e mantidos",
        [
            "Data (Manaus)",
            "Centro de custo",
            "Módulo",
            "Tipo",
            "Item",
            "Apontamento",
            "Ação",
            "Correção ou motivo",
            "Por",
        ],
        [
            [
                when(r["created_at"]),
                r["cost_center"],
                r["module_label"],
                r["kind_label"],
                r["subject"],
                r["message"],
                r["action_label"],
                r["note"],
                r["user"],
            ]
            for r in logged
        ],
        widths={1: 17, 2: 40, 4: 28, 5: 40, 6: 60, 7: 12, 8: 60, 9: 24},
    )
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
