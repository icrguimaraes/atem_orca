"""Central de dados: acesso pessoal (não do perfil), linhagem/fontes/etapas, parâmetro com motivo obrigatório e
histórico, concessão do acesso só por quem já o tem."""

from tests.conftest import login

USERS = "/api/v1/users"
C = "/api/v1/control"


def make_user(client, admin, email: str, roles: list[str]) -> tuple[int, dict]:
    resp = client.post(
        USERS, headers=admin, json={"email": email, "name": "Pessoa Teste", "password": "Senha@12345", "roles": roles}
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"], login(client, email, "Senha@12345")


def test_access_is_personal_flag(client, admin):
    me = client.get("/api/v1/auth/me", headers=admin).json()
    assert me["can_control_data"] is True  # administrador inicial do seed
    for path in ("lineage", "sources", "steps", "parameters", "history"):
        assert client.get(f"{C}/{path}", headers=admin).status_code == 200, path
    uid, other = make_user(client, admin, "controller@test.com", ["CONTROLLER"])
    assert client.get(f"{C}/parameters", headers=other).status_code == 403  # perfil Controladoria não basta
    # quem não tem o acesso não concede
    assert client.patch(f"{USERS}/{uid}", headers=other, json={"can_control_data": True}).status_code == 403
    assert client.patch(f"{USERS}/{uid}", headers=admin, json={"can_control_data": True}).status_code == 200
    assert client.get(f"{C}/parameters", headers=other).status_code == 200
    # ninguém retira o próprio acesso
    admin_id = client.get("/api/v1/auth/me", headers=admin).json()["id"]
    assert client.patch(f"{USERS}/{admin_id}", headers=admin, json={"can_control_data": False}).status_code == 409


def test_parameter_change_requires_reason_and_is_audited(client, admin):
    url = f"{C}/parameters/pj.adjustment_pct"
    assert client.put(url, headers=admin, json={"value": 0.04, "reason": ""}).status_code == 422
    assert (
        client.put(f"{C}/parameters/nao.existe", headers=admin, json={"value": 1, "reason": "teste"}).status_code == 404
    )
    resp = client.put(url, headers=admin, json={"value": 0.04, "reason": "IPCA confirmado"})
    assert resp.status_code == 200, resp.text
    p = resp.json()
    assert p["value"] == 0.04 and p["default"] == 0.035 and not p["is_default"] and p["kind"] == "percent"
    assert p["last_change"]["reason"] == "IPCA confirmado" and p["last_change"]["before"] == 0.035
    items = {i["key"]: i for i in client.get(f"{C}/parameters", headers=admin).json()["items"]}
    assert items["pj.adjustment_pct"]["changes"] == 1 and items["personnel.charges_split"]["kind"] == "json"
    history = client.get(f"{C}/history", headers=admin).json()
    assert history[0]["action"] == "SET_PARAMETER" and history[0]["reason"] == "IPCA confirmado"
    # o rateio passa pela mesma validação do Ciclo
    bad = client.put(f"{C}/parameters/personnel.charges_split", headers=admin, json={"value": [1], "reason": "teste"})
    assert bad.status_code == 422


def test_steps_and_lineage_shape(client, admin):
    steps = client.get(f"{C}/steps", headers=admin).json()
    assert [s["title"] for s in steps["pipeline"]][:2] == ["Upload", "Detecção do layout"]
    ksb1 = next(layout for layout in steps["layouts"] if layout["layout"] == "SAP_KSB1")
    assert any(c["field"] == "Centro de custo" for c in ksb1["columns"]) and ksb1["imports"] == 0
    lineage = client.get(f"{C}/lineage", headers=admin).json()
    assert lineage["budget"]["year"] and lineage["actual"] == [] and lineage["reference"] == []
    assert {c["module"] for c in lineage["budget"]["components"]} >= {"OPEX", "CAPEX"}
