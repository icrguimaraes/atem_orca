"""Regras dos contratos PJ, sem banco: CNPJ, situação, tempo de casa e bonificação proporcional.

A regra da bonificação (`bonus_months` / `bonus_due`) foi confirmada pelo dono do produto em 08/10/2026 (admissão
"livre no mês"); fica toda aqui para ser trocada num lugar só (a tela mostra a fórmula para conferência).
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

ACTIVE, ENDED = "ACTIVE", "ENDED"
CENTS = Decimal("0.01")
# CNPJ alfanumérico (IN RFB 2.229/2024, a partir de 07/2026): 12 posições [0-9A-Z] + 2 dígitos verificadores
_CNPJ_RE = re.compile(r"^[0-9A-Z]{12}[0-9]{2}$")
_WEIGHTS_1 = (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_WEIGHTS_2 = (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2)


class PjError(Exception):
    """Dado inválido do contrato PJ (mensagem pronta para o usuário)."""


# ---------------------------------------------------------------- CNPJ


def _check_digit(chars: str, weights: tuple[int, ...]) -> str:
    # valor de cada posição = código ASCII − 48 (dígitos 0–9; letras A=17 … Z=42), módulo 11
    total = sum((ord(c) - 48) * w for c, w in zip(chars, weights, strict=True))
    rest = total % 11
    return "0" if rest < 2 else str(11 - rest)


def cnpj_check_digits(base: str) -> str:
    """Os dois dígitos verificadores das 12 primeiras posições."""
    base = base.upper()
    first = _check_digit(base, _WEIGHTS_1)
    return first + _check_digit(base + first, _WEIGHTS_2)


def normalize_cnpj(value: str | None) -> str:
    """Texto digitado (com ou sem máscara) → 14 posições sem pontuação; `PjError` se inválido.

    Não há bloqueio de CNPJ duplicado (decisão do handoff): a mesma empresa pode ter mais de um contrato.
    """
    text = re.sub(r"[\s./-]", "", str(value or "")).upper()
    if not _CNPJ_RE.match(text) or len(set(text)) == 1 or cnpj_check_digits(text[:12]) != text[12:]:
        raise PjError("CNPJ inválido")
    return text


def format_cnpj(value: str | None) -> str | None:
    """00.000.000/0000-00"""
    if not value or len(value) != 14:
        return value
    return f"{value[:2]}.{value[2:5]}.{value[5:8]}/{value[8:12]}-{value[12:]}"


# ---------------------------------------------------------------- situação e tempo de casa


def status(end_date: date | None, today: date) -> str:
    """Encerrado depois do dia do término; até lá (e sem término), ativo."""
    return ENDED if end_date is not None and end_date < today else ACTIVE


def validate_dates(start: date | None, end: date | None) -> None:
    if start is None:
        raise PjError("Informe a data de admissão")
    if end is not None and end < start:
        raise PjError("A data de término não pode ser anterior à admissão")


@dataclass(frozen=True)
class Tenure:
    years: int
    months: int


def tenure(start: date, until: date) -> Tenure:
    """Meses completos de `start` até `until` (no término, se encerrado), em anos e meses."""
    total = (until.year - start.year) * 12 + (until.month - start.month) - (1 if until.day < start.day else 0)
    total = max(total, 0)
    return Tenure(total // 12, total % 12)


# ---------------------------------------------------------------- bonificação


def bonus_months(start: date, end: date | None, year: int) -> int:
    """Meses do ano de referência que dão direito à bonificação (0 a 12).

    - admissão antes do ano: conta desde janeiro;
    - admissão no ano: conta desde o mês da admissão, em qualquer dia ("livre no mês", decisão de 08/10/2026);
    - término no ano: conta até o mês do término (inclusive); término antes do ano: 0;
    - admissão depois do ano: 0.
    """
    if start.year > year or (end is not None and end.year < year):
        return 0
    first = 1 if start.year < year else start.month
    last = end.month if end is not None and end.year == year else 12
    return max(0, min(12, last - first + 1))


def bonus_due(annual_bonus: Decimal | None, months: int) -> Decimal:
    """Bonificação anual × meses ÷ 12, arredondada aos centavos (meio para cima)."""
    if not annual_bonus or months <= 0:
        return Decimal("0.00")
    return (Decimal(annual_bonus) * months / 12).quantize(CENTS, rounding=ROUND_HALF_UP)
