from decimal import Decimal

from app.services.analytics import fmt_compact, fmt_pct
from tests import builders
from tests.test_consolidation import _setup
from tests.test_imports import import_and_load
from tests.test_opex import CC

REF_BUDGET_2026 = [  # orçado 2026 (orçamento de referência): mesma conta/CC do realizado
    ("1001", "0001", "MANAUS", CC, "CC A", "G", "6010301002", "Telefonia", "DTI", *[400] * 12, 4800),
    ("1001", "0001", "MANAUS", "1050101012", "CC B", "G", "6010301001", "Hospedagem", "Viagens", *[250] * 12, 3000),
]


def test_formatting_pt_br():
    assert fmt_compact(1_234_567) == "R$ 1,2 mi"
    assert fmt_compact(850_000) == "R$ 850 mil"
    assert fmt_compact(125_400) == "R$ 125,4 mil"
    assert fmt_compact(980) == "R$ 980"
    assert fmt_compact(-2_500_000_000) == "-R$ 2,5 bi"
    assert fmt_pct(Decimal("0.125")) == "+12,5%" and fmt_pct(Decimal("-0.082")) == "-8,2%" and fmt_pct(None) == "—"


def test_dashboard_matches_process_numbers_and_filters(client, admin, run_worker):
    cc, _, _ = _setup(client, admin, run_worker)
    import_and_load(
        client,
        admin,
        run_worker,
        builders.realizado_wide(REF_BUDGET_2026, months=12),
        "orc26.xlsx",
        dataset_type="REFERENCE_BUDGET",
    )
    ov = client.get("/api/v1/consolidation/overview", headers=admin).json()
    d = client.get("/api/v1/analytics/dashboard", headers=admin).json()
    k = d["kpis"]
    # mesmos números da consolidação (OPEX 5.400 + CAPEX 50.000 + Pessoal 469.350)
    assert k["target"]["value"] == ov["total"] == "524750.00"
    assert (
        k["opex"]["value"] == "5400.00" and k["capex"]["value"] == "50000.00" and k["personnel"]["value"] == "469350.00"
    )
    assert k["ref_budget"]["value"] == "7800.00"  # 4.800 + 3.000 orçados em 2026
    assert k["var_ref"]["value"] == "516950.00" and k["var_ref"]["pct_label"].startswith("+")
    assert k["prev_actual"]["value"] == "0.00" and d["has"]["prev"] is False  # sem realizado 2025 carregado
    # composição soma o total; mensal soma o total (sem dupla contagem, sem meses perdidos)
    comp = d["figures"]["composition"]["data"][0]
    assert round(sum(comp["x"]), 2) == 524750.00 and d["figures"]["composition"]["meta"]["total"] == "R$ 524.750,00"
    monthly = {t["name"]: t for t in d["figures"]["monthly"]["data"]}
    assert round(sum(monthly["Orçamento 2027"]["y"]), 2) == 524750.00 and len(monthly["Orçamento 2027"]["x"]) == 12
    assert monthly["Orçamento 2027"]["y"][3] >= 50000  # CAPEX em abril
    assert monthly["Orçado 2026"]["y"][0] == 650.0
    # ranking: conta de salários no topo, rótulo pt-BR
    rank = d["figures"]["ranking"]["data"][0]
    assert rank["y"][-1] == "Salários e ordenados" and rank["text"][-1] == "R$ 267,8 mil"
    # status: 1 CC preenchido de 2 ativos no escopo
    assert d["status"]["total"] == 2 and d["status"]["filled"] == 1 and k["filled_pct"]["compact"] == "50,0%"

    # filtro por módulo: só CAPEX
    dm = client.get("/api/v1/analytics/dashboard?module=CAPEX", headers=admin).json()
    assert dm["kpis"]["target"]["value"] == "50000.00" and dm["kpis"]["ref_budget"]["value"] == "0.00"
    # filtro por CC sem lançamentos: zera o alvo mas mantém o orçado 2026 dele
    other = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101012")
    dc = client.get(f"/api/v1/analytics/dashboard?cost_center_id={other['id']}", headers=admin).json()
    assert dc["kpis"]["target"]["value"] == "0.00" and dc["kpis"]["ref_budget"]["value"] == "3000.00"
    # filtro por conta + dimensão mês (drill-down final)
    da = client.get("/api/v1/analytics/dashboard?account=6010301002&dimension=account", headers=admin).json()
    assert da["kpis"]["target"]["value"] == "5400.00" and da["kpis"]["ref_budget"]["value"] == "4800.00"
    # variação em %: Telefonia 5.400 × 4.800 = +12,5%
    dv = client.get("/api/v1/analytics/dashboard?variation_mode=pct&variation_by=account", headers=admin).json()
    var = dv["figures"]["variation"]["data"][0]
    idx = var["y"].index("Telefonia, Links e Internet")
    assert var["text"][idx] == "+12,5%"
    # gestor vê só o seu CC
    from tests.test_opex import _user

    mgr_id, mgr = _user(client, admin, "gestor@t.com", ["MANAGER"])
    client.patch(f"/api/v1/cost-centers/{other['id']}", headers=admin, json={"manager_user_id": mgr_id})
    dg = client.get("/api/v1/analytics/dashboard", headers=mgr).json()
    assert dg["kpis"]["target"]["value"] == "0.00" and dg["status"]["total"] == 1
    assert client.get("/api/v1/analytics/options", headers=mgr).json()["cost_centers"][0]["id"] == other["id"]


def test_analytics_series_selection(client, admin, run_worker):
    """Análise: o usuário escolhe o que comparar (realizado de qual ano, anualizado, orçado); cards e figuras seguem."""
    from tests import builders
    from tests.test_imports import import_and_load

    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t-series.xlsx")
    opts = client.get("/api/v1/analytics/options", headers=admin).json()
    assert opts["series"] and opts["default_series"]["prev"].startswith("actual:")
    d = client.get("/api/v1/analytics/dashboard", headers=admin).json()
    assert d["kpis"]["prev_actual"]["label"].startswith("Realizado")
    ann = next((o["key"] for o in opts["series"] if o["key"].startswith("actual_ann")), None)
    if ann:
        d2 = client.get(f"/api/v1/analytics/dashboard?prev={ann}", headers=admin).json()
        assert "anualizado" in d2["kpis"]["prev_actual"]["label"]
        assert float(d2["kpis"]["prev_actual"]["value"]) >= float(d["kpis"]["prev_actual"]["value"])
    none = client.get("/api/v1/analytics/dashboard?prev=none&ref=none", headers=admin).json()
    assert none["kpis"]["prev_actual"]["value"] == "0.00" and none["has"]["prev"] is False
    assert len(none["figures"]["monthly"]["data"]) == 1  # só o orçamento
