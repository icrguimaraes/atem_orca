"""Fatos financeiros: realizado (layout largo do template ou SAP KSB1) e orçamento de referência."""

from decimal import Decimal

from app.imports.base import (
    Issue,
    ParseResult,
    Record,
    Sheet,
    clean_code,
    clean_str,
    find_table,
    is_blank,
    to_date,
    to_decimal,
)
from app.imports.parsers.common import attach_month_columns

WIDE_ALIASES = {
    "company": ("Empresa", "Código Empresa"),
    "branch": ("Filial", "Local de negócios", "Divisão"),
    "branch_name": ("Nome Filial",),
    "cost_center": ("Centro de Custos", "Centro de Custo"),
    "cost_center_name": ("Denominação do Centro de Custos", "Denominação de centro de custos"),
    "manager": ("Gestor do CC", "Gestor do Centro de Custo"),
    "account": ("Conta Razão", "Conta do Razão", "Conta Contábil", "Conta"),
    "account_name": ("Denominação da Conta do Razão", "Descrição da Conta", "Descrição"),
    "package": ("Pacotes Orçamento", "Pacote GMD", "Pacote"),
    "total": ("Total Realizado 2025", "Total Realizado 2026", "Total Realizado", "Total Orçado", "Total"),
}

KSB1_ALIASES = {
    "company": ("Empresa", "Empresa CO", "Código da empresa", "Area contab custos"),
    "branch": ("Local de negócios", "Divisão", "Filial"),
    "cost_center": ("Centro de custo", "Centro cst", "Centro de custos", "Objeto"),
    "cost_center_name": ("Denominação do objeto", "Denominação centro de custo"),
    "account": ("Classe de custo", "Classe custo", "Conta do Razão", "Conta Razão"),
    "account_name": ("Denominação da classe de custo", "Denom classe custo", "Txt classe custo"),
    "year": ("Exercício", "Ano"),
    "period": ("Período", "Mês", "Período contábil"),
    "posting_date": ("Data de lançamento", "Data lançamento", "Dt lançamento", "Data do documento"),
    "document": ("Nº documento", "No documento", "Número documento", "Documento", "Nº doc ref", "Doc compras"),
    "document_type": ("Tipo de documento", "Tipo doc", "Ctg doc"),
    "amount": (
        "Valor/moeda ACC",
        "Valor/moeda obj",
        "Valor/MACC",
        "Valor/moeda objeto",
        "Valor em moeda da área de contabilidade de custos",
        "Valor",
        "Montante",
    ),
    "currency": ("Moeda", "Moeda ACC", "Moeda da transação"),
    "text": ("Texto", "Denominação", "Texto cabeçalho documento", "Texto do cabeçalho do documento"),
    "vendor_code": ("Fornecedor",),
    "vendor_name": ("Nome do fornecedor", "Nome fornecedor", "Nome 1"),
}


def _identity(rec: Record, table, row) -> dict:
    return {
        "company": clean_code(table.value(row, "company")),
        "branch": clean_code(table.value(row, "branch")),
        "branch_name": clean_str(table.value(row, "branch_name")),
        "cost_center": clean_code(table.value(row, "cost_center")),
        "cost_center_name": clean_str(table.value(row, "cost_center_name")),
        "manager": clean_str(table.value(row, "manager")),
        "account": clean_code(table.value(row, "account")),
        "account_name": clean_str(table.value(row, "account_name")),
        "package": clean_str(table.value(row, "package")),
    }


