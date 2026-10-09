"""Regras do módulo OPEX: histórico por conta, linhas do orçamento, justificativas e pendências."""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules import opex as calc
from app.domain.rules.common import MONTHS, money, normalize_months
from app.models import (
    Account,
    AccountDetail,
    AccountJustification,
    ActualEntry,
    Branch,
    BudgetCycle,
    BudgetLine,
    BudgetLineValue,
    BudgetPackage,
    BudgetSubmission,
    BudgetVersion,
    CostCenter,
    CycleParameter,
    DatasetVersion,
    LookupValue,
    ReferenceBudgetEntry,
    TravelFare,
    TravelRate,
    WorkflowEvent,
)

ZERO = Decimal("0")
OPEX_NATURES = ("OPEX", "FINANCEIRO")


class OpexError(Exception):
    """Erro de regra de negócio (mensagem pronta para o usuário)."""


@dataclass
class Context:
    cycle: BudgetCycle
    version: BudgetVersion
    prev_year: int
    ref_year: int
    target_year: int
    params: dict

    @property
    def frozen(self) -> bool:
        """Versão congelada (consolidada): só leitura; alterações exigem uma revisão (nova versão)."""
        return self.version.status != "WORKING"

    @property
    def justification_blocks(self) -> bool:
        """Justificativa faltando bloqueia o envio? Parâmetro `review.justification_blocks` (padrão: não — tudo
        continua obrigatório em Apontamentos/Justificativas, mas por enquanto nada bloqueia)."""
        value = self.params.get("review.justification_blocks", False)
        return value is True or str(value).strip().lower() in ("true", "1", "sim")

    def param(self, key: str, default):
        value = self.params.get(key, default)
        return Decimal(str(value)) if isinstance(value, (int, float, str)) else value


def context(db: Session) -> Context:
    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    if cycle is None:
        raise OpexError("Nenhum ciclo orçamentário cadastrado")
    version = db.scalar(
        select(BudgetVersion)
        .where(BudgetVersion.cycle_id == cycle.id, BudgetVersion.status == "WORKING")
        .order_by(BudgetVersion.major.desc(), BudgetVersion.minor.desc())
    )
    if version is None:  # todas congeladas: a mais recente fica disponível para consulta
        version = db.scalar(
            select(BudgetVersion)
            .where(BudgetVersion.cycle_id == cycle.id)
            .order_by(BudgetVersion.major.desc(), BudgetVersion.minor.desc())
        )
    if version is None:
        raise OpexError("O ciclo não tem versão de orçamento")
    params = {p.key: p.value for p in db.scalars(select(CycleParameter).where(CycleParameter.cycle_id == cycle.id))}
    ref = cycle.actual_reference_year
    return Context(cycle, version, ref - 1, ref, cycle.fiscal_year, params)


def get_submission(
    db: Session, ctx: Context, cost_center_id: int, *, create: bool = True, module: str = "OPEX"
) -> BudgetSubmission | None:
    sub = db.scalar(
        select(BudgetSubmission).where(
            BudgetSubmission.version_id == ctx.version.id,
            BudgetSubmission.cost_center_id == cost_center_id,
            BudgetSubmission.module == module,
        )
    )
    if sub is None and create:
        sub = BudgetSubmission(version_id=ctx.version.id, cost_center_id=cost_center_id, module=module, status="DRAFT")
        db.add(sub)
        db.flush()
    return sub


def log_event(
    db: Session,
    sub: BudgetSubmission,
    *,
    action: str,
    to_status: str,
    user_id: int | None,
    comment: str | None,
    version_label: str,
) -> None:
    db.add(
        WorkflowEvent(
            submission_id=sub.id,
            from_status=sub.status,
            to_status=to_status,
            action=action,
            comment=comment,
            version_label=version_label,
            user_id=user_id,
        )
    )
    sub.status = to_status
    now = datetime.utcnow()
    if to_status == "SUBMITTED":
        sub.submitted_at = now
    elif to_status == "APPROVED":
        sub.approved_at = now
    elif to_status == "CONSOLIDATED":
        sub.consolidated_at = now


