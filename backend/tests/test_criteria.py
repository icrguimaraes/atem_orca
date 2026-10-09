def test_budget_criteria_seeded_and_editable(client, admin):
    """Critérios do orçamento (informativo no Painel): vêm do seed e a Controladoria edita (parâmetro auditado)."""
    data = client.get("/api/v1/dashboard/criteria", headers=admin).json()
    items = {c["item"]: c["texto"] for c in data["items"]}
    assert {"Passagens", "Pessoal", "Valores rescisórios", "Abono sindical", "Bônus anual"} <= set(items)
    assert "1,8" in items["Pessoal"] and "2.500" in items["Abono sindical"]
    new = [{"item": "Pessoal", "texto": "Salário + dissídio"}, {"item": "", "texto": ""}]
    r = client.put("/api/v1/dashboard/criteria", headers=admin, json={"items": new})
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [{"item": "Pessoal", "texto": "Salário + dissídio"}]  # linha vazia sai
    assert client.get("/api/v1/dashboard/criteria", headers=admin).json()["items"] == r.json()["items"]
    too_long = client.put(
        "/api/v1/dashboard/criteria", headers=admin, json={"items": [{"item": "x", "texto": "y" * 601}]}
    )
    assert too_long.status_code == 422
