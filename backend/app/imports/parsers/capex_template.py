"""Template CAPEX preenchido (arquivo do gestor): cadastros + catálogo de ativos + solicitações 2027.

Num único upload, aproveita:
- aba `BD-Novo` → filiais, centros de custo (com gestor) e contas de ativo (parse_master);
- aba `LISTA ATIVOS` → catálogo item principal → classe de ativo → conta (ignora `#REF!`);
- aba `Template_Orç AAAA` → uma linha por item: filial, CC, conta, Projeto?, tipo, item, descrição,
  valor unitário × quantidade, justificativa e cronograma mensal.

Os cabeçalhos mensais do template vêm datados do ano anterior (erro conhecido do arquivo); os meses são
lidos pela posição (as 12 primeiras colunas de mês após a justificativa) e valem para o ano do ciclo.
"""

from datetime import date, datetime

from app.domain.rules.common import MONTH_LABELS
from app.imports.base import (
    Issue,
    ParseResult,
    Record,
    Sheet,
    clean_code,
    clean_str,
    find_table,
    is_blank,
    norm,
    to_decimal,
)
from app.imports.parsers.master import parse_master

ITEM_ALIASES = {
    "branch_name": ("NOME FILIAL",),
    "branch": ("FILIAL",),
    "cost_center_name": ("NOME CENTRO DE CUSTO", "NOME CC"),
    "cost_center": ("CENTRO DE CUSTO", "CC"),
    "account_name": ("DESCRIÇÃO DA CONTA",),
    "account": ("CONTA",),
    "is_project": ("Projeto?", "Projeto"),
    "project_type": ("TIPO DO PROJETO", "Tipo de Projeto"),
    "item": ("ITEM",),
    "description": ("DESCRIÇÃO DETALHADA DO ITEM OU PROJETO", "DESCRIÇÃO DETALHADA", "Descrição"),
    "unit_value": ("VLR UNIT", "Valor Unitário"),
    "quantity": ("QTD", "Quantidade"),
    "total": ("VLR TOTAL", "Valor Total"),
    "justification": ("JUSTIFICATIVA",),
    "useful_life": ("VIDA ÚTIL", "Vida útil (meses)"),
}
REQUIRED = ("cost_center", "account", "item", "unit_value", "quantity")
MONTH_NAMES = set(MONTH_LABELS) | {
    "JANEIRO",
    "FEVEREIRO",
    "MARCO",
    "ABRIL",
    "MAIO",
    "JUNHO",
    "JULHO",
    "AGOSTO",
    "SETEMBRO",
    "OUTUBRO",
    "NOVEMBRO",
    "DEZEMBRO",
}
YES = {"sim", "s", "yes", "x"}


def _item_table(sheet: Sheet):
    return find_table(sheet, ITEM_ALIASES, REQUIRED, max_scan_rows=30)


def is_capex_template(sheets: list[Sheet]) -> bool:
    return any(_item_table(s) is not None for s in sheets if not norm(s.name).startswith("bd"))


def _month_columns(sheet: Sheet, header_row: int, after: int) -> dict[int, int]:
    months: dict[int, int] = {}
    for col, cell in enumerate(sheet.rows[header_row - 1], start=1):
        if col <= after or len(months) == 12:
            continue
        if isinstance(cell, (datetime, date)) or norm(cell).upper() in MONTH_NAMES:
            months[len(months) + 1] = col
    return months


def parse_catalog(sheet: Sheet, result: ParseResult) -> int:
    """Pares (Item Principal, Nome Classe[, conta]) lado a lado; o primeiro par que nomeia o item vale."""
    header = sheet.rows[0] if sheet.rows else ()
    pairs = []
    for i in range(len(header) - 1):
        if norm(header[i]) == "item principal" and norm(header[i + 1]) == "nome classe":
            pairs.append((i, i + 1, i + 2 if i + 2 < len(header) else None))
    seen: set[str] = set()
    for row_no, row in sheet.iter_rows(2):
        for item_col, class_col, code_col in pairs:
            name = clean_str(row[item_col]) if item_col < len(row) else None
            klass = clean_str(row[class_col]) if class_col < len(row) else None
            if not name or not klass or "#REF" in name.upper() or "#REF" in klass.upper():
                continue
            key = " ".join(name.upper().split())
            if key in seen:
                continue
            seen.add(key)
            code = clean_code(row[code_col]) if code_col is not None and code_col < len(row) else None
            result.records.append(
                Record(
                    "ASSET_ITEM",
                    sheet.name,
                    row_no,
                    {"name": key, "asset_class": klass, "account": code if code and code.isdigit() else None},
                    natural_key=f"ASSET|{key}",
                )
            )
    return len(seen)