def mark_in_progress(db: Session, sub: BudgetSubmission, ctx: Context, user_id: int | None) -> None:
    if sub.status == "DRAFT":
        log_event(
            db,
            sub,
            action="start",
            to_status="IN_PROGRESS",
            user_id=user_id,
            comment=None,
            version_label=ctx.version.label,
        )


# ------------------------------------------------------------------ histórico


def _current_sum(db: Session, model, cost_center_id: int, year: int, by_period: bool):
    cols = [model.account_id] + ([model.period] if by_period else [])
    stmt = (
        select(*cols, func.sum(model.amount))
        .join(DatasetVersion, DatasetVersion.id == model.dataset_version_id)
        .where(DatasetVersion.is_current, model.cost_center_id == cost_center_id, model.fiscal_year == year)
        .group_by(*cols)
    )
    return db.execute(stmt).all()


def closed_period(db: Session, year: int, company_code: str) -> int | None:
    """Último mês do realizado da empresa no ano. Com a projeção do gestor carregada (meses sem KSB1), o realizado
    cobre o ano todo: vale o último mês da projeção e o "anualizado" não multiplica de novo (08/10/2026)."""
    projected = db.scalar(
        select(DatasetVersion.last_closed_period).where(
            DatasetVersion.dataset_type == "PROJECTION",
            DatasetVersion.is_current,
            DatasetVersion.scope_key == f"PROJECTION:{year}:{company_code}",
        )
    )
    if projected:
        return int(projected)
    return db.scalar(
        select(DatasetVersion.last_closed_period).where(
            DatasetVersion.dataset_type == "ACTUAL",
            DatasetVersion.is_current,
            DatasetVersion.scope_key == f"ACTUAL:{year}:{company_code}",
        )
    )


def pj_monthly_by_account(db: Session, ctx: Context, cc_id: int) -> dict[int, list[Decimal]]:
    """Contratos PJ do CC por conta × mês (conta pj.budget_account), como a consolidação lança."""
    from app.services import consolidation as cons

    accounts = list(db.scalars(select(Account)))
    by_code = {a.code: a.id for a in accounts}
    out: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO] * 12)
    for _cc, code, _name, _pkg, month, amount in cons.pj_budget_amounts(db, ctx, {cc_id}, accounts, {}):
        if code in by_code:
            out[by_code[code]][month - 1] += amount
    return out


def proposed_by_account(db: Session, submission_id: int) -> dict[int, Decimal]:
    rows = db.execute(
        select(BudgetLine.account_id, func.sum(BudgetLine.total_amount))
        .where(BudgetLine.submission_id == submission_id)
        .group_by(BudgetLine.account_id)
    )
    return {a: t for a, t in rows}


