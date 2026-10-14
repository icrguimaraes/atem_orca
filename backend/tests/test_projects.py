"""Projetos: números lidos do sistema (CC × conta e CAPEX do ciclo), farol, apontamentos, acesso e auditoria."""

from sqlalchemy import select

from app.models import (
    Account,
    ActualEntry,
    AuditLog,
    BudgetSubmission,
    CapexItem,
    CapexItemValue,
    CapexProject,
    Company,
    CostCenter,
    DatasetVersion,
    ReferenceBudgetEntry,
)
from app.services.opex import context
from tests.test_control import make_user

P = "/api/v1/projects"


def _setup(db):
    ctx = context(db)
    ref, target = ctx.ref_year, ctx.target_year
    atem = db.scalar(select(Company).where(Company.code == "1001"))
    acc = Account(code="9990201002", name="Intangível em Andamento (Resultado)", nature="CAPEX")
    other = Account(code="9990601005", name="Equipamentos de Informática", nature="CAPEX")
    cc = CostCenter(company_id=atem.id, code="P1", name="VP DE CONTROLADORIA")
    cc27 = CostCenter(company_id=atem.id, code="P9", name="PROJETO DE CONTROLADORIA")
    db.add_all([acc, other, cc, cc27])
    db.flush()
    actual = DatasetVersion(
        dataset_type="ACTUAL", scope_key=f"ACTUAL:{ref}:1001", version_number=1, last_closed_period=9
    )
    budget = DatasetVersion(
        dataset_type="REFERENCE_BUDGET", scope_key=f"REFERENCE_BUDGET:ORC:{ref}:1001", version_number=1
    )
    old = DatasetVersion(dataset_type="ACTUAL", scope_key=f"ACTUAL:{ref}:1001", version_number=0, is_current=False)
    db.add_all([actual, budget, old])
    db.flush()
    rows = [
        (actual, cc, acc, 1, "110233.94", False),
        (actual, cc, acc, 3, "378751.25", False),
        (actual, cc, acc, 11, "82903.76", True),  # projeção do gestor
        (actual, cc, other, 1, "5000.00", False),  # outra conta: não é do projeto
        (old, cc, acc, 1, "999999.00", False),  # versão antiga: fora
    ]
    for version, c, a, period, amount, projected in rows:
        db.add(
            ActualEntry(
                dataset_version_id=version.id,
                company_id=atem.id,
                cost_center_id=c.id,
                account_id=a.id,
                fiscal_year=ref,
                period=period,
                amount=amount,
                projected=projected,
                document_number=f"DOC{period}",
                vendor_name="FORNECEDOR",
            )
        )
    db.add(
        ReferenceBudgetEntry(
            dataset_version_id=budget.id,
            fiscal_year=ref,
            company_id=atem.id,
            cost_center_id=cc.id,
            account_id=acc.id,
            period=1,
            amount="400000.00",
        )
    )
    sub = BudgetSubmission(version_id=ctx.version.id, cost_center_id=cc27.id, module="CAPEX")
    db.add(sub)
    db.flush()
    req = CapexProject(
        submission_id=sub.id,
        code="CPX-001",
        company_id=atem.id,
        cost_center_id=cc27.id,
        title="PROJETO TDC",
        is_project=True,
    )
    req.items = [
        CapexItem(
            account_id=other.id,
            item_name="MELHORIAS DE SISTEMAS",
            unit_value="668000.00",
            quantity=1,
            total_value="668000.00",
            values=[CapexItemValue(month=1, amount="600000.00")],
        )
    ]
    db.add(req)
    db.commit()
    return ref, target, cc, cc27, acc


