"""Regras do módulo Pessoal: quadro por CC, movimentações, projeção mensal e cenários (what-if).

Cada colaborador ativo tem no máximo uma movimentação no ano (como no template: uma AÇÃO por linha).
- Manter: sem registro (salário atual o ano todo).
- Promover / Reajustar: novo salário a partir do mês da ação.
- Desligar: custo até o mês anterior; verba rescisória opcional no mês do desligamento.
- Transferir: sai do CC de origem no mês e entra no CC de destino com o salário informado (ou o atual).
- Contratação (vaga): movimento sem colaborador, com cargo, quantidade, salário e mês de entrada.

Custo mensal = salário × (1 + reajuste do cenário a partir da data-base) × multiplicador do contrato
(CLT 1,8 como proxy de encargos e benefícios; PJ sem multiplicador). Ver domain/rules/personnel.py.
"""

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules import personnel as pr
from app.domain.rules.common import MONTHS, money
from app.domain.workflow import EDITABLE, STATUS_LABELS
from app.models import (
    Account,
    ActualEntry,
    BenefitType,
    BudgetSubmission,
    ContractType,
    CostCenter,
    DatasetVersion,
    Employee,
    EmployeeBenefit,
    JobPosition,
    PersonnelMovement,
    PersonnelScenario,
    ScenarioMultiplier,
)
from app.services.opex import Context, OpexError, closed_period

ZERO = Decimal("0")
MODULE = "PERSONNEL"
EMPLOYEE_MOVES = ("PROMOTION", "SALARY_ADJUSTMENT", "TERMINATION", "TRANSFER", "HIRE")
MOVE_LABELS = {
    "KEEP": "Manter",
    "PROMOTION": "Promover",
    "SALARY_ADJUSTMENT": "Reajuste individual",
    "TERMINATION": "Desligar",
    "TRANSFER": "Transferir",
    "HIRE": "Admissão no mês",
}


class PersonnelError(OpexError):
    """Erro de regra de negócio do módulo Pessoal."""


def month_pending(mv: PersonnelMovement | None) -> bool:
    """Ação importada sem mês (pendência em Apontamentos): não mexe no custo até o mês ser informado."""
    return mv is not None and "month" in ((mv.attributes or {}).get("pending") or [])


# ------------------------------------------------------------------ cenários


@dataclass
class ScenarioInfo:
    id: int | None
    name: str
    salary_adjustment_pct: Decimal
    adjustment_month: int
    multipliers: dict[str, Decimal]
    rules: pr.Scenario
    # abono anual por colaborador (R$/ano em 12 parcelas, sem multiplicador) nos tipos de contrato listados
    annual_bonus: Decimal = ZERO
    bonus_contracts: tuple[str, ...] = ("CLT",)


def contract_types(db: Session, *, active_only: bool = False) -> dict[str, ContractType]:
    """Todos os tipos por padrão: colaboradores já cadastrados podem ter contrato inativado depois."""
    stmt = select(ContractType)
    if active_only:
        stmt = stmt.where(ContractType.is_active)
    return {c.code: c for c in db.scalars(stmt)}


def scenario_info(
    db: Session,
    row: PersonnelScenario | None,
    *,
    multipliers: dict[str, Decimal] | None = None,
    salary_adjustment_pct: Decimal | None = None,
    adjustment_month: int | None = None,
    name: str | None = None,
) -> ScenarioInfo:
    contracts = contract_types(db)
    stored = {}
    if row is not None:
        stored = {
            m.contract_type_code: Decimal(m.multiplier)
            for m in db.scalars(select(ScenarioMultiplier).where(ScenarioMultiplier.scenario_id == row.id))
        }
    mults = {code: stored.get(code, Decimal(c.default_multiplier)) for code, c in contracts.items()}
    for code, value in (multipliers or {}).items():
        if code not in contracts:
            raise PersonnelError(f"Tipo de contrato não parametrizado: {code}")
        if Decimal(value) <= 0:
            raise PersonnelError("Multiplicador deve ser positivo")
        mults[code] = Decimal(value)
    adj = Decimal(
        salary_adjustment_pct
        if salary_adjustment_pct is not None
        else (row.salary_adjustment_pct if row is not None else 0)
    )
    month = int(adjustment_month or (row.adjustment_month if row is not None else 1))
    if not 1 <= month <= 12:
        raise PersonnelError("Mês da data-base deve estar entre 1 e 12")
    rules = pr.Scenario(
        {code: pr.ContractRule(c.apply_multiplier, mults[code]) for code, c in contracts.items()}, adj, month
    )
    return ScenarioInfo(row.id if row else None, name or (row.name if row else "Base"), adj, month, mults, rules)


