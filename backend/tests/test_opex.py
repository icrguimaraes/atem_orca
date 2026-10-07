from decimal import Decimal

from tests import builders
from tests.conftest import login
from tests.test_imports import import_and_load

CC = "1050101011"
HIST_2026 = [
    ("1001", "0001", "MANAUS", CC, "CC A", "G", "6010301001", "Hospedagem", "Viagens", *[1000] * 8, 8000),
    ("1001", "0001", "MANAUS", CC, "CC A", "G", "6010301002", "Telefonia", "DTI", *[500] * 8, 4000),
]
HIST_2025 = [r[:9] + (*[900] * 12, 10800) for r in HIST_2026]


def _user(client, admin, email, roles):
    resp = client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": email, "name": email.split("@")[0], "password": "Senha@123", "roles": roles},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], login(client, email, "Senha@123")


def _setup(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    import_and_load(client, admin, run_worker, builders.realizado_wide(HIST_2026), "r26.xlsx")
    import_and_load(client, admin, run_worker, builders.realizado_wide(HIST_2025, year=2025, months=12), "r25.xlsx")
    mgr_id, mgr = _user(client, admin, "gestor@t.com", ["MANAGER"])
    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == CC)
    client.patch(f"/api/v1/cost-centers/{cc['id']}", headers=admin, json={"manager_user_id": mgr_id})
    opts = client.get("/api/v1/opex/options", headers=admin).json()
    acc = {a["code"]: a for a in opts["accounts"]}
    pkg = {p["name"]: p for p in opts["packages"]}
    return cc, mgr, acc, pkg


