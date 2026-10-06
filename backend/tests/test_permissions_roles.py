"""Perfis Consulta e Gestor de pacote com escopo: leem, mas não editam nem enviam."""

from tests import builders
from tests.test_imports import import_and_load
from tests.test_opex import CC, _user


def _cc(client, admin):
    return next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == CC)


def test_viewer_scope_is_read_only(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.post(f"/api/v1/cycles/{cycle_id}/open", headers=admin)
    cc = _cc(client, admin)
    viewer_id, viewer = _user(client, admin, "consulta@t.com", ["VIEWER"])
    r = client.put(f"/api/v1/users/{viewer_id}/scopes", headers=admin, json=[{"cost_center_id": cc["id"]}])
    assert r.status_code in (200, 204), r.text
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    sub = head["submission_id"]
    # vê
    mine = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=viewer)
    assert mine.status_code == 200 and mine.json()["permissions"]["edit"] is False and mine.json()["actions"] == []
    assert client.get(f"/api/v1/opex/submissions/{sub}/lines", headers=viewer).status_code == 200
    assert client.get(f"/api/v1/capex/cost-centers/{cc['id']}", headers=viewer).status_code == 200
    # não edita nem envia
    acc = client.get("/api/v1/opex/options", headers=admin).json()["accounts"][0]
    r = client.post(
        f"/api/v1/opex/submissions/{sub}/lines", headers=viewer, json={"account_id": acc["id"], "values": {"1": 10}}
    )
    assert r.status_code == 403 and "consulta" in r.json()["detail"]
    r = client.post(f"/api/v1/opex/submissions/{sub}/actions/submit", headers=viewer, json={})
    assert r.status_code in (403, 409)
    # gestor de CC do mesmo escopo edita normalmente
    mgr_id, mgr = _user(client, admin, "gestor2@t.com", ["MANAGER"])
    client.put(f"/api/v1/users/{mgr_id}/scopes", headers=admin, json=[{"cost_center_id": cc["id"]}])
    r = client.post(
        f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json={"account_id": acc["id"], "values": {"1": 10}}
    )
    assert r.status_code == 201, r.text