def account_view(db: Session, ctx: Context, sub: BudgetSubmission) -> dict:
    """Uma linha por conta: 2025 R → 2026 R (até o mês fechado) / anualizado → 2026 O → 2027 P, com alertas."""
    cc = db.get(CostCenter, sub.cost_center_id)
    closed = closed_period(db, ctx.ref_year, cc.company.code)

    def by_month(model, year):
        out: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO] * 12)
        for acc_id, period, amount in _current_sum(db, model, cc.id, year, True):
            out[acc_id][int(period) - 1] += amount
        return out

    prev_m = by_month(ActualEntry, ctx.prev_year)
    ref_m = by_month(ActualEntry, ctx.ref_year)
    budget_m = by_month(ReferenceBudgetEntry, ctx.ref_year)
    prop_m: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO] * 12)
    for acc_id, month, amount in db.execute(
        select(BudgetLine.account_id, BudgetLineValue.month, func.sum(BudgetLineValue.amount))
        .join(BudgetLine, BudgetLine.id == BudgetLineValue.line_id)
        .where(BudgetLine.submission_id == sub.id)
        .group_by(BudgetLine.account_id, BudgetLineValue.month)
    ):
        prop_m[acc_id][int(month) - 1] += amount
    # contratos PJ do CC (09/10/2026): entram na conta pj.budget_account como valor derivado (só o total, sem o
    # contrato) — a tela do CC mostrava a conta zerada enquanto o Painel já somava
    pj_m = pj_monthly_by_account(db, ctx, cc.id)
    for acc_id, series in pj_m.items():
        for i, amount in enumerate(series):
            prop_m[acc_id][i] += amount
    prev = {k: sum(v, ZERO) for k, v in prev_m.items()}
    ref = {k: sum(v, ZERO) for k, v in ref_m.items()}
    ref_budget = {k: sum(v, ZERO) for k, v in budget_m.items()}
    proposed = proposed_by_account(db, sub.id)
    for acc_id, series in pj_m.items():
        proposed[acc_id] = proposed.get(acc_id, ZERO) + sum(series, ZERO)
    justifications = {
        j.account_id: j
        for j in db.scalars(select(AccountJustification).where(AccountJustification.submission_id == sub.id))
    }
    # justificativa escrita nas linhas do template (coluna JUSTIFICATIVA) também justifica a conta
    line_justified = set(
        db.scalars(
            select(BudgetLine.account_id).where(
                BudgetLine.submission_id == sub.id,
                func.length(func.trim(func.coalesce(BudgetLine.justification, ""))) > 0,
            )
        )
    )
    ids = set(prev) | set(ref) | set(ref_budget) | set(proposed)
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(ids)))} if ids else {}
    packages = {p.id: p for p in db.scalars(select(BudgetPackage))}

    growth = ctx.param("alert.growth_pct", 0.2)
    reduction = ctx.param("alert.reduction_pct", 0.3)
    min_relevant = ctx.param("alert.min_relevant_amount", 1000)

    rows = []
    totals = defaultdict(lambda: ZERO)
    for acc_id in ids:
        acc = accounts.get(acc_id)
        if acc is None or acc.nature not in OPEX_NATURES:
            continue
        p = prev.get(acc_id, ZERO)
        r = ref.get(acc_id, ZERO)
        annualized = (r * 12 / closed) if closed else ZERO
        b = ref_budget.get(acc_id, ZERO)
        prop = proposed.get(acc_id, ZERO)
        base = annualized or b or p  # referência para a variação: 2026 anualizado → orçado 2026 → 2025
        flags = []
        if max(base, prop) >= min_relevant:  # variações em valores irrelevantes não geram alerta
            if base == 0:
                flags.append("NEW_ACCOUNT")
            elif prop == 0:
                flags.append("NO_BUDGET")
            elif prop > base * (1 + growth):
                flags.append("GROWTH_ABOVE")
            elif prop < base * (1 - reduction):
                flags.append("REDUCTION_ABOVE")
        just = justifications.get(acc_id)
        pkg = packages.get(acc.package_id)
        # regra de 08/10/2026 ("justificar tudo"): toda conta orçada, ou com histórico relevante que foi zerado
        requires = prop > 0 or base >= min_relevant
        row = {
            "account_id": acc_id,
            "code": acc.code,
            "name": acc.name,
            "package_id": acc.package_id,
            "package": pkg.name if pkg else None,
            "package_type": pkg.package_type if pkg else None,
            "prev_actual": str(money(p)),
            "ref_actual_ytd": str(money(r)),
            "ref_annualized": str(money(annualized)),
            "ref_budget": str(money(b)),
            "proposed": str(money(prop)),
            "variation_base": str(money(base)),
            "variation_pct": str(((prop - base) / base).quantize(Decimal("0.0001"))) if base else None,
            "flags": flags,
            "needs_justification": requires,
            "justification": just.text if just else None,
            "line_justified": acc_id in line_justified,
            "ref_monthly": [str(money(v)) for v in ref_m[acc_id]] if acc_id in ref_m else None,
            # parte da conta que vem dos contratos PJ (derivada; não é linha do gestor)
            "pj_amount": str(money(sum(pj_m[acc_id], ZERO))) if acc_id in pj_m else None,
        }
        rows.append(row)
        for k in ("prev_actual", "ref_actual_ytd", "ref_annualized", "ref_budget", "proposed"):
            totals[k] += Decimal(row[k])
    rows.sort(key=lambda r: (r["package"] or "zz", r["code"]))
    included = {r["account_id"] for r in rows}

    def monthly_total(series: dict[int, list[Decimal]]) -> list[str]:
        return [str(money(sum((series[a][i] for a in included if a in series), ZERO))) for i in range(12)]

    return {
        "prev_year": ctx.prev_year,
        "ref_year": ctx.ref_year,
        "target_year": ctx.target_year,
        "closed_period": closed,
        "accounts": rows,
        "totals": {k: str(money(v)) for k, v in totals.items()},
        "monthly": {
            "prev": monthly_total(prev_m),
            "ref": monthly_total(ref_m),
            "budget": monthly_total(budget_m),
            "proposed": monthly_total(prop_m),
        },
        "pending_justifications": sum(1 for r in rows if justification_missing(r)),
    }


