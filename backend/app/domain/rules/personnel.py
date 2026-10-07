"""Projeção de pessoal e what-if.

Excel (QUADRO FUNCIONARIOS, colunas JAN..DEZ):
  =IF(AÇÃO="Manter", H,
     IF(AND(AÇÃO="promover", mês>=J), L,
     IF(AND(AÇÃO="remover",  mês>=J), 0,
     IF(AND(AÇÃO="incluir",  mês>=J), L, H))))
Acrescentado pelo sistema: multiplicador por tipo de contrato (CLT 1,8; PJ sem multiplicador)
e reajuste coletivo do cenário (premissa E9 = 5% do template).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.rules.common import MONTHS, money

# Mapeamento das ações do template para os tipos de movimento do sistema
# palavras do cargo que não indicam o setor (nível, função genérica)
_POSITION_STOPWORDS = {
    "ANALISTA",
    "ASSISTENTE",
    "AUXILIAR",
    "ESPECIALISTA",
    "COORDENADOR",
    "COORDENADORA",
    "GERENTE",
    "SUPERVISOR",
    "SUPERVISORA",
    "ESTAGIARIO",
    "ESTAGIARIA",
    "TECNICO",
    "TECNICA",
    "JOVEM",
    "APRENDIZ",
    "JR",
    "PL",
    "SR",
    "DE",
    "DA",
    "DO",
    "DOS",
    "DAS",
    "E",
    "EM",
    "I",
    "II",
    "III",
    "IV",
}


def _words(text: str) -> list[str]:
    import unicodedata

    plain = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().upper()
    return [w for w in "".join(c if c.isalnum() else " " for c in plain).split() if w]


def sector_for_position(position: str | None, sectors: dict[int, str]) -> int | None:
    """Setor indicado pelo cargo ("ANALISTA CONTABIL JR" → Contabilidade; "ANALISTA FISCAL SR" → Fiscal).

    Uma palavra do cargo (fora nível e função genérica) casa com uma palavra do nome do setor quando uma é
    início da outra ("CONTABIL" × "CONTABILIDADE"). Só devolve o setor quando exatamente um casa."""
    words = [w for w in _words(position or "") if w not in _POSITION_STOPWORDS and len(w) >= 3]
    if not words:
        return None
    found = set()
    for sector_id, name in sectors.items():
        for sw in _words(name):
            if len(sw) >= 3 and any(sw.startswith(w) or w.startswith(sw) for w in words):
                found.add(sector_id)
    return found.pop() if len(found) == 1 else None


TEMPLATE_ACTIONS = {
    "MANTER": "KEEP",
    "PROMOVER": "PROMOTION",
    "INCLUIR": "HIRE",
    "REMOVER": "TERMINATION",
}


@dataclass(frozen=True)
class ContractRule:
    apply_multiplier: bool
    multiplier: Decimal


@dataclass(frozen=True)
class PositionPlan:
    """Uma posição (colaborador existente ou vaga a contratar) com no máximo um movimento no ano."""

    key: str
    base_salary: Decimal
    contract_type: str
    movement: str = "KEEP"
    effective_month: int | None = None
    new_salary: Decimal | None = None
    quantity: int = 1
    multiplier_override: Decimal | None = None
    group: Mapping[str, str] = field(default_factory=dict)  # área, departamento, cc...


@dataclass(frozen=True)
class Scenario:
    contract_rules: Mapping[str, ContractRule]
    salary_adjustment_pct: Decimal = Decimal("0")
    adjustment_month: int = 1


def monthly_salary(plan: PositionPlan) -> dict[int, Decimal]:
    movement = plan.movement
    month = plan.effective_month
    if movement != "KEEP" and (month is None or not 1 <= month <= 12):
        raise ValueError(f"{plan.key}: mês da ação obrigatório (1-12) para {movement}")
    base = Decimal(plan.base_salary or 0)
    new = Decimal(plan.new_salary if plan.new_salary is not None else base)
    result: dict[int, Decimal] = {}
    for m in MONTHS:
        if movement == "KEEP":
            value = base
        elif movement in ("PROMOTION", "SALARY_ADJUSTMENT", "TRANSFER"):
            value = new if m >= month else base
        elif movement == "TERMINATION":
            value = Decimal("0") if m >= month else base
        elif movement == "HIRE":
            value = new * plan.quantity if m >= month else Decimal("0")
        else:
            raise ValueError(f"Movimento desconhecido: {movement}")
        result[m] = value
    return result


def multiplier_for(plan: PositionPlan, scenario: Scenario) -> Decimal:
    rule = scenario.contract_rules.get(plan.contract_type)
    if rule is None:
        raise ValueError(f"Tipo de contrato sem parametrização: {plan.contract_type}")
    if not rule.apply_multiplier:
        return Decimal("1")  # PJ: valor do contrato, sem encargos
    return Decimal(plan.multiplier_override) if plan.multiplier_override is not None else rule.multiplier


def monthly_cost(plan: PositionPlan, scenario: Scenario) -> dict[int, Decimal]:
    salaries = monthly_salary(plan)
    mult = multiplier_for(plan, scenario)
    adj = Decimal(scenario.salary_adjustment_pct or 0)
    result = {}
    for m, salary in salaries.items():
        if adj and m >= scenario.adjustment_month:
            salary = salary * (1 + adj)
        result[m] = money(salary * mult)
    return result


def headcount(plan: PositionPlan) -> dict[int, int]:
    if plan.movement == "HIRE":
        return {m: (plan.quantity if m >= plan.effective_month else 0) for m in MONTHS}
    return {m: (0 if plan.movement == "TERMINATION" and m >= plan.effective_month else 1) for m in MONTHS}


@dataclass
class ProjectionSummary:
    monthly: dict[int, Decimal]
    annual: Decimal
    headcount: dict[int, int]


def project(plans: Iterable[PositionPlan], scenario: Scenario) -> ProjectionSummary:
    monthly = {m: Decimal("0") for m in MONTHS}
    hc = {m: 0 for m in MONTHS}
    for plan in plans:
        for m, v in monthly_cost(plan, scenario).items():
            monthly[m] += v
        for m, v in headcount(plan).items():
            hc[m] += v
    monthly = {m: money(v) for m, v in monthly.items()}
    return ProjectionSummary(monthly, money(sum(monthly.values())), hc)


@dataclass
class WhatIfResult:
    current_annual: Decimal
    projected_annual: Decimal
    difference: Decimal
    difference_pct: Decimal | None
    monthly_current: dict[int, Decimal]
    monthly_projected: dict[int, Decimal]
    monthly_impact: dict[int, Decimal]


def what_if(plans: list[PositionPlan], baseline: Scenario, simulated: Scenario) -> WhatIfResult:
    """Compara cenário base × simulado (ex.: multiplicador CLT 1,8 → 2,0)."""
    cur = project(plans, baseline)
    new = project(plans, simulated)
    diff = money(new.annual - cur.annual)
    pct = (diff / cur.annual).quantize(Decimal("0.0001")) if cur.annual else None
    impact = {m: money(new.monthly[m] - cur.monthly[m]) for m in MONTHS}
    return WhatIfResult(cur.annual, new.annual, diff, pct, cur.monthly, new.monthly, impact)


def terminations_summary(plans: Iterable[PositionPlan], scenario: Scenario, by: str | None = None) -> dict:
    """Desligamentos por mês (ou por dimensão `by`: area, department, cc): pessoas e economia anual."""
    result: dict = {}
    for plan in plans:
        if plan.movement != "TERMINATION":
            continue
        key = plan.group.get(by, "N/D") if by else plan.effective_month
        saving = sum(
            (money(plan.base_salary * multiplier_for(plan, scenario)) for m in MONTHS if m >= plan.effective_month),
            Decimal("0"),
        )
        bucket = result.setdefault(key, {"people": 0, "annual_saving": Decimal("0")})
        bucket["people"] += 1
        bucket["annual_saving"] = money(bucket["annual_saving"] + saving)
    return result
