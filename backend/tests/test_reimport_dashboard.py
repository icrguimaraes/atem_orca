from decimal import Decimal

from sqlalchemy import func, select

from app.models import ActualEntry, DatasetVersion
from tests import builders
from tests.conftest import login
from tests.test_imports import import_and_load, status, upload

ROWS = [
    (
        "1001",
        "0001",
        "MANAUS",
        "1050101011",
        "CC A",
        "G",
        "6010301001",
        "Hospedagem",
        "Viagens",
        100,
        200,
        None,
        None,
        None,
        None,
        None,
        50,
        350,
    ),
    (
        "1001",
        "0001",
        "MANAUS",
        "1050101012",
        "CC B",
        "G",
        "6010301002",
        "Telefonia",
        "DTI",
        10,
        10,
        10,
        10,
        10,
        10,
        10,
        10,
        80,
    ),
]


def _current_total(db, year=2026):
    db.expire_all()
    return db.scalar(
        select(func.sum(ActualEntry.amount))
        .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
        .where(DatasetVersion.is_current, ActualEntry.fiscal_year == year)
    )


def _setup(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    import_and_load(client, admin, run_worker, builders.realizado_wide(ROWS), "r2026.xlsx")


def test_same_file_is_blocked_until_forced(client, admin, run_worker, db):
    _setup(client, admin, run_worker)
    batch_id = upload(client, admin, builders.realizado_wide(ROWS), "r2026.xlsx")
    run_worker()
    batch = status(client, admin, batch_id)
    comparison = batch["summary"]["comparison"]
    assert comparison["no_changes"] and comparison["scopes"][0]["unchanged"] == 2
    assert batch["summary"]["same_file_imported_in"]
    resp = client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin)
    assert resp.status_code == 409 and "já foi importado" in resp.json()["detail"]
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin, params={"force": True}).status_code == 200
    run_worker()
    assert _current_total(db) == Decimal("430.00")  # forçar não duplica: só a versão vigente soma