def justification_missing(row: dict) -> bool:
    """Conta que precisa de justificativa e não tem (nem na conta, nem nas linhas do template)."""
    return row["needs_justification"] and not (row["justification"] or "").strip() and not row["line_justified"]


def account_monthly(db: Session, ctx: Context, sub: BudgetSubmission, account_id: int) -> dict:
    def series(model, year):
        out = {m: ZERO for m in MONTHS}
        for acc, period, amount in _current_sum(db, model, sub.cost_center_id, year, True):
            if acc == account_id:
                out[int(period)] += amount
        return {m: str(money(v)) for m, v in out.items()}

    proposed = {m: ZERO for m in MONTHS}
    for value in db.scalars(
        select(BudgetLineValue)
        .join(BudgetLine, BudgetLine.id == BudgetLineValue.line_id)
        .where(BudgetLine.submission_id == sub.id, BudgetLine.account_id == account_id)
    ):
        proposed[value.month] += value.amount
    return {
        "prev_actual": series(ActualEntry, ctx.prev_year),
        "ref_actual": series(ActualEntry, ctx.ref_year),
        "ref_budget": series(ReferenceBudgetEntry, ctx.ref_year),
        "proposed": {m: str(money(v)) for m, v in proposed.items()},
    }


def submit_blockers(db: Session, ctx: Context, sub: BudgetSubmission) -> list[str]:
    blockers = []
    if not db.scalar(select(func.count()).select_from(BudgetLine).where(BudgetLine.submission_id == sub.id)):
        blockers.append("nenhuma linha orçada")
    # "justificar tudo" (08/10/2026): toda conta orçada ou zerada com histórico relevante precisa de justificativa
    if ctx.justification_blocks:
        missing = account_view(db, ctx, sub)["pending_justifications"]
        if missing:
            blockers.append(f"{missing} conta(s) sem justificativa")
    return blockers


# ------------------------------------------------------------------ linhas


def _validate_account(db: Session, account_id: int, package_id: int | None) -> Account:
    acc = db.get(Account, account_id)
    if acc is None or not acc.is_active:
        raise OpexError("Conta contábil inválida ou inativa")
    if acc.nature not in OPEX_NATURES:
        raise OpexError(f"A conta {acc.code} é de {acc.nature}; orce-a no módulo correspondente")
    if package_id and acc.package_id != package_id:
        raise OpexError(f"A conta {acc.code} não pertence a este pacote")
    return acc


def _set_values(line: BudgetLine, values: dict[int, Decimal]) -> None:
    for v in values.values():
        if v < 0:
            raise OpexError("Valores mensais não podem ser negativos")
    existing = {v.month: v for v in line.values}
    for month, amount in values.items():
        if month in existing:
            existing[month].amount = amount
        elif amount:
            line.values.append(BudgetLineValue(month=month, amount=amount))
    line.total_amount = money(sum(values.values(), ZERO))


def set_justification(db: Session, sub: BudgetSubmission, account_id: int, text: str, user_id: int | None) -> dict:
    """Grava (ou apaga, com texto vazio) a justificativa da conta. Devolve o texto anterior para a auditoria."""
    just = db.get(AccountJustification, (sub.id, account_id))
    before = {"text": just.text} if just else None
    text = (text or "").strip()
    if not text:
        if just:
            db.delete(just)
    elif just:
        just.text, just.updated_by = text, user_id
    else:
        db.add(AccountJustification(submission_id=sub.id, account_id=account_id, text=text, updated_by=user_id))
    db.flush()
    return {"before": before, "after": {"text": text}}


