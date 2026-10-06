from decimal import Decimal

from tests import builders
from tests.test_imports import import_and_load, status, upload
from tests.test_opex import _user

CC1, CC2 = "1050101011", "1050101012"
QUADRO = [
    # matrícula, nome, cargo, empresa, divisão, CC, salário, ação, mês, novo cargo, novo salário, contrato, creche, VT
    ("100", "ANA", "ANALISTA", "1001", "0001", CC1, 10000, "MANTER", None, None, None, "CLT", "NÃO", "SIM"),
    ("200", "BRUNO", "ANALISTA", "1001", "0001", CC1, 8000, "PROMOVER", 7, "COORDENADOR", 9000, "CLT", "SIM", "SIM"),
    ("300", "CARLA", "CONSULTORA", "1001", "0001", CC1, 5000, "REMOVER", 4, None, None, "PJ", None, None),
    (
        None,
        "VAGA ANALISTA DE DADOS",
        "ANALISTA",
        "1001",
        "0001",
        CC1,
        6000,
        "INCLUIR",
        10,
        None,
        None,
        "CLT",
        None,
        None,
    ),
]


def _setup(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    final = import_and_load(client, admin, run_worker, builders.quadro_funcionarios(QUADRO), "quadro.xlsx")
    assert final["status"] == "COMPLETED", final
    mgr_id, mgr = _user(client, admin, "gestor@t.com", ["MANAGER"])
    ccs = {c["code"]: c for c in client.get("/api/v1/cost-centers", headers=admin).json()}
    client.patch(f"/api/v1/cost-centers/{ccs[CC1]['id']}", headers=admin, json={"manager_user_id": mgr_id})
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.post(f"/api/v1/cycles/{cycle_id}/open", headers=admin)
    return ccs, mgr, final


def _by_name(view):
    return {p["name"]: p for p in view["positions"]}


def test_personnel_projection_workflow_and_transfer(client, admin, run_worker):
    ccs, mgr, final = _setup(client, admin, run_worker)
    assert final["summary"]["load"]["movements"] == {
        "promotion": 1,
        "termination": 1,
        "hires": 1,
        "cost_centers": 1,
    }

    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=mgr).json()
    sub = head["submission_id"]
    assert head["status"] == "IN_PROGRESS" and head["permissions"]["edit"]
    view = client.get(f"/api/v1/personnel/submissions/{sub}/view", headers=mgr).json()
    rows = _by_name(view)
    # reajuste 5% desde JAN; CLT × 1,8; PJ sem multiplicador
    assert rows["ANA"]["annual"] == "226800.00"
    assert rows["BRUNO"]["annual"] == "192780.00" and rows["BRUNO"]["movement"]["new_position"] == "COORDENADOR"
    assert rows["CARLA"]["annual"] == "15750.00" and rows["CARLA"]["headcount"][3] == 0
    vaga = next(p for p in view["positions"] if p["kind"] == "HIRE")
    assert vaga["annual"] == "34020.00" and vaga["monthly"][8] == "0.00" and vaga["monthly"][9] == "11340.00"
    t = view["totals"]
    assert t["annual"] == "469350.00" and t["headcount_start"] == 3 and t["headcount_end"] == 3
    assert (t["hires"], t["terminations"], t["promotions"]) == (1, 1, 1)
    assert Decimal(t["salary_total"]) + Decimal(t["charges_total"]) + Decimal(t["severance_total"]) == Decimal(
        "469350.00"
    )
    assert {b["code"] for b in view["benefits"]} == {"AUX_CRECHE", "VALE_TRANSPORTE"}

    # desligamento e contratação exigem justificativa para enviar
    resp = client.post(f"/api/v1/personnel/submissions/{sub}/actions/submit", headers=mgr, json={})
    assert resp.status_code == 409 and "justificativa" in resp.json()["detail"]
    carla = rows["CARLA"]["employee_id"]
    v = client.put(
        f"/api/v1/personnel/submissions/{sub}/employees/{carla}/movement",
        headers=mgr,
        json={"type": "TERMINATION", "month": 4, "severance_cost": 3000, "reason": "Fim do contrato"},
    ).json()
    assert _by_name(v)["CARLA"]["annual"] == "18750.00"  # + verba rescisória em ABR
    client.patch(
        f"/api/v1/personnel/movements/{vaga['movement']['id']}",
        headers=mgr,
        json={
            "position_name": "ANALISTA DE DADOS",
            "quantity": 2,
            "month": 10,
            "new_salary": 6000,
            "reason": "Expansão do time",
        },
    )

    # transferência: Ana sai do CC1 em JUL e entra no CC2
    ana = rows["ANA"]["employee_id"]
    bad = client.put(
        f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement",
        headers=mgr,
        json={"type": "TRANSFER", "month": 7, "target_cost_center_id": ccs[CC1]["id"]},
    )
    assert bad.status_code == 422
    v = client.put(  # transferência entre CCs de gestores diferentes: feita pela Controladoria
        f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement",
        headers=admin,
        json={"type": "TRANSFER", "month": 7, "target_cost_center_id": ccs[CC2]["id"], "reason": "Nova área"},
    ).json()
    assert _by_name(v)["ANA"]["annual"] == "113400.00"
    head2 = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC2]['id']}", headers=admin).json()
    v2 = client.get(f"/api/v1/personnel/submissions/{head2['submission_id']}/view", headers=admin).json()
    incoming = v2["positions"][0]
    assert incoming["kind"] == "TRANSFER_IN" and incoming["annual"] == "113400.00"
    assert incoming["from_cost_center"].startswith(CC1)

    head = client.post(f"/api/v1/personnel/submissions/{sub}/actions/submit", headers=mgr, json={}).json()
    assert head["status"] == "SUBMITTED" and head["package_review"]["status"] == "PENDING"
    client.post(f"/api/v1/personnel/submissions/{sub}/actions/start_review", headers=admin, json={})
    resp = client.post(f"/api/v1/personnel/submissions/{sub}/actions/approve", headers=admin, json={})
    assert resp.status_code == 409 and "Pessoas" in resp.json()["detail"]
    client.post(f"/api/v1/personnel/submissions/{sub}/package-review", headers=admin, json={"status": "APPROVED"})
    head = client.post(f"/api/v1/personnel/submissions/{sub}/actions/approve", headers=admin, json={}).json()
    assert head["status"] == "APPROVED"
    assert (
        client.put(
            f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement", headers=mgr, json={"type": "KEEP"}
        ).status_code
        == 409
    )

    summary = client.get("/api/v1/personnel/summary", headers=admin).json()
    row = next(r for r in summary["rows"] if r["code"] == CC1)
    assert row["status"] == "APPROVED" and row["hires"] == 2 and row["terminations"] == 1
    assert summary["hires_by_month"][9] == 2 and summary["terminations_by_month"][3] == 1
    # gestor sem vínculo não vê o CC
    _, other = _user(client, admin, "outro@t.com", ["MANAGER"])
    assert client.get(f"/api/v1/personnel/submissions/{sub}/view", headers=other).status_code == 403