def test_master_reimport_detects_unchanged(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    batch_id = upload(client, admin, builders.opex_template_bd(), "bd_copia.xlsx", dataset_type="MASTER_DATA")
    run_worker()
    comparison = status(client, admin, batch_id)["summary"]["comparison"]
    assert comparison["no_changes"]
    assert comparison["by_type"]["COST_CENTER"] == {"CREATE": 0, "UPDATE": 0, "UNCHANGED": 2}


def test_partial_file_merges_by_default_and_replace_removes(client, admin, run_worker, db):
    _setup(client, admin, run_worker)
    changed = [ROWS[0][:9] + (999, 200, None, None, None, None, None, 50, 1249)]
    final = import_and_load(client, admin, run_worker, builders.realizado_wide(changed), "parcial.xlsx")
    scope = final["summary"]["comparison"]["scopes"][0]
    assert (scope["changed"], scope["absent"], scope["absent_action"]) == (1, 1, "KEEP")
    assert final["summary"]["load"]["entries_kept_from_previous"] == 8
    assert _current_total(db) == Decimal("1329.00")  # 1249 do arquivo + 80 mantidos do CC B

    final = import_and_load(
        client, admin, run_worker, builders.realizado_wide(changed), "subst.xlsx", force=True, mode="REPLACE"
    )
    scope = final["summary"]["comparison"]["scopes"][0]
    assert (scope["absent"], scope["absent_action"], scope["after_total"]) == (1, "REMOVE", "1249")
    assert _current_total(db) == Decimal("1249.00")


def test_dashboard_overview_and_scope(client, admin, run_worker):
    _setup(client, admin, run_worker)
    rows_2025 = [r[:9] + tuple(v * 2 if v else v for v in r[9:]) for r in ROWS]
    import_and_load(client, admin, run_worker, builders.realizado_wide(rows_2025, year=2025, months=8), "r2025.xlsx")

    data = client.get("/api/v1/dashboard/overview", headers=admin).json()
    # padrão: ano mais recente com realizado (2026), comparado com 2025 no mesmo período (até AGO)
    assert data["reference_year"] == 2026 and data["last_closed_period"] == 8
    assert data["selected_years"] == [2026] and data["previous_year"] == 2025 and data["has_prev"] is True
    assert data["period"]["years_label"] == "2026" and data["period"]["base_label"] == "Realizado 2025 até AGO"
    assert data["period"]["main"] == "actual" and data["period"]["main_label"] == "Realizado 2026 até AGO"
    assert data["period"]["compare_available"] is True and data["period"]["same_period_available"] is True
    assert data["kpis"]["ref_ytd"] == "430.00" and data["kpis"]["prev_ytd"] == "860.00"
    assert data["kpis"]["ytd_var_pct"] == "-0.5000"
    assert data["kpis"]["ref_annualized"] == "645.00"
    assert data["monthly"][0] == {"month": 1, "prev": "220.00", "ref": "110.00", "budget": "0.00"}
    assert [c["code"] for c in data["top_cost_centers"]] == ["1050101011", "1050101012"]
    assert 2027 in data["available_years"] and data["target_year"] == 2027  # ano do ciclo (orçamento proposto)

    # comparação desligada: nada do ano anterior entra
    off = client.get("/api/v1/dashboard/overview?years=2026&compare=false", headers=admin).json()
    assert off["previous_year"] is None and off["has_prev"] is False and off["kpis"]["prev_ytd"] == "0.00"
    assert off["kpis"]["ytd_var_pct"] is None
    # 2025 sozinho: 2024 não existe, então não há comparação; o ano fecha em AGO
    only = client.get("/api/v1/dashboard/overview?years=2025", headers=admin).json()
    assert (only["reference_year"], only["previous_year"], only["selected_years"]) == (2025, None, [2025])
    assert only["period"]["compare_available"] is False and only["last_closed_period"] == 8
    assert only["kpis"]["ref_ytd"] == "860.00" and only["monthly"][0]["ref"] == "220.00"
    assert only["heatmap"]["years"] == [2025] and only["top_cost_centers"][0]["ytd_var_pct"] is None
    # dois anos: os períodos são somados (sem comparação entre eles)
    both = client.get("/api/v1/dashboard/overview?years=2026,2025", headers=admin).json()
    assert both["selected_years"] == [2025, 2026] and both["previous_year"] is None
    assert both["period"]["years_label"] == "2025 e 2026" and both["kpis"]["ref_ytd"] == "1290.00"
    assert both["monthly"][0]["ref"] == "330.00" and both["period"]["compare_available"] is False
    # ano sem base carregada é ignorado; filtro de meses limita todos os números (2026: 110 + 210; 2025: o dobro)
    assert client.get("/api/v1/dashboard/overview?years=2019", headers=admin).json()["selected_years"] == [2026]
    jf = client.get("/api/v1/dashboard/overview?years=2026&months=1,2", headers=admin).json()
    assert jf["selected_months"] == [1, 2] and jf["kpis"]["ref_ytd"] == "320.00" and jf["kpis"]["prev_ytd"] == "640.00"
    assert jf["period"]["same_period_available"] is False
    # ano-alvo do ciclo (orçamento proposto) é selecionável; sem lançamentos, soma zero
    target = client.get("/api/v1/dashboard/overview?years=2027", headers=admin).json()
    assert target["period"]["main"] == "budget" and target["period"]["main_label"] == "Orçamento 2027"
    assert target["kpis"]["ref_ytd"] == "0.00" and target["has_actual"] is False
    # 2026 + 2027: realizado 2026 (série principal) e orçamento 2027 (orçado) como base, sem repetir o ano
    mixed = client.get("/api/v1/dashboard/overview?years=2026,2027&compare=false", headers=admin).json()
    assert mixed["period"]["main_label"] == "Realizado 2026 até AGO" and mixed["period"]["base_kind"] == "budget"
    assert mixed["period"]["base_label"] == "Orçamento 2027 até AGO"
    # tipo de orçamento: as contas do fixture são OPEX; Pessoal não tem lançamentos
    opex = client.get("/api/v1/dashboard/overview?years=2026&modules=OPEX", headers=admin).json()
    people = client.get("/api/v1/dashboard/overview?years=2026&modules=PERSONNEL", headers=admin).json()
    assert opex["kpis"]["ref_ytd"] == "430.00" and opex["period"]["modules"] == ["OPEX"]
    assert people["kpis"]["ref_ytd"] == "0.00" and people["top_accounts"] == []

    # tabela com drill-down: pacote → conta → centro de custo, base = ano anterior no mesmo período
    bd = client.get("/api/v1/dashboard/breakdown?years=2026", headers=admin).json()
    assert bd["base"] == "prev" and bd["base_label"] == "Realizado 2025 até AGO"
    assert bd["thresholds"] == {"growth": 0.2, "reduction": 0.3}
    by_name = {r["name"]: r for r in bd["rows"]}
    assert by_name["Viagens"]["ref"] == "350.00" and by_name["Viagens"]["base"] == "700.00"
    assert by_name["Viagens"]["var_pct"] == "-0.5000" and by_name["Viagens"]["has_children"] is True
    assert bd["total"]["ref"] == "430.00" and bd["total"]["base"] == "860.00"
    pkg = by_name["Viagens"]["id"]
    accounts = client.get(
        f"/api/v1/dashboard/breakdown?years=2026&group_by=account&parent_package_id={pkg}", headers=admin
    ).json()
    assert [(r["code"], r["ref"]) for r in accounts["rows"]] == [("6010301001", "350.00")]
    # filtro por conta (clique nos visuais do Painel 2): só a conta entra nos números
    by_account = client.get(
        f"/api/v1/dashboard/overview?years=2026&account_id={accounts['rows'][0]['id']}", headers=admin
    ).json()
    assert by_account["kpis"]["ref_ytd"] == "350.00" and by_account["kpis"]["prev_ytd"] == "700.00"
    ccs = client.get(
        f"/api/v1/dashboard/breakdown?years=2026&group_by=cost_center&parent_account_id={accounts['rows'][0]['id']}",
        headers=admin,
    ).json()
    assert [(r["code"], r["ref"], r["has_children"]) for r in ccs["rows"]] == [("1050101011", "350.00", False)]
    # sem comparação e sem orçado: sem base
    solo = client.get("/api/v1/dashboard/breakdown?years=2026&compare=false", headers=admin).json()
    assert solo["base"] is None and solo["total"]["ref"] == "430.00" and solo["total"]["var_pct"] is None
    # "mesmo período" desligado: a base é o ano cheio
    full = client.get("/api/v1/dashboard/breakdown?years=2026&same_period=false", headers=admin).json()
    assert full["base_label"] == "Realizado 2025 (ano cheio)" and full["total"]["base"] == "860.00"
    assert next(r for r in full["rows"] if r["name"] == "DTI")["base"] == "160.00"  # DTI 2025, ano cheio

    # gestor só enxerga o próprio CC
    client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": "g@t.com", "name": "Gestor B", "password": "Senha@123", "roles": ["MANAGER"]},
    )
    manager = login(client, "g@t.com", "Senha@123")
    me = client.get("/api/v1/auth/me", headers=manager).json()["id"]
    cc_b = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101012")
    client.patch(f"/api/v1/cost-centers/{cc_b['id']}", headers=admin, json={"manager_user_id": me})
    mine = client.get("/api/v1/dashboard/overview", headers=manager).json()
    assert mine["kpis"]["ref_ytd"] == "80.00"
    assert client.get("/api/v1/dashboard/data-quality", headers=manager).status_code == 403


