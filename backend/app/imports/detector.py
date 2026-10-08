from collections.abc import Callable

from app.imports.base import ParseResult, Sheet, StructureError, norm
from app.imports.parsers.capex_template import is_capex_template, parse_capex_template
from app.imports.parsers.financial import parse_capex_acum, parse_ksb1, parse_projection, parse_wide
from app.imports.parsers.master import parse_master
from app.imports.parsers.opex_template import is_opex_template, parse_opex_template
from app.imports.parsers.people import parse_employees, parse_macro

Parser = Callable[[list[Sheet], dict], ParseResult]

PARSERS: dict[str, list[Parser]] = {
    "EMPLOYEES": [parse_employees],
    "MACRO_ASSUMPTIONS": [parse_macro],
    "ACTUAL": [
        parse_ksb1,
        lambda s, o: parse_capex_acum(s, o, "ACTUAL"),
        lambda s, o: parse_wide(s, o, "ACTUAL"),
    ],
    "REFERENCE_BUDGET": [lambda s, o: parse_wide(s, o, "REFERENCE_BUDGET")],
    "PROJECTION": [parse_projection, lambda s, o: parse_capex_acum(s, o, "PROJECTION")],
    "OPEX_TEMPLATE": [parse_opex_template],
    "CAPEX_TEMPLATE": [parse_capex_template],
    "MASTER_DATA": [parse_master],
    "COST_CENTERS": [parse_master],
    "ACCOUNTS": [parse_master],
}

# Ordem de tentativa quando o usuário não informa o tipo (mais específico primeiro)
AUTO_ORDER = ("EMPLOYEES", "MACRO_ASSUMPTIONS", "OPEX_TEMPLATE", "CAPEX_TEMPLATE", "ACTUAL", "MASTER_DATA")


def _sheet_hint(sheets: list[Sheet]) -> str | None:
    if is_opex_template(sheets):
        return "OPEX_TEMPLATE"  # template OPEX: cadastros + realizado + orçamento num só arquivo
    if is_capex_template(sheets):
        return "CAPEX_TEMPLATE"  # template CAPEX: cadastros + catálogo de ativos + solicitações
    names = {norm(s.name) for s in sheets}
    if "quadro funcionarios" in names:
        return "EMPLOYEES"
    if "premissas macroeconomicas" in names:
        return "MACRO_ASSUMPTIONS"
    if any(n.startswith("realizado") for n in names) and not any(n.startswith("bd") for n in names):
        return "ACTUAL"
    if any(n.startswith("bd") for n in names):
        return "MASTER_DATA"
    return None


def detect_and_parse(sheets: list[Sheet], dataset_type: str | None, options: dict) -> ParseResult:
    """Identifica o tipo/layout do arquivo e devolve os registros lidos.

    Com `dataset_type` informado, apenas os parsers daquele tipo são tentados; caso
    contrário usa-se o nome das abas como pista e, por fim, tentativa em ordem.
    """
    candidates = [dataset_type] if dataset_type else []
    if not candidates:
        hint = _sheet_hint(sheets)
        candidates = ([hint] if hint else []) + [t for t in AUTO_ORDER if t != hint]
    last: ParseResult | None = None
    for candidate in candidates:
        if candidate not in PARSERS:
            raise StructureError(f"Tipo de dados não suportado: {candidate}")
        for parser in PARSERS[candidate]:
            result = parser(sheets, options)
            if result.records or result.structural:
                result.dataset_type = candidate if dataset_type else result.dataset_type
                return result
            last = result
    expected = dataset_type or "qualquer layout conhecido"
    raise StructureError(
        f"Estrutura não reconhecida para {expected}: cabeçalhos obrigatórios não encontrados"
        + ("" if last is None else f" (último layout testado: {last.layout})")
    )