def test_what_if_and_scenarios(client, admin, run_worker):
    ccs, mgr, _ = _setup(client, admin, run_worker)
    r = client.post("/api/v1/personnel/what-if", headers=admin, json={"multipliers": {"CLT": 2.0}}).json()
    assert r["base"]["annual"] == "469350.00"
    # CLT: (226800 + 192780 + 34020) / 1,8 × 2,0 = 504000; PJ inalterado
    assert r["simulation"]["annual"] == "519750.00"
    pj = next(c for c in r["by_contract"] if c["contract"] == "PJ")
    assert pj["difference"] == "0.00"
    clt = next(c for c in r["by_contract"] if c["contract"] == "CLT")
    assert Decimal(clt["simulated"]) == Decimal("504000.00") and r["difference"] == "50400.00"
    assert "PJ" in r["simulated"]["ignored_multiplier_for"]

    # gestor não cria cenário; controladoria cria e torna base
    assert client.post("/api/v1/personnel/scenarios", headers=mgr, json={"name": "x"}).status_code == 403
    sc = client.post(
        "/api/v1/personnel/scenarios", headers=admin, json={"name": "Reajuste 7%", "salary_adjustment_pct": 0.07}
    ).json()
    assert sc["multipliers"]["CLT"] == "1.800000" and not sc["is_baseline"]
    client.post(f"/api/v1/personnel/scenarios/{sc['id']}/baseline", headers=admin)
    names = {s["name"]: s for s in client.get("/api/v1/personnel/scenarios", headers=admin).json()}
    assert names["Reajuste 7%"]["is_baseline"] and not names["Base"]["is_baseline"]
    assert client.delete(f"/api/v1/personnel/scenarios/{sc['id']}", headers=admin).status_code == 409
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin).json()
    assert _by_name(view)["ANA"]["annual"] == "231120.00"  # 10000 × 1,07 × 1,8 × 12


def test_quadro_without_cost_center_uses_default(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    rows = [r[:5] + (None,) + r[6:] for r in QUADRO]
    batch_id = upload(client, admin, builders.quadro_funcionarios(rows), "q.xlsx", cost_center_code=CC2)
    run_worker()
    b = status(client, admin, batch_id)
    assert b["summary"]["comparison"]["movements"][0]["cost_center"] == CC2
    assert b["summary"]["comparison"]["movements"][0]["actions"] == 2


def test_inactive_contract_type_still_computes(client, admin, run_worker):
    ccs, mgr, _ = _setup(client, admin, run_worker)
    r = client.patch("/api/v1/contract-types/PJ", headers=admin, json={"is_active": False})
    assert r.status_code == 200, r.text
    head = client.get(f"/api/v1/personnel/cost-centers/{ccs[CC1]['id']}", headers=admin).json()
    view = client.get(f"/api/v1/personnel/submissions/{head['submission_id']}/view", headers=admin)
    assert view.status_code == 200 and _by_name(view.json())["CARLA"]["annual"] == "15750.00"
    assert client.get("/api/v1/consolidation/overview", headers=admin).status_code == 200
    opts = client.get("/api/v1/personnel/options", headers=admin).json()
    assert "PJ" not in {c["code"] for c in opts["contract_types"]}  # inativo não entra em novas vagas
    # transferência para CC que o gestor não gerencia é recusada
    ana = _by_name(view.json())["ANA"]["employee_id"]
    sub = head["submission_id"]
    resp = client.put(
        f"/api/v1/personnel/submissions/{sub}/employees/{ana}/movement",
        headers=mgr,
        json={"type": "TRANSFER", "month": 7, "target_cost_center_id": ccs[CC2]["id"], "reason": "x"},
    )
    assert resp.status_code == 422 and "gerencia" in resp.json()["detail"]
