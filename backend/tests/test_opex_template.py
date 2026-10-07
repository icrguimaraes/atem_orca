from decimal import Decimal

from tests import builders
from tests.test_imports import import_and_load, status, upload


def test_findings_fix_travel_fare_and_account_justification(client, admin, run_worker):
    """Página Apontamentos: passagem informada na viagem importada sem tarifa e justificativa da conta."""
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "template.xlsx")
    found = client.get("/api/v1/findings", headers=admin).json()
    fare = next(i for i in found["items"] if i["kind"] == "TRAVEL_NO_FARE")
    assert fare["fix"] == {"type": "ticket", "route": "AM → PA"} and fare["editable"] is True
    zero = client.post("/api/v1/findings/fix", headers=admin, json={"key": fare["key"], "ticket_amount": 0})
    assert zero.status_code == 422
    fixed = client.post("/api/v1/findings/fix", headers=admin, json={"key": fare["key"], "ticket_amount": 1500})
    assert fixed.status_code == 200, fixed.text
    assert fixed.json()["note"] == "Passagem incluída: R$ 1.500,00"
    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101011")
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    belem = [line for line in lines if line["line_type"] == "TRAVEL" and line["description"] == "Visita base Belém"]
    assert sorted(line["values"]["3"] for line in belem) == ["1500.00", "2400.00"]
    assert all(line["attributes"]["warnings"] == [] for line in belem)
    assert all(line["attributes"]["ticket_amount"] == "1500.00" for line in belem)
    after = client.get("/api/v1/findings", headers=admin).json()
    assert not any(i["kind"] == "TRAVEL_NO_FARE" for i in after["items"])
    assert after["reviews"][0]["note"] == "Passagem incluída: R$ 1.500,00"

    # conta sem justificativa é aviso (recomendada, não bloqueia o envio): dá para justificar ou manter
    account = next(i for i in after["items"] if i["kind"] == "OPEX_JUSTIFICATION")
    assert account["severity"] == "WARNING" and account["can_keep"] is True
    ok = client.post("/api/v1/findings/fix", headers=admin, json={"key": account["key"], "text": "Auditorias em 2027"})
    assert ok.status_code == 200, ok.text
    assert account["key"] not in {i["key"] for i in client.get("/api/v1/findings", headers=admin).json()["items"]}
    template = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/template.xlsx", headers=admin)
    assert template.status_code == 200  # template corrigido para devolver à área


def test_ream_template_without_key_column(client, admin, run_worker):
    """Template da REAM (layout antigo, sem CHAVE): empresa pela divisão (2001), CC alfanumérico e filial criados a
    partir das linhas; a aba BD (tabelas empilhadas) não vira cadastro nem gera erros."""
    batch_id = upload(client, admin, builders.opex_template_ream(), "Template_OPEX 2027 - REFMAN - Custos.xlsx")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["dataset_type"] == "OPEX_TEMPLATE", b
    assert b["error_rows"] == 0
    assert b["summary"]["meta"]["parts"] == {"master": 2, "actual": 0, "budget_lines": 3}
    budget = b["summary"]["comparison"]["budget"]
    assert [(x["company"], x["cost_center"], x["lines"], x["total"]) for x in budget] == [
        ("2001", "RFM6003000", 3, "20400")
    ]
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    assert status(client, admin, batch_id)["status"] == "COMPLETED"
    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "RFM6003000")
    assert cc["name"] == "Custos"
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    assert len(lines) == 3
    assert sum(Decimal(v) for line in lines for v in line["values"].values()) == Decimal("20400")
    assert all(line["branch_id"] for line in lines)  # filial 2001 criada junto


def test_nave_template_two_month_blocks_and_new_account(client, admin, run_worker):
    """Template da NAVE (sem CHAVE): orçamento no segundo bloco JAN..DEZ, empresa 1012 pela aba "Base de dados" e conta
    fora do cadastro criada com aviso na linha."""
    batch_id = upload(client, admin, builders.opex_template_nave(), "Template_OPEX 2027_Despesa - NAVE.xlsx")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["error_rows"] == 0, b
    budget = b["summary"]["comparison"]["budget"]
    assert [(x["company"], x["cost_center"], x["lines"], x["total"]) for x in budget] == [
        ("1012", "5010102102", 2, "18000")
    ]
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin).json()
    codes = {e["code"] for e in (errors["items"] if isinstance(errors, dict) else errors)}
    assert "NEW_ACCOUNT" in codes
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    assert status(client, admin, batch_id)["status"] == "COMPLETED"
    account = next(a for a in client.get("/api/v1/accounts?q=6030101999", headers=admin).json())
    assert account["name"] == "Juros s/ Impostos e Parcelamentos"