def set_trip_ticket(
    db: Session, sub: BudgetSubmission, group_ref: str, amount: Decimal, user_id: int | None
) -> list[BudgetLine]:
    """Passagem informada para uma viagem já lançada (ex.: importada com a tabela de tarifas vazia): cria ou
    atualiza a linha de passagem no mês de ida e tira o alerta de passagem zerada."""
    lines = list(
        db.scalars(
            select(BudgetLine).where(
                BudgetLine.submission_id == sub.id, BudgetLine.group_ref == group_ref, BudgetLine.line_type == "TRAVEL"
            )
        )
    )
    if not lines:
        raise OpexError("Viagem não encontrada")
    if amount is None or amount <= 0:
        raise OpexError("Informe o valor da passagem")
    ticket_acc = db.scalar(select(Account).where(Account.code == calc.TRAVEL_TICKET_ACCOUNT))
    if ticket_acc is None:
        raise OpexError(f"Conta de viagem {calc.TRAVEL_TICKET_ACCOUNT} não cadastrada")
    attrs = dict(lines[0].attributes or {})
    month = attrs.get("departure_month") or next((v.month for v in lines[0].values if v.amount), None)
    if not month:
        raise OpexError("Viagem sem mês de ida")
    attrs["ticket_amount"] = str(money(amount))
    attrs["warnings"] = [w for w in attrs.get("warnings") or [] if not w.startswith(("Passagem", "Tarifa de passagem"))]
    ticket = next((line for line in lines if line.account_id == ticket_acc.id), None)
    if ticket is None:
        first = lines[0]
        ticket = BudgetLine(
            submission_id=sub.id,
            company_id=first.company_id,
            branch_id=first.branch_id,
            cost_center_id=first.cost_center_id,
            account_id=ticket_acc.id,
            package_id=ticket_acc.package_id,
            line_type="TRAVEL",
            group_ref=group_ref,
            description=first.description,
            created_by=user_id,
        )
        db.add(ticket)
        lines.append(ticket)
    _set_values(ticket, {int(month): money(amount)})
    for line in lines:
        line.attributes = dict(attrs)
        line.updated_by = user_id
    db.flush()
    return lines


def _branch(db: Session, cc: CostCenter, branch_id: int | None) -> int | None:
    if branch_id is None:
        return None
    branch = db.get(Branch, branch_id)
    if branch is None or branch.company_id != cc.company_id:
        raise OpexError("Filial inválida para a empresa do centro de custo")
    return branch.id


def create_line(db: Session, ctx: Context, sub: BudgetSubmission, data: dict, user_id: int | None) -> list[BudgetLine]:
    cc = db.get(CostCenter, sub.cost_center_id)
    line_type = data.get("line_type") or "GENERIC"
    branch_id = _branch(db, cc, data.get("branch_id"))
    common = {
        "submission_id": sub.id,
        "company_id": cc.company_id,
        "branch_id": branch_id,
        "cost_center_id": cc.id,
        "description": data.get("description"),
        "justification": data.get("justification"),
        "supplier": data.get("supplier"),
        "contract_manager": data.get("contract_manager"),
        "created_by": user_id,
        "updated_by": user_id,
    }
    if line_type == "TRAVEL":
        lines = _travel_lines(db, ctx, data, common)
    elif line_type == "EVENT":
        lines = [_event_line(db, data, common)]
    else:
        acc = _validate_account(db, data["account_id"], data.get("package_id"))
        detail_id = data.get("account_detail_id")
        if detail_id:
            detail = db.get(AccountDetail, detail_id)
            if detail is None or detail.account_id != acc.id:
                raise OpexError("Detalhamento não corresponde à conta")
        line = BudgetLine(
            **common,
            account_id=acc.id,
            package_id=acc.package_id,
            account_detail_id=detail_id,
            line_type="GENERIC",
            attributes=data.get("attributes"),
        )
        _set_values(line, normalize_months(data.get("values") or {}))
        lines = [line]
    for line in lines:
        db.add(line)
    mark_in_progress(db, sub, ctx, user_id)
    db.flush()
    return lines