def test_opex_full_flow(client, admin, run_worker):
    cc, mgr, acc, pkg = _setup(client, admin, run_worker)
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]

    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=mgr).json()
    sub = head["submission_id"]
    assert head["status"] == "DRAFT" and head["permissions"]["cycle_blocked"]
    line = {
        "account_id": acc["6010301002"]["id"],
        "package_id": pkg["DTI"]["id"],
        "values": {m: 450 for m in range(1, 13)},
    }
    resp = client.post(f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json=line)
    assert resp.status_code == 409 and "não está aberto" in resp.json()["detail"]

    client.post(f"/api/v1/cycles/{cycle_id}/open", headers=admin)
    created = client.post(f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json=line).json()
    assert created[0]["total"] == "5400.00"
    # conta de outro pacote é rejeitada
    bad = line | {"account_id": acc["6010301001"]["id"]}
    assert client.post(f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json=bad).status_code == 422

    travel = {
        "line_type": "TRAVEL",
        "description": "Visita base Belém",
        "travel": {
            "trip_type": "Nacional",
            "job_level": "Gerentes",
            "origin": "AM",
            "destination": "PA",
            "departure_month": 3,
            "return_month": 3,
            "days": 4,
            "ticket_amount": 2000,
        },
    }
    trip = client.post(f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json=travel).json()
    assert sorted(t["total"] for t in trip) == ["2000.00", "3600.00", "600.00"]
    assert len({t["group_ref"] for t in trip}) == 1

    event = {
        "line_type": "EVENT",
        "account_id": acc["6010501002"]["id"],
        "description": "Confraternização",
        "event": {"event_type": "Interno", "month": 12, "people": 50, "structure": 2000},
    }
    ev = client.post(f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json=event).json()
    assert ev[0]["total"] == "5000.00" and ev[0]["values"]["12"] == "5000.00"

    view = client.get(f"/api/v1/opex/submissions/{sub}/accounts", headers=mgr).json()
    rows = {r["code"]: r for r in view["accounts"]}
    assert rows["6010301002"]["ref_annualized"] == "6000.00" and rows["6010301002"]["flags"] == []
    assert rows["6010301001"]["flags"] == ["REDUCTION_ABOVE"]  # 3.600 vs 12.000 anualizado
    assert rows["6010301011"]["flags"] == ["NEW_ACCOUNT"]
    assert view["pending_justifications"] == 3

    # justificativa de conta é recomendada (aviso em Apontamentos), não bloqueia o envio; o gestor justifica antes
    for code in ("6010301001", "6010301011", "6010501002"):
        client.put(
            f"/api/v1/opex/submissions/{sub}/justifications/{acc[code]['id']}",
            headers=mgr,
            json={"text": "Redução de viagens com reuniões online"},
        )
    head = client.post(f"/api/v1/opex/submissions/{sub}/actions/submit", headers=mgr, json={}).json()
    assert head["status"] == "SUBMITTED"
    reviews = {r["package"]: r["status"] for r in head["package_reviews"]}
    assert reviews == {"Viagens": "PENDING", "Comunicação e marketing": "PENDING"}  # DTI é Tipo 2

    # depois de enviado, gestor não edita
    assert client.post(f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json=line).status_code == 409

    # gestor do pacote Viagens valida
    pm_id, pm = _user(client, admin, "viagens@t.com", ["PACKAGE_MANAGER"])
    managers = client.get("/api/v1/package-managers", headers=admin, params={"cycle_id": cycle_id}).json()
    viagens_pm = next(m for m in managers if m["package_id"] == pkg["Viagens"]["id"])
    client.patch(f"/api/v1/package-managers/{viagens_pm['id']}", headers=admin, json={"user_id": pm_id})
    queue = client.get("/api/v1/opex/review-queue", headers=pm).json()
    assert [q["package"] for q in queue] == ["Viagens"]
    assert (
        client.post(
            f"/api/v1/opex/submissions/{sub}/package-reviews/{pkg['DTI']['id']}",
            headers=pm,
            json={"status": "APPROVED"},
        ).status_code
        == 403
    )

    client.post(f"/api/v1/opex/submissions/{sub}/actions/start_review", headers=admin, json={})
    blocked = client.post(f"/api/v1/opex/submissions/{sub}/actions/approve", headers=admin, json={})
    assert blocked.status_code == 409 and "validação GMD" in blocked.json()["detail"]
    client.post(
        f"/api/v1/opex/submissions/{sub}/package-reviews/{pkg['Viagens']['id']}",
        headers=pm,
        json={"status": "APPROVED"},
    )
    client.post(
        f"/api/v1/opex/submissions/{sub}/package-reviews/{pkg['Comunicação e marketing']['id']}",
        headers=admin,
        json={"status": "APPROVED"},
    )
    head = client.post(f"/api/v1/opex/submissions/{sub}/actions/approve", headers=admin, json={}).json()
    assert head["status"] == "APPROVED"
    assert client.post(f"/api/v1/opex/submissions/{sub}/actions/consolidate", headers=mgr, json={}).status_code == 409
    head = client.post(f"/api/v1/opex/submissions/{sub}/actions/consolidate", headers=admin, json={}).json()
    assert head["status"] == "CONSOLIDATED"

    events = client.get(f"/api/v1/opex/submissions/{sub}/events", headers=mgr).json()
    assert [e["action"] for e in events][::-1] == ["start", "submit", "start_review", "approve", "consolidate"]

    summary = client.get("/api/v1/opex/summary", headers=mgr).json()
    assert len(summary["rows"]) == 1 and summary["rows"][0]["status"] == "CONSOLIDATED"
    assert Decimal(summary["rows"][0]["proposed"]) == Decimal("16600.00")


