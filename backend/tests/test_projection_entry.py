"""Projeção digitada pelo gestor: grade conta × mês, origem de cada valor, comparação antiga × nova e gravação."""

from decimal import Decimal

from sqlalchemy import func, select

from app.models import Account, ActualEntry, AuditLog, Company, CostCenter, DatasetVersion, ReferenceBudgetEntry
from app.services.opex import context
from tests.test_control import make_user

P = "/api/v1/projection"


def _setup(db):
    ref = context(db).ref_year
    atem = db.scalar(select(Company).where(Company.code == "1001"))
    opex = Account(code="9960101001", name="MATERIAL DE ESCRITÓRIO", nature="OPEX")
    capex = Account(code="9960201002", name="INTANGÍVEL EM ANDAMENTO", nature="CAPEX")
    fin = Account(code="9930101001", name="JUROS", nature="FINANCEIRO")
    cc = CostCenter(company_id=atem.id, code="PJ1", name="CONTROLADORIA TESTE")
    other = CostCenter(company_id=atem.id, code="PJ2", name="OUTRO CC")
    db.add_all([opex, capex, fin, cc, other])
    db.flush()
    actual = DatasetVersion(
        dataset_type="ACTUAL", scope_key=f"ACTUAL:{ref}:1001", version_number=1, last_closed_period=9
    )
    proj = DatasetVersion(dataset_type="PROJECTION", scope_key=f"PROJECTION:{ref}:1001", version_number=1)
    budget = DatasetVersion(
        dataset_type="REFERENCE_BUDGET", scope_key=f"REFERENCE_BUDGET:ORC:{ref}:1001", version_number=1
    )
    db.add_all([actual, proj, budget])
    db.flush()
    rows = [
        (actual, cc, opex, 1, "1000.00", False),
        (actual, cc, opex, 9, "1200.00", False),
        (proj, cc, opex, 10, "1300.00", True),
        (proj, cc, opex, 11, "1300.00", True),
        (proj, cc, opex, 12, "1300.00", True),
        (proj, other, opex, 10, "777.00", True),  # outro CC: não pode mudar
        (proj, cc, fin, 10, "50.00", True),  # financeiro: fora da grade, continua
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
            )
        )
    for period in (10, 11, 12):  # CAPEX sem projeção: a grade sugere o orçado
        db.add(
            ReferenceBudgetEntry(
                dataset_version_id=budget.id,
                fiscal_year=ref,
                company_id=atem.id,
                cost_center_id=cc.id,
                account_id=capex.id,
                period=period,
                amount="5000.00",
            )
        )
    db.commit()
    return cc, other, opex, capex, proj


def _row(grid, account_id):
    return next(r for r in grid["rows"] if r["account_id"] == account_id)


def test_grid_shows_closed_months_and_origin_of_each_value(client, admin, db):
    cc, _other, opex, capex, proj = _setup(db)
    grid = client.get(f"{P}/{cc.id}", headers=admin).json()
    assert grid["closed"] == 9 and grid["open_months"] == [10, 11, 12]
    assert grid["version_id"] == proj.id
    r = _row(grid, opex.id)
    assert r["origin"][:9] == ["R"] * 9 and r["values"][8] == "1200.00"
    assert r["origin"][9:] == ["P", "P", "P"] and r["values"][9] == "1300.00"
    c = _row(grid, capex.id)
    # conta sem projeção: o orçado não preenche nada sozinho (fica só como referência em "budget")
    assert c["origin"][9:] == ["", "", ""] and c["values"][9] == "0" and c["budget"][9] == "5000.00"
    assert all(row["nature"] != "FINANCEIRO" for row in grid["rows"])
    items = client.get(f"{P}/cost-centers", headers=admin).json()["items"]
    item = next(i for i in items if i["id"] == cc.id)
    assert Decimal(item["actual"]) == Decimal("2200") and Decimal(item["projection"]) == Decimal("3900")