def _travel_lines(db: Session, ctx: Context, data: dict, common: dict) -> list[BudgetLine]:
    """Uma viagem gera até 3 linhas (passagem, diária, hospedagem) ligadas pelo mesmo group_ref."""
    t = data.get("travel") or {}
    required = ("trip_type", "job_level", "origin", "destination", "departure_month", "days")
    missing = [k for k in required if t.get(k) in (None, "")]
    if missing:
        raise OpexError("Viagem incompleta: informe tipo, cargo, origem, destino, mês de ida e nº de dias")

    def rate(kind: str) -> Decimal:
        v = db.scalar(
            select(TravelRate.daily_amount).where(
                TravelRate.cycle_id == ctx.cycle.id,
                TravelRate.rate_type == kind,
                TravelRate.trip_type == t["trip_type"],
                TravelRate.job_level == t["job_level"],
            )
        )
        return Decimal(v or 0)

    fare = db.scalar(
        select(TravelFare.round_trip_amount).where(
            TravelFare.cycle_id == ctx.cycle.id,
            TravelFare.origin == t["origin"],
            TravelFare.destination == t["destination"],
        )
    )
    manual_fare = t.get("ticket_amount")
    if manual_fare not in (None, ""):
        fare = Decimal(str(manual_fare))  # valor informado pelo gestor quando a matriz não tem a rota
    per_diem, lodging = rate("PER_DIEM"), rate("LODGING")
    result = calc.calculate_travel(
        calc.TravelInput(
            int(t["departure_month"]), int(t["return_month"]) if t.get("return_month") else None, int(t["days"])
        ),
        calc.TravelRates(fare, per_diem, lodging, ctx.param("travel.one_way_factor", 0.5)),
    )
    if int(t["days"]) > 0:
        for kind, value in (("Diária", per_diem), ("Hospedagem", lodging)):
            if value == 0:
                result.warnings.append(f"{kind} sem tarifa para {t['trip_type']} / {t['job_level']}: orçada em R$ 0")
    group = uuid.uuid4().hex[:12]
    attributes = {k: t.get(k) for k in (*required, "return_month", "purpose")} | {
        "warnings": result.warnings,
        "ticket_amount": manual_fare,
    }
    lines = []
    for code, months in result.by_account().items():
        if sum(months.values()) == 0:
            continue
        acc = db.scalar(select(Account).where(Account.code == code))
        if acc is None:
            raise OpexError(f"Conta de viagem {code} não cadastrada")
        line = BudgetLine(
            **common,
            account_id=acc.id,
            package_id=acc.package_id,
            line_type="TRAVEL",
            group_ref=group,
            attributes=attributes,
        )
        _set_values(line, months)
        lines.append(line)
    if not lines:
        raise OpexError("A viagem não gerou valores: verifique as tarifas (passagem não cadastrada e 0 dias)")
    return lines


def _event_line(db: Session, data: dict, common: dict) -> BudgetLine:
    e = data.get("event") or {}
    acc = _validate_account(db, data.get("account_id"), data.get("package_id"))
    if e.get("event_type") in (None, "") or e.get("month") in (None, ""):
        raise OpexError("Evento incompleto: informe tipo (interno/externo) e mês")
    lookup = db.scalar(
        select(LookupValue).where(LookupValue.domain == "EVENT_TYPE", LookupValue.code == e["event_type"])
    )
    if lookup is None:
        raise OpexError("Tipo de evento inválido")
    meal = Decimal(str((lookup.extra or {}).get("meal_per_person", 0)))
    num = lambda k: Decimal(str(e.get(k) or 0))  # noqa: E731
    total, months = calc.calculate_event(
        calc.EventInput(
            int(e["month"]),
            int(e.get("people") or 0),
            meal,
            num("graphic_material"),
            num("structure"),
            num("gifts"),
            num("transport"),
        )
    )
    line = BudgetLine(
        **common,
        account_id=acc.id,
        package_id=acc.package_id,
        line_type="EVENT",
        attributes=e | {"meal_per_person": str(meal)},
    )
    _set_values(line, months)
    return line


