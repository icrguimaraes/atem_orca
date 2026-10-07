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

from app.domain.rules.common import MONTH_LABELS, month_from_label
from app.imports.base import (
    Issue,
    ParseResult,
    Record,
    Sheet,
    clean_code,
    clean_str,
    norm,
    system_export,
    to_decimal,
)
from app.imports.parsers.financial import parse_wide
from app.imports.parsers.master import parse_master

PACKAGE_SHEET = re.compile(r"^\s*([IVX]+)\s*-\s*(.+)$")
KEY = re.compile(r"^(\d{3,10})-(\d*)-([0-9A-Za-z]{3,20})-(\d{4,20})$")  # CC da REAM tem letras (RFM6003000)
DESCRIPTION_HEADERS = ("detalhamento", "objetivo da viagem", "objetivo do evento")
SUPPLIER_HEADERS = ("fornecedor",)
NOTE_HEADERS = ("justificativa", "observacoes", "finalidade")
CONTRACT_MANAGER_HEADERS = ("gestor do contrato",)
DETAIL_HEADERS = ("produto/servico", "produto / servico", "detalhamento da conta")
ASSUMPTION_HEADERS = ("premissa",)


def is_opex_template(sheets: list[Sheet]) -> bool:
    names = [s.name for s in sheets]
    has_bd = any(norm(n).startswith("bd") for n in names)
    packages = sum(1 for n in names if PACKAGE_SHEET.match(n))
    return has_bd and packages >= 3


def _header(sheet: Sheet) -> tuple[int, dict[int, int], int, dict[str, int], bool] | None:
    """Linha de cabeçalho com CHAVE e JAN..DEZ.

    Devolve (linha, {mês: coluna}, coluna da CHAVE, demais colunas, é_consolidador). Na aba de Viagens
    há duas colunas CHAVE; a usada é a mais próxima dos meses (o consolidador).

    Modelo sem CHAVE (template da REAM, layout antigo): vale o cabeçalho com JAN..DEZ, CENTRO DE CUSTO e CONTA
    CONTÁBIL; a coluna da CHAVE volta None e a linha é identificada pelas colunas de código."""
    fallback = None
    for idx, row in sheet.iter_rows(1):
        if idx > 80:
            break
        texts = [norm(v).upper() for v in row]
        months = {}
        for col, text in enumerate(texts, start=1):
            if text in MONTH_LABELS and MONTH_LABELS.index(text) + 1 not in months:
                months[MONTH_LABELS.index(text) + 1] = col
        if len(months) == 12 and "CHAVE" not in texts and fallback is None:
            before = {norm(v): col for col, v in enumerate(row, start=1) if col < months[1] and norm(v)}
            if "centro de custo" in before and "conta contabil" in before:
                fallback = (idx, months, None, before, False)
        if len(months) == 12 and "CHAVE" in texts:
            first_month = months[1]
            key_cols = [c for c, t in enumerate(texts, start=1) if t == "CHAVE" and c < first_month]
            if not key_cols:
                continue  # CHAVE só depois dos meses: não é a tabela do pacote
            key_col = max(key_cols)  # a CHAVE mais próxima dos meses (consolidador da aba Viagens)
            others = {}
            for col, raw in enumerate(row, start=1):
                text = norm(raw)
                if col < first_month and text and text not in others:
                    others[text] = col
            return idx, months, key_col, others, len(key_cols) > 1
    return fallback


def _find(others: dict[str, int], prefixes: tuple[str, ...], before: int) -> int | None:
    for text, col in others.items():
        if col < before and any(text.startswith(p) for p in prefixes) and not text.startswith("detalhamento da conta"):
            return col
    return None


def _find_exact(others: dict[str, int], prefixes: tuple[str, ...], before: int) -> int | None:
    for text, col in others.items():
        if col < before and any(text.startswith(p) for p in prefixes):
            return col
    return None


# Aba I - Viagens: cada linha é uma viagem; os valores (passagem, diária, hospedagem) caem no mês de ida
TRAVEL_ACCOUNTS = {"ticket": "6010301011", "per_diem": "6010301036", "lodging": "6010301001"}
TRAVEL_COLUMNS = {
    "branch_name": ("filial",),
    "branch": ("divisao",),
    "cost_center_name": ("denominacao do centro",),
    "cost_center": ("centro de custo",),
    "purpose": ("objetivo da viagem",),
    "job_level": ("cargo",),
    "departure_month": ("ida",),
    "return_month": ("volta",),
    "days": ("periodo",),
    "origin": ("origem",),
    "destination": ("destino",),
    "trip_type": ("tipo",),
    "ticket": ("despesas com passagens",),
    "per_diem": ("diaria de viagem",),
    "lodging": ("hospedagem",),
}