def test_compare_then_save_replaces_only_changed_cells(client, admin, db):
    cc, other, opex, capex, proj = _setup(db)
    body = {
        "version_id": proj.id,
        "values": [
            {"account_id": opex.id, "month": 10, "amount": "1500.00"},  # alterado
            {"account_id": opex.id, "month": 11, "amount": "1300.00"},  # igual: não muda
            {"account_id": capex.id, "month": 11, "amount": "5000.00"},  # conta sem projeção, digitada agora
        ],
    }
    before_versions = db.scalar(select(func.count()).select_from(DatasetVersion))
    cmp = client.post(f"{P}/{cc.id}/compare", headers=admin, json=body).json()
    assert len(cmp["changes"]) == 2
    assert Decimal(cmp["totals"]["difference"]) == Decimal("5200")
    assert Decimal(cmp["totals"]["closing_after"]) == Decimal("2200") + Decimal("3900") + Decimal("5200")
    assert db.scalar(select(func.count()).select_from(DatasetVersion)) == before_versions  # comparar não grava

    saved = client.put(f"{P}/{cc.id}", headers=admin, json=body | {"comment": "revisão de outubro"})
    assert saved.status_code == 200, saved.text
    db.expire_all()
    current = db.scalar(
        select(DatasetVersion).where(DatasetVersion.scope_key == proj.scope_key, DatasetVersion.is_current)
    )
    assert current.id != proj.id and current.version_number == 2
    rows = {
        (r.cost_center_id, r.account_id, r.period): r
        for r in db.scalars(select(ActualEntry).where(ActualEntry.dataset_version_id == current.id))
    }
    assert rows[(cc.id, opex.id, 10)].amount == Decimal("1500.00") and rows[(cc.id, opex.id, 10)].source == "GESTOR"
    assert rows[(cc.id, opex.id, 11)].source != "GESTOR"  # sem alteração: continua a da planilha
    assert rows[(cc.id, capex.id, 11)].amount == Decimal("5000.00")
    assert (cc.id, capex.id, 10) not in rows  # sugestão não aceita não vira projeção
    assert rows[(other.id, opex.id, 10)].amount == Decimal("777.00")  # outro CC intacto
    assert len([k for k in rows if k[1] != opex.id and k[1] != capex.id]) == 1  # financeiro intacto
    log = db.scalar(select(AuditLog).where(AuditLog.entity_type == "projection", AuditLog.entity_id == str(cc.id)))
    assert log.reason == "revisão de outubro"
    assert log.before[f"{opex.code} · OUT"] == "1300.00" and log.after[f"{opex.code} · OUT"] == "1500.00"

    grid = client.get(f"{P}/{cc.id}", headers=admin).json()
    assert _row(grid, opex.id)["origin"][9:] == ["G", "P", "P"]
    assert _row(grid, capex.id)["origin"][9:] == ["", "G", ""]  # conta já projetada: sem sugestão
    hist = client.get(f"{P}/{cc.id}/history", headers=admin).json()
    assert len(hist) == 1 and len(hist[0]["cells"]) == 2

    # a versão lida ficou velha: não grava por cima de quem gravou antes
    stale = client.put(f"{P}/{cc.id}", headers=admin, json=body)
    assert stale.status_code == 409


def test_closed_month_access_and_viewer(client, admin, db):
    cc, other, opex, _capex, proj = _setup(db)
    closed = {"version_id": proj.id, "values": [{"account_id": opex.id, "month": 9, "amount": "1"}]}
    assert client.put(f"{P}/{cc.id}", headers=admin, json=closed).status_code == 422
    uid, manager = make_user(client, admin, "gestor.proj@test.com", ["MANAGER"])
    db.get(CostCenter, cc.id).manager_user_id = uid
    db.commit()
    assert client.get(f"{P}/{other.id}", headers=manager).status_code == 403
    assert [i["id"] for i in client.get(f"{P}/cost-centers", headers=manager).json()["items"]] == [cc.id]
    ok = {"version_id": proj.id, "values": [{"account_id": opex.id, "month": 12, "amount": "-10,50"}]}
    assert client.put(f"{P}/{cc.id}", headers=manager, json=ok).status_code == 200
    vid, viewer = make_user(client, admin, "consulta.proj@test.com", ["VIEWER"])
    db.get(CostCenter, cc.id).manager_user_id = vid
    db.commit()
    grid = client.get(f"{P}/{cc.id}", headers=viewer).json()
    assert grid["can_edit"] is False
    again = {"version_id": grid["version_id"], "values": [{"account_id": opex.id, "month": 12, "amount": "1"}]}
    assert client.put(f"{P}/{cc.id}", headers=viewer, json=again).status_code == 403
