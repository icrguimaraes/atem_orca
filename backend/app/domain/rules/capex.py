"""Regras do template CAPEX."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.domain.rules.common import money, total

TOLERANCE = Decimal("0.01")


@dataclass
class Issue:
    code: str
    severity: str  # CRITICAL | WARNING
    message: str


@dataclass
class CapexItemCheck:
    total_value: Decimal
    schedule_total: Decimal
    difference: Decimal
    issues: list[Issue] = field(default_factory=list)

    @property
    def is_consistent(self) -> bool:
        return not any(i.severity == "CRITICAL" for i in self.issues)


def brl(value: Decimal) -> str:
    """R$ 1.234,56 (mensagens para o usuário)."""
    text = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{'-' if value < 0 else ''}R$ {text}"


def item_total(unit_value: Decimal, quantity: Decimal) -> Decimal:
    """Excel: VLR TOTAL = VLR UNIT × QTD."""
    return money(Decimal(unit_value) * Decimal(quantity))


def check_item(
    unit_value: Decimal,
    quantity: Decimal,
    schedule: Mapping[int, Decimal],
    *,
    min_unit_value: Decimal = Decimal("1200"),
    useful_life_months: int | None = None,
    min_useful_life_months: int = 12,
) -> CapexItemCheck:
    """Excel: Check = IF(SUM(meses) - VLR TOTAL = 0, "ok", "diferença - verificar").

    No sistema a divergência é CRÍTICA (bloqueia envio/aprovação). Os requisitos da aba
    Instruções (valor > R$ 1.200, vida útil > 12 meses) viram alertas de enquadramento.
    """
    value = item_total(unit_value, quantity)
    scheduled = total(schedule)
    diff = money(scheduled - value)
    issues: list[Issue] = []
    if quantity <= 0 or unit_value <= 0:
        issues.append(Issue("CAPEX_INVALID_VALUE", "CRITICAL", "Valor unitário e quantidade devem ser positivos"))
    if abs(diff) > TOLERANCE:
        issues.append(
            Issue(
                "CAPEX_SCHEDULE_MISMATCH",
                "CRITICAL",
                f"Cronograma ({brl(scheduled)}) difere do valor total ({brl(value)}) em {brl(diff)}",
            )
        )
    if 0 < unit_value <= min_unit_value:
        issues.append(
            Issue(
                "CAPEX_BELOW_MIN_VALUE",
                "WARNING",
                f"Valor unitário até {brl(Decimal(min_unit_value))}: avaliar se é OPEX (bem de pequeno valor)",
            )
        )
    if useful_life_months is not None and useful_life_months <= min_useful_life_months:
        issues.append(
            Issue("CAPEX_SHORT_LIFE", "WARNING", f"Vida útil ≤ {min_useful_life_months} meses não caracteriza CAPEX")
        )
    return CapexItemCheck(value, scheduled, diff, issues)


def check_project(is_project: bool, project_type: str | None, justification: str | None) -> list[Issue]:
    """Instrução: se 'Projeto?' = Sim → tipo de projeto e justificativa obrigatórios."""
    issues: list[Issue] = []
    if not (justification or "").strip():
        issues.append(Issue("CAPEX_NO_JUSTIFICATION", "CRITICAL" if is_project else "WARNING", "Justificativa ausente"))
    if is_project and not project_type:
        issues.append(Issue("CAPEX_NO_PROJECT_TYPE", "CRITICAL", "Projeto sem tipo de projeto"))
    return issues
