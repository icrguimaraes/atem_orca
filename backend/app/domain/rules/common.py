from collections.abc import Iterable, Mapping
from decimal import ROUND_HALF_UP, Decimal

MONTHS = tuple(range(1, 13))
MONTH_LABELS = ("JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ")
CENT = Decimal("0.01")


def money(value: Decimal | float | int | str | None) -> Decimal:
    if value is None or value == "":
        return Decimal("0.00")
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def month_from_label(value: object) -> int | None:
    """Aceita 1..12, 'JAN'..'DEZ', 'janeiro', datas. Retorna None se inválido."""
    if value is None or value == "":
        return None
    if hasattr(value, "month"):
        return int(value.month)  # date/datetime
    text = str(value).strip().upper()
    if text.isdigit() or (text.replace(".", "", 1).isdigit() and float(text).is_integer()):
        number = int(float(text))
        return number if 1 <= number <= 12 else None
    prefix = text[:3]
    return MONTH_LABELS.index(prefix) + 1 if prefix in MONTH_LABELS else None


def empty_months() -> dict[int, Decimal]:
    return {m: Decimal("0.00") for m in MONTHS}


def normalize_months(values: Mapping[int, object] | Iterable[object] | None) -> dict[int, Decimal]:
    """Aceita dict {mês: valor} ou lista de 12 valores."""
    result = empty_months()
    if values is None:
        return result
    items = values.items() if isinstance(values, Mapping) else enumerate(values, start=1)
    for month, amount in items:
        month = int(month)
        if month not in result:
            raise ValueError(f"Mês inválido: {month}")
        result[month] = money(amount)
    return result


def total(values: Mapping[int, Decimal]) -> Decimal:
    """Excel: =SUM(JAN:DEZ)."""
    return money(sum(values.values(), Decimal("0")))


def budget_key(company_code: str, branch_code: str | None, cost_center_code: str, account_code: str) -> str:
    """Excel: =CONCATENATE("1001-", filial, "-", cc, "-", conta). Empresa vem do CC, não é fixa."""
    return f"{company_code}-{branch_code or ''}-{cost_center_code}-{account_code}"
