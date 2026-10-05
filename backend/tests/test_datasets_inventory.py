from decimal import Decimal

from sqlalchemy import func, select

from app.models import ActualEntry, Employee
from tests import builders
from tests.conftest import login
from tests.test_imports import import_and_load, status, upload
from tests.test_reimport_dashboard import ROWS


def _load(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    import_and_load(client, admin, run_worker, builders.realizado_wide(ROWS), "r2026.xlsx")


def test_delete_dataset_allows_clean_reimport(client, admin, run_worker, db):
    _load(client, admin, run_worker)
    datasets = client.get("/api/v1/datasets", headers=admin).json()
    actual = next(d for d in datasets if d["dataset_type"] == "ACTUAL")
    assert (
        actual["scope_key"] == "ACTUAL:2026:1001" and actual["deletable"] and actual["last_file_name"] == "r2026.xlsx"
    )
    assert not next(d for d in datasets if d["dataset_type"] == "MASTER_DATA")["deletable"]

    params = {"dataset_type": "ACTUAL", "scope_key": "ACTUAL:2026:1001"}
    assert client.delete("/api/v1/datasets", headers=admin, params=params | {"confirm": "sim"}).status_code == 422
    assert (
        client.delete(
            "/api/v1/datasets", headers=admin, params={"dataset_type": "MASTER_DATA", "confirm": "EXCLUIR"}
        ).status_code
        == 409
    )
    result = client.delete("/api/v1/datasets", headers=admin, params=params | {"confirm": "excluir"}).json()
    assert result["versions_deleted"] == 1 and result["imports_reverted"] == 1
    assert db.scalar(select(func.count()).select_from(ActualEntry)) == 0

    # o mesmo arquivo volta a ser importável sem bloqueio
    batch_id = upload(client, admin, builders.realizado_wide(ROWS), "r2026.xlsx")
    run_worker()
    assert not status(client, admin, batch_id)["summary"].get("same_file_imported_in")
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200

    # só administrador exclui
    client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": "c@t.com", "name": "Controller", "password": "Senha@123", "roles": ["CONTROLLER"]},
    )
    controller = login(client, "c@t.com", "Senha@123")
    assert (
        client.delete("/api/v1/datasets", headers=controller, params=params | {"confirm": "EXCLUIR"}).status_code == 403
    )
    logs = client.get("/api/v1/audit-logs", headers=admin, params={"action": "DELETE_DATASET"}).json()
    assert logs["total"] == 1


def test_delete_employees_dataset(client, admin, run_worker, db):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    rows = [("1", "A", "ANALISTA", "1001", None, "1050101011", 5000, "MANTER", None, None, None, "CLT", None, None)]
    import_and_load(client, admin, run_worker, builders.quadro_funcionarios(rows), "quadro.xlsx")
    scope = next(d for d in client.get("/api/v1/datasets", headers=admin).json() if d["dataset_type"] == "EMPLOYEES")
    result = client.delete(
        "/api/v1/datasets",
        headers=admin,
        params={"dataset_type": "EMPLOYEES", "scope_key": scope["scope_key"], "confirm": "EXCLUIR"},
    ).json()
    assert result["employees_deleted"] == 1
    assert db.scalar(select(func.count()).select_from(Employee)) == 0


def test_overview_uses_loaded_years_and_inventory(client, admin, run_worker):
    _load(client, admin, run_worker)
    data = client.get("/api/v1/dashboard/overview", headers=admin).json()
    assert data["available_years"] == [2026] and data["has_actual"] and not data["has_prev"]
    # só 2025 carregado também funciona: o painel mostra o ano que existe
    old = client.get("/api/v1/dashboard/overview", headers=admin, params={"year": 2025}).json()
    assert old["reference_year"] == 2025 and not old["has_actual"]

    rows = [
        ("1", "A", "ANALISTA", "1001", None, "1050101011", 5000, "MANTER", None, None, None, "CLT", None, None),
        ("2", "B", "CONSULTOR", "1001", None, "1050101011", 10000, "MANTER", None, None, None, "PJ", None, None),
    ]
    import_and_load(client, admin, run_worker, builders.quadro_funcionarios(rows), "quadro.xlsx")
    import_and_load(client, admin, run_worker, builders.premissas(), "premissas.xlsx")
    inv = client.get("/api/v1/dashboard/inventory", headers=admin).json()
    p = inv["personnel"]
    assert p["headcount"] == 2 and p["monthly_payroll"] == "15000.00"
    assert Decimal(p["monthly_estimated_cost"]) == Decimal("19000.00")  # 5000 × 1,8 + 10000 (PJ sem multiplicador)
    assert inv["master"]["cost_centers"] == 2
    ipca = next(r for r in inv["macro"]["rows"] if r["indicator"] == "IPCA")
    assert ipca["values"]["2027"] == "0.0350000000"


def test_delete_budget_and_everything(client, admin, run_worker):
    from tests.test_imports import import_and_load, status, upload

    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "opex.xlsx")
    import_and_load(client, admin, run_worker, builders.capex_template_filled(), "capex.xlsx")
    inv = {b["module"]: b for b in client.get("/api/v1/datasets/budget", headers=admin).json()}
    assert inv["OPEX"]["rows"] == 4 and inv["CAPEX"]["rows"] == 3 and inv["CAPEX"]["total"] == "34000.00"
    assert inv["PERSONNEL"]["rows"] == 0 and inv["PERSONNEL"]["total"] is None

    bad = client.delete("/api/v1/datasets/budget", headers=admin, params={"module": "OPEX", "confirm": "x"})
    assert bad.status_code == 422
    r = client.delete("/api/v1/datasets/budget", headers=admin, params={"module": "CAPEX", "confirm": "EXCLUIR"})
    assert r.json()["cost_centers"] == 1
    inv = {b["module"]: b for b in client.get("/api/v1/datasets/budget", headers=admin).json()}
    assert inv["CAPEX"]["rows"] == 0 and inv["OPEX"]["rows"] == 4

    r = client.delete("/api/v1/datasets/all", headers=admin, params={"confirm": "EXCLUIR"})
    assert r.status_code == 200, r.text
    assert {d["dataset_type"] for d in client.get("/api/v1/datasets", headers=admin).json()} == {"MASTER_DATA"}
    inv = {b["module"]: b for b in client.get("/api/v1/datasets/budget", headers=admin).json()}
    assert inv["OPEX"]["rows"] == 0 and inv["OPEX"]["cost_centers"] == 0
    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101011")
    assert client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()["status"] == "DRAFT"

    # o mesmo arquivo volta a ser aceito sem aviso de duplicidade
    batch_id = upload(client, admin, builders.opex_template_filled(), "opex.xlsx")
    run_worker()
    assert not status(client, admin, batch_id)["summary"].get("same_file_imported_in")
