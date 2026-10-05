"""Template OPEX preenchido (arquivo do gestor): cadastros + realizado + orçamento 2027 por pacote.

O template é distribuído por gestor e volta preenchido. Num único upload, aproveita:
- aba `BD-Novo` → filiais, centros de custo e contas (parse_master);
- aba `Realizado AAAA` → realizado (parse_wide), quando a Controladoria a preencheu;
- abas de pacote (I - Viagens … XII - Comercial) → linhas do orçamento 2027.

Nas abas de pacote, a coluna CHAVE (`empresa-filial-cc-conta`, calculada pelo próprio template)
identifica a linha; na aba de Viagens usa-se o "Consolidador" (CHAVE + JAN..DEZ), que já soma
passagem, diária e hospedagem por mês de ida.
"""

import re
from decimal import Decimal

from app.domain.rules.common import MONTH_LABELS
from app.imports.base import Issue, ParseResult, Record, Sheet, clean_str, norm, to_decimal
from app.imports.parsers.financial import parse_wide
from app.imports.parsers.master import parse_master

PACKAGE_SHEET = re.compile(r"^\s*([IVX]+)\s*-\s*(.+)$")
KEY = re.compile(r"^(\d{3,10})-(\d*)-(\d{3,20})-(\d{4,20})$")
DESCRIPTION_HEADERS = ("detalhamento", "objetivo da viagem", "objetivo do evento")
SUPPLIER_HEADERS = ("fornecedor",)
NOTE_HEADERS = ("justificativa", "observacoes", "finalidade")


def is_opex_template(sheets: list[Sheet]) -> bool:
    names = [s.name for s in sheets]
    has_bd = any(norm(n).startswith("bd") for n in names)
    packages = sum(1 for n in names if PACKAGE_SHEET.match(n))
    return has_bd and packages >= 3


def _header(sheet: Sheet) -> tuple[int, dict[int, int], int, dict[str, int], bool] | None:
    """Linha de cabeçalho com CHAVE e JAN..DEZ.

    Devolve (linha, {mês: coluna}, coluna da CHAVE, demais colunas, é_consolidador). Na aba de Viagens
    há duas colunas CHAVE; a usada é a mais próxima dos meses (o consolidador)."""
    for idx, row in sheet.iter_rows(1):
        if idx > 80:
            return None
        texts = [norm(v).upper() for v in row]
        months = {}
        for col, text in enumerate(texts, start=1):
            if text in MONTH_LABELS and MONTH_LABELS.index(text) + 1 not in months:
                months[MONTH_LABELS.index(text) + 1] = col
        if len(months) == 12 and "CHAVE" in texts:
            first_month = months[1]
            key_cols = [c for c, t in enumerate(texts, start=1) if t == "CHAVE" and c < first_month]
            key_col = max(key_cols)  # a CHAVE mais próxima dos meses (consolidador da aba Viagens)
            others = {}
            for col, raw in enumerate(row, start=1):
                text = norm(raw)
                if col < first_month and text and text not in others:
                    others[text] = col
            return idx, months, key_col, others, len(key_cols) > 1
    return None


def _find(others: dict[str, int], prefixes: tuple[str, ...], before: int) -> int | None:
    for text, col in others.items():
        if col < before and any(text.startswith(p) for p in prefixes):
            return col
    return None


def parse_package_sheet(sheet: Sheet, result: ParseResult) -> int:
    head = _header(sheet)
    if head is None:
        return 0
    header_row, months, key_col, others, consolidator = head
    first_month = months[1]
    desc_col = None if consolidator else _find(others, DESCRIPTION_HEADERS, first_month)
    supplier_col = None if consolidator else _find(others, SUPPLIER_HEADERS, first_month)
    note_col = None if consolidator else _find(others, NOTE_HEADERS, first_month)
    match = PACKAGE_SHEET.match(sheet.name)
    package_hint = match.group(2).strip() if match else sheet.name
    count = 0
    for row_no, row in sheet.iter_rows(header_row + 1):
        raw_key = clean_str(row[key_col - 1]) if key_col - 1 < len(row) else None
        m = KEY.match(raw_key or "")
        if not m:
            continue
        values: dict[int, str] = {}
        rec = Record("BUDGET_LINE", sheet.name, row_no, {})
        for month, col in months.items():
            raw = row[col - 1] if col - 1 < len(row) else None
            try:
                amount = to_decimal(raw)
            except ValueError:
                rec.error("INVALID_NUMBER", "Valor mensal inválido", MONTH_LABELS[month - 1], raw)
                continue
            if amount:
                values[month] = str(amount)
        if not values and not rec.issues:
            continue  # linha sem valores (template em branco)
        if any(Decimal(v) < 0 for v in values.values()):
            rec.error("NEGATIVE_VALUE", "Valores do orçamento não podem ser negativos")
        company, branch, cc, account = m.groups()
        description = clean_str(row[desc_col - 1]) if desc_col else None
        rec.data = {
            "company": company,
            "branch": branch.zfill(4) if branch and branch != "0" else None,
            "cost_center": cc,
            "account": account,
            "description": description or (f"{package_hint} (consolidado da planilha)" if consolidator else None),
            "supplier": clean_str(row[supplier_col - 1]) if supplier_col else None,
            "justification": clean_str(row[note_col - 1]) if note_col else None,
            "package_hint": package_hint,
            "values": values,
        }
        result.records.append(rec)
        count += 1
    return count


def parse_opex_template(sheets: list[Sheet], options: dict) -> ParseResult:
    result = ParseResult("OPEX_TEMPLATE", "TEMPLATE_OPEX")
    if not is_opex_template(sheets):
        return result
    master = parse_master(sheets, options)
    result.records.extend(master.records)

    realized = [s for s in sheets if norm(s.name).startswith("realizado")]
    actual = parse_wide(realized, options, "ACTUAL") if realized else ParseResult("ACTUAL", "WIDE_MONTHLY")
    result.records.extend(actual.records)
    result.structural.extend(i for i in actual.structural if i.code != "UNKNOWN_COLUMNS")

    per_sheet = {}
    for sheet in sheets:
        if PACKAGE_SHEET.match(sheet.name):
            per_sheet[sheet.name] = parse_package_sheet(sheet, result)
    result.meta = {
        "parts": {
            "master": len(master.records),
            "actual": len(actual.records),
            "budget_lines": sum(per_sheet.values()),
        },
        "budget_by_sheet": {k: v for k, v in per_sheet.items() if v},
        "actual_year": actual.meta.get("year"),
    }
    if not per_sheet or not any(per_sheet.values()):
        result.structural.append(
            Issue(
                "NO_BUDGET_VALUES",
                "Nenhum valor de orçamento 2027 encontrado nas abas de pacote (I a XII)",
                severity="WARNING",
            )
        )
    return result
