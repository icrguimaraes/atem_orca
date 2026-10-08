"""Defesa do orçamento: "por quê?" de uma linha do Painel e perguntas ao gestor da área."""

from tests import builders
from tests.test_imports import import_and_load
from tests.test_opex import _user


def test_why_and_question_flow(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t-defesa.xlsx")
    why = client.get("/api/v1/dashboard/why?modules=OPEX", headers=admin)
    assert why.status_code == 200, why.text
    w = why.json()
    assert w["total"]["proposed"] != "0.00" and w["by_module"][0]["module"] == "OPEX"
    assert w["drivers"]["accounts"] and w["opex"]
    assert w["missing"] == sum(1 for i in w["opex"] if not i["justified"]) > 0
    scope = w["scope"]["cost_center_ids"]
    assert scope

    # justifica uma conta: o "por quê?" passa a trazer o texto do gestor
    first = next(i for i in w["opex"] if not i["justified"])
    r = client.put("/api/v1/justifications", headers=admin, json={"key": first["key"], "text": "Contrato novo de 2027"})
    assert r.status_code == 200, r.text
    again = client.get("/api/v1/dashboard/why?modules=OPEX", headers=admin).json()
    item = next(i for i in again["opex"] if i["key"] == first["key"])
    assert item["justified"] and item["text"] == "Contrato novo de 2027" and again["missing"] == w["missing"] - 1

    # o VP (consulta) pergunta; o gestor do CC responde; quem perguntou encerra — tudo na auditoria
    cc_id = scope[0]
    mgr_id, mgr = _user(client, admin, "gestor.defesa@t.com", ["MANAGER"])
    assert (
        client.patch(f"/api/v1/cost-centers/{cc_id}", headers=admin, json={"manager_user_id": mgr_id}).status_code
        == 200
    )
    vp_id, vp = _user(client, admin, "vp.defesa@t.com", ["VIEWER"])
    client.post(f"/api/v1/users/{vp_id}/scopes", headers=admin, json={"cost_center_id": cc_id})
    body = {
        "subject": first["subject"],
        "question": "Por que essa conta cresceu?",
        "scope": {"cost_center_ids": [cc_id]},
    }
    asked = client.post("/api/v1/questions", headers=admin, json=body)
    assert asked.status_code == 201, asked.text
    q = asked.json()
    assert q["status"] == "OPEN" and q["mine"]

    inbox = client.get("/api/v1/questions?to_answer=true", headers=mgr).json()
    assert [x["id"] for x in inbox["items"]] == [q["id"]] and inbox["counts"]["to_answer"] == 1
    assert client.post(f"/api/v1/questions/{q['id']}/answer", headers=vp, json={"answer": "x"}).status_code in (
        403,
        404,
    )
    ans = client.post(f"/api/v1/questions/{q['id']}/answer", headers=mgr, json={"answer": "Novo contrato de auditoria"})
    assert ans.status_code == 200 and ans.json()["status"] == "ANSWERED"
    assert client.post(f"/api/v1/questions/{q['id']}/close", headers=mgr).status_code == 403  # só quem perguntou
    closed = client.post(f"/api/v1/questions/{q['id']}/close", headers=admin)
    assert closed.status_code == 200 and closed.json()["status"] == "CLOSED"

    in_why = client.get("/api/v1/dashboard/why?modules=OPEX", headers=admin).json()["questions"]
    assert in_why[0]["answer"] == "Novo contrato de auditoria"
    logs = client.get("/api/v1/audit-logs?entity_type=budget_question", headers=admin).json()
    actions = {x["action"] for x in (logs["items"] if isinstance(logs, dict) else logs)}
    assert {"QUESTION", "ANSWER", "CLOSE"} <= actions