def test_painel2_figures(client, admin, run_worker):
    """Painel 2: as figuras Plotly saem do mesmo overview (mesmos números), só com `figures=true`."""
    _setup(client, admin, run_worker)
    rows_2025 = [r[:9] + tuple(v * 2 if v else v for v in r[9:]) for r in ROWS]
    import_and_load(client, admin, run_worker, builders.realizado_wide(rows_2025, year=2025, months=8), "r2025.xlsx")
    assert "figures" not in client.get("/api/v1/dashboard/overview?years=2026", headers=admin).json()
    data = client.get("/api/v1/dashboard/overview?years=2026&figures=true", headers=admin).json()
    figs = data["figures"]
    expected = {
        "monthly",
        "cumulative",
        "top_cost_centers",
        "top_accounts",
    }
    assert expected <= set(figs)
    monthly = {t["name"]: t for t in figs["monthly"]["data"]}
    assert set(monthly) == {"Realizado 2025 até AGO", "Realizado 2026 até AGO"}
    real = monthly["Realizado 2026 até AGO"]
    assert real["y"][0] == float(data["monthly"][0]["ref"]) == 110.0 and real["customdata"][0][:2] == ["R$ 110,00", 1]
    assert real["marker"]["color"] == "#4472c4"  # azul do realizado, sem degradê
    assert monthly["Realizado 2025 até AGO"]["marker"]["color"] == "#8a6bbf"  # roxo do ano anterior
    # tooltip próprio: linhas por mês (série e variação) e o modo vão na figura
    assert figs["monthly"]["meta"] == {"tooltip": "unified"} and figs["top_cost_centers"]["meta"] == {
        "tooltip": "point"
    }
    assert real["customdata"][0][2] == [["Realizado 2026: R$ 110,00", "#4472c4"], ["Variação: -50,0%", ""]]
    assert real["textfont"]["color"] == "#3a64b4"  # rótulo no tom escuro do azul
    ccs = figs["top_cost_centers"]["data"]
    assert ccs[0]["customdata"][0][3] == data["top_cost_centers"][0]["id"]  # id do CC para o clique filtrar o painel
    assert len(ccs) == 2 and ccs[1]["name"] == "Realizado 2025 até AGO"  # barra fina da base
    cumulative = {t["name"]: t for t in figs["cumulative"]["data"]}
    assert cumulative["Realizado 2026 até AGO"]["y"][-1] == 430.0

    # destaque estilo Power BI: com um CC escolhido, os números seguem filtrados, mas o ranking de CCs mostra todos,
    # com os outros esmaecidos (o escolhido continua clicável para desmarcar)
    cc_b = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101012")
    picked = client.get(
        f"/api/v1/dashboard/overview?years=2026&figures=true&cost_center_id={cc_b['id']}", headers=admin
    )
    picked = picked.json()
    assert picked["kpis"]["ref_ytd"] == "80.00"
    ranking = picked["figures"]["top_cost_centers"]["data"][0]
    assert len(ranking["y"]) == 2 and ranking["marker"]["opacity"] == [0.3, 1.0]
    assert [r["code"] for r in picked["heatmap_all"]["rows"]] == ["1050101011", "1050101012"]
    # com um mês escolhido, o comparativo mensal mostra os 12 meses e esmaece os outros
    jan = client.get("/api/v1/dashboard/overview?years=2026&figures=true&months=1", headers=admin).json()
    assert jan["kpis"]["ref_ytd"] == "110.00"
    real_jan = next(t for t in jan["figures"]["monthly"]["data"] if t["name"] == "Realizado 2026")
    assert real_jan["y"][1] == 210.0 and real_jan["marker"]["opacity"][:2] == [1.0, 0.3]