def _travel_header(sheet: Sheet) -> tuple[int, dict[str, int]] | None:
    for idx, row in sheet.iter_rows(1):
        if idx > 80:
            return None
        texts = [norm(v) for v in row]
        if "objetivo da viagem" not in texts:
            continue
        cols: dict[str, int] = {}
        for col, text in enumerate(texts, start=1):
            for key, prefixes in TRAVEL_COLUMNS.items():
                if key not in cols and any(text == p or text.startswith(p + " ") for p in prefixes):
                    cols[key] = col
                    break
        if {"purpose", "departure_month", "lodging"} <= set(cols):
            cols["_keys"] = [c for c, t in enumerate(texts, start=1) if t == "chave"]  # type: ignore[assignment]
            return idx, cols
    return None


def parse_travel_rows(sheet: Sheet, result: ParseResult) -> int:
    """Viagens linha a linha (objetivo, cargo, ida/volta, dias, origem/destino e os três valores)."""
    head = _travel_header(sheet)
    if head is None:
        return 0
    header_row, cols = head

    def get(row, key):
        col = cols.get(key)
        return row[col - 1] if col and col - 1 < len(row) else None

    count = 0
    for row_no, row in sheet.iter_rows(header_row + 1):
        purpose = clean_str(get(row, "purpose"))
        job = clean_str(get(row, "job_level"))
        dep_raw = get(row, "departure_month")
        if not (purpose or job or dep_raw):
            continue
        rec = Record("TRAVEL", sheet.name, row_no, {})
        amounts: dict[str, str] = {}
        cached = False
        for key, account in TRAVEL_ACCOUNTS.items():
            raw = get(row, key)
            try:
                value = to_decimal(raw)
            except ValueError:
                value = None  # "-" e textos de erro do Excel contam como zero
            if value is not None:
                cached = True
            if value:
                amounts[account] = str(value)
        departure = month_from_label(dep_raw)
        if departure is None:
            rec.error("INVALID_PERIOD", "Mês de ida inválido ou vazio", "IDA (mês)", dep_raw)
        ret_raw = get(row, "return_month")
        days = to_decimal_safe(get(row, "days"))
        cc = clean_code(get(row, "cost_center"))
        branch = clean_code(get(row, "branch"), 4)
        company = "1001"
        for key_col in cols.get("_keys") or []:  # empresa vem da CHAVE (empresa-filial-cc-conta), se resolvida
            m = KEY.match(clean_str(row[key_col - 1]) or "") if key_col - 1 < len(row) else None
            if m:
                company = m.group(1)
                break
        rec.data = {
            "company": company,
            "branch": branch if branch and branch.strip("0") else None,
            "branch_name": clean_str(get(row, "branch_name")),
            "cost_center": cc if cc and cc.strip("0") else None,
            "cost_center_name": clean_str(get(row, "cost_center_name")),
            "travel": {
                "purpose": purpose,
                "job_level": job,
                "trip_type": clean_str(get(row, "trip_type")),
                "origin": clean_str(get(row, "origin")),
                "destination": clean_str(get(row, "destination")),
                "departure_month": departure,
                "return_month": month_from_label(ret_raw),
                "days": int(days) if days else 0,
            },
            "amounts": amounts if cached else None,  # None = recalcular pelas tarifas do ciclo
            "values": {departure: str(sum(Decimal(v) for v in amounts.values()))} if departure and amounts else {},
            "package_hint": "Viagens",
        }
        if any(Decimal(v) < 0 for v in amounts.values()):
            rec.error("NEGATIVE_VALUE", "Valores da viagem não podem ser negativos")
        result.records.append(rec)
        count += 1
    return count


def to_decimal_safe(value):
    try:
        return to_decimal(value)
    except ValueError:
        return None


LINE_COLUMNS = {
    "branch_name": ("filial",),
    "branch": ("divisao",),
    "cost_center_name": ("denominacao do centro",),
    "cost_center": ("centro de custo",),
    "account_name": ("descricao da conta",),
    "account": ("conta contabil",),
}


def _line_columns(sheet: Sheet, header_row: int, before: int) -> dict[str, int]:
    cols: dict[str, int] = {}
    for col, raw in enumerate(sheet.rows[header_row - 1], start=1):
        text = norm(raw)
        if col >= before:
            break
        for key, prefixes in LINE_COLUMNS.items():
            if key not in cols and any(text == p or text.startswith(p + " ") for p in prefixes):
                cols[key] = col
                break
    return cols


def _code(value, width: int | None = None) -> str | None:
    """Código vindo de fórmula: 0, vazio ou erro do Excel = não resolvido."""
    code = clean_code(value, width)
    return code if code and code.isdigit() and code.strip("0") else None


