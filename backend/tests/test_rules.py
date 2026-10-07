from decimal import Decimal as D

import pytest

from app.domain.rules import capex, opex
from app.domain.rules import personnel as pr
from app.domain.rules.common import budget_key, month_from_label, normalize_months, total


@pytest.fixture(autouse=True)
def fresh_db():  # regras puras não precisam de banco
    yield


RATES = opex.TravelRates(round_trip_fare=D("1800"), per_diem_daily=D("150"), lodging_daily=D("900"))


def test_travel_round_trip_all_in_departure_month():
    r = opex.calculate_travel(opex.TravelInput(departure_month=3, return_month=3, days=4), RATES)
    assert (r.ticket, r.per_diem, r.lodging, r.total) == (D("1800.00"), D("600.00"), D("3600.00"), D("6000.00"))
    by_acc = r.by_account()
    assert by_acc[opex.TRAVEL_TICKET_ACCOUNT][3] == D("1800.00")
    assert sum(by_acc[opex.TRAVEL_LODGING_ACCOUNT].values()) == D("3600.00")
    assert by_acc[opex.TRAVEL_PER_DIEM_ACCOUNT][4] == 0


def test_travel_one_way_is_half_fare():
    r = opex.calculate_travel(opex.TravelInput(5, None, 2), RATES)
    assert r.ticket == D("900.00")


def test_travel_missing_fare_warns():
    rates = opex.TravelRates(None, D("150"), D("600"))
    r = opex.calculate_travel(opex.TravelInput(1, 2, 1), rates)
    assert r.ticket == 0 and r.warnings


def test_travel_without_fare_between_places_warns():
    assert opex.missing_fare_warning("AM", "SP", D("0")) == (
        "Passagem orçada em R$ 0: a planilha não trouxe tarifa para AM → SP"
    )
    assert opex.missing_fare_warning("AM", "SP", D("1800")) is None
    assert opex.missing_fare_warning("AM", "am", D("0")) is None  # viagem local: sem passagem
    assert opex.missing_fare_warning(None, "SP", D("0")) is None


def test_capex_classification_against_catalog_and_software():
    software = ("1020701002", "Licenças e Software")
    equipment = ("1020601005", "Equipamentos de Informática")
    mismatch = capex.check_classification("NOTEBOOK", software, equipment)
    assert [(i.code, i.severity) for i in mismatch] == [("CAPEX_ACCOUNT_MISMATCH", "WARNING")]
    assert "1020601005 (Equipamentos de Informática)" in mismatch[0].message
    assert [i.code for i in capex.check_classification("QIVE", software)] == ["CAPEX_SOFTWARE"]
    assert capex.check_classification("NOTEBOOK", equipment, equipment) == []
    assert capex.check_classification("X", None) == []


def test_sector_for_position():
    from app.domain.rules.personnel import sector_for_position

    sectors = {1: "Contabilidade", 2: "Controladoria", 3: "Custos", 4: "Fiscal", 5: "CSC", 6: "Diretoria"}
    assert sector_for_position("ANALISTA CONTABIL JR", sectors) == 1
    assert sector_for_position("Assistente Contábil Sr", sectors) == 1
    assert sector_for_position("ESPECIALISTA DE CONTROLADORIA", sectors) == 2
    assert sector_for_position("ANALISTA DE CUSTOS PL", sectors) == 3
    assert sector_for_position("ANALISTA FISCAL SR", sectors) == 4
    assert sector_for_position("ANALISTA DE CSC SR", sectors) == 5
    assert sector_for_position("ANALISTA", sectors) is None  # sem pista de setor
    assert sector_for_position(None, sectors) is None


def test_event_meal_by_type_and_month():
    total_value, months = opex.calculate_event(opex.EventInput(6, 50, D("120"), structure=D("1000"), gifts=D("500")))
    assert total_value == D("7500.00")
    assert months[6] == D("7500.00") and sum(months.values()) == D("7500.00")


def test_capex_total_and_schedule_mismatch_is_critical():
    ok = capex.check_item(D("2500"), D("4"), {1: D("5000"), 6: D("5000")})
    assert ok.total_value == D("10000.00") and ok.is_consistent
    bad = capex.check_item(D("2500"), D("4"), {1: D("5000")})
    assert not bad.is_consistent
    assert [i.code for i in bad.issues] == ["CAPEX_SCHEDULE_MISMATCH"]