def _number(rec: Record, raw, column: str):
    try:
        return to_decimal(raw)
    except ValueError:
        rec.error("INVALID_NUMBER", f"{column} inválido", column, raw)
        return None


def parse_items(sheet: Sheet, result: ParseResult) -> int:
    table = _item_table(sheet)
    if table is None:
        return 0
    after = table.columns.get("justification") or table.columns["quantity"]
    months = _month_columns(sheet, table.header_row, after)
    if len(months) < 12:
        result.structural.append(
            Issue("NO_MONTHS", f"Aba {sheet.name}: cronograma mensal (12 colunas) não encontrado", severity="ERROR")
        )
        return 0
    count = 0
    for row_no, row in table.rows():
        if is_blank(row):
            continue
        cc = clean_code(table.value(row, "cost_center"))
        item = clean_str(table.value(row, "item"))
        rec = Record("CAPEX_ITEM", sheet.name, row_no, {})
        unit = _number(rec, table.value(row, "unit_value"), "VLR UNIT")
        qty = _number(rec, table.value(row, "quantity"), "QTD")
        values: dict[int, str] = {}
        for month, col in months.items():
            amount = _number(rec, row[col - 1] if col - 1 < len(row) else None, MONTH_LABELS[month - 1])
            if amount:
                values[month] = str(amount)
        if not (cc or item or unit or qty or values):
            continue  # linha do modelo sem preenchimento (só fórmulas zeradas)
        if not cc:
            rec.error("REQUIRED", "Centro de custo não informado", "CENTRO DE CUSTO")
        if not clean_code(table.value(row, "account")):
            rec.error("REQUIRED", "Conta não informada", "CONTA")
        if not item:
            rec.error("REQUIRED", "Item não informado", "ITEM")
        if not unit or not qty:
            rec.error("REQUIRED", "Valor unitário e quantidade são obrigatórios", "VLR UNIT")
        if any(v.startswith("-") for v in values.values()) or (unit is not None and unit < 0):
            rec.error("NEGATIVE_VALUE", "Valores do CAPEX não podem ser negativos")
        life = _number(rec, table.value(row, "useful_life"), "VIDA ÚTIL") if "useful_life" in table.columns else None
        rec.data = {
            "company": None,
            "branch": clean_code(table.value(row, "branch"), 4),
            "branch_name": clean_str(table.value(row, "branch_name")),
            "cost_center": cc,
            "cost_center_name": clean_str(table.value(row, "cost_center_name")),
            "account": clean_code(table.value(row, "account")),
            "account_name": clean_str(table.value(row, "account_name")),
            "is_project": norm(table.value(row, "is_project")) in YES,
            "project_type": clean_str(table.value(row, "project_type")),
            "item": item,
            "description": clean_str(table.value(row, "description")),
            "unit_value": None if unit is None else str(unit),
            "quantity": None if qty is None else str(qty),
            "file_total": _str(_safe_decimal(table.value(row, "total"))),
            "justification": clean_str(table.value(row, "justification")),
            "useful_life_months": int(life) if life else None,
            "values": values,
        }
        result.records.append(rec)
        count += 1
    return count


def _safe_decimal(value):
    try:
        return to_decimal(value)
    except ValueError:
        return None


def _str(value) -> str | None:
    return None if value is None else str(value)


def parse_capex_template(sheets: list[Sheet], options: dict) -> ParseResult:
    result = ParseResult("CAPEX_TEMPLATE", "TEMPLATE_CAPEX")
    if not is_capex_template(sheets):
        return result
    bd = [s for s in sheets if norm(s.name).startswith("bd")]
    master = parse_master(bd, options) if bd else ParseResult("MASTER_DATA", "TEMPLATE_BD")
    result.records.extend(master.records)
    catalog = sum(parse_catalog(s, result) for s in sheets if norm(s.name).startswith("lista ativos"))
    items = 0
    for sheet in sheets:
        if not norm(sheet.name).startswith("bd"):
            items += parse_items(sheet, result)
    result.meta = {"parts": {"master": len(master.records), "asset_items": catalog, "capex_items": items}}
    if not items:
        result.structural.append(
            Issue("NO_CAPEX_ITEMS", "Nenhuma linha de CAPEX preenchida na aba Template_Orç", severity="WARNING")
        )
    return result