def baseline(db: Session, ctx: Context) -> ScenarioInfo:
    row = db.scalar(
        select(PersonnelScenario).where(PersonnelScenario.cycle_id == ctx.cycle.id, PersonnelScenario.is_baseline)
    )
    return with_cycle_bonus(scenario_info(db, row), ctx)


def with_cycle_bonus(info: ScenarioInfo, ctx: Context) -> ScenarioInfo:
    """Abono anual do CLT (parâmetro `personnel.annual_bonus_clt`) no cenário."""
    raw = ctx.params.get("personnel.annual_bonus_clt", 0)
    try:
        bonus = Decimal(str(raw or 0))
    except ArithmeticError:
        bonus = ZERO
    return dataclasses.replace(info, annual_bonus=max(bonus, ZERO))


# ------------------------------------------------------------------ quadro e projeção


@dataclass
class Position:
    kind: str  # EMPLOYEE | HIRE | TRANSFER_IN
    cost_center_id: int
    plan: pr.PositionPlan
    employee: Employee | None = None
    movement: PersonnelMovement | None = None
    severance: Decimal = ZERO
    extra: dict = field(default_factory=dict)


def submissions_by_cc(db: Session, ctx: Context) -> dict[int, BudgetSubmission]:
    return {
        s.cost_center_id: s
        for s in db.scalars(
            select(BudgetSubmission).where(
                BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.module == MODULE
            )
        )
    }