def test_filled_opex_template_loads_everything(client, admin, run_worker):
    batch_id = upload(client, admin, builders.opex_template_filled(), "Template_OPEX 2027_Gestor.xlsx")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["dataset_type"] == "OPEX_TEMPLATE", b
    meta = b["summary"]["meta"]
    assert meta["parts"] == {"master": 9, "actual": 1, "budget_lines": 4}
    budget = b["summary"]["comparison"]["budget"]
    assert budget == [
        {
            "cost_center": "1050101011",
            "company": "1001",
            "status": "DRAFT",
            "editable": True,
            "lines": 4,
            "total": "20400",
            "replaces_lines": 0,
            "replaces_total": "0",
        }
    ]
    assert client.post(f"/api/v1/imports/{batch_id}/confirm", headers=admin).status_code == 200
    run_worker()
    final = status(client, admin, batch_id)
    assert final["status"] == "COMPLETED", final
    assert final["summary"]["load"]["budget"]["lines_created"] == 4

    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101011")
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    assert head["status"] == "IN_PROGRESS"
    view = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/accounts", headers=admin).json()
    rows = {r["code"]: r for r in view["accounts"]}
    assert rows["6010301002"]["proposed"] == "4200.00" and rows["6010301002"]["ref_actual_ytd"] == "2400.00"
    assert rows["6010301011"]["proposed"] == "1800.00"
    assert meta["consolidator_check"]["I - Viagens"]["ok"] is True
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    trips = [line for line in lines if line["line_type"] == "TRAVEL"]
    assert {t["description"] for t in trips} == {"Auditoria SP", "Visita base Belém"}
    belem = next(t for t in trips if t["description"] == "Visita base Belém")
    assert belem["attributes"]["destination"] == "PA" and belem["attributes"]["return_month"] is None
    assert belem["values"]["3"] == "2400.00"
    # passagem zerada entre lugares diferentes: apontada na viagem (tela) e na prévia da importação
    assert belem["attributes"]["warnings"] == ["Passagem orçada em R$ 0: a planilha não trouxe tarifa para AM → PA"]
    auditoria = next(t for t in trips if t["description"] == "Auditoria SP")
    assert auditoria["attributes"]["warnings"] == []
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin).json()
    codes = {e["code"] for e in (errors["items"] if isinstance(errors, dict) else errors)}
    assert "TRAVEL_NO_FARE" in codes
    found = client.get("/api/v1/findings", headers=admin).json()["items"]
    fare = [i for i in found if i["kind"] == "TRAVEL_NO_FARE"]
    assert [(i["severity"], i["subject"], i["amount"]) for i in fare] == [
        ("WARNING", "Visita base Belém · AM → PA · MAR", "2400.00")
    ]
    assert fare[0]["link"] == f"/orcamento/{cc['id']}" and fare[0]["kind_label"] == "Passagem zerada"
    consult = next(line for line in lines if line["supplier"] == "KPMG")
    assert consult["description"] == "Consultoria tributária" and consult["justification"] == "Reajuste IPCA"

    # linha digitada no sistema é preservada; reimportar o template substitui só as linhas do template
    acc = next(
        a for a in client.get("/api/v1/opex/options", headers=admin).json()["accounts"] if a["code"] == "6010301005"
    )
    client.post(
        f"/api/v1/opex/submissions/{head['submission_id']}/lines",
        headers=admin,
        json={"account_id": acc["id"], "values": {"1": 100}},
    )
    final = import_and_load(client, admin, run_worker, builders.opex_template_filled(), "v2.xlsx", force=True)
    assert final["summary"]["load"]["budget"] == {
        "lines_replaced": 4,
        "lines_created": 4,
        "cost_centers": 1,
        "trips": 2,
    }
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    assert len(lines) == 5


def test_template_blocked_after_submission(client, admin, run_worker):
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t.xlsx")
    cc = next(c for c in client.get("/api/v1/cost-centers", headers=admin).json() if c["code"] == "1050101011")
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    sub = head["submission_id"]
    view = client.get(f"/api/v1/opex/submissions/{sub}/accounts", headers=admin).json()
    for r in view["accounts"]:
        if r["needs_justification"]:
            client.put(
                f"/api/v1/opex/submissions/{sub}/justifications/{r['account_id']}", headers=admin, json={"text": "ok"}
            )
    assert client.post(f"/api/v1/opex/submissions/{sub}/actions/submit", headers=admin, json={}).status_code == 200
    batch_id = upload(client, admin, builders.opex_template_filled(), "t2.xlsx")
    run_worker()
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin, params={"code": "BUDGET_LOCKED"}).json()
    assert len(errors) == 4


