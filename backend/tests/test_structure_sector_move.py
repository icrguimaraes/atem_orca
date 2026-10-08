"""Setor que muda de área leva os seus centros de custo (o Painel agrupa a área pelo CC)."""


def test_moving_sector_moves_its_cost_centers(client, admin):
    dept_a = client.post("/api/v1/departments", headers=admin, json={"name": "Área A"}).json()
    dept_b = client.post("/api/v1/departments", headers=admin, json={"name": "Área B"}).json()
    sector = client.post("/api/v1/areas", headers=admin, json={"name": "Setor X", "department_id": dept_a["id"]}).json()
    company = client.post("/api/v1/companies", headers=admin, json={"code": "3901", "name": "EMPRESA SETOR"}).json()
    body = {"company_id": company["id"], "code": "3901001", "name": "CC SETOR", "area_id": sector["id"]}
    r = client.post("/api/v1/cost-centers", headers=admin, json=body | {"department_id": dept_a["id"]})
    assert r.status_code == 201, r.text
    cc = r.json()

    r = client.patch(f"/api/v1/areas/{sector['id']}", headers=admin, json={"department_id": dept_b["id"]})
    assert r.status_code == 200, r.text
    moved = client.get(f"/api/v1/cost-centers/{cc['id']}", headers=admin).json()
    assert moved["department_id"] == dept_b["id"] and moved["area_id"] == sector["id"]
    logs = client.get("/api/v1/audit-logs?entity_type=cost_center", headers=admin).json()
    items = logs["items"] if isinstance(logs, dict) else logs
    assert any("mudou de área" in str(x.get("after")) for x in items)