def build_positions(db: Session, ctx: Context, cc_ids: set[int] | None = None) -> dict[int, list[Position]]:
    """Posições (colaboradores, vagas e transferências recebidas) por CC, com a movimentação do ano."""
    subs = submissions_by_cc(db, ctx)
    sub_cc = {s.id: cc for cc, s in subs.items()}
    movements = list(
        db.scalars(select(PersonnelMovement).where(PersonnelMovement.submission_id.in_(list(sub_cc) or [-1])))
    )
    by_employee = {m.employee_id: m for m in movements if m.employee_id}
    positions_names = {p.id: p.name for p in db.scalars(select(JobPosition))}
    out: dict[int, list[Position]] = defaultdict(list)

    active_ccs = select(CostCenter.id).where(CostCenter.is_active)
    stmt = select(Employee).where(Employee.is_active, Employee.cost_center_id.in_(active_ccs))
    if cc_ids is not None:
        moved_out = {
            m.employee_id for m in movements if m.movement_type == "TRANSFER" and m.target_cost_center_id in cc_ids
        }
        stmt = stmt.where(Employee.cost_center_id.in_(cc_ids) | Employee.id.in_(moved_out or {-1}))
    for emp in db.scalars(stmt.order_by(Employee.name)):
        mv = by_employee.get(emp.id)
        if mv is not None and sub_cc.get(mv.submission_id) != emp.cost_center_id:
            mv = None  # colaborador mudou de CC na base: a movimentação antiga não se aplica
        base = Decimal(emp.base_salary or 0)
        extra = {"position": positions_names.get(emp.position_id)}
        if mv is None:
            plan = pr.PositionPlan(f"E{emp.id}", base, emp.contract_type_code)
        elif mv.movement_type == "TRANSFER":
            plan = pr.PositionPlan(f"E{emp.id}", base, emp.contract_type_code, "TERMINATION", mv.effective_month)
            if cc_ids is None or mv.target_cost_center_id in cc_ids:
                incoming = pr.PositionPlan(
                    f"T{emp.id}",
                    ZERO,
                    mv.contract_type_code or emp.contract_type_code,
                    "HIRE",
                    mv.effective_month,
                    Decimal(mv.new_salary) if mv.new_salary else base,
                )
                out[mv.target_cost_center_id].append(
                    Position(
                        "TRANSFER_IN",
                        mv.target_cost_center_id,
                        incoming,
                        emp,
                        mv,
                        extra=extra | {"from_cost_center_id": emp.cost_center_id},
                    )
                )
        elif month_pending(mv):  # mês pendente: segue como está (salário atual o ano todo)
            plan = pr.PositionPlan(f"E{emp.id}", base, mv.contract_type_code or emp.contract_type_code)
        else:
            plan = pr.PositionPlan(
                f"E{emp.id}",
                base,
                mv.contract_type_code or emp.contract_type_code,
                mv.movement_type,
                mv.effective_month,
                Decimal(mv.new_salary) if mv.new_salary is not None else None,
                multiplier_override=mv.multiplier_override,
            )
            if mv.position_id:
                extra["new_position"] = positions_names.get(mv.position_id)
        if cc_ids is None or emp.cost_center_id in cc_ids:
            severance = Decimal(mv.severance_cost or 0) if mv and mv.movement_type == "TERMINATION" else ZERO
            out[emp.cost_center_id].append(Position("EMPLOYEE", emp.cost_center_id, plan, emp, mv, severance, extra))

    for mv in movements:
        if mv.movement_type != "HIRE":
            continue
        cc = sub_cc.get(mv.submission_id)
        if cc is None or (cc_ids is not None and cc not in cc_ids):
            continue
        plan = pr.PositionPlan(
            f"H{mv.id}",
            ZERO,
            mv.contract_type_code or "CLT",
            "HIRE",
            mv.effective_month,
            Decimal(mv.new_salary or 0),
            quantity=mv.quantity or 1,
            multiplier_override=mv.multiplier_override,
        )
        attrs = mv.attributes or {}
        out[cc].append(
            Position(
                "HIRE",
                cc,
                plan,
                None,
                mv,
                extra={"position": positions_names.get(mv.position_id) or attrs.get("position_name")},
            )
        )
    return out


@dataclass
class Totals:
    monthly: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)
    salary: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)
    headcount: list[int] = field(default_factory=lambda: [0] * 12)
    severance: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)
    bonus: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)  # abono anual do CLT, por mês
    hires: int = 0
    terminations: int = 0
    transfers_out: int = 0
    transfers_in: int = 0
    promotions: int = 0

    @property
    def annual(self) -> Decimal:
        return money(sum(self.monthly, ZERO))

    def add(self, other: "Totals") -> None:
        for i in range(12):
            self.monthly[i] += other.monthly[i]
            self.salary[i] += other.salary[i]
            self.headcount[i] += other.headcount[i]
            self.severance[i] += other.severance[i]
            self.bonus[i] += other.bonus[i]
        for key in ("hires", "terminations", "transfers_out", "transfers_in", "promotions"):
            setattr(self, key, getattr(self, key) + getattr(other, key))


def position_bonus(pos: Position, scenario: ScenarioInfo) -> list[Decimal]:
    """Abono anual da posição por mês: 1/12 do valor anual por pessoa ativa no mês (só nos contratos elegíveis)."""
    if not scenario.annual_bonus or pos.plan.contract_type not in scenario.bonus_contracts:
        return [ZERO] * 12
    hc = pr.headcount(pos.plan)
    # parcelas pelo acumulado (12 meses ativos somam exatamente o valor anual, sem sobra de centavos)
    out, cum, paid = [], 0, ZERO
    for m in MONTHS:
        cum += hc[m]
        due = money(scenario.annual_bonus * cum / 12)
        out.append(due - paid)
        paid = due
    return out