def test_package_adjustment_and_access(client, admin, run_worker):
    cc, mgr, acc, pkg = _setup(client, admin, run_worker)
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.post(f"/api/v1/cycles/{cycle_id}/open", headers=admin)
    sub = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=mgr).json()["submission_id"]
    line = {"account_id": acc["6010301001"]["id"], "values": {m: 1000 for m in range(1, 13)}}
    created = client.post(f"/api/v1/opex/submissions/{sub}/lines", headers=mgr, json=line).json()[0]
    upd = client.patch(f"/api/v1/opex/lines/{created['id']}", headers=mgr, json={"values": {"1": 0, "2": 2000}}).json()
    assert upd["total"] == "12000.00" and upd["values"]["1"] == "0.00"
    client.put(
        f"/api/v1/opex/submissions/{sub}/justifications/{acc['6010301002']['id']}",
        headers=mgr,
        json={"text": "Telefonia migrou para o CC de TI"},
    )
    assert client.post(f"/api/v1/opex/submissions/{sub}/actions/submit", headers=mgr, json={}).status_code == 200

    adj = client.post(
        f"/api/v1/opex/submissions/{sub}/package-reviews/{pkg['Viagens']['id']}",
        headers=admin,
        json={"status": "ADJUST_REQUESTED"},
    )
    assert adj.status_code == 422  # comentário obrigatório
    head = client.post(
        f"/api/v1/opex/submissions/{sub}/package-reviews/{pkg['Viagens']['id']}",
        headers=admin,
        json={"status": "ADJUST_REQUESTED", "comment": "Detalhar destinos"},
    ).json()
    assert head["status"] == "ADJUSTMENT_REQUESTED" and head["permissions"]["edit"]  # Controladoria também ajusta
    assert client.get(f"/api/v1/opex/submissions/{sub}", headers=mgr).json()["permissions"]["edit"]

    _, other = _user(client, admin, "outro@t.com", ["MANAGER"])
    assert client.get(f"/api/v1/opex/submissions/{sub}", headers=other).status_code == 403
    assert client.get("/api/v1/opex/summary", headers=other).json()["rows"] == []


def test_chart_data(client, admin, run_worker):
    cc, mgr, acc, pkg = _setup(client, admin, run_worker)
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    client.post(f"/api/v1/cycles/{cycle_id}/open", headers=admin)
    sub = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=mgr).json()["submission_id"]
    client.post(
        f"/api/v1/opex/submissions/{sub}/lines",
        headers=mgr,
        json={"account_id": acc["6010301002"]["id"], "values": {m: 600 for m in range(1, 13)}},
    )

    view = client.get(f"/api/v1/opex/submissions/{sub}/accounts", headers=mgr).json()
    assert view["monthly"]["ref"][:8] == ["1500.00"] * 8 and view["monthly"]["ref"][8] == "0.00"
    assert view["monthly"]["proposed"] == ["600.00"] * 12
    tel = next(r for r in view["accounts"] if r["code"] == "6010301002")
    assert tel["ref_monthly"][0] == "500.00"

    data = client.get("/api/v1/dashboard/overview", headers=admin).json()
    assert data["heatmap"]["year"] == 2026 and data["heatmap"]["rows"][0]["values"][0] == "1500.00"
    accounts = client.get("/api/v1/dashboard/breakdown?group_by=account", headers=admin).json()
    deltas = {r["code"]: r["var"] for r in accounts["rows"]}
    assert deltas["6010301001"] == "800.00"  # 8×1000 (2026) − 8×900 (2025 até ago)
    progress = data["budget_progress"]
    assert progress["started_cost_centers"] == 1 and progress["status_counts"]["IN_PROGRESS"] == 1
    dti = next(p for p in progress["by_package"] if p["package"] == "DTI")
    assert dti == {"package_id": pkg["DTI"]["id"], "package": "DTI", "proposed": "7200.00", "ref_annualized": "6000.00"}
    assert client.get("/api/v1/opex/summary", headers=mgr).json()["progress"]["proposed_total"] == "7200.00"
    # Painel com o ano do ciclo: o orçamento proposto entra como orçado (verde), comparado com 2026 anualizado
    plan = client.get("/api/v1/dashboard/overview?years=2027", headers=admin).json()
    assert plan["period"]["main"] == "budget" and plan["kpis"]["budget_total"] == "7200.00"
    assert plan["monthly"][0]["budget"] == "600.00" and plan["period"]["base_label"] == "Realizado 2026 anualizado"
    assert plan["period"]["annualized_base"] is True
    capex_only = client.get("/api/v1/dashboard/overview?years=2027&modules=CAPEX", headers=admin).json()
    assert capex_only["kpis"]["budget_total"] == "0.00"
