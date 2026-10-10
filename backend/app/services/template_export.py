"""Exportação do orçamento de um centro de custo no layout dos templates Excel (OPEX e CAPEX).

A planilha gerada segue a estrutura que o importador reconhece (aba BD-Novo, abas de pacote
`I - Viagens` … `XII - Comercial` com CHAVE + JAN..DEZ; aba `Template_Orç AAAA` no CAPEX), de modo que
o gestor pode ajustar no Excel e reimportar em Importação de dados. Os valores são os lançados no
sistema (viagens agrupadas em uma linha por viagem, com os três valores; consolidador conferido).
"""

import io
from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules.common import MONTH_LABELS
from app.imports.base import export_marker
from app.models import (
    Account,
    AccountDetail,
    AccountJustification,
    Branch,
    BudgetLine,
    BudgetPackage,
    BudgetSubmission,
    CapexProject,
    CostCenter,
)
from app.services.opex import Context

MONEY = '#,##0.00;[Red]-#,##0.00;"-"'
HEADER_FILL = PatternFill("solid", fgColor="1F2937")
HEADER_FONT = Font(bold=True, color="FFFFFF")
TITLE_FONT = Font(bold=True, size=14)
MUTED = Font(italic=True, color="717171")
TRAVEL_ACCOUNTS = ("6010301011", "6010301036", "6010301001")  # passagem, diária, hospedagem
OTHER_SHEET = "XIII - Outros lançamentos"


def _f(value) -> float | None:
    return None if value in (None, "") else float(value)


def _header_row(ws: Worksheet, row: int, start_col: int, headers: list) -> None:
    for i, text in enumerate(headers):
        cell = ws.cell(row, start_col + i, text)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
        cell.alignment = Alignment(vertical="center", wrap_text=True)


def _widths(ws: Worksheet, widths: dict[int, int]) -> None:
    for col, width in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = width


def _key(company: str, branch: str | None, cc: str, account: str) -> str:
    return f"{company}-{branch or ''}-{cc}-{account}"