def position_cost(pos: Position, scenario: ScenarioInfo) -> tuple[list[Decimal], list[Decimal], list[int]]:
    """Custo mensal (com verba rescisória no mês do desligamento e o abono anual do CLT), salário mensal (com
    reajuste, sem multiplicador) e headcount da posição."""
    cost = dict(pr.monthly_cost(pos.plan, scenario.rules))
    if pos.severance:
        cost[pos.plan.effective_month] += pos.severance
    for m, bonus in zip(MONTHS, position_bonus(pos, scenario), strict=True):
        cost[m] += bonus
    salary = pr.monthly_salary(pos.plan)
    adj = scenario.salary_adjustment_pct
    salary_adj = [money(salary[m] * (1 + adj) if adj and m >= scenario.adjustment_month else salary[m]) for m in MONTHS]
    hc = pr.headcount(pos.plan)
    return [cost[m] for m in MONTHS], salary_adj, [hc[m] for m in MONTHS]


def totals_for(positions: list[Position], scenario: ScenarioInfo) -> Totals:
    t = Totals()
    for pos in positions:
        cost, salary, hc = position_cost(pos, scenario)
        for i in range(12):
            t.monthly[i] += cost[i]
            t.salary[i] += salary[i]
            t.headcount[i] += hc[i]
        if pos.severance:
            t.severance[pos.plan.effective_month - 1] += pos.severance
        for i, bonus in enumerate(position_bonus(pos, scenario)):
            t.bonus[i] += bonus
        move = pos.movement.movement_type if pos.movement else "KEEP"
        if pos.kind == "HIRE":
            t.hires += pos.plan.quantity
        elif pos.kind == "TRANSFER_IN":
            t.transfers_in += 1
        elif move == "TERMINATION":
            t.terminations += 1
        elif move == "TRANSFER":
            t.transfers_out += 1
        elif move in ("PROMOTION", "SALARY_ADJUSTMENT"):
            t.promotions += 1
        elif move == "HIRE":
            t.hires += 1  # colaborador já cadastrado com admissão prevista no ano
    return t


def totals_out(t: Totals) -> dict:
    charges = [money(t.monthly[i] - t.salary[i] - t.severance[i] - t.bonus[i]) for i in range(12)]
    return {
        "monthly": [str(money(v)) for v in t.monthly],
        "salary_monthly": [str(money(v)) for v in t.salary],
        "charges_monthly": [str(v) for v in charges],
        "headcount": t.headcount,
        "annual": str(t.annual),
        "salary_total": str(money(sum(t.salary, ZERO))),
        "charges_total": str(money(sum(charges, ZERO))),
        "severance_total": str(money(sum(t.severance, ZERO))),
        "bonus_monthly": [str(money(v)) for v in t.bonus],
        "bonus_total": str(money(sum(t.bonus, ZERO))),
        "headcount_start": t.headcount[0],
        "headcount_end": t.headcount[11],
        "hires": t.hires,
        "terminations": t.terminations,
        "transfers_out": t.transfers_out,
        "transfers_in": t.transfers_in,
        "promotions": t.promotions,
    }


def personnel_actual(db: Session, ctx: Context, cc_ids: set[int] | None) -> dict[int, dict[str, Decimal]]:
    """Realizado de pessoal (contas de natureza PESSOAL) por CC: ano anterior e ano de referência anualizado."""
    out: dict[int, dict[str, Decimal]] = defaultdict(lambda: {"prev": ZERO, "ref_ytd": ZERO, "ref_annualized": ZERO})
    for year, key in ((ctx.prev_year, "prev"), (ctx.ref_year, "ref_ytd")):
        stmt = (
            select(ActualEntry.cost_center_id, func.sum(ActualEntry.amount))
            .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
            .join(Account, Account.id == ActualEntry.account_id)
            .where(DatasetVersion.is_current, ActualEntry.fiscal_year == year, Account.nature == "PESSOAL")
            .group_by(ActualEntry.cost_center_id)
        )
        if cc_ids is not None:
            stmt = stmt.where(ActualEntry.cost_center_id.in_(cc_ids or {-1}))
        for cc, amount in db.execute(stmt):
            out[cc][key] += amount or ZERO
    closed_cache: dict[int, int | None] = {}
    for cc_id, values in out.items():
        cc = db.get(CostCenter, cc_id)
        if cc.company_id not in closed_cache:
            closed_cache[cc.company_id] = closed_period(db, ctx.ref_year, cc.company.code)
        closed = closed_cache[cc.company_id]
        values["ref_annualized"] = money(values["ref_ytd"] * 12 / closed) if closed else ZERO
    return out


