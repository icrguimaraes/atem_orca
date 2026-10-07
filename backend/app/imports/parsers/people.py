"""Quadro de funcionários (template Pessoal) e premissas macroeconômicas."""

import re

from app.domain.rules.common import month_from_label
from app.domain.rules.personnel import ACTION_NOUNS, MONTH_PENDING_ACTIONS, TEMPLATE_ACTION_ALIASES, TEMPLATE_ACTIONS
from app.imports.base import (
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

EMPLOYEE_ALIASES = {
    "registration": ("Matrícula", "Matricula", "ID"),
    "name": ("Nome", "Nome do Colaborador"),
    "position": ("Cargo Atual", "Cargo"),
    "company": ("Empresa",),
    "branch": ("Divisão", "Filial", "Local de negócios"),
    "cost_center": ("Centro de Custo", "Centro de Custos"),
    "salary": ("Salário Mensal R$", "Salário Mensal", "Salário", "Salário Base"),
    "contract": ("Tipo de Contrato", "Tipo Contrato", "Regime"),
    "department": ("Departamento",),
    "area": ("Área",),
    "admission": ("Data de Admissão", "Admissão"),
    "termination": ("Data Prevista de Desligamento", "Data de Desligamento"),
    "action": ("Ação",),
    "action_month": ("Mês da Ação",),
    "new_position": ("Novo Cargo",),
    "new_salary": ("Novo Salário", "Novo Salario"),
    # benefícios (dados ingressados pelo líder)
    "b_daycare": ("Auxilio Creche",),
    "b_dependents": ("Total Dependentes",),
    "b_transport": ("Vale Transporte",),
    "b_college": ("Auxilio Faculdade",),
    "b_night": ("AdicionalNoturno", "Adicional Noturno"),
    "b_hazard": ("Adicional 30%", "Adicional 30"),
    "b_fuel": ("Auxilio Combustivel",),
    "b_pharmacy": ("Auxilio Farmácia",),
    "b_act": ("Abono ACT",),
    "b_parking": ("Estacionamento",),
}

BENEFIT_CODES = {
    "b_daycare": "AUX_CRECHE",
    "b_dependents": "DEPENDENTES",
    "b_transport": "VALE_TRANSPORTE",
    "b_college": "AUX_FACULDADE",
    "b_night": "ADIC_NOTURNO",
    "b_hazard": "ADIC_30",
    "b_fuel": "AUX_COMBUSTIVEL",
    "b_pharmacy": "AUX_FARMACIA",
    "b_act": "ABONO_ACT",
    "b_parking": "ESTACIONAMENTO",
}


def parse_employees(sheets: list[Sheet], options: dict) -> ParseResult:
    result = ParseResult("EMPLOYEES", "TEMPLATE_QUADRO")
    for sheet in sheets:
        table = find_table(sheet, EMPLOYEE_ALIASES, ("name", "salary"))
        if table is None:
            continue
        result.meta["sheet"] = sheet.name
        for row_no, row in table.rows():
            if is_blank(row):
                continue
            name = clean_str(table.value(row, "name"))
            raw_salary = table.value(row, "salary")
            if name is None and raw_salary in (None, ""):
                continue
            rec = Record("EMPLOYEE", sheet.name, row_no, {})
            registration = clean_code(table.value(row, "registration"))
            try:
                salary = to_decimal(raw_salary)
            except ValueError:
                rec.error("INVALID_NUMBER", "Salário inválido", "Salário", raw_salary)
                salary = None
            action_raw = norm(table.value(row, "action")).upper() or None
            action = TEMPLATE_ACTIONS.get(action_raw) if action_raw else None
            if action_raw and action is None and action_raw in TEMPLATE_ACTION_ALIASES:
                action = TEMPLATE_ACTION_ALIASES[action_raw]
                rec.warn(
                    "ACTION_ALIAS",
                    f"Ação '{clean_str(table.value(row, 'action'))}' fora da lista (MANTER, PROMOVER, INCLUIR, "
                    f"REMOVER): lida como {ACTION_NOUNS[action]}",
                    "Ação",
                    action_raw,
                )
            elif action_raw and action is None:
                rec.error("INVALID_DOMAIN", "Ação deve ser MANTER, PROMOVER, INCLUIR ou REMOVER", "Ação", action_raw)
            action_month = month_from_label(table.value(row, "action_month"))
            try:
                new_salary = to_decimal(table.value(row, "new_salary"))
            except ValueError:
                rec.error("INVALID_NUMBER", "Novo salário inválido", "Novo Salário", table.value(row, "new_salary"))
                new_salary = None
            dates = {}
            for key, label in (("admission", "Data de admissão"), ("termination", "Data de desligamento")):
                try:
                    value = to_date(table.value(row, key))
                    dates[key] = value.isoformat() if value else None
                except ValueError:
                    rec.error("INVALID_DATE", f"{label} inválida", label, table.value(row, key))
                    dates[key] = None
            benefits = {}
            for key, code in BENEFIT_CODES.items():
                value = table.value(row, key)
                if value in (None, ""):
                    continue
                text = norm(value)
                if text in ("sim", "s"):
                    benefits[code] = 1
                elif text in ("nao", "n"):
                    continue
                else:
                    try:
                        number = to_decimal(value)
                        if number:
                            benefits[code] = str(number)
                    except ValueError:
                        rec.warn("INVALID_DOMAIN", "Benefício deve ser SIM/NÃO ou número", key, value)
            rec.data = {
                "registration": registration,
                "name": name,
                "position": clean_str(table.value(row, "position")),
                "company": clean_code(table.value(row, "company")),
                "branch": clean_code(table.value(row, "branch")),
                "cost_center": clean_code(table.value(row, "cost_center")),
                "salary": None if salary is None else str(salary),
                "contract": (clean_str(table.value(row, "contract")) or "CLT").upper(),
                "department": clean_str(table.value(row, "department")),
                "area": clean_str(table.value(row, "area")),
                "action": action,
                "action_month": action_month,
                "new_position": clean_str(table.value(row, "new_position")),
                "new_salary": None if new_salary is None else str(new_salary),
                "benefits": benefits,
                **dates,
            }
            if name is None:
                rec.error("REQUIRED", "Nome obrigatório", "Nome")
            # vaga: sem matrícula e marcada como vaga (no nome) ou com INCLUIR
            is_vacancy = registration is None and (
                bool(re.search(r"\bVAGA\b", (name or "").upper())) or action == "HIRE"
            )
            if salary is None and not rec.issues and is_vacancy and new_salary is None:
                rec.warn(
                    "VACANCY_NO_SALARY",
                    "Vaga sem salário: entra pendente, sem custo, até informar o salário em Apontamentos",
                    "Salário",
                )
            elif salary is None and not rec.issues and not is_vacancy:
                rec.error("REQUIRED", "Salário obrigatório", "Salário")
            elif salary is not None and salary < 0:
                rec.error("INVALID_NUMBER", "Salário negativo", "Salário", raw_salary)
            if action in MONTH_PENDING_ACTIONS and action_month is None and registration is not None:
                noun = ACTION_NOUNS[action]
                rec.warn(
                    "ACTION_NO_MONTH",
                    f"{noun[0].upper() + noun[1:]} sem mês: entra pendente, sem efeito no custo, até informar o mês "
                    "em Apontamentos",
                    "Mês da Ação",
                )
            elif action is not None and action != "KEEP" and action_month is None:
                rec.error("REQUIRED", "Mês da ação obrigatório para a ação informada", "Mês da Ação")
            if action == "PROMOTION" and new_salary is None:
                rec.warn(
                    "PROMOTION_NO_SALARY",
                    "Promoção sem novo salário: entra pendente, sem aumento, até informar o valor em Apontamentos",
                    "Novo Salário",
                )
            elif action == "SALARY_ADJUSTMENT" and new_salary is None:
                rec.warn(
                    "ADJUSTMENT_NO_SALARY",
                    "Reajuste individual sem novo salário: entra pendente, sem aumento, até informar o valor em "
                    "Apontamentos",
                    "Novo Salário",
                )
            if registration is None:
                if is_vacancy:
                    rec.record_type = "VACANCY"
                    rec.warn("VACANCY", "Vaga sem matrícula: será tratada como admissão planejada (módulo Pessoal)")
                else:
                    rec.error("REQUIRED", "Matrícula obrigatória", "Matrícula")
            rec.natural_key = f"{rec.data['company']}|{registration}" if registration else None
            result.records.append(rec)
        return result
    return result


MACRO_ALIASES = {
    "indicator": ("Indicador",),
    "source": ("Fonte e Informações Adicionais", "Fonte"),
    "reference_date": ("Atualização", "Data de Atualização"),
}


def parse_macro(sheets: list[Sheet], options: dict) -> ParseResult:
    """Aba PREMISSAS MACROECONOMICAS: indicador × fonte × ano. Linhas sem data, com nome curto ou em
    maiúsculas (Diesel, Gasolina, ATEM, COF...) logo abaixo de um indicador com unidade são segmentos."""
    result = ParseResult("MACRO_ASSUMPTIONS", "TEMPLATE_PREMISSAS")
    for sheet in sheets:
        category = "MACRO"
        header_cols: dict[int, int] = {}
        table = None
        parent = None
        for row_no, row in sheet.iter_rows(1):
            texts = [norm(v) for v in row]
            if "indicador" in texts:  # nova seção (macro ou premissas de negócio)
                table = find_table(Sheet(sheet.name, [row]), MACRO_ALIASES, ("indicator",))
                header_cols = {}
                for idx, cell in enumerate(row, start=1):
                    text = str(cell).strip() if cell is not None else ""
                    if text.replace(".0", "").isdigit() and 1990 < int(float(text)) < 2100:
                        header_cols[int(float(text))] = idx
                prev = sheet.rows[row_no - 2] if row_no > 1 else ()
                category = "NEGOCIO" if any("negocio" in norm(v) for v in prev) else category
                parent = None
                continue
            if table is None or is_blank(row):
                continue
            indicator = clean_str(table.value(row, "indicator"))
            if indicator is None:
                continue
            source = clean_str(table.value(row, "source"))
            raw_date = table.value(row, "reference_date")
            # segmentos (Diesel, ATEM, COF...) ficam sob indicadores agregadores com unidade: "Volume (k M³)"
            is_segment = (
                parent is not None
                and "(" in parent
                and raw_date is None
                and (len(indicator) <= 12 or indicator.isupper())
            )
            if not is_segment:
                parent = indicator
            for year, col in header_cols.items():
                value = row[col - 1] if col - 1 < len(row) else None
                if value in (None, ""):
                    continue
                rec = Record("MACRO", sheet.name, row_no, {})
                try:
                    number = to_decimal(value)
                except ValueError:
                    rec.error("INVALID_NUMBER", "Valor inválido", str(year), value)
                    number = None
                try:
                    ref = to_date(raw_date)
                except ValueError:
                    ref = None
                rec.data = {
                    "category": category,
                    "indicator": parent if is_segment else indicator,
                    "segment": indicator if is_segment else None,
                    "source": source,
                    "year": year,
                    "value": None if number is None else str(number),
                    "reference_date": ref.isoformat() if ref else None,
                }
                rec.natural_key = f"{rec.data['indicator']}|{rec.data['segment']}|{source}|{year}"
                result.records.append(rec)
        if result.records:
            return result
    return result
