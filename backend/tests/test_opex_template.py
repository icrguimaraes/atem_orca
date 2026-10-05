from tests import builders
from tests.test_imports import import_and_load, status, upload


def test_filled_opex_template_loads_everything(client, admin, run_worker):
    batch_id = upload(client, admin, builders.opex_template_filled(), "Template_OPEX 2027_Gestor.xlsx")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["dataset_type"] == "OPEX_TEMPLATE", b
    meta = b["summary"]["meta"]
    assert meta["parts"] == {"master": 9, "actual": 1, "budget_lines": 4}
    budget = b["summary"]["comparison"]["budget"]
    assert budget == [
        {
            "cost_center": "1050101011",
            "company": "1001",
            "status": "DRAFT",
            "editable": True,
            "lines": 4,
            "total": "20400",
            "replaces_lines": 0,
            "replaces_total": "0",
        }
    ]
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    final = status(client, admin, batch_id)
    assert final["status"] == "COMPLETED", final
    assert final["summary"]["load"]["budget"]["lines_created"] == 4

    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101011")
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    assert head["status"] == "IN_PROGRESS"
    view = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/accounts", headers=admin).json()
    rows = {r["code"]: r for r in view["accounts"]}
    assert rows["6010301002"]["proposed"] == "4200.00" and rows["6010301002"]["ref_actual_ytd"] == "2400.00"
    assert rows["6010301011"]["proposed"] == "1800.00"
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    consult = next(line for line in lines if line["supplier"] == "KPMG")
    assert consult["description"] == "Consultoria tributária" and consult["justification"] == "Reajuste IPCA"

    # linha digitada no sistema é preservada; reimportar o template substitui só as linhas do template
    acc = next(
        a for a in client.get("/api/v1/opex/options", headers=admin).json()["accounts"] if a["code"] == "6010301005"
    )
    client.post(
        f"/api/v1/opex/submissions/{head['submission_id']}/lines",
        headers=admin,
        json={"account_id": acc["id"], "values": {"1": 100}},
    )
    final = import_and_load(client, admin, run_worker, builders.opex_template_filled(), "v2.xlsx", force=True)
    assert final["summary"]["load"]["budget"] == {"lines_replaced": 4, "lines_created": 4, "cost_centers": 1}
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    assert len(lines) == 5


def test_template_blocked_after_submission(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t.xlsx")
    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101011")
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    sub = head["submission_id"]
    view = client.get(f"/api/v1/opex/submissions/{sub}/accounts", headers=admin).json()
    for r in view["accounts"]:
        if r["needs_justification"]:
            client.put(
                f"/api/v1/opex/submissions/{sub}/justifications/{r['account_id']}", headers=admin, json={"text": "ok"}
            )
    assert client.post(f"/api/v1/opex/submissions/{sub}/actions/submit", headers=admin, json={}).status_code == 200
    batch_id = upload(client, admin, builders.opex_template_filled(), "t2.xlsx")
    run_worker()
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin, params={"code": "BUDGET_LOCKED"}).json()
    assert len(errors) == 4