def position_out(pos: Position, scenario: ScenarioInfo, cc_names: dict[int, str]) -> dict:
    cost, _salary, hc = position_cost(pos, scenario)
    mv = pos.movement
    emp = pos.employee
    attrs = (mv.attributes if mv else None) or {}
    movement = None
    if mv is not None:
        movement = {
            "id": mv.id,
            "type": mv.movement_type,
            "label": MOVE_LABELS.get(mv.movement_type, mv.movement_type),
            "month": None if month_pending(mv) else mv.effective_month,
            "pending": attrs.get("pending") or [],
            "new_salary": None if mv.new_salary is None else str(mv.new_salary),
            "new_position": pos.extra.get("new_position"),
            "target_cost_center_id": mv.target_cost_center_id,
            "target_cost_center": cc_names.get(mv.target_cost_center_id),
            "severance_cost": None if mv.severance_cost is None else str(mv.severance_cost),
            "contract_type_code": mv.contract_type_code,
            "quantity": mv.quantity,
            "reason": mv.reason,
            "source": attrs.get("source", "SYSTEM"),
        }
    return {
        "kind": pos.kind,
        "key": pos.plan.key,
        "employee_id": emp.id if emp else None,
        "registration": emp.registration if emp else None,
        "name": emp.name if emp else (attrs.get("position_name") or pos.extra.get("position") or "Vaga"),
        "position": pos.extra.get("position"),
        "contract_type_code": pos.plan.contract_type,
        "base_salary": str(money(emp.base_salary)) if emp else None,
        "movement": movement,
        "from_cost_center": cc_names.get(pos.extra.get("from_cost_center_id")),
        "monthly": [str(v) for v in cost],
        "headcount": hc,
        "severance": str(money(pos.severance)),
        "annual": str(money(sum(cost, ZERO))),
    }


def benefits_summary(db: Session, employee_ids: list[int]) -> list[dict]:
    if not employee_ids:
        return []
    names = {b.code: b.name for b in db.scalars(select(BenefitType))}
    rows = db.execute(
        select(EmployeeBenefit.benefit_code, func.count())
        .where(EmployeeBenefit.employee_id.in_(employee_ids))
        .group_by(EmployeeBenefit.benefit_code)
    )
    return [{"code": code, "name": names.get(code, code), "employees": n} for code, n in rows]


def submission_view(db: Session, ctx: Context, sub: BudgetSubmission) -> dict:
    scenario = baseline(db, ctx)
    positions = build_positions(db, ctx, {sub.cost_center_id}).get(sub.cost_center_id, [])
    cc_names = {c.id: f"{c.code} · {c.name}" for c in db.scalars(select(CostCenter))}
    totals = totals_for(positions, scenario)
    actual = personnel_actual(db, ctx, {sub.cost_center_id}).get(sub.cost_center_id) or {
        "prev": ZERO,
        "ref_ytd": ZERO,
        "ref_annualized": ZERO,
    }
    by_contract: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for pos in positions:
        by_contract[pos.plan.contract_type] += sum(position_cost(pos, scenario)[0], ZERO)
    employee_ids = [p.employee.id for p in positions if p.employee and p.kind == "EMPLOYEE"]
    return {
        "prev_year": ctx.prev_year,
        "ref_year": ctx.ref_year,
        "target_year": ctx.target_year,
        "scenario": scenario_out(scenario),
        "positions": [position_out(p, scenario, cc_names) for p in positions],
        "totals": totals_out(totals),
        "actual": {k: str(money(v)) for k, v in actual.items()},
        "by_contract": [
            {"label": k, "total": str(money(v))} for k, v in sorted(by_contract.items(), key=lambda kv: -kv[1])
        ],
        "benefits": benefits_summary(db, employee_ids),
    }


