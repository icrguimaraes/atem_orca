"""Exportação do orçamento do Painel em Excel (13/11/2026, "vai ajudar na validação"): o orçado dos anos escolhidos, com
os mesmos filtros do Painel, por empresa × centro de custo × conta × mês.

- Ano carregado (ex.: 2026): o orçado de referência vigente. Ano do ciclo (ex.: 2027): o orçamento da versão em
  elaboração (OPEX + CAPEX + Pessoal), como na Consolidação; o CAPEX sem cronograma vai na coluna "Sem mês".
- Duas abas de dados: "Orçamento mensal" (12 meses em colunas) e "Mensal empilhado" (uma linha por mês, no layout do
  relatório do SAP), para cruzar com a planilha da área.
"""

from __future__ import annotations

import io
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules.common import MONTH_LABELS
from app.models import Account, BudgetPackage, Company, CostCenter, ReferenceBudgetEntry, User
from app.services.exports import TITLE_FONT, _sheet
from app.services.painel_facts import Facts, Period

ZERO = Decimal("0")
MODULE_LABELS = {"OPEX": "OPEX", "FINANCEIRO": "OPEX", "CAPEX": "CAPEX", "PESSOAL": "Pessoal"}


def workbook(db: Session, user: User, P: Period, facts_for_year) -> bytes:
    """`facts_for_year(year)` devolve um `Facts` com os filtros do Painel e só aquele ano."""
    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    companies = {c.id: c for c in db.scalars(select(Company))}
    accounts = {a.id: a for a in db.scalars(select(Account))}
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}

    monthly_rows: list[list] = []
    stacked: list[list] = []
    totals_by_year: dict[int, Decimal] = {}
    for year in sorted(P.years):
        f: Facts = facts_for_year(year)
        by_month: dict[tuple, list[Decimal]] = defaultdict(lambda: [ZERO] * 12)
        for cc_id, acc_id, period, amount in f.sums(
            ReferenceBudgetEntry, [year], "cost_center_id", "account_id", "period"
        ):
            if amount and 1 <= int(period) <= 12:
                by_month[(cc_id, acc_id)][int(period) - 1] += amount
        annual = {
            (cc, acc): amount
            for cc, acc, amount in f.sums(ReferenceBudgetEntry, [year], "cost_center_id", "account_id")
        }
        keys = set(by_month) | {k for k, v in annual.items() if v}
        year_total = ZERO
        for cc_id, acc_id in sorted(
            keys,
            key=lambda k: (
                companies[ccs[k[0]].company_id].code if k[0] in ccs else "",
                ccs[k[0]].code if k[0] in ccs else "",
                accounts[k[1]].code if k[1] in accounts else "",
            ),
        ):
            cc, acc = ccs.get(cc_id), accounts.get(acc_id)
            months = by_month.get((cc_id, acc_id), [ZERO] * 12)
            total = annual.get((cc_id, acc_id), sum(months, ZERO))
            unscheduled = total - sum(months, ZERO)
            year_total += total
            company = companies.get(cc.company_id) if cc else None
            base = [
                year,
                company.code if company else "",
                (company.short_name or company.name) if company else "",
                cc.code if cc else "",
                cc.name if cc else "",
                acc.code if acc else "",
                acc.name if acc else "",
                MODULE_LABELS.get(acc.nature, acc.nature) if acc else "",
                packages.get(acc.package_id, "") if acc else "",
            ]
            monthly_rows.append([*base, *[float(v) for v in months], float(unscheduled), float(total)])
            for m, v in enumerate(months, start=1):
                if v:
                    stacked.append([MONTH_LABELS[m - 1], *base, float(v)])
        totals_by_year[year] = year_total

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    ws["A1"] = "Orçamento exportado do Painel"
    ws["A1"].font = TITLE_FONT
    ws["A2"] = f"Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')} por {user.name}"
    ws["A3"] = (
        "Ano carregado = orçado de referência vigente; ano do ciclo = orçamento da versão em elaboração "
        "(OPEX + CAPEX + Pessoal). Valores em R$, com os filtros do Painel e só os centros de custo que você vê."
    )
    ws["A5"], ws["B5"] = "Ano", "Total orçado (R$)"
    for i, (year, total) in enumerate(sorted(totals_by_year.items()), start=6):
        ws.cell(i, 1, year)
        cell = ws.cell(i, 2, float(total))
        cell.number_format = "#,##0.00"
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 22

    head = [
        "Ano",
        "Empresa",
        "Nome da empresa",
        "Centro de custo",
        "Nome do CC",
        "Conta",
        "Nome da conta",
        "Tipo",
        "Pacote",
    ]
    n = len(head)
    _sheet(
        wb,
        "Orçamento mensal",
        [*head, *[m.capitalize() for m in MONTH_LABELS], "Sem mês", "Total"],
        monthly_rows,
        money_cols=set(range(n + 1, n + 15)),
        widths={4: 14, 5: 34, 6: 13, 7: 34, 9: 22},
        total=True,
    )
    _sheet(
        wb,
        "Mensal empilhado",
        ["Mês", *head, "Valor"],
        stacked,
        money_cols={n + 2},
        widths={5: 14, 6: 34, 7: 13, 8: 34, 10: 22},
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
