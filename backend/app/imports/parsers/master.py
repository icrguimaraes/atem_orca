"""Cadastros: filiais, centros de custo e contas/pacotes.

Lê tanto a aba oculta `BD-Novo` dos templates (tabelas Filiais, Centro_de_Custos, Pacotes
lado a lado) quanto planilhas planas exportadas do SAP com os mesmos cabeçalhos.
"""

from app.imports.base import ParseResult, Record, Sheet, clean_code, clean_str, find_table, is_blank

BRANCH_ALIASES = {
    "code": ("Local de negócios", "Codigo Filial", "Cod Filial", "Divisão"),
    "name": ("Filial", "Nome Filial"),
    "company": ("Empresa", "Código Empresa"),
    "uf": ("UF",),
}
COST_CENTER_ALIASES = {
    "code": ("Centro de Custo", "Centro de Custos", "CC"),
    "name": (
        "Denominação de centro de custos",
        "Denominação do Centro de Custos",
        "Nome Centro de Custo",
        "Descrição Centro de Custo",
    ),
    "manager": ("Gestor do Centro de Custo", "Gestor do CC", "Gestor"),
    "manager_email": ("E-mail Gestor", "Email Gestor"),
    "company": ("Empresa", "Código Empresa"),
    "department": ("Departamento",),
    "area": ("Área", "Area"),
}
ACCOUNT_ALIASES = {
    "code": ("Conta do Razão", "Conta Razão", "Conta Contábil", "Conta"),
    "name": ("Descrição", "Denominação da Conta do Razão", "Descrição da Conta", "Descrição da Conta Contábil"),
    "dre_group": ("Agrupamento DRE",),
    "package": ("Pacote GMD", "Pacotes Orçamento", "Pacote"),
    "detail": ("Detalhamento",),
    "nature": ("Natureza",),
}


def parse_master(sheets: list[Sheet], options: dict) -> ParseResult:
    result = ParseResult("MASTER_DATA", "TEMPLATE_BD")
    ordered = sorted(sheets, key=lambda s: 0 if s.name.lower().startswith("bd") else 1)
    found = set()
    for sheet in ordered:
        branch_t = find_table(sheet, BRANCH_ALIASES, ("code", "name"))
        cc_t = find_table(sheet, COST_CENTER_ALIASES, ("code", "name"))
        acc_t = find_table(sheet, ACCOUNT_ALIASES, ("code", "name"))
        if "branch" not in found and branch_t:
            found.add("branch")
            for row_no, row in branch_t.rows():
                code = clean_code(branch_t.value(row, "code"), 4)
                if code is None:
                    continue
                rec = Record(
                    "BRANCH",
                    sheet.name,
                    row_no,
                    {
                        "code": code,
                        "name": clean_str(branch_t.value(row, "name")),
                        "company": clean_code(branch_t.value(row, "company")),
                        "uf": clean_str(branch_t.value(row, "uf")),
                    },
                )
                if not rec.data["name"]:
                    rec.error("REQUIRED", "Nome da filial obrigatório", "Filial")
                result.records.append(rec)
        if "cc" not in found and cc_t:
            found.add("cc")
            for row_no, row in cc_t.rows():
                code = clean_code(cc_t.value(row, "code"))
                if code is None:
                    continue
                rec = Record(
                    "COST_CENTER",
                    sheet.name,
                    row_no,
                    {
                        "code": code,
                        "name": clean_str(cc_t.value(row, "name")),
                        "manager": clean_str(cc_t.value(row, "manager")),
                        "manager_email": clean_str(cc_t.value(row, "manager_email")),
                        "company": clean_code(cc_t.value(row, "company")),
                        "department": clean_str(cc_t.value(row, "department")),
                        "area": clean_str(cc_t.value(row, "area")),
                    },
                )
                if not code.isdigit():
                    rec.error("INVALID_CODE", "Código de centro de custo deve ser numérico", "Centro de Custo", code)
                if not rec.data["name"]:
                    rec.error("REQUIRED", "Denominação do centro de custo obrigatória", "Denominação")
                result.records.append(rec)
        if "account" not in found and acc_t:
            found.add("account")
            for row_no, row in acc_t.rows():
                if is_blank(row):
                    continue
                code = clean_code(acc_t.value(row, "code"))
                if code is None:
                    continue
                detail = clean_str(acc_t.value(row, "detail"))
                rec = Record(
                    "ACCOUNT",
                    sheet.name,
                    row_no,
                    {
                        "code": code,
                        "name": clean_str(acc_t.value(row, "name")),
                        "dre_group": clean_str(acc_t.value(row, "dre_group")),
                        "package": clean_str(acc_t.value(row, "package")),
                        "detail": None if detail in (None, "0") else detail,
                        "nature": clean_str(acc_t.value(row, "nature")),
                    },
                )
                if not code.isdigit():
                    rec.error("INVALID_CODE", "Conta contábil deve ser numérica", "Conta do Razão", code)
                if not rec.data["name"]:
                    rec.error("REQUIRED", "Descrição da conta obrigatória", "Descrição")
                result.records.append(rec)
    if not found:
        return result
    result.meta["tables"] = sorted(found)
    return result