def scenario_out(s: ScenarioInfo) -> dict:
    return {
        "id": s.id,
        "name": s.name,
        "salary_adjustment_pct": str(s.salary_adjustment_pct),
        "adjustment_month": s.adjustment_month,
        "multipliers": {k: str(v) for k, v in s.multipliers.items()},
        "ignored_multiplier_for": sorted(c for c, r in s.rules.contract_rules.items() if not r.apply_multiplier),
    }


# ------------------------------------------------------------------ edição


def _position_id(db: Session, name: str | None) -> int | None:
    name = (name or "").strip()
    if not name:
        return None
    pos = db.scalar(select(JobPosition).where(func.upper(JobPosition.name) == name.upper()))
    if pos is None:
        pos = JobPosition(name=name)
        db.add(pos)
        db.flush()
    return pos.id


def _month(value) -> int:
    try:
        month = int(value)
    except (TypeError, ValueError):
        raise PersonnelError("Informe o mês da ação (1 a 12)") from None
    if not 1 <= month <= 12:
        raise PersonnelError("Mês da ação deve estar entre 1 e 12")
    return month


def _money(value, label: str, *, required: bool = False) -> Decimal | None:
    if value in (None, ""):
        if required:
            raise PersonnelError(f"Informe {label}")
        return None
    amount = money(value)
    if amount < 0 or (required and amount == 0):
        raise PersonnelError(f"{label.capitalize()} deve ser positivo")
    return amount


def set_employee_movement(
    db: Session,
    sub: BudgetSubmission,
    employee: Employee,
    data: dict,
    user_id: int | None,
    *,
    allowed_targets: set[int] | None = None,
) -> PersonnelMovement | None:
    """Define (ou limpa, com KEEP) a movimentação do colaborador no ano."""
    if employee.cost_center_id != sub.cost_center_id or not employee.is_active:
        raise PersonnelError("Colaborador não pertence ao quadro ativo deste centro de custo")
    current = db.scalar(
        select(PersonnelMovement).where(
            PersonnelMovement.submission_id == sub.id, PersonnelMovement.employee_id == employee.id
        )
    )
    kind = data.get("type") or "KEEP"
    if kind == "KEEP":
        if current is not None:
            db.delete(current)
        return None
    if kind not in EMPLOYEE_MOVES:
        raise PersonnelError("Ação inválida")
    # importação: ação sem mês fica pendente (Apontamentos); o mês gravado é só marcador, o cálculo ignora a ação
    no_month = bool(data.get("pending_ok")) and data.get("month") in (None, "") and kind in pr.MONTH_PENDING_ACTIONS
    month = 12 if no_month else _month(data.get("month"))
    new_salary = None
    target = None
    severance = None
    if kind in ("PROMOTION", "SALARY_ADJUSTMENT"):
        if data.get("pending_ok") and data.get("new_salary") in (None, ""):
            new_salary = None  # importação: promoção sem novo salário fica pendente (sem aumento até informar)
        else:
            new_salary = _money(data.get("new_salary"), "o novo salário", required=True)
            if new_salary == Decimal(employee.base_salary or 0):
                raise PersonnelError("O novo salário é igual ao atual")
    elif kind == "TERMINATION":
        severance = _money(data.get("severance_cost"), "a verba rescisória")
    elif kind == "TRANSFER":
        target = data.get("target_cost_center_id")
        cc = db.get(CostCenter, target) if target else None
        if cc is None or not cc.is_active:
            raise PersonnelError("Informe o centro de custo de destino")
        if cc.id == sub.cost_center_id:
            raise PersonnelError("O destino deve ser outro centro de custo")
        if allowed_targets is not None and cc.id not in allowed_targets:
            raise PersonnelError("Você só pode transferir para centros de custo que gerencia")
        target_sub = db.scalar(
            select(BudgetSubmission).where(
                BudgetSubmission.version_id == sub.version_id,
                BudgetSubmission.cost_center_id == cc.id,
                BudgetSubmission.module == MODULE,
            )
        )
        if target_sub is not None and target_sub.status not in EDITABLE:
            raise PersonnelError(
                f"O orçamento de pessoal de {cc.code} está '{STATUS_LABELS.get(target_sub.status, target_sub.status)}' "
                "e não aceita transferências; peça à Controladoria para devolvê-lo para ajuste"
            )
        new_salary = _money(data.get("new_salary"), "o salário no destino")
    elif kind == "HIRE":
        new_salary = _money(data.get("new_salary"), "o salário de admissão")
    mv = current or PersonnelMovement(
        submission_id=sub.id, employee_id=employee.id, cost_center_id=sub.cost_center_id, created_by=user_id
    )
    mv.movement_type = kind
    mv.effective_month = month
    mv.new_salary = new_salary
    mv.target_cost_center_id = target
    mv.severance_cost = severance
    mv.position_id = _position_id(db, data.get("new_position")) if kind == "PROMOTION" else None
    mv.reason = (data.get("reason") or "").strip() or None
    mv.quantity = 1
    mv.updated_by = user_id
    attrs = (mv.attributes or {}) | {"source": (data.get("source") or (mv.attributes or {}).get("source") or "SYSTEM")}
    attrs.pop("pending", None)
    pending = ["new_salary"] if kind in ("PROMOTION", "SALARY_ADJUSTMENT") and new_salary is None else []
    if no_month:
        pending.append("month")
    if pending:
        attrs["pending"] = pending
    mv.attributes = attrs
    if current is None:
        db.add(mv)
    db.flush()
    return mv