def _instructions(wb: Workbook, title: str, ctx: Context, cc: CostCenter, sub: BudgetSubmission, notes: list[str]):
    ws = wb.active
    ws.title = "Instruções"
    ws["A1"] = export_marker(sub.module, cc.code, ctx.version.file_tag)  # lido pelo importador; não alterar
    ws["A1"].font = Font(size=8, color="AAAAAA")
    ws["B2"], ws["B2"].font = title, TITLE_FONT
    ws["B3"] = f"Centro de custo {cc.company.code} · {cc.code} · {cc.name} · gestor {cc.manager_name or '—'}"
    ws["B4"] = (
        f"{ctx.cycle.name} · versão {ctx.version.label} · situação {sub.status} · "
        f"gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )
    for i, note in enumerate(notes, start=6):
        ws.cell(i, 2, note).font = MUTED
    ws.column_dimensions["B"].width = 120
    return ws


def _bd_sheet(db: Session, wb: Workbook, cc: CostCenter, accounts: list[Account], tables_row: int = 5) -> None:
    """Aba BD-Novo no layout do template: tabelas lado a lado (CC em B, filiais em E, contas em H)."""
    ws = wb.create_sheet("BD-Novo")
    ws["B2"], ws["B2"].font = "Base de dados do template (centro de custo, filiais e contas)", TITLE_FONT
    headers = {
        2: "Centro de Custo",
        3: "Denominação de centro de custos",
        4: "Empresa",
        6: "Local de negócios",
        7: "Filial",
        8: "Empresa",
        10: "Descrição",
        11: "Conta do Razão",
        12: "Agrupamento DRE",
        13: "Pacote GMD",
        14: "Detalhamento",
    }
    for col, text in headers.items():
        cell = ws.cell(tables_row, col, text)
        cell.fill, cell.font = HEADER_FILL, HEADER_FONT
    ws.cell(tables_row + 1, 2, cc.code)
    ws.cell(tables_row + 1, 3, cc.name)
    ws.cell(tables_row + 1, 4, cc.company.code)
    branches = db.scalars(select(Branch).where(Branch.company_id == cc.company_id).order_by(Branch.code)).all()
    for i, b in enumerate(branches, start=tables_row + 1):
        ws.cell(i, 6, b.code)
        ws.cell(i, 7, b.name)
        ws.cell(i, 8, cc.company.code)
    details = defaultdict(list)
    for d in db.scalars(select(AccountDetail)):
        details[d.account_id].append(d.name)
    row = tables_row + 1
    for acc in accounts:
        for detail in details.get(acc.id) or [None]:
            ws.cell(row, 10, acc.name)
            ws.cell(row, 11, acc.code)
            ws.cell(row, 12, acc.dre_group)
            ws.cell(row, 13, acc.package.name if acc.package else None)
            ws.cell(row, 14, detail or 0)
            row += 1
    _widths(ws, {2: 16, 3: 44, 4: 10, 6: 16, 7: 24, 8: 10, 10: 44, 11: 16, 12: 22, 13: 28, 14: 30})


# ---------------------------------------------------------------- OPEX


def _travel_sheet(
    ws: Worksheet,
    ctx: Context,
    cc: CostCenter,
    trips: list[list[BudgetLine]],
    accounts: dict[int, Account],
    branches: dict[int, Branch],
) -> None:
    """Aba I - Viagens: uma linha por viagem (objetivo, cargo, ida/volta, dias, rota, valores) e o
    consolidador (CHAVE × JAN..DEZ) à direita, que o importador usa só para conferência."""
    company = cc.company.code
    ws["B2"], ws["B2"].font = f"Viagens · {cc.code} · {cc.name} · orçamento {ctx.target_year}", TITLE_FONT
    ws["B3"] = "Uma linha por viagem; os valores caem no mês de ida. O consolidador à direita só confere os totais."
    ws["B3"].font = MUTED
    head = 6
    headers = [
        "CHAVE",
        "CHAVE",
        "CHAVE",
        "FILIAL",
        "DIVISÃO",
        "DENOMINAÇÃO DO CENTRO DE CUSTO",
        "CENTRO DE CUSTO",
        "OBJETIVO DA VIAGEM",
        "CARGO",
        "IDA (mês)",
        "VOLTA (mês)",
        "PERÍODO (nº de dias)",
        "ORIGEM",
        "DESTINO",
        "TIPO",
        "DESPESAS COM PASSAGENS",
        "DIÁRIA DE VIAGEM",
        "HOSPEDAGEM",
    ]
    _header_row(ws, head, 2, headers)
    cons_col = 2 + len(headers) + 2  # consolidador: CHAVE + JAN..DEZ + total
    _header_row(ws, head, cons_col, ["CHAVE", *MONTH_LABELS, ctx.target_year])
    consolidator: dict[str, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    row = head + 1
    for group in trips:
        first = group[0]
        attrs = first.attributes or {}
        branch = branches.get(first.branch_id)
        branch_code = branch.code if branch else None
        amounts = {}
        for line in group:
            acc = accounts.get(line.account_id)
            if acc is None:
                continue
            amounts[acc.code] = line.total_amount
            for v in line.values:
                if v.amount:
                    consolidator[_key(company, branch_code, cc.code, acc.code)][v.month] += v.amount
        dep = attrs.get("departure_month")
        ret = attrs.get("return_month")
        values = [
            *[_key(company, branch_code, cc.code, a) for a in TRAVEL_ACCOUNTS],
            branch.name if branch else None,
            branch_code,
            cc.name,
            cc.code,
            attrs.get("purpose") or first.description,
            attrs.get("job_level"),
            MONTH_LABELS[int(dep) - 1] if dep else None,
            MONTH_LABELS[int(ret) - 1] if ret else None,
            attrs.get("days"),
            attrs.get("origin"),
            attrs.get("destination"),
            attrs.get("trip_type"),
            *[_f(amounts.get(a, 0)) for a in TRAVEL_ACCOUNTS],
        ]
        for i, value in enumerate(values):
            cell = ws.cell(row, 2 + i, value)
            if i >= len(values) - 3:
                cell.number_format = MONEY
        row += 1
    crow = head + 1
    for key in sorted(consolidator):
        months = consolidator[key]
        ws.cell(crow, cons_col, key)
        for m in range(1, 13):
            cell = ws.cell(crow, cons_col + m, _f(months.get(m, 0)))
            cell.number_format = MONEY
        total = ws.cell(crow, cons_col + 13, _f(sum(months.values(), Decimal(0))))
        total.number_format = MONEY
        crow += 1
    ws.freeze_panes = ws.cell(head + 1, 2)
    _widths(ws, {2: 26, 3: 26, 4: 26, 5: 14, 6: 10, 7: 34, 8: 14, 9: 34, 10: 20, 15: 10, 16: 10, 17: 12})
    _widths(ws, {18: 16, 19: 16, 20: 16, cons_col: 28})


def _package_sheet(
    ws: Worksheet,
    ctx: Context,
    cc: CostCenter,
    lines: list[BudgetLine],
    accounts: dict[int, Account],
    branches: dict[int, Branch],
    details: dict[int, str],
    account_texts: dict[int, str] | None = None,
) -> None:
    """Aba de pacote genérico: CHAVE, detalhamento, fornecedor, justificativa, códigos e JAN..DEZ. Linha sem
    justificativa própria leva a justificativa da conta (tela de Justificativas), para o template final."""
    account_texts = account_texts or {}
    company = cc.company.code
    ws["B2"], ws["B2"].font = f"{ws.title} · {cc.code} · {cc.name} · orçamento {ctx.target_year}", TITLE_FONT
    ws["B3"] = "Uma linha por lançamento (conta × detalhamento). A CHAVE é empresa-filial-centro de custo-conta."
    ws["B3"].font = MUTED
    head = 6
    headers = [
        "CHAVE",
        "DETALHAMENTO",
        "GESTOR DO CONTRATO",
        "FORNECEDOR",
        "JUSTIFICATIVA",
        "PREMISSA",
        "PRODUTO/SERVIÇO",
        "FILIAL",
        "DIVISÃO",
        "DENOMINAÇÃO DO CENTRO DE CUSTOS",
        "CENTRO DE CUSTO",
        "DESCRIÇÃO DA CONTA CONTÁBIL",
        "CONTA CONTÁBIL",
        *MONTH_LABELS,
        ctx.target_year,
    ]
    _header_row(ws, head, 2, headers)
    first_month = 2 + 13
    row = head + 1
    for line in lines:
        acc = accounts.get(line.account_id)
        if acc is None:
            continue
        branch = branches.get(line.branch_id)
        branch_code = branch.code if branch else None
        values = {v.month: v.amount for v in line.values}
        cells = [
            _key(company, branch_code, cc.code, acc.code),
            line.description,
            line.contract_manager,
            line.supplier,
            line.justification or account_texts.get(line.account_id),
            line.assumption,
            details.get(line.account_detail_id) if line.account_detail_id else None,
            branch.name if branch else None,
            branch_code,
            cc.name,
            cc.code,
            acc.name,
            acc.code,
        ]
        for i, value in enumerate(cells):
            ws.cell(row, 2 + i, value)
        for m in range(1, 13):
            cell = ws.cell(row, first_month + m - 1, _f(values.get(m, 0)))
            cell.number_format = MONEY
        a, b = get_column_letter(first_month), get_column_letter(first_month + 11)
        total = ws.cell(row, first_month + 12, f"=SUM({a}{row}:{b}{row})")
        total.number_format = MONEY
        row += 1
    ws.freeze_panes = ws.cell(head + 1, 3)
    _widths(ws, {2: 30, 3: 36, 4: 20, 5: 24, 6: 36, 7: 30, 8: 24, 9: 14, 10: 10, 11: 34, 12: 14, 13: 36, 14: 14})
    for col in range(first_month, first_month + 13):
        ws.column_dimensions[get_column_letter(col)].width = 13


def opex_template_workbook(db: Session, ctx: Context, sub: BudgetSubmission) -> bytes:
    cc = db.get(CostCenter, sub.cost_center_id)
    branches = {b.id: b for b in db.scalars(select(Branch).where(Branch.company_id == cc.company_id))}
    accounts = {a.id: a for a in db.scalars(select(Account))}
    details = {d.id: d.name for d in db.scalars(select(AccountDetail))}
    packages = db.scalars(
        select(BudgetPackage).where(BudgetPackage.roman.is_not(None)).order_by(BudgetPackage.sort_order)
    ).all()
    lines = db.scalars(select(BudgetLine).where(BudgetLine.submission_id == sub.id).order_by(BudgetLine.id)).all()
    texts = {
        j.account_id: j.text
        for j in db.scalars(select(AccountJustification).where(AccountJustification.submission_id == sub.id))
    }

    by_package: dict[int | None, list[BudgetLine]] = defaultdict(list)
    trips: dict[str, list[BudgetLine]] = defaultdict(list)
    travel_pkg = next((p for p in packages if p.form_type == "TRAVEL"), None)
    events: list[BudgetLine] = []
    for line in lines:
        if line.line_type == "TRAVEL":
            trips[line.group_ref or f"line-{line.id}"].append(line)
        elif line.line_type == "EVENT":
            events.append(line)  # calculados pelo sistema: só leitura (a reimportação os preserva)
        else:
            by_package[line.package_id].append(line)

    wb = Workbook()
    _instructions(
        wb,
        f"Template OPEX {ctx.target_year} preenchido",
        ctx,
        cc,
        sub,
        [
            "Planilha gerada pelo sistema a partir dos lançamentos deste centro de custo, no layout do template.",
            "Para alterar valores em lote: edite as abas de pacote (JAN..DEZ) e reimporte em Importação de dados; "
            "as linhas vindas de template são substituídas, as digitadas no sistema são preservadas.",
            "Na aba I - Viagens, cada linha é uma viagem; o consolidador à direita é só conferência.",
            "Linhas sem valor são ignoradas na importação. Não altere a CHAVE nem os códigos de CC e conta.",
            "Eventos (pacote III) são calculados pelo sistema: aparecem em 'Eventos (leitura)' e não são reimportados.",
        ],
    )
    opex_accounts = sorted(
        (a for a in accounts.values() if a.nature == "OPEX" and a.is_active), key=lambda a: (a.package_id or 0, a.code)
    )
    _bd_sheet(db, wb, cc, opex_accounts)
    for pkg in packages:
        ws = wb.create_sheet(f"{pkg.roman} - {pkg.name}"[:31])
        if pkg.form_type == "TRAVEL":
            ordered = [trips[k] for k in sorted(trips, key=lambda k: trips[k][0].id)]
            _travel_sheet(ws, ctx, cc, ordered, accounts, branches)
        else:
            _package_sheet(ws, ctx, cc, by_package.pop(pkg.id, []), accounts, branches, details, texts)
    leftovers = [line for pid, group in by_package.items() for line in group]  # sem pacote, ou do pacote Viagens
    if travel_pkg is not None:
        leftovers = [line for line in leftovers if line.package_id != travel_pkg.id] + by_package.get(travel_pkg.id, [])
    if leftovers:
        ws = wb.create_sheet(OTHER_SHEET)
        _package_sheet(ws, ctx, cc, sorted(leftovers, key=lambda line: line.id), accounts, branches, details, texts)
    if events:
        _events_sheet(wb.create_sheet("Eventos (leitura)"), ctx, events, accounts)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _events_sheet(ws: Worksheet, ctx: Context, events: list[BudgetLine], accounts: dict[int, Account]) -> None:
    ws["B2"], ws["B2"].font = f"Eventos · orçamento {ctx.target_year} (somente leitura)", TITLE_FONT
    ws["B3"] = (
        "Calculados pelo sistema (pessoas × refeição + material + estrutura + brindes + transporte). Edite na tela."
    )
    ws["B3"].font = MUTED
    headers = ["Conta", "Descrição da conta", "Evento", "Tipo", "Mês", "Pessoas", *MONTH_LABELS, "Total"]
    _header_row(ws, 6, 2, headers)
    for r, line in enumerate(events, start=7):
        acc = accounts.get(line.account_id)
        attrs = line.attributes or {}
        values = {v.month: v.amount for v in line.values}
        month = attrs.get("month")
        cells = [
            acc.code if acc else None,
            acc.name if acc else None,
            line.description,
            attrs.get("event_type"),
            MONTH_LABELS[int(month) - 1] if month else None,
            attrs.get("people"),
            *[_f(values.get(m, 0)) for m in range(1, 13)],
            _f(line.total_amount),
        ]
        for i, value in enumerate(cells):
            cell = ws.cell(r, 2 + i, value)
            if i >= 6:
                cell.number_format = MONEY
    _widths(ws, {2: 14, 3: 36, 4: 36, 5: 12, 6: 8, 7: 10})


# ---------------------------------------------------------------- CAPEX


def capex_template_workbook(db: Session, ctx: Context, sub: BudgetSubmission) -> bytes:
    cc = db.get(CostCenter, sub.cost_center_id)
    branches = {b.id: b for b in db.scalars(select(Branch).where(Branch.company_id == cc.company_id))}
    accounts = {a.id: a for a in db.scalars(select(Account))}
    projects = db.scalars(
        select(CapexProject).where(CapexProject.submission_id == sub.id).order_by(CapexProject.id)
    ).all()
    wb = Workbook()
    _instructions(
        wb,
        f"Template CAPEX {ctx.target_year} preenchido",
        ctx,
        cc,
        sub,
        [
            "Planilha gerada pelo sistema a partir das solicitações deste centro de custo, no layout do template.",
            "Uma linha por item; itens com o mesmo tipo de projeto e justificativa formam uma solicitação.",
            "Para alterar em lote: edite a aba Template_Orç e reimporte em Importação de dados.",
        ],
    )
    ws = wb.create_sheet(f"Template_Orç {ctx.target_year}")
    ws["B2"], ws["B2"].font = f"CAPEX · {cc.code} · {cc.name} · orçamento {ctx.target_year}", TITLE_FONT
    ws.cell(5, 10, "Não preencher").font = MUTED
    headers = [
        "EMPRESA",
        "SOLICITAÇÃO",
        "NOME FILIAL",
        "FILIAL",
        "NOME CENTRO DE CUSTO",
        "CENTRO DE CUSTO",
        "DESCRIÇÃO DA CONTA",
        "CONTA",
        "Projeto?",
        "TIPO DO PROJETO",
        "ITEM",
        "DESCRIÇÃO DETALHADA DO ITEM OU PROJETO",
        "VLR UNIT",
        "QTD",
        "VLR TOTAL",
        "JUSTIFICATIVA",
        *[datetime(ctx.target_year, m, 1) for m in range(1, 13)],
        f"Orçamento {ctx.target_year}",
        "VIDA ÚTIL",
    ]
    _header_row(ws, 6, 2, headers)
    money_idx = {i for i, h in enumerate(headers) if h in ("VLR UNIT", "VLR TOTAL") or isinstance(h, datetime)}
    money_idx.add(headers.index(f"Orçamento {ctx.target_year}"))
    for i, h in enumerate(headers):
        if isinstance(h, datetime):
            ws.cell(6, 2 + i).number_format = "mmm/yy"
    row = 7
    for p in projects:
        branch = branches.get(p.branch_id)
        for item in p.items:
            acc = accounts.get(item.account_id)
            values = {v.month: v.amount for v in item.values}
            cells = [
                cc.company.code,
                f"{p.code} · {p.title}",
                branch.name if branch else None,
                branch.code if branch else None,
                cc.name,
                cc.code,
                acc.name if acc else None,
                acc.code if acc else None,
                "Sim" if p.is_project else "Não",
                p.project_type_code,
                item.item_name,
                item.description or p.description or p.title,
                _f(item.unit_value),
                _f(item.quantity),
                _f(item.total_value),
                p.justification,
                *[_f(values.get(m, 0)) for m in range(1, 13)],
                _f(sum(values.values(), Decimal(0))),
                item.useful_life_months,
            ]
            for i, value in enumerate(cells):
                cell = ws.cell(row, 2 + i, value)
                if i in money_idx:
                    cell.number_format = MONEY
            row += 1
    ws.freeze_panes = "H7"
    _widths(ws, {2: 10, 3: 36, 4: 14, 5: 8, 6: 34, 7: 14, 8: 34, 9: 14, 10: 10, 11: 34, 12: 28, 13: 44, 14: 14})
    _widths(ws, {15: 8, 16: 14, 17: 44})
    for col in range(18, 32):
        ws.column_dimensions[get_column_letter(col)].width = 13
    capex_accounts = sorted((a for a in accounts.values() if a.nature == "CAPEX" and a.is_active), key=lambda a: a.code)
    _bd_sheet(db, wb, cc, capex_accounts, tables_row=2)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
