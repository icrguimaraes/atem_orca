"""Template de gestor não sobrescreve realizado nem cadastros; prévia obsoleta não é confirmada."""

from tests import builders
from tests.test_imports import import_and_load, status, upload
from tests.test_opex import CC


def test_template_keeps_actual_and_master(client, admin, run_worker):
    # base da Controladoria: realizado 2026 de Telefonia = 300 × 8
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    ref = [("1001", "0001", "MANAUS", CC, "CC A", "G", "6010301002", "Telefonia", "DTI", *[300] * 8, 2400)]
    import_and_load(client, admin, run_worker, builders.realizado_wide(ref), "real.xlsx", dataset_type="ACTUAL")
    ccs = {c["code"]: c for c in client.get("/api/v1/cost-centers", headers=admin).json()}
    client.patch(f"/api/v1/cost-centers/{ccs[CC]['id']}", headers=admin, json={"name": "NOME OFICIAL"})

    # template do gestor traz outro nome de CC e outro realizado: nada disso entra; só o orçamento 2027
    batch_id = upload(client, admin, builders.opex_template_filled(), "tpl.xlsx")
    run_worker()
    b = status(client, admin, batch_id)
    cmp_ = b["summary"]["comparison"]
    assert cmp_["master"]["template"] is True and cmp_["master"]["updates_ignored"] >= 1
    assert cmp_["actual"]["skipped_scopes"] == ["ACTUAL:2026:1001"]
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin, params={"force": True}).status_code == 200
    run_worker()
    final = status(client, admin, batch_id)
    assert final["status"] == "COMPLETED"
    assert final["summary"]["load"]["budget"]["lines_created"] == 4
    assert "skipped" in final["summary"]["load"]["actual"]
    cc = client.get(f"/api/v1/cost-centers/{ccs[CC]['id']}", headers=admin).json()
    assert cc["name"] == "NOME OFICIAL"
    head = client.get(f"/api/v1/opex/cost-centers/{ccs[CC]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/accounts", headers=admin).json()
    rows = {r["code"]: r for r in view["accounts"]}
    assert rows["6010301002"]["ref_actual_ytd"] == "2400.00"  # realizado da Controladoria preservado


def test_stale_preview_is_rejected(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    first = upload(client, admin, builders.opex_template_filled(), "a.xlsx")
    run_worker()
    assert status(client, admin, first)["status"] == "VALIDATED"
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "b.xlsx", force=True)
    resp = client.post(f"/api/v1/imports/{first}/confirm", headers=admin, params={"force": True})
    assert resp.status_code == 409 and "desatualizada" in resp.json()["detail"]