def save_hire(
    db: Session, sub: BudgetSubmission, data: dict, user_id: int | None, mv: PersonnelMovement | None = None
) -> PersonnelMovement:
    contracts = contract_types(db, active_only=True)
    position_name = (data.get("position_name") or "").strip()
    if not position_name:
        raise PersonnelError("Informe o cargo da vaga")
    contract = data.get("contract_type_code") or "CLT"
    if contract not in contracts:
        raise PersonnelError(f"Tipo de contrato não parametrizado: {contract}")
    quantity = int(data.get("quantity") or 1)
    if quantity < 1:
        raise PersonnelError("Quantidade deve ser pelo menos 1")
    mv = mv or PersonnelMovement(submission_id=sub.id, cost_center_id=sub.cost_center_id, created_by=user_id)
    mv.movement_type = "HIRE"
    mv.effective_month = _month(data.get("month"))
    salary_pending = bool(data.get("pending_ok")) and data.get("new_salary") in (None, "")
    mv.new_salary = None if salary_pending else _money(data.get("new_salary"), "o salário", required=True)
    mv.quantity = quantity
    mv.contract_type_code = contract
    mv.position_id = _position_id(db, position_name)
    mv.reason = (data.get("reason") or "").strip() or None
    mv.updated_by = user_id
    mv.attributes = (mv.attributes or {}) | {
        "position_name": position_name,
        "source": data.get("source") or (mv.attributes or {}).get("source") or "SYSTEM",
    }
    mv.attributes.pop("pending", None)
    if salary_pending:  # importação: vaga sem salário fica sem custo até informar (Apontamentos)
        mv.attributes = mv.attributes | {"pending": ["new_salary"]}
    if mv.id is None:
        db.add(mv)
    db.flush()
    return mv


def blockers(db: Session, ctx: Context, sub: BudgetSubmission) -> list[str]:
    """Contratações e desligamentos precisam de justificativa para o envio (análise da Controladoria/RH)."""
    out = []
    missing = [label for _, label in missing_reasons(db, sub)] if ctx.justification_blocks else []
    if missing:
        extra = f" e mais {len(missing) - 5}" if len(missing) > 5 else ""
        out.append(f"justificativa obrigatória: {', '.join(missing[:5])}{extra}")
    salaries = [label for _, label in pending_salaries(db, sub)]
    if salaries:
        extra = f" e mais {len(salaries) - 5}" if len(salaries) > 5 else ""
        out.append(f"novo salário pendente: {', '.join(salaries[:5])}{extra}")
    months = [label for _, label in pending_months(db, sub)]
    if months:
        extra = f" e mais {len(months) - 5}" if len(months) > 5 else ""
        out.append(f"mês da ação pendente: {', '.join(months[:5])}{extra}")
    return out


