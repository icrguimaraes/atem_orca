"""Regras puras dos contratos PJ (sem banco): CNPJ, situação, tempo de casa e bonificação proporcional."""

from datetime import date
from decimal import Decimal

import pytest

from app.domain.rules import pj as R


def test_cnpj_check_digits_and_normalization():
    assert R.cnpj_check_digits("112223330001") == "81"  # exemplo clássico 11.222.333/0001-81
    assert R.normalize_cnpj("11.222.333/0001-81") == "11222333000181"
    assert R.normalize_cnpj(" 11222333000181 ") == "11222333000181"
    assert R.format_cnpj("11222333000181") == "11.222.333/0001-81"
    # CNPJ alfanumérico (a partir de 07/2026): exemplo da Receita 12.ABC.345/01DE-35
    assert R.normalize_cnpj("12.abc.345/01de-35") == "12ABC34501DE35"
    assert R.format_cnpj("12ABC34501DE35") == "12.ABC.345/01DE-35"


@pytest.mark.parametrize(
    "value",
    ["11.222.333/0001-82", "1122233300018", "112223330001811", "00000000000000", "11111111111111", "abc"],
)
def test_invalid_cnpj(value):
    with pytest.raises(R.PjError, match="CNPJ inválido"):
        R.normalize_cnpj(value)


def test_empty_cnpj_is_not_an_error():
    # nada é obrigatório: CNPJ vazio vira pendência, não "CNPJ inválido"
    assert R.normalize_cnpj("") is None
    assert R.normalize_cnpj(None) is None
    assert R.normalize_cnpj(" ./- ") is None


def test_missing_fields():
    full = {
        "name": "P",
        "company_name": "E",
        "cnpj": "11222333000181",
        "role": "Consultor",
        "cost_center_id": 1,
        "monthly_value": Decimal("0"),  # zero é valor informado
        "start_date": date(2026, 1, 1),
    }
    assert R.missing_fields(full) == []
    assert R.missing_fields(full | {"cnpj": None, "monthly_value": None, "name": "  "}) == [
        "Nome da pessoa",
        "CNPJ",
        "Valor mensal",
    ]
    assert R.missing_fields({}) == [label for _, label in R.IMPORTANT_FIELDS]


def test_status_and_dates():
    today = date(2026, 10, 8)
    assert R.status(None, today) == R.ACTIVE
    assert R.status(date(2026, 10, 8), today) == R.ACTIVE  # o dia do término ainda é ativo
    assert R.status(date(2026, 10, 7), today) == R.ENDED
    R.validate_dates(date(2026, 1, 1), date(2026, 1, 1))
    with pytest.raises(R.PjError, match="anterior à admissão"):
        R.validate_dates(date(2026, 5, 1), date(2026, 4, 30))
    # admissão vazia é pendência, não erro (com ou sem término)
    R.validate_dates(None, None)
    R.validate_dates(None, date(2026, 1, 1))
    assert R.bonus_months(None, None, 2026) == 0


def test_tenure():
    assert R.tenure(date(2024, 3, 10), date(2026, 10, 8)) == R.Tenure(2, 6)  # dia 8 < 10: 6 meses completos
    assert R.tenure(date(2024, 3, 10), date(2026, 10, 10)) == R.Tenure(2, 7)
    assert R.tenure(date(2026, 10, 1), date(2026, 10, 8)) == R.Tenure(0, 0)
    assert R.tenure(date(2027, 1, 1), date(2026, 10, 8)) == R.Tenure(0, 0)  # admissão futura


@pytest.mark.parametrize(
    ("start", "end", "months"),
    [
        (date(2020, 6, 20), None, 12),  # admissão antes do ano: 12
        (date(2026, 1, 1), None, 12),
        (date(2026, 3, 1), None, 10),  # admissão "livre no mês": o mês da admissão sempre conta
        (date(2026, 3, 15), None, 10),
        (date(2026, 3, 31), None, 10),  # mesmo no último dia do mês
        (date(2026, 12, 20), None, 1),  # dezembro conta 1
        (date(2026, 12, 1), None, 1),
        (date(2024, 1, 1), date(2026, 6, 30), 6),  # encerrado no meio do ano: até o mês do término
        (date(2026, 3, 10), date(2026, 8, 5), 6),  # março a agosto
        (date(2026, 3, 20), date(2026, 3, 31), 1),  # entrou e saiu no mesmo mês: 1
        (date(2024, 1, 1), date(2025, 12, 31), 0),  # encerrado antes do ano
        (date(2027, 1, 1), None, 0),  # admissão depois do ano
        (date(2025, 1, 1), date(2027, 6, 1), 12),  # término depois do ano: limite de 12
    ],
)
def test_bonus_months(start, end, months):
    assert R.bonus_months(start, end, 2026) == months


def test_bonus_due_rounding():
    assert R.bonus_due(Decimal("12000.00"), 12) == Decimal("12000.00")
    assert R.bonus_due(Decimal("12000.00"), 9) == Decimal("9000.00")
    assert R.bonus_due(Decimal("10000.00"), 7) == Decimal("5833.33")  # 5833,333…
    assert R.bonus_due(Decimal("1000.00"), 1) == Decimal("83.33")
    assert R.bonus_due(Decimal("0.06"), 1) == Decimal("0.01")  # 0,005 → meio para cima
    assert R.bonus_due(None, 12) == Decimal("0.00")
    assert R.bonus_due(Decimal("5000.00"), 0) == Decimal("0.00")