def test_numbers_come_from_the_system_and_budget_is_untouched(client, admin, db):
    ref, target, cc, cc27, acc = _setup(db)
    facts_before = db.scalar(select(ActualEntry.id).order_by(ActualEntry.id.desc()))
    body = {
        "code": "600561",
        "name": "Transf. Digital Controladoria",
        "cost_center_id": cc.id,
        "start_planned": f"{ref}-01-01",
        "end_planned": f"{target}-12-31",
        "fact_keys": [{"cost_center_id": cc.id, "account_id": acc.id}],
        "capex_links": [{"cost_center_id": cc27.id, "capex_code": "cpx-001"}],
    }
    resp = client.post(P, headers=admin, json=body)
    assert resp.status_code == 201, resp.text
    d = resp.json()
    s = d["summary"]
    assert s["actual_year"] == "488985.19" and s["projection_year"] == "82903.76"
    assert s["budget_year"] == "400000.00" and s["light"] == "bad"  # realizado acima do orçado
    assert any("acima do orçado" in w for w in s["warnings"])
    assert s["cycle_budget"] == "668000.00" and s["cycle_unscheduled"] == "68000.00"
    assert d["capex_links"][0]["found"] and d["capex_links"][0]["title"] == "PROJETO TDC"
    assert [x["document"] for x in d["documents"]] == ["DOC3", "DOC1"]  # sem projeção, outra conta ou versão antiga
    by_year = {r["year"]: r for r in d["by_year"]}
    assert by_year[target]["budget"] == "668000.00" and by_year[ref]["actual"] == "488985.19"
    fig = client.get(f"{P}/{d['id']}/figure", headers=admin).json()
    assert [t["name"] for t in fig["data"]] == [f"Realizado {ref}", "Projeção do gestor", f"Orçado {ref}"]
    fig27 = client.get(f"{P}/{d['id']}/figure?year={target}", headers=admin).json()
    assert fig27["data"][0]["y"][0] == 600000.0
    # nada do orçamento/realizado foi criado ou alterado
    assert db.scalar(select(ActualEntry.id).order_by(ActualEntry.id.desc())) == facts_before
    # código repetido não entra; solicitação inexistente vira apontamento
    assert client.post(P, headers=admin, json=body | {"name": "Outro"}).status_code == 422
    upd = client.patch(
        f"{P}/{d['id']}", headers=admin, json={"capex_links": [{"cost_center_id": cc27.id, "capex_code": "CPX-999"}]}
    ).json()
    assert not upd["capex_links"][0]["found"] and any("CPX-999" in w for w in upd["summary"]["warnings"])
    actions = db.scalars(select(AuditLog.action).where(AuditLog.entity_type == "project")).all()
    assert actions == ["CREATE", "UPDATE"]


def test_children_access_and_audit(client, admin, db):
    ref, target, cc, cc27, acc = _setup(db)
    pid = client.post(P, headers=admin, json={"code": "600801", "name": "Reforma Tributária"}).json()["id"]
    late = client.post(
        f"{P}/{pid}/milestones", headers=admin, json={"title": "Parametrizar SAP", "due_date": "2020-01-31"}
    ).json()
    assert late["milestones"][0]["late"] and any("atrasado" in w for w in late["summary"]["warnings"])
    mid = late["milestones"][0]["id"]
    done = client.patch(f"{P}/{pid}/milestones/{mid}", headers=admin, json={"progress_pct": 100}).json()
    assert not done["milestones"][0]["late"] and done["summary"]["progress_pct"] == 100
    assert (
        client.post(f"{P}/{pid}/links", headers=admin, json={"title": "x", "url": "javascript:alert(1)"}).status_code
        == 422
    )
    assert (
        client.post(f"{P}/{pid}/risks", headers=admin, json={"description": "Atraso", "impact": "HIGH"}).status_code
        == 201
    )
    upd = client.post(f"{P}/{pid}/updates", headers=admin, json={"text": "Kick-off feito"}).json()
    assert upd["updates"][0]["author"] and any("risco" in w for w in upd["summary"]["warnings"])
    assert upd["summary"]["light"] == "none"  # sem vínculo nem orçado

    # gestor sem relação não vê; responsável vê e edita, mas não cria nem exclui
    uid, other = make_user(client, admin, "gestor.proj@test.com", ["MANAGER"])
    assert client.get(f"{P}/{pid}", headers=other).status_code == 403
    assert client.get(P, headers=other).json()["items"] == []
    client.patch(f"{P}/{pid}", headers=admin, json={"owner_user_id": uid})
    assert client.get(f"{P}/{pid}", headers=other).status_code == 200
    assert client.patch(f"{P}/{pid}", headers=other, json={"status": "PAUSED"}).json()["status_label"] == "Pausado"
    assert client.patch(f"{P}/{pid}", headers=other, json={"owner_user_id": None}).status_code == 403
    assert client.post(P, headers=other, json={"code": "1", "name": "x"}).status_code == 403
    assert client.delete(f"{P}/{pid}", headers=other).status_code == 403
    assert client.delete(f"{P}/{pid}/updates/{upd['updates'][0]['id']}", headers=other).status_code == 403
    assert client.delete(f"{P}/{pid}", headers=admin).status_code == 204
    actions = db.scalars(
        select(AuditLog.action).where(AuditLog.entity_type == "project", AuditLog.entity_id == str(pid))
    ).all()
    assert actions.count("CREATE") == 4 and actions[-1] == "DELETE"