def pending_months(db: Session, sub: BudgetSubmission) -> list[tuple[PersonnelMovement, str]]:
    """Ações que vieram da planilha sem mês (ex.: REMOVER sem mês): sem efeito no custo até informar."""
    out = []
    for mv in db.scalars(
        select(PersonnelMovement)
        .where(PersonnelMovement.submission_id == sub.id, PersonnelMovement.employee_id.is_not(None))
        .order_by(PersonnelMovement.id)
    ):
        if not month_pending(mv):
            continue
        emp = db.get(Employee, mv.employee_id)
        if emp is None or emp.cost_center_id != sub.cost_center_id:
            continue
        out.append((mv, f"{pr.ACTION_NOUNS.get(mv.movement_type, mv.movement_type.lower())} de {emp.name}"))
    return out


def pending_salaries(db: Session, sub: BudgetSubmission) -> list[tuple[PersonnelMovement, str]]:
    """Promoções, reajustes e vagas sem salário (vieram assim da planilha): sem aumento/custo até informar."""
    out = []
    for mv in db.scalars(
        select(PersonnelMovement).where(
            PersonnelMovement.submission_id == sub.id,
            PersonnelMovement.movement_type.in_(("PROMOTION", "SALARY_ADJUSTMENT", "HIRE")),
            PersonnelMovement.new_salary.is_(None),
        )
    ):
        if mv.movement_type == "HIRE":
            if mv.employee_id is None:
                out.append((mv, f"vaga de {(mv.attributes or {}).get('position_name') or 'cargo não informado'}"))
            continue
        emp = db.get(Employee, mv.employee_id) if mv.employee_id else None
        if emp is None or emp.cost_center_id != sub.cost_center_id:
            continue
        noun = {"PROMOTION": "promoção", "SALARY_ADJUSTMENT": "reajuste individual"}[mv.movement_type]
        out.append((mv, f"{noun} de {emp.name}"))
    return out


def pending_cost_centers(db: Session, sub: BudgetSubmission) -> list[Employee]:
    """Colaboradores do CC cujo centro de custo veio vazio na planilha e foi definido pelo cargo (confirmar)."""
    return list(
        db.scalars(
            select(Employee)
            .where(
                Employee.cost_center_id == sub.cost_center_id,
                Employee.is_active,
                Employee.attributes.has_key("pending_cc"),
            )
            .order_by(Employee.name)
        )
    )


# movimentações que precisam de justificativa ("justificar tudo", 08/10/2026: promoção e reajuste também)
REASON_REQUIRED = ("HIRE", "TERMINATION", "TRANSFER", "PROMOTION", "SALARY_ADJUSTMENT")
REASON_NOUNS = {"PROMOTION": "promoção", "SALARY_ADJUSTMENT": "reajuste individual"}


def missing_reasons(db: Session, sub: BudgetSubmission) -> list[tuple[PersonnelMovement, str]]:
    """Movimentações (contratação, desligamento, transferência, promoção, reajuste) sem justificativa, com o rótulo
    ("desligamento de Fulano")."""
    missing = []
    for mv in db.scalars(select(PersonnelMovement).where(PersonnelMovement.submission_id == sub.id)):
        if mv.employee_id:
            emp = db.get(Employee, mv.employee_id)
            if emp is None or emp.cost_center_id != sub.cost_center_id:
                continue  # colaborador mudou de CC na base: a movimentação não aparece nem bloqueia
        if mv.movement_type in REASON_REQUIRED and not (mv.reason or "").strip():
            who = (mv.attributes or {}).get("position_name") if mv.movement_type == "HIRE" else None
            if who is None and mv.employee_id:
                emp = db.get(Employee, mv.employee_id)
                who = emp.name if emp else f"#{mv.employee_id}"
            label = (
                "contratação"
                if mv.movement_type == "HIRE"
                else REASON_NOUNS.get(mv.movement_type, MOVE_LABELS[mv.movement_type].lower())
            )
            missing.append((mv, f"{label} de {who}"))
    return missing
