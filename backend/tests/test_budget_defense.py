"""Defesa do orçamento: base (versão marcada como base) × proposta (em elaboração), cortes com prioridade sugerida e
argumento lidos do sistema, ajuste da Controladoria com auditoria e Excel."""

import io
from decimal import Decimal

from openpyxl import load_workbook
from sqlalchemy import select

from app.models import Account, AuditLog, BudgetLine, CostCenter
from app.services import opex as opex_svc
from app.services.opex import context
from tests.test_consolidation import _setup
from tests.test_opex import _user

D = "/api/v1/defense"


def _scenario(client, admin, run_worker, db):
    cc, opex_sub, _line = _setup(client, admin, run_worker)
    ctx = context(db)
    interest = Account(code="9930101008", name="Juros sobre parcelamento Teste", nature="FINANCEIRO")
    db.add(interest)
    db.flush()
    center = db.get(CostCenter, cc["id"])
    line = BudgetLine(
        submission_id=opex_sub,
        company_id=center.company_id,
        cost_center_id=center.id,
        account_id=interest.id,
        line_type="GENERIC",
        description="Parcelamento federal",
        justification="Parcelamento em 60 vezes",
    )
    opex_svc._set_values(line, {m: Decimal("100") for m in range(1, 13)})
    db.add(line)
    db.commit()
    assert ctx.version.label == "Revisão 0"
    client.post("/api/v1/consolidation/freeze", headers=admin, json={"reason": "VP"})
    rv = client.post("/api/v1/consolidation/revise", headers=admin, json={"reason": "Planejamento"}).json()
    v0, v1 = (next(v["id"] for v in rv["versions"] if v["label"] == lb) for lb in ("Revisão 0", "Revisão 1"))
    client.patch(f"/api/v1/consolidation/versions/{v0}", headers=admin, json={"name": "VP", "is_base": True})
    head = client.get(f"/api/v1/opex/cost-centers/{cc['id']}", headers=admin).json()
    lines = client.get(f"/api/v1/opex/submissions/{head['submission_id']}/lines", headers=admin).json()
    phone_id = db.scalar(select(Account.id).where(Account.code == "6010301002"))
    phone = next(x for x in lines if x["account_id"] == phone_id)
    juros = next(x for x in lines if x["account_id"] == interest.id)
    # o Planejamento corta telefonia (450 → 200 por mês) e zera os juros
    client.patch(f"/api/v1/opex/lines/{phone['id']}", headers=admin, json={"values": {m: 200 for m in range(1, 13)}})
    client.patch(f"/api/v1/opex/lines/{juros['id']}", headers=admin, json={"values": {m: 0 for m in range(1, 13)}})
    return cc, v0, v1, interest


def test_defense_suggests_priorities_and_records_adjustments(client, admin, run_worker, db):
    cc, v0, v1, interest = _scenario(client, admin, run_worker, db)
    res = client.get(D, headers=admin)
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["base"]["id"] == v0 and data["base"]["label"] == "Revisão 0 · VP" and data["proposal"]["id"] == v1
    assert Decimal(data["difference"]) == Decimal("-4200.00")  # telefonia −3.000 e juros −1.200
    items = {i["account"]: i for i in data["items"]}
    juros, phone = items[interest.code], items["6010301002"]
    assert juros["kind"] == "CUT" and juros["priority"] == "HIGH"
    assert {t["key"] for t in juros["tags"]} >= {"legal", "zeroed", "justified"}
    assert "Parcelamento em 60 vezes" in juros["suggested_argument"]
    # telefonia: proposta (2.400) abaixo do realizado 2026 anualizado (500 × 8 → 6.000)
    assert phone["actual_ref"] == "6000.00" and phone["priority"] == "MEDIUM"
    assert any(t["key"] == "below_actual" for t in phone["tags"])
    assert data["ask"]["total"] == "4200.00" and data["ask"]["keys"][0] == juros["key"]
    opex = next(g for g in data["groups"] if g["group"] == "OPEX")
    assert opex["cut"] == "4200.00" and opex["actual_ref"] == "18000.00"

    body = {"cost_center_id": cc["id"], "module": "OPEX", "account": "6010301002"}
    too_much = client.put(f"{D}/items", headers=admin, json=body | {"priority": "HIGH", "request_amount": "3000.01"})
    assert too_much.status_code == 422 and "até o corte" in too_much.json()["detail"]
    ok = client.put(
        f"{D}/items", headers=admin, json=body | {"priority": "HIGH", "request_amount": "1000", "argument": "Contrato"}
    )
    assert ok.status_code == 200, ok.text
    data = client.get(D, headers=admin).json()
    phone = next(i for i in data["items"] if i["account"] == "6010301002")
    assert (phone["priority"], phone["request_amount"], phone["argument"], phone["edited"]) == (
        "HIGH",
        "1000.00",
        "Contrato",
        True,
    )
    assert data["ask"]["total"] == "2200.00"
    log = db.scalar(select(AuditLog).where(AuditLog.entity_type == "budget_defense"))
    assert log is not None and log.after["priority"] == "HIGH" and log.after["request_amount"] == "1000.00"

    # aceitar o corte tira do pedido; voltar à sugestão apaga o ajuste (também auditado)
    client.put(f"{D}/items", headers=admin, json=body | {"priority": "ACCEPT"})
    data = client.get(D, headers=admin).json()
    assert data["ask"]["total"] == "1200.00" and len(data["ask"]["keys"]) == 1
    assert client.put(f"{D}/items", headers=admin, json=body | {"reset": True}).json() == {"edited": False}
    data = client.get(D, headers=admin).json()
    assert data["ask"]["total"] == "4200.00"
    assert db.scalar(select(AuditLog).where(AuditLog.entity_type == "budget_defense", AuditLog.action == "DELETE"))

    # aumento não é corte
    bad = client.put(f"{D}/items", headers=admin, json=body | {"account": "6010301001", "priority": "HIGH"})
    assert bad.status_code == 422

    wb = load_workbook(io.BytesIO(client.get(f"{D}/export.xlsx", headers=admin).content))
    assert wb.sheetnames == ["Resumo", "Pedir de volta", "Cortes", "Aumentos", "Em aberto"]
    assert wb["Pedir de volta"].cell(2, 4).value == interest.code

    # proposta congelada: só consulta
    client.post("/api/v1/consolidation/freeze", headers=admin, json={})
    frozen = client.put(f"{D}/items", headers=admin, json=body | {"priority": "LOW"})
    assert frozen.status_code == 422 and "congelada" in frozen.json()["detail"]


def test_defense_is_only_for_the_controller(client, admin, run_worker, db):
    _scenario(client, admin, run_worker, db)
    _, mgr = _user(client, admin, "gestor-defesa@t.com", ["MANAGER"])
    assert client.get(D, headers=mgr).status_code == 403
    assert client.get(f"{D}/export.xlsx", headers=mgr).status_code == 403


def test_defense_needs_two_versions(client, admin):
    res = client.get(D, headers=admin)
    assert res.status_code == 422 and "duas versões" in res.json()["detail"]