def test_broken_key_resolved_by_name_and_unresolved_is_reported(client, admin, run_worker):
    """CHAVE com CC = 0 (XLOOKUP não achou): identifica pelo nome; sem nome válido, erro visível."""
    import io

    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(builders.opex_template_filled()))
    ws = wb["II - Serviços de Terceiros"]
    row = next(r for r in range(15, ws.max_row + 1) if ws.cell(r, 5).value == "KPMG")
    ws.cell(row, 2, "1001-0001-0-6010201003")
    ws.cell(row, 10, 0)
    ws.cell(row, 9, "DADOS E PROJ. APLICADOS A CONTROLADORIA")
    ws.append(
        [
            None,
            "1001-0001-0-6010201003",
            "Linha órfã",
            None,
            None,
            None,
            "MANAUS",
            "0001",
            "CC INEXISTENTE",
            0,
            "Serviços de Consultoria e Assessoria",
            "6010201003",
            *[500] * 12,
            6000,
        ]
    )
    via = wb["I - Viagens"]
    via.cell(63, 19, 2000)  # hospedagem alterada sem atualizar o consolidador
    buf = io.BytesIO()
    wb.save(buf)

    batch_id = upload(client, admin, buf.getvalue(), "t.xlsx")
    run_worker()
    b = status(client, admin, batch_id)
    assert b["summary"]["comparison"]["budget"][0]["lines"] == 4
    errors = client.get(f"/api/v1/imports/{batch_id}/errors", headers=admin).json()
    items = errors["items"] if isinstance(errors, dict) else errors
    codes = {e["code"] for e in items}
    assert {"UNRESOLVED_LINE", "CONSOLIDATOR_MISMATCH"} <= codes
    assert b["summary"]["meta"]["consolidator_check"]["I - Viagens"]["ok"] is False


def test_preview_opens_on_budget_records(client, admin, run_worker):
    """Prévia: "MAIN" mostra só o que vira orçamento (sem catálogo e cadastros); sem filtro, mostra tudo."""
    batch_id = upload(client, admin, builders.opex_template_filled(), "t-preview.xlsx")
    run_worker()
    main = client.get(f"/api/v1/imports/{batch_id}/preview?record_type=MAIN", headers=admin).json()["rows"]
    assert main and {r["record_type"] for r in main} <= {"BUDGET_LINE", "TRAVEL"}
    every = client.get(f"/api/v1/imports/{batch_id}/preview", headers=admin).json()["rows"]
    assert {"BRANCH", "ACCOUNT"} <= {r["record_type"] for r in every}
    accounts = client.get(f"/api/v1/imports/{batch_id}/preview?record_type=ACCOUNT", headers=admin).json()["rows"]
    assert accounts and {r["record_type"] for r in accounts} == {"ACCOUNT"}


def test_move_line_to_other_cost_center(client, admin, run_worker):
    """Controladoria corrige no sistema o CC lançado errado na planilha: o lançamento vai para o OPEX do outro CC,
    com motivo, e fica no registro dos apontamentos (corrigido)."""
    import_and_load(client, admin, run_worker, builders.opex_template_filled(), "t-move.xlsx")
    ccs = {c["code"]: c for c in client.get("/api/v1/cost-centers", headers=admin).json()}
    head = client.get(f"/api/v1/opex/cost-centers/{ccs['1050101011']['id']}", headers=admin).json()
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    line = next(x for x in lines if x["line_type"] == "GENERIC")
    url = f"/api/v1/opex/lines/{line['id']}/move"
    target = ccs["1050101012"]["id"]
    assert client.post(url, headers=admin, json={"target_cost_center_id": target, "reason": " "}).status_code == 422
    moved = client.post(url, headers=admin, json={"target_cost_center_id": target, "reason": "CC da outra área"})
    assert moved.status_code == 200, moved.text
    assert moved.json()["note"].startswith("Movido de 1050101011 para 1050101012")
    left = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    assert line["id"] not in {x["id"] for x in left}
    there = client.get(f"/api/v1/opex/submissions/{moved.json()['target_submission_id']}/lines", headers=admin).json()
    assert line["id"] in {x["id"] for x in there}
    reviews = client.get("/api/v1/findings", headers=admin).json()["reviews"]
    assert any(r["kind"] == "OPEX_MOVED_CC" and r["action"] == "CORRECTED" for r in reviews)