def test_breakdown_drills_into_accounts_without_package(client, admin, run_worker):
    """A linha "Sem pacote" da tabela do painel também abre (contas sem pacote → centros de custo)."""
    _setup(client, admin, run_worker)
    row = ("1001", "0001", "MANAUS", "1050101011", "CC A", "G", "6010309998", "Conta sem pacote", None, 40)
    row += (None,) * 7 + (40,)
    import_and_load(
        client, admin, run_worker, builders.realizado_wide([row]), "r2026b.xlsx", create_missing_dimensions="true"
    )
    root = client.get("/api/v1/dashboard/breakdown?years=2026&compare=false", headers=admin).json()
    no_pkg = next(r for r in root["rows"] if r["name"] == "Sem pacote")
    assert (no_pkg["id"], no_pkg["ref"], no_pkg["has_children"]) == (None, "40.00", True)
    assert all(r["has_children"] for r in root["rows"])
    accounts = client.get(
        "/api/v1/dashboard/breakdown?years=2026&compare=false&group_by=account&parent_no_package=true", headers=admin
    ).json()
    assert [(r["code"], r["ref"], r["has_children"]) for r in accounts["rows"]] == [("6010309998", "40.00", True)]
    ccs = client.get(
        f"/api/v1/dashboard/breakdown?years=2026&compare=false&group_by=cost_center&parent_account_id={accounts['rows'][0]['id']}",
        headers=admin,
    ).json()
    assert [(r["code"], r["ref"], r["has_children"]) for r in ccs["rows"]] == [("1050101011", "40.00", False)]


def test_data_quality(client, admin, run_worker):
    _setup(client, admin, run_worker)
    checks = {c["code"]: c for c in client.get("/api/v1/dashboard/data-quality", headers=admin).json()["checks"]}
    assert checks["NO_ACTUAL_2026"]["severity"] == "OK"
    assert checks["NO_ACTUAL_2025"]["severity"] == "WARNING"
    assert checks["NO_ACTUAL_2025"]["title"] == "Realizado 2025 ainda não carregado"
    assert checks["MULTIPLE_CURRENT"]["severity"] == "OK"
    assert checks["CC_NO_USER"]["count"] == 2
