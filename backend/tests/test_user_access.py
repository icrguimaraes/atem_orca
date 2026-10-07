"""Usuários → Acessos: Controladoria atribui CCs (ou empresa inteira) a um usuário; tudo auditado."""

from tests.conftest import login

USERS = "/api/v1/users"


def _user(client, admin, email, roles):
    resp = client.post(
        USERS,
        headers=admin,
        json={"email": email, "name": email.split("@")[0], "password": "Senha@123", "roles": roles},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], login(client, email, "Senha@123")


def _setup(client, admin):
    """Duas empresas: A com CC do gestor + CC livre; B com um CC. Gestor e Controladoria criados."""
    mgr_id, mgr = _user(client, admin, "gestor.acesso@t.com", ["MANAGER"])
    comp_a = client.post("/api/v1/companies", headers=admin, json={"code": "3001", "name": "EMPRESA A"}).json()["id"]
    comp_b = client.post("/api/v1/companies", headers=admin, json={"code": "3002", "name": "EMPRESA B"}).json()["id"]
    ccs = {}
    for code, company, manager in (("3001001", comp_a, mgr_id), ("3001002", comp_a, None), ("3002001", comp_b, None)):
        cc = {"company_id": company, "code": code, "name": f"CC {code}", "manager_user_id": manager}
        resp = client.post("/api/v1/cost-centers", headers=admin, json=cc)
        assert resp.status_code == 201, resp.text
        ccs[code] = resp.json()["id"]
    return mgr_id, mgr, comp_b, ccs


def _visible(client, headers) -> list[str]:
    return [c["code"] for c in client.get("/api/v1/cost-centers", headers=headers).json()]


def _summary(client, headers, user_id) -> dict:
    return next(u for u in client.get(USERS, headers=headers).json() if u["id"] == user_id)["access"]


def test_assign_and_remove_cost_center_access(client, admin):
    mgr_id, mgr, comp_b, ccs = _setup(client, admin)
    _, ctrl = _user(client, admin, "controladoria@t.com", ["CONTROLLER"])
    assert _visible(client, mgr) == ["3001001"]

    access = client.get(f"{USERS}/{mgr_id}/access", headers=ctrl).json()
    assert access["is_global"] is False and access["cost_centers"] == 1
    assert [c["code"] for c in access["managed"]] == ["3001001"] and access["scopes"] == []

    # Controladoria libera um CC: o gestor passa a enxergá-lo
    resp = client.post(f"{USERS}/{mgr_id}/scopes", headers=ctrl, json={"cost_center_id": ccs["3001002"]})
    assert resp.status_code == 201, resp.text
    scope = resp.json()["scopes"][0]
    assert scope["kind"] == "COST_CENTER" and scope["code"] == "3001002" and resp.json()["cost_centers"] == 2
    assert _visible(client, mgr) == ["3001001", "3001002"]
    assert _summary(client, ctrl, mgr_id) == {"is_global": False, "cost_centers": 2, "managed": 1, "scopes": 1}
    assert _summary(client, ctrl, client.get("/api/v1/auth/me", headers=admin).json()["id"])["is_global"] is True

    # recusas: repetido, CC de que já é gestor, nenhum alvo, CC inexistente
    dup = client.post(f"{USERS}/{mgr_id}/scopes", headers=ctrl, json={"cost_center_id": ccs["3001002"]})
    assert dup.status_code == 409
    own = client.post(f"{USERS}/{mgr_id}/scopes", headers=ctrl, json={"cost_center_id": ccs["3001001"]})
    assert own.status_code == 409 and "gestor" in own.json()["detail"]
    assert client.post(f"{USERS}/{mgr_id}/scopes", headers=ctrl, json={}).status_code == 422
    assert client.post(f"{USERS}/{mgr_id}/scopes", headers=ctrl, json={"cost_center_id": 999999}).status_code == 404

    # empresa inteira libera todos os CCs dela
    resp = client.post(f"{USERS}/{mgr_id}/scopes", headers=ctrl, json={"company_id": comp_b})
    assert resp.status_code == 201, resp.text
    company_scope = next(s for s in resp.json()["scopes"] if s["kind"] == "COMPANY")
    assert company_scope["cost_centers"] == 1 and resp.json()["cost_centers"] == 3
    assert _visible(client, mgr) == ["3001001", "3001002", "3002001"]

    # remover tira o acesso
    resp = client.delete(f"{USERS}/{mgr_id}/scopes/{scope['id']}", headers=ctrl)
    assert resp.status_code == 200, resp.text
    assert _visible(client, mgr) == ["3001001", "3002001"]
    resp = client.delete(f"{USERS}/{mgr_id}/scopes/{company_scope['id']}", headers=ctrl)
    assert resp.json()["scopes"] == [] and _visible(client, mgr) == ["3001001"]
    assert client.delete(f"{USERS}/{mgr_id}/scopes/{scope['id']}", headers=ctrl).status_code == 404

    # auditoria: antes/depois de cada alteração
    logs = client.get("/api/v1/audit-logs", headers=admin, params={"entity_type": "user"}).json()["items"]
    actions = [log["action"] for log in logs if log["entity_id"] == str(mgr_id)]
    assert actions.count("ADD_SCOPE") == 2 and actions.count("REMOVE_SCOPE") == 2
    added = next(log for log in reversed(logs) if log["action"] == "ADD_SCOPE")
    assert added["before"] == {"scopes": []}
    assert added["after"]["scopes"][0]["label"] == "CC 3001002 · CC 3001002"


def test_only_controller_changes_access(client, admin):
    mgr_id, mgr, comp_b, ccs = _setup(client, admin)
    other_id, other = _user(client, admin, "outro.gestor@t.com", ["MANAGER"])
    body = {"cost_center_id": ccs["3001002"]}
    # gestor não vê nem altera acessos (nem os próprios)
    assert client.get(f"{USERS}/{mgr_id}/access", headers=mgr).status_code == 403
    assert client.post(f"{USERS}/{mgr_id}/scopes", headers=mgr, json=body).status_code == 403
    assert client.post(f"{USERS}/{other_id}/scopes", headers=mgr, json=body).status_code == 403
    assert client.put(f"{USERS}/{mgr_id}/scopes", headers=mgr, json=[body]).status_code == 403
    assert "3001002" not in _visible(client, mgr)
    # administrador altera; o gestor não remove
    resp = client.post(f"{USERS}/{other_id}/scopes", headers=admin, json=body)
    assert resp.status_code == 201, resp.text
    scope_id = resp.json()["scopes"][0]["id"]
    assert client.delete(f"{USERS}/{other_id}/scopes/{scope_id}", headers=other).status_code == 403
    assert client.delete(f"{USERS}/{mgr_id}/scopes/{scope_id}", headers=admin).status_code == 404  # escopo de outro
    assert _visible(client, other) == ["3001002"]
