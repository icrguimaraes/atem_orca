"""Painel (10/10/2026): execução do orçamento (realizado contábil × orçado no mesmo período, % executado e projeção
de fechamento só com a projeção do gestor completa) e maiores desvios por conta × CC, por CC e por conta."""

from tests import builders
from tests.test_imports import import_and_load
from tests.test_reimport_dashboard import ROWS, _setup

# orçado 2026 nos mesmos CC × conta do realizado: 40/mês (CC A, Hospedagem) e 20/mês (CC B, Telefonia)
BUDGET = [
    ROWS[0][:9] + (*[40] * 12, 480),
    ROWS[1][:9] + (*[20] * 12, 240),
]


def _with_budget(client, admin, run_worker):
    _setup(client, admin, run_worker)  # realizado 2026 até AGO: CC A 350, CC B 80
    orc = builders.realizado_wide(BUDGET, months=12)
    import_and_load(client, admin, run_worker, orc, "orc26.xlsx", dataset_type="REFERENCE_BUDGET")


def _projection(client, admin, run_worker, months, name):
    rows = [("1050101011", m, "6010301001", "Hospedagem", 25) for m in months]
    import_and_load(client, admin, run_worker, builders.projecao_base(rows), name, dataset_type="PROJECTION")


def _overview(client, admin, query="years=2026&compare=false", figures=False):
    resp = client.get(f"/api/v1/dashboard/overview?{query}{'&figures=true' if figures else ''}", headers=admin)
    assert resp.status_code == 200, resp.text[:300]
    return resp.json()


def test_execution_kpis_and_forecast_only_with_full_projection(client, admin, run_worker):
    _with_budget(client, admin, run_worker)
    ex = _overview(client, admin)["execution"]
    assert ex["available"] and ex["closed_month"] == "AGO"
    assert (ex["actual_label"], ex["budget_ytd_label"]) == ("Realizado 2026 até AGO", "Orçado 2026 até AGO")
    # realizado 430 × orçado até AGO 480 (8 × 60); orçado do ano 720
    assert (ex["actual_ytd"], ex["budget_ytd"], ex["budget_year"]) == ("430.00", "480.00", "720.00")
    assert ex["var"] == "-50.00" and ex["var_pct"] == "-0.1042"
    assert ex["execution_pct"] == "0.5972" and ex["budget_phase_pct"] == "0.6667"
    assert ex["monthly_avg"] == "53.75"
    # sem projeção do gestor: indisponível, com o motivo (nada de anualização no lugar)
    fc = ex["forecast"]
    assert fc["available"] is False and fc["value"] is None and "SET–DEZ" in fc["reason"]

    # projeção só até NOV: continua indisponível (dezembro sem valor)
    _projection(client, admin, run_worker, [9, 10, 11], "PROJETADO parcial.xlsx")
    fc = _overview(client, admin)["execution"]["forecast"]
    assert fc["available"] is False and "não cobre até DEZ" in fc["reason"], fc

    # projeção set–dez completa: fechamento = realizado contábil + projeção; o realizado até AGO não muda
    _projection(client, admin, run_worker, [9, 10, 11, 12], "PROJETADO completo.xlsx")
    data = _overview(client, admin, figures=True)
    ex = data["execution"]
    assert ex["actual_ytd"] == "430.00" and ex["closed_month"] == "AGO"
    fc = ex["forecast"]
    assert fc["available"] and fc["kind"] == "projection"
    assert (fc["value"], fc["projected"], fc["var"], fc["var_pct"]) == ("530.00", "100.00", "-190.00", "-0.2639")
    assert (fc["from_month"], fc["to_month"]) == ("SET", "DEZ")
    # coluna TOTAL do comparativo: projeção empilhada sobre o realizado, com os meses reais na legenda
    total = {t["name"]: t for t in data["figures"]["monthly_total"]["data"]}
    proj = total["Projeção set–dez (gestor)"]
    real = next(t for n, t in total.items() if n.startswith("Realizado"))
    assert proj["y"] == [100.0] and proj["base"] == [430.0] and real["y"] == [430.0]
    assert proj["offsetgroup"] == real["offsetgroup"]
    assert any(t["name"] == "Projeção set–dez (gestor)" for t in data["figures"]["monthly"]["data"])

    # filtro de meses só com meses fechados: o "fechamento" é o próprio realizado do recorte
    ex = _overview(client, admin, "years=2026&compare=false&months=1,2")["execution"]
    assert ex["actual_ytd"] == "320.00" and ex["budget_year"] == "120.00"
    assert ex["forecast"]["kind"] == "closed" and ex["forecast"]["value"] == "320.00"


def test_execution_unavailable_cases(client, admin, run_worker):
    _setup(client, admin, run_worker)
    # sem orçado do ano: indisponível com o motivo
    ex = _overview(client, admin)["execution"]
    assert ex["available"] is False and "Sem orçado 2026" in ex["reason"]
    # anos somados: a execução é de um ano só
    ex = _overview(client, admin, "years=2026,2027")["execution"]
    assert ex["available"] is False and "único ano" in ex["reason"]


def test_deviations_by_pair_cost_center_and_account(client, admin, run_worker):
    _with_budget(client, admin, run_worker)
    resp = client.get("/api/v1/dashboard/deviations?years=2026&compare=false", headers=admin)
    assert resp.status_code == 200, resp.text[:300]
    d = resp.json()
    assert d["basis"] == "execution" and d["main_label"] == "Realizado 2026 até AGO"
    assert d["base_label"] == "Orçado 2026 até AGO"
    pairs = d["groups"]["pair"]
    # maior desvio em valor absoluto primeiro: CC B (80 × 160 = -80) antes de CC A (350 × 320 = +30)
    assert [(p["cost_center"]["code"], p["var"]) for p in pairs] == [("1050101012", "-80.00"), ("1050101011", "30.00")]
    assert pairs[0]["account"]["code"] == "6010301002" and pairs[0]["var_pct"] == "-0.5000"
    assert pairs[0]["cost_center"]["company"] and pairs[0]["out_of_range"] is True
    assert pairs[1]["out_of_range"] is False  # +9,4%: dentro da faixa
    assert [r["cost_center"]["code"] for r in d["groups"]["cost_center"]] == ["1050101012", "1050101011"]
    assert d["groups"]["cost_center"][0]["account"] is None
    assert [r["account"]["code"] for r in d["groups"]["account"]] == ["6010301002", "6010301001"]

    # mesmos filtros do Painel: com o CC filtrado, só ele aparece; limite respeitado
    cc_b = pairs[0]["cost_center"]["id"]
    one = client.get(f"/api/v1/dashboard/deviations?years=2026&cost_center_id={cc_b}&limit=1", headers=admin).json()
    assert [r["cost_center"]["id"] for r in one["groups"]["pair"]] == [cc_b]

    # com a projeção carregada, o desvio continua sobre o realizado contábil (a projeção não entra)
    _projection(client, admin, run_worker, [9, 10, 11, 12], "PROJETADO.xlsx")
    again = client.get("/api/v1/dashboard/deviations?years=2026&compare=false", headers=admin).json()
    assert [p["var"] for p in again["groups"]["pair"]] == ["-80.00", "30.00"]

    # ano do ciclo com o realizado: série principal do Painel × base (orçamento 2027 × realizado 2026)
    cycle = client.get("/api/v1/dashboard/deviations?years=2026,2027", headers=admin).json()
    assert cycle["basis"] == "period" and cycle["main_series"] == "budget"
