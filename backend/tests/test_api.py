from tests.conftest import login


def _create_user(client, admin, email, roles):
    resp = client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": email, "name": "Usuário Teste", "password": "Senha@123", "roles": roles},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health_and_login(client, admin):
    assert client.get("/api/health").json() == {"status": "ok"}
    me = client.get("/api/v1/auth/me", headers=admin).json()
    assert me["roles"] == ["ADMIN"]
    assert client.post("/api/v1/auth/login", json={"email": "admin@test.com", "password": "errada"}).status_code == 401
    assert client.get("/api/v1/auth/me").status_code == 401


def test_rbac_and_cost_center_scope(client, admin):
    _create_user(client, admin, "gestor@test.com", ["MANAGER"])
    manager = login(client, "gestor@test.com", "Senha@123")
    # gestor não altera cadastros nem importa
    company = {"code": "3001", "name": "NAVEMAZONIA"}
    assert client.post("/api/v1/companies", headers=manager, json=company).status_code == 403
    assert client.get("/api/v1/imports", headers=manager).status_code == 403
    # admin cria empresa e CC atribuído ao gestor
    company_id = client.post("/api/v1/companies", headers=admin, json=company).json()["id"]
    me_id = client.get("/api/v1/auth/me", headers=manager).json()["id"]
    cc = {"company_id": company_id, "code": "3001001", "name": "CC DO GESTOR", "manager_user_id": me_id}
    assert client.post("/api/v1/cost-centers", headers=admin, json=cc).status_code == 201
    other = {"company_id": company_id, "code": "3001002", "name": "OUTRO CC"}
    client.post("/api/v1/cost-centers", headers=admin, json=other)
    visible = client.get("/api/v1/cost-centers", headers=manager).json()
    assert [c["code"] for c in visible] == ["3001001"]
    assert len(client.get("/api/v1/cost-centers", headers=admin).json()) == 2


def test_crud_is_audited_and_validated(client, admin):
    accounts = client.get("/api/v1/accounts", headers=admin, params={"q": "Hosped"}).json()
    assert accounts[0]["code"] == "6010301001"
    acc_id = accounts[0]["id"]
    resp = client.patch(f"/api/v1/accounts/{acc_id}", headers=admin, json={"name": "Hospedagem (ajustado)"})
    assert resp.status_code == 200 and resp.json()["name"] == "Hospedagem (ajustado)"
    assert client.patch(f"/api/v1/accounts/{acc_id}", headers=admin, json={"code": "abc"}).status_code == 422
    logs = client.get("/api/v1/audit-logs", headers=admin, params={"entity_type": "account"}).json()
    assert logs["items"][0]["before"] == {"name": "Hospedagem"}
    assert logs["items"][0]["after"] == {"name": "Hospedagem (ajustado)"}
    dup = client.post("/api/v1/companies", headers=admin, json={"code": "1001", "name": "Duplicada"})
    assert dup.status_code == 409


def test_cycle_parameters_and_lookups(client, admin):
    cycle = client.get("/api/v1/cycles", headers=admin).json()[0]
    assert cycle["fiscal_year"] == 2027 and cycle["status"] == "DRAFT"
    params = {
        p["key"]: p["value"] for p in client.get(f"/api/v1/cycles/{cycle['id']}/parameters", headers=admin).json()
    }
    assert params["alert.growth_pct"] == 0.2
    client.put(f"/api/v1/cycles/{cycle['id']}/parameters/alert.growth_pct", headers=admin, json={"value": 0.15})
    assert client.post(f"/api/v1/cycles/{cycle['id']}/open", headers=admin).json()["status"] == "OPEN"
    assert client.post(f"/api/v1/cycles/{cycle['id']}/open", headers=admin).status_code == 409
    versions = client.get(f"/api/v1/cycles/{cycle['id']}/versions", headers=admin).json()
    assert versions[0]["label"] == "1.0"
    types = client.get("/api/v1/lookups", headers=admin, params={"domain": "CAPEX_PROJECT_TYPE"}).json()
    assert len(types) == 8
    contracts = {c["code"]: c for c in client.get("/api/v1/contract-types", headers=admin).json()}
    assert contracts["CLT"]["default_multiplier"] == "1.800000" and not contracts["PJ"]["apply_multiplier"]


def test_rule_endpoints(client, admin):
    cycle_id = client.get("/api/v1/cycles", headers=admin).json()[0]["id"]
    travel = client.post(
        "/api/v1/rules/travel",
        headers=admin,
        json={
            "cycle_id": cycle_id,
            "trip_type": "Nacional",
            "job_level": "Gerentes",
            "origin": "AM",
            "destination": "SP",
            "departure_month": 4,
            "return_month": 4,
            "days": 3,
        },
    ).json()
    assert travel["lodging"] == "2700.00" and travel["per_diem"] == "450.00" and travel["warnings"]
    event = client.post(
        "/api/v1/rules/event", headers=admin, json={"event_type": "Interno", "month": 12, "people": 100}
    ).json()
    assert event["total"] == "6000.00"
    capex = client.post(
        "/api/v1/rules/capex-item",
        headers=admin,
        json={
            "cycle_id": cycle_id,
            "unit_value": 5000,
            "quantity": 2,
            "schedule": {"1": 5000, "2": 4000},
            "is_project": True,
            "project_type": "Segurança",
            "justification": "Adequação NR",
        },
    ).json()
    assert not capex["is_consistent"] and capex["difference"] == "-1000.00"
    what_if = client.post(
        "/api/v1/rules/personnel/what-if",
        headers=admin,
        json={
            "positions": [
                {"key": "a", "base_salary": 10000, "contract_type": "CLT"},
                {"key": "b", "base_salary": 10000, "contract_type": "PJ"},
            ],
            "multipliers": {"CLT": 2.0, "PJ": 3.0},
        },
    ).json()
    assert what_if["current_annual"] == "336000.00" and what_if["projected_annual"] == "360000.00"
    assert what_if["ignored_multiplier_for"] == ["PJ"]


def test_change_password_and_login_rate_limit(client, admin):
    from tests.conftest import login

    created = client.post(
        "/api/v1/users",
        headers=admin,
        json={"email": "x@t.com", "name": "Usuário X", "password": "Senha@123", "roles": ["VIEWER"]},
    )
    assert created.status_code == 201, created.text
    h = login(client, "x@t.com", "Senha@123")
    bad = client.post(
        "/api/v1/auth/change-password", headers=h, json={"current_password": "errada", "new_password": "NovaSenha@1"}
    )
    assert bad.status_code == 400
    short = client.post(
        "/api/v1/auth/change-password", headers=h, json={"current_password": "Senha@123", "new_password": "curta"}
    )
    assert short.status_code == 422
    ok = client.post(
        "/api/v1/auth/change-password", headers=h, json={"current_password": "Senha@123", "new_password": "NovaSenha@1"}
    )
    assert ok.status_code == 204
    assert client.post("/api/v1/auth/login", json={"email": "x@t.com", "password": "Senha@123"}).status_code == 401
    assert client.post("/api/v1/auth/login", json={"email": "x@t.com", "password": "NovaSenha@1"}).status_code == 200
    # 5 falhas bloqueiam a 6ª tentativa, mesmo com a senha certa
    for _ in range(5):
        client.post("/api/v1/auth/login", json={"email": "bloq@t.com", "password": "x"})
    assert client.post("/api/v1/auth/login", json={"email": "bloq@t.com", "password": "x"}).status_code == 429
    r = client.get("/api/v1/auth/me", headers=h)
    assert r.headers.get("x-content-type-options") == "nosniff" and r.headers.get("x-frame-options") == "DENY"