def update_line(db: Session, ctx: Context, line: BudgetLine, data: dict, user_id: int | None) -> BudgetLine:
    if line.line_type in ("TRAVEL", "EVENT") and ("values" in data or "account_id" in data):
        raise OpexError("Linhas calculadas (viagem/evento) não são editadas mês a mês: exclua e lance novamente")
    if "account_id" in data and data["account_id"] != line.account_id:
        acc = _validate_account(db, data["account_id"], line.package_id)
        line.account_id = acc.id
        if line.account_detail_id and data.get("account_detail_id") is None:
            line.account_detail_id = None  # o detalhamento era da conta anterior
    cc = db.get(CostCenter, line.cost_center_id)
    if "branch_id" in data:
        line.branch_id = _branch(db, cc, data["branch_id"])
    for key in ("description", "justification", "supplier", "contract_manager", "account_detail_id", "attributes"):
        if key in data:
            setattr(line, key, data[key])
    if "values" in data:
        # atualização parcial: só os meses enviados mudam
        current = {v.month: v.amount for v in line.values}
        incoming = {int(m): money(v) for m, v in data["values"].items()}
        if any(m not in MONTHS for m in incoming):
            raise OpexError("Mês inválido")
        _set_values(line, {m: incoming.get(m, current.get(m, ZERO)) for m in MONTHS})
    line.updated_by = user_id
    db.flush()
    return line


def move_lines(
    db: Session, ctx: Context, line: BudgetLine, target_cc: CostCenter, reason: str, user_id: int | None
) -> tuple[BudgetSubmission, list[BudgetLine]]:
    """Leva o lançamento (viagem: as 3 contas) para o orçamento OPEX de outro CC da mesma empresa — correção de CC
    lançado errado na planilha, sem reimportar. Guarda a origem em `attributes.moved_from`."""
    from app.domain.workflow import EDITABLE, STATUS_LABELS

    if ctx.frozen:
        raise OpexError(f"Versão {ctx.version.label} congelada")
    if not reason.strip():
        raise OpexError("Informe o motivo da mudança de centro de custo")
    if target_cc.id == line.cost_center_id:
        raise OpexError("O lançamento já está neste centro de custo")
    if target_cc.company_id != line.company_id:
        raise OpexError("O centro de custo de destino precisa ser da mesma empresa do lançamento")
    if not target_cc.is_active:
        raise OpexError(f"Centro de custo {target_cc.code} inativo")
    target = get_submission(db, ctx, target_cc.id)
    if target.status not in EDITABLE:
        raise OpexError(
            f"O orçamento de {target_cc.code} está '{STATUS_LABELS.get(target.status, target.status)}' e não recebe "
            "lançamentos; peça a devolução para ajuste"
        )
    source = db.get(CostCenter, line.cost_center_id)
    moved = group_lines(db, line)
    for item in moved:
        attrs = dict(item.attributes or {})
        attrs["moved_from"] = {
            "cost_center": source.code,
            "submission_id": item.submission_id,
            "reason": reason.strip(),
        }
        item.attributes = attrs
        item.submission_id, item.cost_center_id, item.updated_by = target.id, target_cc.id, user_id
    db.flush()
    return target, moved


def group_lines(db: Session, line: BudgetLine) -> list[BudgetLine]:
    if not line.group_ref:
        return [line]
    return list(
        db.scalars(
            select(BudgetLine).where(
                BudgetLine.submission_id == line.submission_id, BudgetLine.group_ref == line.group_ref
            )
        )
    )


def line_out(line: BudgetLine) -> dict:
    values = {m: ZERO for m in MONTHS}
    for v in line.values:
        values[v.month] = v.amount
    return {
        "id": line.id,
        "account_id": line.account_id,
        "package_id": line.package_id,
        "branch_id": line.branch_id,
        "account_detail_id": line.account_detail_id,
        "line_type": line.line_type,
        "group_ref": line.group_ref,
        "description": line.description,
        "justification": line.justification,
        "supplier": line.supplier,
        "contract_manager": line.contract_manager,
        "attributes": line.attributes,
        "values": {m: str(money(v)) for m, v in values.items()},
        "total": str(money(line.total_amount)),
        "updated_at": line.updated_at.isoformat() if line.updated_at else None,
    }
