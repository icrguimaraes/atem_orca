"""Fatos financeiros: realizado (layout largo do template ou SAP KSB1) e orçamento de referência."""

import re
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
    norm,
    to_date,
    to_decimal,
)
from app.imports.parsers.common import attach_month_columns, parse_month_header

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

# A exportação do SAP abrevia os cabeçalhos ("Centro custo", "Denominação objeto", "Denom.classe custo");
# "Centro" é a planta (C001 = filial 0001) — ver resolver.Dimensions.branch_id.
KSB1_ALIASES = {
    "company": ("Empresa", "Empresa CO", "Código da empresa", "Area contab custos"),
    "branch": ("Local de negócios", "Divisão", "Filial", "Centro"),
    "cost_center": ("Centro de custo", "Centro custo", "Centro cst", "Centro de custos", "Objeto"),
    "cost_center_name": ("Denominação do objeto", "Denominação objeto", "Denominação centro de custo"),
    "account": ("Classe de custo", "Classe custo", "Conta do Razão", "Conta Razão"),
    "account_name": ("Denominação da classe de custo", "Denom classe custo", "Denom.classe custo", "Txt classe custo"),
    "year": ("Exercício", "Ano"),
    "period": ("Período", "Mês", "Período contábil"),
    # a data de lançamento define o período; a data do documento é só reserva (ordem = prioridade)
    "posting_date": ("Data de lançamento", "Data lançamento", "Dt lançamento", "Data do documento"),
    "document": (
        "Nº documento",
        "No documento",
        "Número documento",
        "Documento",
        "Nº doc.de referência",
        "Nº doc de referência",
        "Nº doc ref",
        "Doc compras",
    ),
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


def _pick_block_months(table, dataset_type: str) -> str | None:
    """Acompanhamento orçamentário (ex.: 'Realizado 2025', aba ANÁLISE): os meses aparecem duas vezes no
    cabeçalho, com a linha de cima dizendo ORÇADO ou REAL. Realizado usa o bloco REAL; orçado de referência, o
    bloco ORÇADO. Sem essa linha, vale a detecção normal (primeira ocorrência de cada mês)."""
    markers: dict[int, str] = {}
    for back in (2, 3, 4):  # a linha das marcas pode vir acima de uma linha de totais
        if table.header_row - back < 0:
            break
        above = table.sheet.rows[table.header_row - back]
        found = {idx: norm(cell).upper() for idx, cell in enumerate(above, start=1) if cell not in (None, "")}
        if {"REAL", "ORCADO"} <= set(found.values()):
            markers = found
            break
    if not markers:
        return None
    want = "REAL" if dataset_type == "ACTUAL" else "ORCADO"
    header = table.sheet.rows[table.header_row - 1]
    months: dict[int, int] = {}
    for idx, cell in enumerate(header, start=1):
        parsed = parse_month_header(cell)
        if parsed is None or markers.get(idx) != want or parsed[1] in months:
            continue
        months[parsed[1]] = idx
    table.month_columns = months
    return want


def parse_wide(sheets: list[Sheet], options: dict, dataset_type: str) -> ParseResult:
    """Layout do template ('Realizado 2026'): uma linha por chave, colunas mensais."""
    result = ParseResult(dataset_type, "WIDE_MONTHLY")
    for sheet in sheets:
        table = find_table(sheet, WIDE_ALIASES, ("cost_center", "account"))
        if table is None:
            continue
        detected_year = attach_month_columns(table)
        block = _pick_block_months(table, dataset_type)
        if block:
            result.meta["block"] = block
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
            declared = table.value(row, "total") if block != "ORCADO" else None
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


PROJECTION_ALIASES = {
    "company": ("Empresa",),
    "cost_center": ("Cód CC", "Cod CC"),
    "cost_center_name": ("Descrição C. Custo", "Descricao C. Custo"),
    "account": ("Código conta", "Codigo conta"),
    "account_name": ("Descrição Conta Contábil", "Descricao Conta Contabil"),
    "month": ("Mês", "Mes"),
    "amount": ("Total 3ª Projeção", "Total 3a Projeção", "Total 3 Projeção"),
}


# projeção com o CC escrito por extenso (sem código): descrição na planilha → CC do sistema (sempre com aviso)
PROJECTION_CC_BY_NAME = {"CSC": "1050101012"}


def parse_projection(sheets: list[Sheet], options: dict) -> ParseResult:
    """Projeção do gestor (planilha 'Base'): CC × conta × mês, usando a 3ª projeção (é a 'Projeção Atual' do
    resumo). Cada linha vira um lançamento mensal; a carga só mantém os meses sem realizado (KSB1)."""
    result = ParseResult("PROJECTION", "PROJECTION_BASE")
    for sheet in sheets:
        if not norm(sheet.name).startswith("base"):
            continue
        table = find_table(sheet, PROJECTION_ALIASES, ("cost_center", "account", "amount", "month"))
        if table is None:
            continue
        result.meta.update({"sheet": sheet.name})
        closed = options.get("projection_closed") or {}
        default_company = options.get("company_code")
        merged: dict[tuple, Record] = {}  # linhas repetidas do mesmo CC × conta × mês somam (não são duplicidade)
        ignored = 0
        for row_no, row in table.rows():
            if is_blank(row):
                continue
            rec = Record("FACT", sheet.name, row_no, {})
            if clean_code(table.value(row, "cost_center")) is None and clean_code(table.value(row, "account")) is None:
                continue  # linha de total geral da planilha
            raw = table.value(row, "amount")
            try:
                amount = to_decimal(raw)
            except ValueError:
                rec.error("INVALID_NUMBER", "Projeção inválida", "Total 3ª Projeção", raw)
                result.records.append(rec)
                continue
            if amount in (None, 0):
                continue  # sem valor (inclui linhas de total/em branco)
            month_value = table.value(row, "month")
            if not hasattr(month_value, "month"):
                rec.error("INVALID_DATE", "Mês inválido", "Mês", month_value)
                result.records.append(rec)
                continue
            company = clean_code(table.value(row, "company")) or default_company
            year, month = int(month_value.year), int(month_value.month)
            # empresa sem KSB1 carregado (ex.: REAM) usa o mesmo mês de corte das demais no ano
            cut = closed.get(f"{year}:{company}") or max(
                (int(v or 0) for k, v in closed.items() if k.startswith(f"{year}:")), default=0
            )
            if month <= int(cut):
                ignored += 1  # mês com realizado (KSB1): a projeção não entra
                continue
            cc = clean_code(table.value(row, "cost_center"))
            cc_name = clean_str(table.value(row, "cost_center_name"))
            by_name: str | None = None
            if cc is None and cc_name and cc_name.strip().upper() in PROJECTION_CC_BY_NAME:
                cc = PROJECTION_CC_BY_NAME[cc_name.strip().upper()]
                by_name = cc_name.strip()
            account = clean_code(table.value(row, "account"))
            key = (company, cc, account, year, month)
            if key in merged:
                prev = merged[key]
                prev.data["values"][month] = str(Decimal(prev.data["values"][month]) + amount)
                continue
            rec.data.update(
                {
                    "company": company,
                    "branch": None,
                    "branch_name": None,
                    "cost_center": cc,
                    "cost_center_name": clean_str(table.value(row, "cost_center_name")),
                    "manager": None,
                    "account": account,
                    "account_name": clean_str(table.value(row, "account_name")),
                    "package": None,
                    "year": year,
                    "values": {month: str(amount)},
                }
            )
            rec.natural_key = f"{company}|{cc}|{account}|{month}"
            if by_name:
                rec.warn(
                    "CC_BY_NAME",
                    f"Linha sem código de CC na planilha ('{by_name}'): associada ao CC {cc} pela descrição. "
                    "Confirmar com o gestor (conta e valor)",
                    "Cód CC",
                    by_name,
                )
            merged[key] = rec
        result.records.extend(merged.values())
        result.meta["ignored_months_with_actual"] = ignored
        return result
    return result


BUDGET_LONG_ALIASES = PROJECTION_ALIASES | {
    "amount": (
        *(f"Orçamento {y}" for y in range(2020, 2036)),
        *(f"Orçado {y}" for y in range(2020, 2036)),
        "Orçamento",
        "Orçado",
        "Valor Orçado",
    ),
}
# CC com prefixo de filial ou sufixo na planilha ("03-1050101003", "1050101003(1)"): código SAP dentro do texto
_CC_IN_TEXT = re.compile(r"(\d{10}|[A-Z]{3}\d{7})")


def parse_budget_long(sheets: list[Sheet], options: dict) -> ParseResult:
    """Orçado de referência em linhas (aba 'Orçamento 2026' da Base de controle da Controladoria): CC × conta × mês
    com a coluna 'Orçamento AAAA'. Linhas repetidas do mesmo CC × conta × mês somam; linha de total geral (sem CC e
    conta) fica de fora; CC escrito com prefixo/sufixo é lido pelo código SAP, sempre com aviso."""
    result = ParseResult("REFERENCE_BUDGET", "BUDGET_LONG")
    for sheet in sheets:
        table = find_table(sheet, BUDGET_LONG_ALIASES, ("cost_center", "account", "amount", "month"))
        if table is None:
            continue
        result.meta["sheet"] = sheet.name
        target = options.get("reference_year")
        default_company = options.get("company_code")
        merged: dict[tuple, Record] = {}
        other_year = Decimal("0")
        other_year_rows = 0
        for row_no, row in table.rows():
            if is_blank(row):
                continue
            raw_cc = clean_code(table.value(row, "cost_center"))
            cc_name = clean_str(table.value(row, "cost_center_name"))
            account = clean_code(table.value(row, "account"))
            if raw_cc is None and account is None:
                continue  # linha de total geral da planilha
            rec = Record("FACT", sheet.name, row_no, {})
            raw = table.value(row, "amount")
            try:
                amount = to_decimal(raw)
            except ValueError:
                rec.error("INVALID_NUMBER", "Valor orçado inválido", "Orçamento", raw)
                result.records.append(rec)
                continue
            if amount in (None, 0):
                continue
            month_value = table.value(row, "month")
            if not hasattr(month_value, "month"):
                rec.error("INVALID_DATE", "Mês inválido", "Mês", month_value)
                result.records.append(rec)
                continue
            year, month = int(month_value.year), int(month_value.month)
            if target and year != int(target):
                other_year += amount
                other_year_rows += 1
                continue
            cc, note = raw_cc, None
            if cc is None and cc_name and cc_name.strip().upper() in PROJECTION_CC_BY_NAME:
                cc = PROJECTION_CC_BY_NAME[cc_name.strip().upper()]
                note = f"Linha sem código de CC ('{cc_name}'): associada ao CC {cc} pela descrição"
            elif cc and not cc.isalnum() and (found := _CC_IN_TEXT.search(cc)):
                cc = found.group(1)
                note = f"CC escrito como '{raw_cc}' na planilha: lido como {cc}. Confirmar com o gestor"
            company = clean_code(table.value(row, "company")) or default_company
            key = (company, cc, account, year, month)
            if key in merged:
                prev = merged[key]
                prev.data["values"][month] = str(Decimal(prev.data["values"][month]) + amount)
                if note:
                    prev.warn("CC_NORMALIZED", note, "Cód CC", raw_cc or cc_name)
                continue
            rec.data.update(
                {
                    "company": company,
                    "branch": None,
                    "branch_name": None,
                    "cost_center": cc,
                    "cost_center_name": cc_name,
                    "manager": None,
                    "account": account,
                    "account_name": clean_str(table.value(row, "account_name")),
                    "package": None,
                    "year": year,
                    "values": {month: str(amount)},
                }
            )
            rec.natural_key = f"{company}|{cc}|{account}|{year}|{month}"
            if note:
                rec.warn("CC_NORMALIZED", note, "Cód CC", raw_cc or cc_name)
            merged[key] = rec
        result.records.extend(merged.values())
        result.meta["years"] = sorted({r.data["year"] for r in merged.values()})
        if other_year_rows:
            result.structural.append(
                Issue(
                    "OTHER_YEAR_IGNORED",
                    f"{other_year_rows} linha(s) de outro ano (R$ {other_year:,.2f}) ficaram fora desta carga",
                    severity="WARNING",
                )
            )
        return result
    return result


CAPEX_ACUM_ALIASES = {
    "company": ("EMPRESA",),
    "cost_center": ("CENTRO DE CUSTO",),
    "item": ("CENTRO FINANC./ ITEM", "Centro financ./item orçamento"),
    "period": ("PERIODO", "PERÍODO"),
    "actual": ("REALIZADO",),
    "projection": ("PROJEÇÃO", "PROJECAO"),
}
_TEN_DIGITS = re.compile(r"(\d{10})")


def parse_capex_acum(sheets: list[Sheet], options: dict, dataset_type: str) -> ParseResult:
    """CAPEX acumulado (aba 'Base Capex': CC × projeto × item × mês). Como "ACTUAL": realizado do ano do ciclo
    anterior (jan–set); como "PROJECTION": coluna Projeção dos meses sem realizado. Meses de outro ano ficam de fora
    (aviso na prévia); linhas repetidas do mesmo CC × conta × mês somam."""
    result = ParseResult(dataset_type, "CAPEX_ACUM")
    for sheet in sheets:
        if "capex" not in norm(sheet.name):
            continue
        table = find_table(sheet, CAPEX_ACUM_ALIASES, ("cost_center", "item", "period", "actual", "projection"))
        if table is None:
            continue
        result.meta["sheet"] = sheet.name
        closed = options.get("projection_closed") or {}
        year_target = int(
            options.get("reference_year") or max((int(k.split(":")[0]) for k in closed), default=0) or 2026
        )
        merged: dict[tuple, Record] = {}
        other_year = Decimal("0")
        other_year_rows = 0
        for row_no, row in table.rows():
            if is_blank(row):
                continue
            period = table.value(row, "period")
            cc_match = _TEN_DIGITS.search(str(table.value(row, "cost_center") or ""))
            acc_match = _TEN_DIGITS.search(str(table.value(row, "item") or ""))
            if not hasattr(period, "month") or cc_match is None or acc_match is None:
                continue  # linha de total/em branco
            company = clean_code(table.value(row, "company")) or options.get("company_code")
            year, month = int(period.year), int(period.month)
            try:
                actual = to_decimal(table.value(row, "actual")) or Decimal("0")
                projection = to_decimal(table.value(row, "projection")) or Decimal("0")
            except ValueError:
                rec = Record("FACT", sheet.name, row_no, {})
                rec.error("INVALID_NUMBER", "Valor inválido", "Realizado/Projeção", None)
                result.records.append(rec)
                continue
            cut = int(
                closed.get(f"{year}:{company}")
                or max((int(v or 0) for k, v in closed.items() if k.startswith(f"{year}:")), default=0)
            )
            if year != year_target:
                other = actual if dataset_type == "ACTUAL" else projection
                if other:
                    other_year += other
                    other_year_rows += 1
                continue
            if dataset_type == "ACTUAL":
                if cut and month > cut:
                    continue  # realizado de mês ainda não fechado (compromissos): fica de fora
                amount = actual
            else:
                if month <= cut:
                    continue  # mês com realizado: a projeção não entra
                amount = projection
            if not amount:
                continue
            cc, account = cc_match.group(1), acc_match.group(1)
            key = (company, cc, account, year, month)
            if key in merged:
                prev = merged[key]
                prev.data["values"][month] = str(Decimal(prev.data["values"][month]) + amount)
                continue
            rec = Record("FACT", sheet.name, row_no, {})
            rec.data.update(
                {
                    "company": company,
                    "branch": None,
                    "branch_name": None,
                    "cost_center": cc,
                    "cost_center_name": None,
                    "manager": None,
                    "account": account,
                    "account_name": clean_str(str(table.value(row, "item") or "").split("  ", 1)[-1]),
                    "package": None,
                    "year": year,
                    "values": {month: str(amount)},
                }
            )
            rec.natural_key = f"{company}|{cc}|{account}|{year}|{month}"
            merged[key] = rec
        result.records.extend(merged.values())
        if other_year_rows:
            result.structural.append(
                Issue(
                    "OTHER_YEAR_IGNORED",
                    f"{other_year_rows} linha(s) de outro ano (ex.: dez/2025, R$ {other_year:,.2f}) "
                    "ficaram fora desta carga",
                    severity="WARNING",
                )
            )
        return result
    return result


def parse_ksb1(sheets: list[Sheet], options: dict) -> ParseResult:
    """Exportação SAP KSB1 (partidas individuais de centro de custo)."""
    result = ParseResult("ACTUAL", "SAP_KSB1")
    for sheet in sheets:
        table = find_table(sheet, KSB1_ALIASES, ("cost_center", "account", "amount"), prefer_alias_order=True)
        if table is None or not ({"year", "period"} <= table.columns.keys() or "posting_date" in table.columns):
            continue
        result.meta["sheet"] = sheet.name
        for row_no, row in table.rows():
            if is_blank(row):
                continue
            rec = Record("FACT", sheet.name, row_no, {})
            rec.data.update(_identity(rec, table, row))
            dated = any(
                table.value(row, k) not in (None, "") for k in ("posting_date", "year", "period") if k in table.columns
            )
            if rec.data["account"] is None and not dated:
                continue  # total geral ou subtotal por CC do relatório (só CC e valor preenchidos)
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
            # Sem chave natural: cada partida individual é um fato. Linhas idênticas (mesmo documento,
            # conta, valor e texto) são comuns e legítimas — ex.: depreciação de dois ativos iguais.
            result.records.append(rec)
        years = {r.data["year"] for r in result.records if r.data.get("year")}
        result.meta["years"] = sorted(years)
        # último mês com lançamento por ano: vira o mês fechado da versão carregada (anualização)
        result.meta["last_periods"] = {
            str(y): max(
                (r.data["period"] for r in result.records if r.data.get("year") == y and r.data.get("period")),
                default=None,
            )
            for y in sorted(years)
        }
        return result
    return result