def parse_wide(sheets: list[Sheet], options: dict, dataset_type: str) -> ParseResult:
    """Layout do template ('Realizado 2026'): uma linha por chave, colunas mensais."""
    result = ParseResult(dataset_type, "WIDE_MONTHLY")
    for sheet in sheets:
        table = find_table(sheet, WIDE_ALIASES, ("cost_center", "account"))
        if table is None:
            continue
        detected_year = attach_month_columns(table)
        if not table.month_columns:
            continue
        year = options.get("reference_year") or detected_year
        if year is None:
            result.structural.append(
                Issue("YEAR_REQUIRED", "Ano de referência não identificado nos cabeçalhos; informe-o na importação")
            )
            return result
        result.meta.update({"sheet": sheet.name, "year": int(year), "months": sorted(table.month_columns)})
        if table.unknown:
            result.structural.append(
                Issue(
                    "UNKNOWN_COLUMNS",
                    "Colunas não reconhecidas (ignoradas): " + ", ".join(table.unknown),
                    severity="WARNING",
                )
            )
        for row_no, row in table.rows():
            if is_blank(row):
                continue
            rec = Record("FACT", sheet.name, row_no, {})
            rec.data.update(_identity(rec, table, row))
            if rec.data["cost_center"] is None and rec.data["account"] is None:
                continue  # linha de texto ("Não há realizado no centro de custo.")
            values: dict[int, str] = {}
            for month, col in table.month_columns.items():
                raw = row[col - 1] if col - 1 < len(row) else None
                try:
                    amount = to_decimal(raw)
                except ValueError:
                    rec.error("INVALID_NUMBER", "Valor mensal inválido", f"mês {month}", raw)
                    continue
                if amount is not None:
                    values[month] = str(amount)
            rec.data["year"] = int(year)
            rec.data["values"] = values
            declared = table.value(row, "total")
            if declared not in (None, ""):
                try:
                    declared_total = to_decimal(declared) or Decimal("0")
                    if abs(declared_total - sum(Decimal(v) for v in values.values())) > Decimal("0.05"):
                        rec.warn("TOTAL_MISMATCH", "Total informado difere da soma dos meses", "Total", declared)
                except ValueError:
                    rec.warn("INVALID_NUMBER", "Total inválido (ignorado)", "Total", declared)
            if not values:
                rec.warn("NO_VALUES", "Linha sem valores mensais")
            rec.natural_key = (
                f"{rec.data['company']}|{rec.data['branch']}|{rec.data['cost_center']}|{rec.data['account']}"
            )
            result.records.append(rec)
        return result
    return result


def parse_ksb1(sheets: list[Sheet], options: dict) -> ParseResult:
    """Exportação SAP KSB1 (partidas individuais de centro de custo)."""
    result = ParseResult("ACTUAL", "SAP_KSB1")
    for sheet in sheets:
        table = find_table(sheet, KSB1_ALIASES, ("cost_center", "account", "amount"))
        if table is None or not ({"year", "period"} <= table.columns.keys() or "posting_date" in table.columns):
            continue
        result.meta["sheet"] = sheet.name
        for row_no, row in table.rows():
            if is_blank(row):
                continue
            rec = Record("FACT", sheet.name, row_no, {})
            rec.data.update(_identity(rec, table, row))
            if rec.data["cost_center"] is None and rec.data["account"] is None:
                continue  # linhas de total/subtotal do relatório
            try:
                amount = to_decimal(table.value(row, "amount"))
            except ValueError as exc:
                rec.error("INVALID_NUMBER", str(exc), "Valor", table.value(row, "amount"))
                amount = None
            try:
                posting = to_date(table.value(row, "posting_date"))
            except ValueError as exc:
                rec.error("INVALID_DATE", str(exc), "Data de lançamento", table.value(row, "posting_date"))
                posting = None
            year = clean_code(table.value(row, "year"))
            period = clean_code(table.value(row, "period"))
            year_i = int(year) if year and year.isdigit() else (posting.year if posting else None)
            period_i = int(period) if period and period.isdigit() else (posting.month if posting else None)
            if year_i is None:
                rec.error("REQUIRED", "Exercício/ano não informado", "Exercício")
            if period_i is None or not 1 <= period_i <= 16:
                rec.error("INVALID_PERIOD", "Período inválido", "Período", period)
            elif period_i > 12:
                period_i = 12  # períodos especiais de fechamento (13-16) agregam em dezembro
                rec.warn("SPECIAL_PERIOD", "Período especial agregado em dezembro", "Período", period)
            if amount is None:
                rec.error("REQUIRED", "Valor não informado", "Valor")
            rec.data.update(
                {
                    "year": year_i,
                    "period": period_i,
                    "amount": None if amount is None else str(amount),
                    "posting_date": posting.isoformat() if posting else None,
                    "document": clean_str(table.value(row, "document")),
                    "document_type": clean_str(table.value(row, "document_type")),
                    "currency": clean_str(table.value(row, "currency")) or "BRL",
                    "text": clean_str(table.value(row, "text")),
                    "vendor_code": clean_code(table.value(row, "vendor_code")),
                    "vendor_name": clean_str(table.value(row, "vendor_name")),
                }
            )
            if rec.data["document"]:
                rec.natural_key = "|".join(
                    str(rec.data[k]) for k in ("company", "cost_center", "account", "document", "amount", "text")
                )
            result.records.append(rec)
        years = {r.data["year"] for r in result.records if r.data.get("year")}
        result.meta["years"] = sorted(years)
        return result
    return result