def _sap_code(value) -> str | None:
    """Código SAP com letras (CC da REAM, "RFM6003000"): só letras e números, com pelo menos um dígito."""
    code = clean_code(value)
    return code if code and code.isalnum() and any(c.isdigit() for c in code) and code.strip("0") else None


def parse_package_sheet(sheet: Sheet, result: ParseResult) -> int:
    """Lê a aba linha a linha. A CHAVE da linha é a fonte preferida; se a fórmula não resolveu
    (CC ou conta não encontrados no BD), usa as colunas de código e, por fim, os nomes. Linha com valor
    nunca é descartada em silêncio: sem CC ou conta identificáveis vira erro na prévia."""
    head = _header(sheet)
    if head is None:
        return parse_travel_rows(sheet, result)
    header_row, months, key_col, others, consolidator = head
    if consolidator:
        return parse_travel_rows(sheet, result)  # Viagens: o consolidador só confere os totais
    if key_col is None:
        result.meta.setdefault("keyless_sheets", []).append(sheet.name)
    first_month = months[1]
    cols = _line_columns(sheet, header_row, first_month)
    desc_col = _find(others, DESCRIPTION_HEADERS, first_month)
    supplier_col = _find(others, SUPPLIER_HEADERS, first_month)
    note_col = _find(others, NOTE_HEADERS, first_month)
    manager_col = _find(others, CONTRACT_MANAGER_HEADERS, first_month)
    detail_col = _find_exact(others, DETAIL_HEADERS, first_month)
    assumption_col = _find(others, ASSUMPTION_HEADERS, first_month)
    match = PACKAGE_SHEET.match(sheet.name)
    package_hint = match.group(2).strip() if match else sheet.name

    def get(row, col):
        return row[col - 1] if col and col - 1 < len(row) else None

    count = 0
    for row_no, row in sheet.iter_rows(header_row + 1):
        rec = Record("BUDGET_LINE", sheet.name, row_no, {})
        values: dict[int, str] = {}
        for month, col in months.items():
            raw = get(row, col)
            try:
                amount = to_decimal(raw)
            except ValueError:
                if isinstance(raw, str) and raw.startswith("#"):
                    continue  # erro de fórmula do Excel em linha vazia
                rec.error("INVALID_NUMBER", "Valor mensal inválido", MONTH_LABELS[month - 1], raw)
                continue
            if amount:
                values[month] = str(amount)
        if not values and not rec.issues:
            continue  # linha sem valores (modelo em branco)
        if any(Decimal(v) < 0 for v in values.values()):
            rec.error("NEGATIVE_VALUE", "Valores do orçamento não podem ser negativos")
        m = KEY.match(clean_str(get(row, key_col)) or "")
        # sem CHAVE no modelo, a empresa não vem na linha: o resolver identifica (CC, filial ou divisão)
        company, branch, cc, account = m.groups() if m else ("1001" if key_col else None, None, None, None)
        branch = _code(branch, 4) or _code(get(row, cols.get("branch")), 4)
        cc = _code(cc) or _code(get(row, cols.get("cost_center"))) or _sap_code(cc)
        if cc is None and key_col is None:
            cc = _sap_code(get(row, cols.get("cost_center")))  # CC da REAM é alfanumérico (RFM6003000)
        account = _code(account) or _code(get(row, cols.get("account")))
        rec.data = {
            "company": company,
            "branch": branch,
            "branch_name": clean_str(get(row, cols.get("branch_name"))),
            "cost_center": cc,
            "cost_center_name": clean_str(get(row, cols.get("cost_center_name"))),
            "account": account,
            "account_name": clean_str(get(row, cols.get("account_name"))),
            "description": clean_str(get(row, desc_col)) if desc_col else None,
            "supplier": clean_str(get(row, supplier_col)) if supplier_col else None,
            "justification": clean_str(get(row, note_col)) if note_col else None,
            "contract_manager": clean_str(get(row, manager_col)) if manager_col else None,
            "account_detail": clean_str(get(row, detail_col)) if detail_col else None,
            "assumption": clean_str(get(row, assumption_col)) if assumption_col else None,
            "package_hint": package_hint,
            "values": values,
        }
        result.records.append(rec)
        count += 1
    return count


