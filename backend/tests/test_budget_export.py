"""Exportação do orçamento do Painel (13/11/2026): CC × conta × mês, com os filtros do Painel, em duas abas."""

import io

from openpyxl import load_workbook
from sqlalchemy import select

from app.models import Account, Company, CostCenter, DatasetVersion, ReferenceBudgetEntry


def test_budget_export_reference_year(client, admin, db):
    atem = db.scalar(select(Company).where(Company.code == "1001"))
    cc = CostCenter(company_id=atem.id, code="EX1", name="COMEX TESTE")
    acc = Account(code="9940301001", name="Hospedagem Export", nature="OPEX")
    db.add_all([cc, acc])
    db.flush()
    v = DatasetVersion(dataset_type="REFERENCE_BUDGET", scope_key="REFERENCE_BUDGET:ORC:2031:1001", version_number=1)
    db.add(v)
    db.flush()
    for period, amount in ((1, "1607.10"), (4, "-2392.90"), (7, "15292.34")):
        db.add(
            ReferenceBudgetEntry(
                dataset_version_id=v.id,
                fiscal_year=2031,
                company_id=atem.id,
                cost_center_id=cc.id,
                account_id=acc.id,
                period=period,
                amount=amount,
            )
        )
    db.commit()

    res = client.get(f"/api/v1/dashboard/budget-export.xlsx?years=2031&cost_center_id={cc.id}", headers=admin)
    assert res.status_code == 200, res.text
    wb = load_workbook(io.BytesIO(res.content), data_only=True)
    assert wb.sheetnames == ["Resumo", "Orçamento mensal", "Mensal empilhado"]
    rows = [r for r in wb["Orçamento mensal"].iter_rows(min_row=2, values_only=True) if r[3] == "EX1"]
    assert len(rows) == 1
    row = rows[0]
    assert row[0] == 2031 and row[5] == acc.code
    assert row[9] == 1607.10 and row[12] == -2392.90 and row[15] == 15292.34  # jan, abr (negativo), jul
    assert round(row[-1], 2) == 14506.54
    stacked = [r for r in wb["Mensal empilhado"].iter_rows(min_row=2, values_only=True) if r[4] == "EX1"]
    assert [r[0] for r in stacked] == ["JAN", "ABR", "JUL"]