def test_capex_low_value_and_project_rules():
    check = capex.check_item(D("900"), D("1"), {1: D("900")}, useful_life_months=6)
    assert {i.code for i in check.issues} == {"CAPEX_BELOW_MIN_VALUE", "CAPEX_SHORT_LIFE"}
    assert check.is_consistent  # avisos não bloqueiam
    issues = capex.check_project(True, None, "")
    assert {i.code for i in issues} == {"CAPEX_NO_JUSTIFICATION", "CAPEX_NO_PROJECT_TYPE"}


@pytest.mark.parametrize(
    ("movement", "month", "new", "expected_jan", "expected_dec"),
    [
        ("KEEP", None, None, 10000, 10000),
        ("PROMOTION", 7, 10500, 10000, 10500),
        ("TERMINATION", 4, None, 10000, 0),
        ("HIRE", 3, 8000, 0, 8000),
    ],
)
def test_personnel_template_formula(movement, month, new, expected_jan, expected_dec):
    plan = pr.PositionPlan(
        "x", D("10000") if movement != "HIRE" else D("0"), "CLT", movement, month, None if new is None else D(new)
    )
    salaries = pr.monthly_salary(plan)
    assert salaries[1] == expected_jan and salaries[12] == expected_dec
    if month:
        assert salaries[month - 1] != salaries[month] or movement == "KEEP"


def test_multiplier_applies_to_clt_not_pj():
    rules = {"CLT": pr.ContractRule(True, D("1.8")), "PJ": pr.ContractRule(False, D("1"))}
    scenario = pr.Scenario(rules)
    clt = pr.PositionPlan("a", D("10000"), "CLT")
    pj = pr.PositionPlan("b", D("10000"), "PJ")
    assert pr.monthly_cost(clt, scenario)[1] == D("18000.00")
    assert pr.monthly_cost(pj, scenario)[1] == D("10000.00")


def test_what_if_multiplier_change_ignores_pj():
    base = pr.Scenario({"CLT": pr.ContractRule(True, D("1.8")), "PJ": pr.ContractRule(False, D("1"))})
    sim = pr.Scenario({"CLT": pr.ContractRule(True, D("2.0")), "PJ": pr.ContractRule(False, D("1"))})
    plans = [pr.PositionPlan("a", D("10000"), "CLT"), pr.PositionPlan("b", D("5000"), "PJ")]
    r = pr.what_if(plans, base, sim)
    assert r.current_annual == D("276000.00")  # (18000 + 5000) × 12
    assert r.projected_annual == D("300000.00")  # (20000 + 5000) × 12
    assert r.difference == D("24000.00") and r.monthly_impact[1] == D("2000.00")
    assert r.difference_pct == D("0.0870")


def test_salary_adjustment_from_base_month_and_headcount():
    rules = {"CLT": pr.ContractRule(True, D("1"))}
    scenario = pr.Scenario(rules, salary_adjustment_pct=D("0.05"), adjustment_month=5)
    plans = [
        pr.PositionPlan("a", D("1000"), "CLT"),
        pr.PositionPlan("b", D("1000"), "CLT", "TERMINATION", 3, group={"area": "TI"}),
        pr.PositionPlan("v", D("0"), "CLT", "HIRE", 10, D("2000"), quantity=2),
    ]
    proj = pr.project(plans, scenario)
    assert proj.monthly[4] == D("1000.00") and proj.monthly[5] == D("1050.00")
    assert proj.headcount[1] == 2 and proj.headcount[3] == 1 and proj.headcount[10] == 3
    by_area = pr.terminations_summary(plans, scenario, by="area")
    assert by_area["TI"] == {"people": 1, "annual_saving": D("10000.00")}


def test_common_helpers():
    assert month_from_label("fev") == 2 and month_from_label(7) == 7 and month_from_label("13") is None
    assert total(normalize_months([1, 2, 3])) == D("6.00")
    assert budget_key("2001", "0001", "123", "6010301001") == "2001-0001-123-6010301001"
    with pytest.raises(ValueError):
        normalize_months({13: 1})


def test_month_header_variants():
    from datetime import datetime

    from app.imports.parsers.common import parse_month_header

    assert parse_month_header(datetime(2026, 3, 1)) == (2026, 3)
    assert parse_month_header("2026-03-01 00:00:00") == (2026, 3)
    assert parse_month_header("01/03/2026") == (2026, 3)
    assert parse_month_header("mar/26") == (2026, 3)
    assert parse_month_header("Março") == (None, 3)
    assert parse_month_header("Total") is None
