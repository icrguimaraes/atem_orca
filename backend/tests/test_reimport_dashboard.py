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
    assert data["reference_year"] == 2026 and data["last_closed_period"] == 8
    assert data["kpis"]["ref_ytd"] == "430.00" and data["kpis"]["prev_ytd"] == "860.00"
    assert data["kpis"]["ytd_var_pct"] == "-0.5000"
    assert data["kpis"]["ref_annualized"] == "645.00"
    assert data["monthly"][0] == {"month": 1, "prev": "220.00", "ref": "110.00", "budget": "0.00"}
    packages = {p["package"]: p for p in data["by_package"]}
    assert packages["Viagens"]["ref_ytd"] == "350.00" and packages["DTI"]["prev_total"] == "160.00"
    assert [c["code"] for c in data["top_cost_centers"]] == ["1050101011", "1050101012"]
    assert data["selected_years"] == [2025, 2026] and data["previous_year"] == 2025

    # um ano só: sem comparação (o ano anterior não entra em nenhum número)
    only = client.get("/api/v1/dashboard/overview?years=2025", headers=admin).json()
    assert (only["reference_year"], only["previous_year"], only["selected_years"]) == (2025, None, [2025])
    assert only["has_prev"] is False and only["last_closed_period"] == 8
    assert only["kpis"]["ref_ytd"] == "860.00" and only["kpis"]["prev_ytd"] == "0.00"
    assert only["kpis"]["ytd_var_pct"] is None and only["account_deltas"] == []
    assert only["monthly"][0] == {"month": 1, "prev": "0.00", "ref": "220.00", "budget": "0.00"}
    assert only["heatmap"]["year"] == 2025 and only["top_cost_centers"][0]["ytd_var_pct"] is None
    # dois anos: o maior é a referência e o menor a base, qualquer que seja a ordem informada
    both = client.get("/api/v1/dashboard/overview?years=2026,2025", headers=admin).json()
    assert (both["reference_year"], both["previous_year"], both["kpis"]["ytd_var_pct"]) == (2026, 2025, "-0.5000")
    # ano sem base carregada é ignorado
    assert client.get("/api/v1/dashboard/overview?years=2019", headers=admin).json()["selected_years"] == [2025, 2026]
    # filtro de meses: só janeiro e fevereiro entram nos números (2026: 110 + 210 = 320; 2025: o dobro)
    jf = client.get("/api/v1/dashboard/overview?years=2025,2026&months=1,2", headers=admin).json()
    assert jf["selected_months"] == [1, 2] and jf["kpis"]["ref_ytd"] == "320.00" and jf["kpis"]["prev_ytd"] == "640.00"

    # tabela com drill-down: pacote → conta → centro de custo, base = ano anterior no mesmo período
    bd = client.get("/api/v1/dashboard/breakdown?years=2025,2026", headers=admin).json()
    assert bd["base"] == "prev" and bd["base_label"] == "Realizado 2025 até 8"
    by_name = {r["name"]: r for r in bd["rows"]}
    assert by_name["Viagens"]["ref"] == "350.00" and by_name["Viagens"]["base"] == "700.00"
    assert by_name["Viagens"]["var_pct"] == "-0.5000" and by_name["Viagens"]["has_children"] is True
    assert bd["total"]["ref"] == "430.00" and bd["total"]["base"] == "860.00"
    pkg = by_name["Viagens"]["id"]
    accounts = client.get(
        f"/api/v1/dashboard/breakdown?years=2025,2026&group_by=account&parent_package_id={pkg}", headers=admin
    ).json()
    assert [(r["code"], r["ref"]) for r in accounts["rows"]] == [("6010301001", "350.00")]
    ccs = client.get(
        f"/api/v1/dashboard/breakdown?years=2025,2026&group_by=cost_center&parent_account_id={accounts['rows'][0]['id']}",
        headers=admin,
    ).json()
    assert [(r["code"], r["ref"], r["has_children"]) for r in ccs["rows"]] == [("1050101011", "350.00", False)]
    # um ano só e sem orçado: sem base de comparação
    solo = client.get("/api/v1/dashboard/breakdown?years=2026", headers=admin).json()
    assert solo["base"] is None and solo["total"]["ref"] == "430.00" and solo["total"]["var_pct"] is None

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


def test_data_quality(client, admin, run_worker):
    _setup(client, admin, run_worker)
    checks = {c["code"]: c for c in client.get("/api/v1/dashboard/data-quality", headers=admin).json()["checks"]}
    assert checks["NO_ACTUAL_2026"]["severity"] == "OK"
    assert checks["NO_ACTUAL_2025"]["severity"] == "WARNING"
    assert checks["NO_ACTUAL_2025"]["title"] == "Realizado 2025 ainda não carregado"
    assert checks["MULTIPLE_CURRENT"]["severity"] == "OK"
    assert checks["CC_NO_USER"]["count"] == 2
