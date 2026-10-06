"""Exportação do orçamento do CC no layout do template e reimportação (ida e volta)."""

from decimal import Decimal

from openpyxl import load_workbook

from app.imports.base import load_sheets
from app.imports.parsers.capex_template import parse_capex_template
from app.imports.parsers.opex_template import parse_opex_template
from tests import builders
from tests.test_imports import import_and_load
from tests.test_opex import CC, _user

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _cc(client, admin):
    return next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == CC)


def test_opex_template_export_roundtrip(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "template.xlsx")
    cc = _cc(client, admin)
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    sub = head["submission_id"]
    # linha digitada no sistema (sem origem template) também vai para a planilha
    acc = next(
        a for a in client.get("/api/v1/opex/options", headers=admin).json()["accounts"] if a["code"] == "6010301005"
    )
    client.post(
        f"/api/v1/opex/submissions/{sub}/lines",
        headers=admin,
        json={"account_id": acc["id"], "values": {"1": 100}, "description": "Digitada", "supplier": "Loja"},
    )
    import io

    resp = client.get(f"/api/v1/opex/submissions/{sub}/template.xlsx", headers=admin)
    assert resp.status_code == 200 and resp.headers["content-type"] == XLSX
    assert resp.headers["content-disposition"].endswith(f'filename="Template_OPEX_2027_{CC}_v1.0.xlsx"')
    content = resp.content

    wb = load_workbook(io.BytesIO(content))
    assert wb.sheetnames[:3] == ["Instruções", "BD-Novo", "I - Viagens"] and "VI - DTI" in wb.sheetnames
    viagens = wb["I - Viagens"]
    purposes = {viagens.cell(r, 9).value for r in range(7, 9)}
    assert purposes == {"Auditoria SP", "Visita base Belém"}

    # o importador lê a planilha exportada como template OPEX e encontra os mesmos valores
    sheets = load_sheets(content, "export.xlsx")
    result = parse_opex_template(sheets, {})
    assert result.meta["system_export"] is True
    assert result.meta["parts"]["budget_lines"] == 5  # 2 viagens + consultoria + DTI + linha digitada
    assert result.meta["consolidator_check"]["I - Viagens"]["ok"] is True
    budget = [r for r in result.records if r.record_type in ("BUDGET_LINE", "TRAVEL")]
    assert not any(r.issues for r in budget), [r.issues for r in budget]
    total = sum((Decimal(v) for r in budget for v in r.data["values"].values()), Decimal(0))
    assert total == Decimal("20500")
    consult = next(r for r in budget if r.data.get("supplier") == "KPMG")
    assert consult.data["account"] == "6010201003" and consult.data["justification"] == "Reajuste IPCA"
    assert consult.data["cost_center"] == CC and consult.data["branch"] == "0001"

    # reimportar a exportação substitui TODOS os lançamentos do CC (sem duplicar a linha digitada)
    final = import_and_load(client, admin, run_worker, content, "export.xlsx", force=True)
    assert final["summary"]["load"]["budget"] == {
        "lines_replaced": 5,
        "lines_created": 5,
        "cost_centers": 1,
        "trips": 2,
    }
    lines = client.get(f"/api/v1/opex/submissions/{sub}/lines", headers=admin).json()
    assert len(lines) == 5
    assert sum(Decimal(line["total"]) for line in lines) == Decimal("20500.00")
    typed = next(line for line in lines if line["supplier"] == "Loja")
    assert typed["description"] == "Digitada" and typed["values"]["1"] == "100.00"

    # gestor de outro CC não baixa
    _, other = _user(client, admin, "outro@t.com", ["MANAGER"])
    assert client.get(f"/api/v1/opex/submissions/{sub}/template.xlsx", headers=other).status_code == 403


def test_capex_template_export_roundtrip(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_bd(), "bd.xlsx", dataset_type="MASTER_DATA")
    final = import_and_load(client, admin, run_worker, builders.capex_template_filled(), "capex.xlsx", force=True)
    cc = _cc(client, admin)
    head = client.get(f"/api/v1/capex/cost-centers/{cc['id']}", headers=admin).json()
    sub = head["submission_id"]
    before = client.get(f"/api/v1/capex/submissions/{sub}/view", headers=admin).json()
    n_items = sum(len(p["items"]) for p in before["projects"])
    assert n_items == final["summary"]["load"]["capex"]["items_created"]

    resp = client.get(f"/api/v1/capex/submissions/{sub}/template.xlsx", headers=admin)
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].endswith(f'filename="Template_CAPEX_2027_{CC}_v1.0.xlsx"')
    content = resp.content
    result = parse_capex_template(load_sheets(content, "export.xlsx"), {})
    assert result.meta["system_export"] is True and result.meta["parts"]["capex_items"] == n_items
    items = [r for r in result.records if r.record_type == "CAPEX_ITEM"]
    assert not any(r.issues for r in items), [r.issues for r in items]
    assert {r.data["item"] for r in items} == {"NOTEBOOK", "LICENÇA", "CADEIRA"}
    notebook = next(r for r in items if r.data["item"] == "NOTEBOOK")
    assert notebook.data["values"] == {3: "18000"} and notebook.data["is_project"] is True
    assert notebook.data["project_type"] == "Automação e Transformação Digital"

    final2 = import_and_load(client, admin, run_worker, content, "export.xlsx", force=True)
    load = final2["summary"]["load"]["capex"]
    assert load["items_created"] == n_items and load["requests_replaced"] == len(before["projects"])
    after = client.get(f"/api/v1/capex/submissions/{sub}/view", headers=admin).json()
    assert len(after["projects"]) == len(before["projects"])
    assert after["totals"]["proposed"] == before["totals"]["proposed"] and Decimal(after["totals"]["proposed"]) > 0
