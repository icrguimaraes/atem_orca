"""Voltar uma base para a versão anterior (12/11/2026): a vigente vai para o histórico, nada é apagado, auditoria."""

from decimal import Decimal

from sqlalchemy import select

from app.models import Account, AuditLog, Company, CostCenter, DatasetVersion, ReferenceBudgetEntry

R = "/api/v1/datasets/restore"


def test_restore_previous_reference_budget(client, admin, db):
    atem = db.scalar(select(Company).where(Company.code == "1001"))
    cc = CostCenter(company_id=atem.id, code="RS1", name="CC RESTAURAR")
    acc = Account(code="9950301001", name="Hospedagem Teste", nature="OPEX")
    db.add_all([cc, acc])
    db.flush()
    scope = "REFERENCE_BUDGET:ORC:2031:1001"
    v1 = DatasetVersion(dataset_type="REFERENCE_BUDGET", scope_key=scope, version_number=1, is_current=False)
    v2 = DatasetVersion(dataset_type="REFERENCE_BUDGET", scope_key=scope, version_number=2, is_current=True)
    db.add_all([v1, v2])
    db.flush()
    for v, amount in ((v1, "1519.96"), (v2, "15205.20")):
        db.add(
            ReferenceBudgetEntry(
                dataset_version_id=v.id,
                fiscal_year=2031,
                company_id=atem.id,
                cost_center_id=cc.id,
                account_id=acc.id,
                period=1,
                amount=amount,
            )
        )
    db.commit()

    body = {
        "dataset_type": "REFERENCE_BUDGET",
        "scope_key": scope,
        "version_number": 1,
        "reason": "Especialista vai rever",
    }
    assert client.post(R, headers=admin, json=body | {"reason": "x"}).status_code == 422
    res = client.post(R, headers=admin, json=body)
    assert res.status_code == 200, res.text
    db.expire_all()
    assert db.get(DatasetVersion, v1.id).is_current and not db.get(DatasetVersion, v2.id).is_current
    current = db.scalar(
        select(ReferenceBudgetEntry.amount)
        .join(DatasetVersion, DatasetVersion.id == ReferenceBudgetEntry.dataset_version_id)
        .where(DatasetVersion.scope_key == scope, DatasetVersion.is_current)
    )
    assert current == Decimal("1519.96")
    assert db.scalar(select(AuditLog).where(AuditLog.action == "RESTORE_VERSION")).reason == "Especialista vai rever"
    assert client.post(R, headers=admin, json=body).status_code == 409  # já é a vigente
    assert client.post(R, headers=admin, json=body | {"dataset_type": "EMPLOYEES"}).status_code == 409
