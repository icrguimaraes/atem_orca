"""Rastro (09/10/2026): do total do Painel ao lançamento de origem."""

from decimal import Decimal

from tests import builders
from tests.test_imports import import_and_load
from tests.test_opex import HIST_2025, HIST_2026, _user
from tests.test_pj import contract, grant

PARENT = {
    "department": ("parent_department_id", "parent_no_department"),
    "area": ("parent_area_id", "parent_no_area"),
    "cost_center": ("parent_cost_center_id", None),
    "package": ("parent_package_id", "parent_no_package"),
    "account": ("parent_account_id", None),
}


def _setup(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t-rastro.xlsx")
    import_and_load(client, admin, run_worker, builders.realizado_wide(HIST_2026), "r26.xlsx")
    import_and_load(client, admin, run_worker, builders.realizado_wide(HIST_2025, year=2025, months=12), "r25.xlsx")
    ccs = {c["code"]: c for c in client.get("/api/v1/cost-centers", headers=admin).json()}
    return ccs["1050101011"], ccs["1050101012"]


def _tree(client, headers, level, **params):
    r = client.get("/api/v1/trace/tree", headers=headers, params={"level": level, **params})
    assert r.status_code == 200, r.text
    return r.json()


def _entries(client, headers, **params):
    r = client.get("/api/v1/trace/entries", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _own(level, row) -> dict:
    by_id, none = PARENT[level]
    if row["id"] is None:
        assert none, f"nível {level} sem linha 'Sem …'"
        return {none: "true"}
    return {by_id: row["id"]}


def _descend(client, headers, **params) -> dict:
    """Desce pelo maior item de cada nível, conferindo que o total do nível é o valor da linha de cima."""
    levels = ["department", "area", "cost_center", "package", "account"]
    chain = dict(params)
    above = None
    for level in levels:
        t = _tree(client, headers, level, **chain)
        assert t["rows"] and t["level"] == level
        assert sum(Decimal(r["value"]) for r in t["rows"]) == Decimal(t["total"])
        if above is not None:
            assert Decimal(t["total"]) == above, (level, t["total"], above)
        top = t["rows"][0]
        assert top["share"] is not None and top["children"] >= 1
        above = Decimal(top["value"])
        chain |= _own(level, top)
    return chain | {"_leaf_value": above}


def test_tree_sums_like_painel_and_drills_to_the_opex_line(client, admin, run_worker):
    cc_a, _cc_b = _setup(client, admin, run_worker)
    painel = client.get(
        "/api/v1/dashboard/breakdown", headers=admin, params={"group_by": "department", "years": 2027, "compare": False}
    ).json()
    root = _tree(client, admin, "department", years=2027)
    assert root["series"] == "budget" and root["total"] == painel["total"]["ref"] and root["total"] != "0.00"
    assert root["next_level"] == "area" and root["trail"] == []
    assert {r["id"]: r["value"] for r in root["rows"]} == {r["id"]: r["ref"] for r in painel["rows"]}

    # mesmo com o filtro de tipo e de CC do Painel
    opex = client.get(
        "/api/v1/dashboard/breakdown",
        headers=admin,
        params={
            "group_by": "package",
            "years": 2027,
            "modules": "OPEX",
            "cost_center_id": cc_a["id"],
            "compare": False,
        },
    ).json()
    pk = _tree(client, admin, "package", years=2027, modules="OPEX", cost_center_id=cc_a["id"])
    assert pk["total"] == opex["total"]["ref"]

    chain = _descend(client, admin, years=2027, modules="OPEX")
    leaf_value = chain.pop("_leaf_value")
    leaf = _tree(client, admin, "account", **{k: v for k, v in chain.items() if k != "parent_account_id"})
    assert [c["level"] for c in leaf["trail"]] == ["department", "area", "cost_center", "package"]
    acc_row = next(r for r in leaf["rows"] if r["id"] == chain["parent_account_id"])

    # a conta abre nos lançamentos: a soma das linhas é o valor da conta e cada uma leva ao orçamento do CC
    e = _entries(client, admin, **chain)
    assert e["count"] == acc_row["children"] >= 1 and len(e["items"]) == e["count"]
    assert Decimal(e["level_total"]) == leaf_value
    assert sum(Decimal(i["total"]) for i in e["items"]) == Decimal(e["total"]) == leaf_value
    assert e["difference"] == "0.00"
    totals = [Decimal(i["total"]) for i in e["items"]]
    assert totals == sorted(totals, reverse=True)
    line = e["items"][0]
    assert line["kind"] == "OPEX_LINE" and line["account"]["id"] == chain["parent_account_id"]
    assert line["link"].startswith(f"/orcamento/{line['cost_center']['id']}?pacote=") and "&linha=" in line["link"]
    assert len(line["values"]) == 12 and line["source"]["label"].startswith("Orçamento OPEX")

    # busca e paginação
    page = _entries(client, admin, years=2027, modules="OPEX", limit=1)
    assert len(page["items"]) == 1 and page["count"] > 1
    second = _entries(client, admin, years=2027, modules="OPEX", limit=1, offset=1)
    assert second["items"][0]["id"] != page["items"][0]["id"]
    word = line["title"].split()[0]
    found = _entries(client, admin, years=2027, modules="OPEX", search=word)
    assert found["items"] and all(word.lower() in (i["title"] + (i["detail"] or "")).lower() for i in found["items"])

    # Excel do recorte
    x = client.get("/api/v1/trace/entries.xlsx", headers=admin, params=chain)
    assert x.status_code == 200 and x.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert len(x.content) > 2000


def test_actual_entries_carry_the_import_batch(client, admin, run_worker):
    cc_a, _cc_b = _setup(client, admin, run_worker)
    painel = client.get(
        "/api/v1/dashboard/breakdown", headers=admin, params={"group_by": "account", "years": 2026, "compare": False}
    ).json()
    t = _tree(client, admin, "account", years=2026)
    assert t["series"] == "actual" and t["total"] == painel["total"]["ref"] == "12000.00"
    hosp = next(r for r in t["rows"] if r["code"] == "6010301001")
    assert hosp["value"] == "8000.00" and hosp["children"] == 8  # uma partida por mês carregado

    e = _entries(client, admin, years=2026, parent_account_id=hosp["id"])
    assert e["count"] == 8 and e["total"] == "8000.00" == e["level_total"]
    first = e["items"][0]
    assert first["kind"] == "ACTUAL" and first["month"] in range(1, 9) and first["total"] == "1000.00"
    assert first["source"]["file_name"] == "r26.xlsx" and first["source"]["batch_id"]
    assert first["source"]["loaded_at"] and isinstance(first["source"]["version"], int)
    assert first["link"] == f"/importacoes/{first['source']['batch_id']}"
    assert first["cost_center"]["id"] == cc_a["id"]

    # filtro de mês e busca pelo texto da conta
    march = _entries(client, admin, years=2026, parent_account_id=hosp["id"], months="3")
    assert march["count"] == 1 and march["items"][0]["month"] == 3
    assert _entries(client, admin, years=2026, search="Hospedagem")["count"] == 8
    assert _entries(client, admin, years=2026, search="nada-disso")["count"] == 0

    # anos somados: 2025 (12 meses) + 2026 (8 meses)
    both = _tree(client, admin, "cost_center", years="2025,2026")
    assert both["total"] == "33600.00" and both["rows"][0]["children"] == 2  # dois pacotes
    assert _entries(client, admin, years="2025,2026")["count"] == 40


def test_manager_sees_only_own_cost_centers(client, admin, run_worker):
    cc_a, cc_b = _setup(client, admin, run_worker)
    import_and_load(client, admin, run_worker, builders.opex_template_filled("1050101012"), "t-rastro-b.xlsx")
    mgr_id, mgr = _user(client, admin, "gestor.rastro@t.com", ["MANAGER"])
    assert (
        client.patch(f"/api/v1/cost-centers/{cc_a['id']}", headers=admin, json={"manager_user_id": mgr_id}).status_code
        == 200
    )
    all_ccs = {r["id"] for r in _tree(client, admin, "cost_center", years=2027)["rows"]}
    assert {cc_a["id"], cc_b["id"]} <= all_ccs
    mine = _tree(client, mgr, "cost_center", years=2027)
    assert {r["id"] for r in mine["rows"]} == {cc_a["id"]}
    items = _entries(client, mgr, years=2027)["items"]
    assert items and {i["cost_center"]["id"] for i in items} == {cc_a["id"]}
    # pedir o CC do outro gestor não devolve nada
    assert _entries(client, mgr, years=2027, parent_cost_center_id=cc_b["id"])["count"] == 0
    assert _tree(client, mgr, "account", years=2027, parent_cost_center_id=cc_b["id"])["rows"] == []
    # o realizado também respeita o escopo (só o CC A tem realizado; o gestor vê o mesmo que o admin nele)
    assert _entries(client, mgr, years=2026)["total"] == _entries(client, admin, years=2026)["total"]


def test_pj_contracts_only_for_who_has_access(client, admin, run_worker):
    cc_a, _cc_b = _setup(client, admin, run_worker)
    me = client.get("/api/v1/auth/me", headers=admin).json()
    grant(client, admin, me["id"], all_=True)
    body = contract(1, cost_center_id=cc_a["id"], start_date="2027-01-01")
    assert client.post("/api/v1/pj", headers=admin, json=body).status_code == 201
    assert client.post("/api/v1/pj", headers=admin, json=contract(2, cost_center_id=cc_a["id"])).status_code == 201
    mgr_id, mgr = _user(client, admin, "gestor.pj.rastro@t.com", ["MANAGER"])
    client.patch(f"/api/v1/cost-centers/{cc_a['id']}", headers=admin, json={"manager_user_id": mgr_id})

    full = _entries(client, admin, years=2027, modules="OPEX", search="PJ", parent_cost_center_id=cc_a["id"])
    named = [i for i in full["items"] if i["kind"] == "PJ"]
    assert len(named) == 2 and {i["title"] for i in named} == {"PESSOA PJ 1", "PESSOA PJ 2"}
    assert all(i["link"] == "/pj" and i["account"]["code"] == "6010201016" for i in named)
    pj_total = sum(Decimal(i["total"]) for i in named)
    assert pj_total >= Decimal("10000.00") * 24 + Decimal("12000.00") * 2  # com o reajuste do ciclo, se houver

    hidden = _entries(client, mgr, years=2027, modules="OPEX", parent_cost_center_id=cc_a["id"])
    kinds = {i["kind"] for i in hidden["items"]}
    assert "PJ" not in kinds and "PJ_GROUP" in kinds
    group = next(i for i in hidden["items"] if i["kind"] == "PJ_GROUP")
    assert group["title"] == "Contratos PJ · 2 contratos" and group["link"] is None
    assert Decimal(group["total"]) == pj_total
    assert "PESSOA" not in str(hidden)
    # o total do recorte é o mesmo para os dois (o gestor só não vê os nomes)
    assert (
        hidden["total"]
        == _entries(client, admin, years=2027, modules="OPEX", parent_cost_center_id=cc_a["id"])["total"]
    )
    # ... e bate com o Painel
    painel = client.get(
        "/api/v1/dashboard/breakdown",
        headers=mgr,
        params={"group_by": "account", "years": 2027, "modules": "OPEX", "compare": False},
    ).json()
    assert hidden["level_total"] == painel["total"]["ref"] == hidden["total"]