def _master_from_lines(records: list[Record]) -> list[Record]:
    """Cadastro mínimo (filial e CC) tirado das linhas do modelo sem CHAVE; a empresa é definida no resolver."""
    out: dict[tuple[str, str], Record] = {}
    for rec in records:
        d = rec.data
        if rec.record_type != "BUDGET_LINE" or d.get("company"):
            continue
        if d.get("cost_center") and ("COST_CENTER", d["cost_center"]) not in out:
            out[("COST_CENTER", d["cost_center"])] = Record(
                "COST_CENTER",
                rec.sheet,
                rec.row_number,
                {
                    "code": d["cost_center"],
                    "name": d.get("cost_center_name") or d["cost_center"],
                    "manager": None,
                    "manager_email": None,
                    "company": None,
                    "department": None,
                    "area": None,
                    "from_lines": True,
                },
            )
        if d.get("branch") and ("BRANCH", d["branch"]) not in out:
            out[("BRANCH", d["branch"])] = Record(
                "BRANCH",
                rec.sheet,
                rec.row_number,
                {
                    "code": d["branch"],
                    "name": d.get("branch_name") or d["branch"],
                    "company": None,
                    "uf": None,
                    "from_lines": True,
                },
            )
    return list(out.values())


def check_travel_consolidator(sheet: Sheet, result: ParseResult) -> None:
    """Confere o consolidador da aba Viagens (CHAVE × JAN..DEZ) contra a soma das viagens lidas."""
    head = _header(sheet)
    if head is None or not head[4]:
        return
    header_row, months, key_col, _, _ = head
    expected: dict[str, Decimal] = {}
    for _, row in sheet.iter_rows(header_row + 1):
        raw_key = clean_str(row[key_col - 1]) if key_col - 1 < len(row) else None
        m = KEY.match(raw_key or "")
        if not m or not _code(m.group(3)):
            continue
        total = Decimal(0)
        for col in months.values():
            total += to_decimal_safe(row[col - 1] if col - 1 < len(row) else None) or Decimal(0)
        if total:
            expected[m.group(4)] = expected.get(m.group(4), Decimal(0)) + total
    found: dict[str, Decimal] = {}
    for rec in result.records:
        if rec.record_type == "TRAVEL" and rec.sheet == sheet.name and rec.data.get("amounts"):
            for account, value in rec.data["amounts"].items():
                found[account] = found.get(account, Decimal(0)) + Decimal(value)
    diffs = [
        f"conta {acc}: linhas {found.get(acc, 0):.2f} × consolidador {expected.get(acc, 0):.2f}"
        for acc in sorted(set(expected) | set(found))
        if abs(found.get(acc, Decimal(0)) - expected.get(acc, Decimal(0))) > Decimal("0.01")
    ]
    result.meta.setdefault("consolidator_check", {})[sheet.name] = {
        "ok": not diffs,
        "total_lines": str(sum(found.values(), Decimal(0))),
        "total_consolidator": str(sum(expected.values(), Decimal(0))),
    }
    if diffs:
        result.structural.append(
            Issue(
                "CONSOLIDATOR_MISMATCH",
                f"{sheet.name}: soma das viagens difere do consolidador ({'; '.join(diffs[:4])})",
                severity="WARNING",
            )
        )


def parse_opex_template(sheets: list[Sheet], options: dict) -> ParseResult:
    result = ParseResult("OPEX_TEMPLATE", "TEMPLATE_OPEX")
    if not is_opex_template(sheets):
        return result
    realized = [s for s in sheets if norm(s.name).startswith("realizado")]
    actual = parse_wide(realized, options, "ACTUAL") if realized else ParseResult("ACTUAL", "WIDE_MONTHLY")
    result.records.extend(actual.records)
    result.structural.extend(i for i in actual.structural if i.code != "UNKNOWN_COLUMNS")

    per_sheet = {}
    for sheet in sheets:
        if PACKAGE_SHEET.match(sheet.name):
            per_sheet[sheet.name] = parse_package_sheet(sheet, result)
    for sheet in sheets:
        if PACKAGE_SHEET.match(sheet.name):
            check_travel_consolidator(sheet, result)
    if result.meta.get("keyless_sheets"):
        # modelo sem CHAVE (REAM): a aba BD é outra (várias tabelas empilhadas) e não serve de cadastro; filial e
        # CC vêm das próprias linhas e são criados se faltarem
        master = ParseResult("MASTER_DATA", "TEMPLATE_BD")
        master.records = _master_from_lines(result.records)
        result.structural.append(
            Issue(
                "LEGACY_LAYOUT",
                "Template sem a coluna CHAVE (modelo da REAM ou versão antiga): empresa, filial e centro de custo "
                "lidos das colunas de cada linha; aba BD não usada como cadastro",
                severity="WARNING",
            )
        )
    else:
        master = parse_master(sheets, options)
    result.records[:0] = master.records
    result.meta = result.meta | {
        "parts": {
            "master": len(master.records),
            "actual": len(actual.records),
            "budget_lines": sum(per_sheet.values()),
        },
        "budget_by_sheet": {k: v for k, v in per_sheet.items() if v},
        "actual_year": actual.meta.get("year"),
        "system_export": system_export(sheets),
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
