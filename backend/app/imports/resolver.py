"""Validação dos registros lidos contra os cadastros (empresa, filial, CC, conta, contrato)."""

import re
from collections import Counter
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules import capex as capex_rules
from app.domain.rules.opex import TRAVEL_TICKET_ACCOUNT, missing_fare_warning
from app.domain.rules.personnel import sector_for_position
from app.imports.base import ParseResult, Record
from app.models import (
    Account,
    Area,
    AssetClass,
    Branch,
    BudgetPackage,
    Company,
    ContractType,
    CostCenter,
    LookupValue,
)


class Dimensions:
    def __init__(self, db: Session) -> None:
        self.companies = {c.code: c.id for c in db.scalars(select(Company))}
        # empresa escrita por nome ("ATEM", "REAM", "NAVE") em vez do código
        self.company_names = {
            (n or "").strip().upper(): c.code for c in db.scalars(select(Company)) for n in (c.name, c.short_name) if n
        }
        self.branches: dict[tuple[int, str], int] = {}
        self.branch_objs: dict[tuple[int, str], Branch] = {}
        self.branch_names: dict[tuple[int, str], int] = {}
        for b in db.scalars(select(Branch)):
            self.branches[(b.company_id, b.code)] = b.id
            self.branch_objs[(b.company_id, b.code)] = b
            self.branch_names[(b.company_id, b.name.upper())] = b.id
        self.cost_centers = {(c.company_id, c.code): c for c in db.scalars(select(CostCenter))}
        self.accounts = {a.code: a for a in db.scalars(select(Account))}
        self.packages = {p.name.upper(): p.id for p in db.scalars(select(BudgetPackage))}
        self.package_names = {p.id: p.name.upper() for p in db.scalars(select(BudgetPackage))}
        self.contracts = {c.code for c in db.scalars(select(ContractType))}
        self.sectors = {a.id: a.name for a in db.scalars(select(Area))}  # setores (página Áreas e setores)
        self.capex_types = {
            lv.code.upper(): lv.code
            for lv in db.scalars(select(LookupValue).where(LookupValue.domain == "CAPEX_PROJECT_TYPE"))
        }
        self.asset_classes = {c.name.upper() for c in db.scalars(select(AssetClass))}

    def branch_id(self, company_id: int, value: str | None) -> int | None:
        if not value:
            return None
        if value.isdigit():
            return self.branches.get((company_id, value.zfill(4)))
        plant = re.fullmatch(r"[A-Za-z](\d{3,4})", value.strip())
        if plant:  # planta SAP da exportação KSB1 ("C001") = filial com o mesmo número ("0001")
            return self.branches.get((company_id, plant.group(1).zfill(4)))
        return self.branch_names.get((company_id, value.upper()))


def _company(rec: Record, dims: Dimensions, default_code: str | None) -> int | None:
    code = rec.data.get("company") or default_code
    if not code:
        rec.error("REQUIRED", "Empresa não informada (coluna Empresa ou opção company_code)", "Empresa")
        return None
    by_name = getattr(dims, "company_names", {}).get(str(code).strip().upper())
    if code not in dims.companies and by_name:
        rec.warn("COMPANY_BY_NAME", f"Empresa '{code}' lida como {by_name}", "Empresa", code)
        code = by_name
    rec.data["company"] = code
    company_id = dims.companies.get(code)
    if company_id is None:
        rec.error("UNKNOWN_COMPANY", f"Empresa {code} não cadastrada", "Empresa", code)
    return company_id


def _mark_duplicates(records: list[Record]) -> None:
    seen: Counter[str] = Counter()
    for rec in records:
        if rec.natural_key is None or rec.status == "ERROR":
            continue
        seen[rec.natural_key] += 1
        if seen[rec.natural_key] > 1:
            rec.data["_duplicate"] = True


def _differs(current: object, new: object) -> bool:
    """Campo vazio no arquivo não conta como alteração (a carga não apaga informação)."""
    return new not in (None, "") and str(current or "").strip().upper() != str(new).strip().upper()


def _master_action(rec: Record, dims: Dimensions, company_id: int | None) -> str:
    d = rec.data
    if rec.record_type == "BRANCH":
        obj = dims.branch_objs.get((company_id, d["code"]))
        if obj is None:
            return "CREATE"
        changed = _differs(obj.name, d.get("name")) or _differs(obj.uf, d.get("uf"))
    elif rec.record_type == "COST_CENTER":
        obj = dims.cost_centers.get((company_id, d["code"]))
        if obj is None:
            return "CREATE"
        changed = _differs(obj.name, d.get("name")) or _differs(obj.manager_name, d.get("manager"))
    else:
        obj = dims.accounts.get(d["code"])
        if obj is None:
            return "CREATE"
        changed = (
            _differs(obj.name, d.get("name"))
            or _differs(obj.dre_group, d.get("dre_group"))
            or _differs(dims.package_names.get(obj.package_id), d.get("package"))
        )
    return "UPDATE" if changed else "UNCHANGED"


def validate_master(result: ParseResult, dims: Dimensions, options: dict) -> None:
    default_company = options.get("company_code")
    for rec in result.records:
        rt = rec.record_type
        if rt in ("BRANCH", "COST_CENTER"):
            company_id = _company(rec, dims, default_company)
            rec.data["company_id"] = company_id
            rec.natural_key = f"{rt}|{rec.data['company']}|{rec.data['code']}"
            if company_id is not None:
                rec.data["_action"] = _master_action(rec, dims, company_id)
        elif rt == "ACCOUNT":
            rec.natural_key = f"ACCOUNT|{rec.data['code']}"
            rec.data["_action"] = _master_action(rec, dims, None)
            package = rec.data.get("package")
            if package and package.upper() not in dims.packages:
                rec.warn("NEW_PACKAGE", f"Pacote '{package}' não cadastrado: será criado", "Pacote GMD", package)
    _mark_duplicates(result.records)


def validate_financial(result: ParseResult, dims: Dimensions, options: dict) -> None:
    default_company = options.get("company_code")
    create_missing = bool(options.get("create_missing_dimensions"))
    # avisos de cadastro (criação, filial desconhecida) valem para o arquivo inteiro: um por código,
    # na primeira linha em que aparece — num KSB1 com 12 mil partidas, repetir por linha esconde o resto
    announced: set[tuple[str, str]] = set()

    def once(kind: str, code: str) -> bool:
        if (kind, code) in announced:
            return False
        announced.add((kind, code))
        return True

    for rec in result.records:
        d = rec.data
        company_id = _company(rec, dims, default_company)
        d["company_id"] = company_id
        if company_id is None:
            continue
        if d.get("branch"):
            d["branch_id"] = dims.branch_id(company_id, d["branch"])
            if d["branch_id"] is None and once("branch", d["branch"]):
                rec.warn(
                    "UNKNOWN_BRANCH",
                    f"Filial {d['branch']} não cadastrada (as linhas dessa filial ficarão sem filial)",
                    "Filial",
                    d["branch"],
                )
        cc_code = d.get("cost_center")
        if not cc_code:
            rec.error("REQUIRED", "Centro de custo não informado", "Centro de Custo")
        else:
            cc = dims.cost_centers.get((company_id, cc_code))
            d["cost_center_id"] = cc.id if cc else None
            if cc is None:
                if create_missing and d.get("cost_center_name"):
                    if once("cc", cc_code):
                        rec.warn(
                            "NEW_COST_CENTER",
                            f"Centro de custo {cc_code} ({d['cost_center_name']}) será criado",
                            "Centro de Custo",
                            cc_code,
                        )
                else:
                    rec.error(
                        "UNKNOWN_COST_CENTER",
                        f"Centro de custo {cc_code} não cadastrado na empresa {d['company']}",
                        "Centro de Custo",
                        cc_code,
                    )
        acc_code = d.get("account")
        if not acc_code:
            rec.error("REQUIRED", "Conta contábil não informada", "Conta")
        else:
            acc = dims.accounts.get(acc_code)
            d["account_id"] = acc.id if acc else None
            if acc is None:
                if create_missing and d.get("account_name"):
                    if once("account", acc_code):
                        rec.warn(
                            "NEW_ACCOUNT", f"Conta {acc_code} ({d['account_name']}) será criada", "Conta", acc_code
                        )
                else:
                    rec.error("UNKNOWN_ACCOUNT", f"Conta {acc_code} não cadastrada", "Conta", acc_code)
        amounts = list(d.get("values", {}).values()) + ([d["amount"]] if d.get("amount") else [])
        if any(Decimal(a) < 0 for a in amounts) and result.dataset_type == "REFERENCE_BUDGET":
            rec.warn("NEGATIVE_VALUE", "Valor orçado negativo")
    _mark_duplicates(result.records)


def validate_employees(result: ParseResult, dims: Dimensions, options: dict) -> None:
    default_company = options.get("company_code")
    for rec in result.records:
        d = rec.data
        company_id = _company(rec, dims, default_company)
        d["company_id"] = company_id
        if d["contract"] not in dims.contracts:
            rec.error(
                "INVALID_CONTRACT",
                f"Tipo de contrato '{d['contract']}' não parametrizado",
                "Tipo de Contrato",
                d["contract"],
            )
        if company_id is None:
            continue
        d["branch_id"] = dims.branch_id(company_id, d.get("branch"))
        if d.get("cost_center"):
            cc = dims.cost_centers.get((company_id, d["cost_center"]))
            d["cost_center_id"] = cc.id if cc else None
            if cc is None:
                rec.error(
                    "UNKNOWN_COST_CENTER",
                    f"Centro de custo {d['cost_center']} não cadastrado",
                    "Centro de Custo",
                    d["cost_center"],
                )
        elif options.get("cost_center_code"):
            cc = dims.cost_centers.get((company_id, options["cost_center_code"]))
            d["cost_center_id"] = cc.id if cc else None
            d["cost_center"] = options["cost_center_code"]
        else:
            inferred = _cc_from_position(dims, company_id, d.get("position"))
            if inferred is not None:
                cc, sector = inferred
                d["cost_center"], d["cost_center_id"], d["cc_from_position"] = cc.code, cc.id, sector
                rec.warn(
                    "CC_FROM_POSITION",
                    f"Centro de custo vazio: usado {cc.code} · {cc.name} pelo cargo (setor {sector}); "
                    "fica pendente para confirmar em Apontamentos",
                    "Centro de Custo",
                    d.get("position"),
                )
            else:
                rec.warn(
                    "NO_COST_CENTER",
                    "Colaborador sem centro de custo: não entra no orçamento de nenhum CC (preencha na planilha ou "
                    "ligue o cargo a um setor com centro de custo em Áreas e setores)",
                    "Centro de Custo",
                )
        if rec.natural_key:
            rec.natural_key = f"{d['company']}|{d['registration']}"
    _mark_duplicates(result.records)


def _cc_from_position(dims: Dimensions, company_id: int, position: str | None) -> tuple[CostCenter, str] | None:
    """CC do setor indicado pelo cargo, quando o setor tem um único CC ativo na empresa."""
    sector_id = sector_for_position(position, dims.sectors)
    if sector_id is None:
        return None
    ccs = [
        c for (cid, _), c in dims.cost_centers.items() if cid == company_id and c.area_id == sector_id and c.is_active
    ]
    return (ccs[0], dims.sectors[sector_id]) if len(ccs) == 1 else None


def _subset(result: ParseResult, *types: str) -> ParseResult:
    """Visão de parte dos registros (mesmos objetos: validações se refletem no resultado original)."""
    return ParseResult(result.dataset_type, result.layout, [r for r in result.records if r.record_type in types])


def _keyless_companies(result: ParseResult, dims: Dimensions, options: dict) -> None:
    """Modelo sem CHAVE (REAM, NAVE): a linha não traz a empresa. Vale, nesta ordem: a base de despesas do próprio
    arquivo (parser), o CC já cadastrado em uma só empresa, a filial já cadastrada em uma só empresa, a divisão igual
    ao código de uma empresa (REAM: divisão 2001 = empresa 2001) e a opção company_code. Filial e CC criados a partir
    das linhas ficam na empresa das linhas; conta que não existe no cadastro é criada (com aviso na linha)."""
    code_of = {cid: code for code, cid in dims.companies.items()}
    found: dict[tuple[str, str | None], str] = {}
    new_accounts: dict[str, Record] = {}
    for rec in result.records:
        d = rec.data
        if rec.record_type != "BUDGET_LINE" or not d.get("keyless"):
            continue
        account = d.get("account")
        if account and account not in dims.accounts:
            name = re.sub(r"^[A-Z]-\s*", "", d.get("account_name") or "") or account
            hint = (d.get("package_hint") or "").strip()
            package = hint if hint.upper() in dims.packages else None
            rec.warn(
                "NEW_ACCOUNT",
                f"Conta {account} não cadastrada: será criada ({name}{', pacote ' + package if package else ''})",
                "Conta",
                account,
            )
            if account not in new_accounts:
                new_accounts[account] = Record(
                    "ACCOUNT",
                    rec.sheet,
                    rec.row_number,
                    {
                        "code": account,
                        "name": name,
                        "dre_group": None,
                        "package": package,
                        "detail": None,
                        "nature": None,
                        "from_lines": True,
                    },
                )
        if d.get("company"):
            found.setdefault(("COST_CENTER", d.get("cost_center")), d["company"])
            found.setdefault(("BRANCH", d.get("branch")), d["company"])
            continue
        owners = {cid for cid, code in dims.cost_centers if code == d.get("cost_center")}
        if len(owners) != 1 and d.get("branch"):
            owners = {cid for cid, code in dims.branches if code == d["branch"]}
        if len(owners) == 1:
            company = code_of.get(owners.pop())
        elif d.get("branch") in dims.companies:
            company = d["branch"]
        else:
            company = options.get("company_code")
        d["company"] = company
        if company:
            found.setdefault(("COST_CENTER", d.get("cost_center")), company)
            found.setdefault(("BRANCH", d.get("branch")), company)
    for rec in result.records:
        if rec.record_type in ("COST_CENTER", "BRANCH") and rec.data.get("from_lines") and not rec.data.get("company"):
            rec.data["company"] = found.get((rec.record_type, rec.data["code"]))
    result.records[:0] = list(new_accounts.values())


def validate_opex_template(result: ParseResult, dims: Dimensions, options: dict) -> None:
    _keyless_companies(result, dims, options)
    validate_master(_subset(result, "BRANCH", "COST_CENTER", "ACCOUNT"), dims, options)
    # o próprio arquivo traz nomes de CC e conta: o que faltar no cadastro é criado
    facts = _subset(result, "FACT")
    facts.dataset_type = "ACTUAL"
    validate_financial(facts, dims, options | {"create_missing_dimensions": True})
    file_ccs = {r.data["code"] for r in result.records if r.record_type == "COST_CENTER" and r.status != "ERROR"}
    file_accounts = {
        r.data["code"]: r.data for r in result.records if r.record_type == "ACCOUNT" and r.status != "ERROR"
    }
    from app.imports.loaders import infer_nature

    file_cc_names = {
        " ".join((r.data.get("name") or "").upper().split()): r.data["code"]
        for r in result.records
        if r.record_type == "COST_CENTER" and r.status != "ERROR"
    }
    # filiais criadas a partir das próprias linhas (modelo sem CHAVE): não avisar "filial não cadastrada"
    line_branches = {
        r.data["code"]
        for r in result.records
        if r.record_type == "BRANCH" and r.data.get("from_lines") and r.status != "ERROR"
    }
    for rec in result.records:
        if rec.record_type == "TRAVEL":
            _validate_travel(rec, dims, file_ccs, file_cc_names)
            continue
        if rec.record_type != "BUDGET_LINE":
            continue
        d = rec.data
        company_id = _company(rec, dims, None)
        d["company_id"] = company_id
        if company_id is None:
            continue
        # a CHAVE/fórmula pode não ter resolvido: identifica CC e conta pelos nomes da linha
        if not d.get("cost_center") and d.get("cost_center_name"):
            d["cost_center"] = _cc_by_name(dims, company_id, d["cost_center_name"], file_cc_names)
        if not d.get("account") and d.get("account_name"):
            d["account"] = _account_by_name(dims, d["account_name"], file_accounts)
        if not d.get("branch") and d.get("branch_name"):
            d["branch"] = _branch_by_name(dims, company_id, d["branch_name"])
        if not d.get("cost_center") or not d.get("account"):
            missing = " e ".join(
                x for x, ok in (("centro de custo", d.get("cost_center")), ("conta", d.get("account"))) if not ok
            )
            rec.error(
                "UNRESOLVED_LINE",
                f"Linha com valor sem {missing} identificável: confira o nome do CC/conta "
                f"('{d.get('cost_center_name') or '—'}' / '{d.get('account_name') or '—'}') no BD-Novo",
            )
            continue
        cc = dims.cost_centers.get((company_id, d["cost_center"]))
        if cc is None and d["cost_center"] not in file_ccs:
            rec.error(
                "UNKNOWN_COST_CENTER",
                f"Centro de custo {d['cost_center']} não cadastrado",
                "Centro de Custo",
                d["cost_center"],
            )
        acc = dims.accounts.get(d["account"])
        if acc is None and d["account"] not in file_accounts:
            rec.error("UNKNOWN_ACCOUNT", f"Conta {d['account']} não cadastrada", "Conta", d["account"])
        nature = (
            acc.nature
            if acc
            else (
                infer_nature(
                    d["account"],
                    file_accounts[d["account"]].get("dre_group"),
                    file_accounts[d["account"]].get("package"),
                )
                if d["account"] in file_accounts
                else None
            )
        )
        if nature and nature not in ("OPEX", "FINANCEIRO"):
            rec.error(
                "WRONG_NATURE",
                f"Conta {d['account']} é de {nature}; não entra no orçamento OPEX",
                "Conta",
                d["account"],
            )
        if d.get("branch") and dims.branch_id(company_id, d["branch"]) is None and d["branch"] not in line_branches:
            rec.warn(
                "UNKNOWN_BRANCH", f"Filial {d['branch']} não cadastrada (linha fica sem filial)", "Filial", d["branch"]
            )


def validate_capex_template(result: ParseResult, dims: Dimensions, options: dict) -> None:
    from app.imports.loaders import infer_nature

    validate_master(_subset(result, "BRANCH", "COST_CENTER", "ACCOUNT"), dims, options)
    file_ccs = {r.data["code"] for r in result.records if r.record_type == "COST_CENTER" and r.status != "ERROR"}
    file_accounts = {
        r.data["code"]: r.data for r in result.records if r.record_type == "ACCOUNT" and r.status != "ERROR"
    }

    def nature(code: str | None) -> str | None:
        if not code:
            return None
        acc = dims.accounts.get(code)
        if acc:
            return acc.nature
        if code in file_accounts:
            return infer_nature(code, file_accounts[code].get("dre_group"), file_accounts[code].get("package"))
        return None

    def account_ref(code: str | None) -> tuple[str, str] | None:
        if not code:
            return None
        acc = dims.accounts.get(code)
        return code, (acc.name if acc else (file_accounts.get(code) or {}).get("name") or code)

    # catálogo de ativos do próprio arquivo (aba LISTA ATIVOS): item → conta da classe
    catalog = {
        r.data["name"].upper(): r.data["account"]
        for r in result.records
        if r.record_type == "ASSET_ITEM" and r.data.get("account") and nature(r.data["account"]) == "CAPEX"
    }

    for rec in result.records:
        d = rec.data
        if rec.record_type == "ASSET_ITEM":
            if d.get("account") and nature(d["account"]) not in (None, "CAPEX"):
                rec.warn("WRONG_NATURE", f"Conta {d['account']} não é de ativo: item fica sem conta sugerida")
                d["account"] = None
            if d["asset_class"].upper() not in dims.asset_classes:
                rec.warn("NEW_ASSET_CLASS", f"Classe de ativo '{d['asset_class']}' será criada", "Nome Classe")
            continue
        if rec.record_type != "CAPEX_ITEM":
            continue
        company_id = _company(rec, dims, options.get("company_code"))
        d["company_id"] = company_id
        if company_id is None or rec.status == "ERROR":
            continue
        if dims.cost_centers.get((company_id, d["cost_center"])) is None and d["cost_center"] not in file_ccs:
            rec.error(
                "UNKNOWN_COST_CENTER",
                f"Centro de custo {d['cost_center']} não cadastrado",
                "CENTRO DE CUSTO",
                d["cost_center"],
            )
        acc_nature = nature(d["account"])
        acc_obj = dims.accounts.get(d["account"])
        if acc_nature is None:
            rec.error("UNKNOWN_ACCOUNT", f"Conta {d['account']} não cadastrada", "CONTA", d["account"])
        elif acc_nature != "CAPEX":
            rec.error("WRONG_NATURE", f"Conta {d['account']} é de {acc_nature}; não entra no CAPEX", "CONTA")
        elif acc_obj is not None and not acc_obj.is_active:
            rec.error("INACTIVE_ACCOUNT", f"Conta {d['account']} está inativa", "CONTA", d["account"])
        else:
            item = (d.get("item") or "").strip()
            for issue in capex_rules.check_classification(
                item, account_ref(d["account"]), account_ref(catalog.get(item.upper()))
            ):
                rec.warn(issue.code, issue.message, "CONTA", d["account"])
        if d.get("branch") and dims.branch_id(company_id, d["branch"]) is None:
            rec.warn("UNKNOWN_BRANCH", f"Filial {d['branch']} não cadastrada (fica sem filial)", "FILIAL", d["branch"])
        if d.get("project_type"):
            code = dims.capex_types.get(d["project_type"].strip().upper())
            if code is None:
                rec.warn("UNKNOWN_PROJECT_TYPE", f"Tipo de projeto '{d['project_type']}' não cadastrado", "TIPO")
            d["project_type"] = code
        if d["is_project"] and not d.get("project_type"):
            rec.warn(
                "CAPEX_NO_PROJECT_TYPE", "Projeto sem tipo: complete no sistema antes de enviar", "TIPO DO PROJETO"
            )
        if d["is_project"] and not d.get("justification"):
            rec.warn("CAPEX_NO_JUSTIFICATION", "Projeto sem justificativa: complete antes de enviar", "JUSTIFICATIVA")
        unit, qty = Decimal(d["unit_value"]), Decimal(d["quantity"])
        total = (unit * qty).quantize(Decimal("0.01"))
        scheduled = sum((Decimal(v) for v in d["values"].values()), Decimal(0))
        if abs(scheduled - total) > Decimal("0.01"):
            rec.warn(
                "CAPEX_SCHEDULE_MISMATCH",
                f"Cronograma ({scheduled}) difere do total ({total}): ajuste no sistema antes de enviar",
            )
        if 0 < unit <= Decimal(str(options.get("capex_min_unit_value", 1200))):
            rec.warn(
                "CAPEX_BELOW_MIN_VALUE", "Valor unitário ≤ R$ 1.200: avaliar se é OPEX", "VLR UNIT", d["unit_value"]
            )


def _cc_by_name(dims: Dimensions, company_id: int, name: str, file_cc_names: dict) -> str | None:
    key = " ".join(name.upper().split())
    for (cid, code), cc in dims.cost_centers.items():
        if cid == company_id and " ".join(cc.name.upper().split()) == key:
            return code
    return file_cc_names.get(key)


def _account_by_name(dims: Dimensions, name: str, file_accounts: dict) -> str | None:
    key = " ".join(name.upper().split())
    found = [code for code, acc in dims.accounts.items() if " ".join(acc.name.upper().split()) == key]
    found += [code for code, d in file_accounts.items() if " ".join((d.get("name") or "").upper().split()) == key]
    unique = set(found)
    return unique.pop() if len(unique) == 1 else None


def _branch_by_name(dims: Dimensions, company_id: int, name: str) -> str | None:
    for (cid, code), branch in dims.branch_objs.items():
        if cid == company_id and branch.name.upper() == name.strip().upper():
            return code
    return None


def _validate_travel(rec: Record, dims: Dimensions, file_ccs: set, file_cc_names: dict) -> None:
    """Viagem da aba I - Viagens: CC pelo código (fórmula do template) ou, sem ele, pelo nome."""
    d = rec.data
    company_id = _company(rec, dims, None)
    d["company_id"] = company_id
    if company_id is None:
        return
    if not d.get("cost_center") and d.get("cost_center_name"):
        d["cost_center"] = _cc_by_name(dims, company_id, d["cost_center_name"], file_cc_names)
    cc = d.get("cost_center")
    if not cc:
        rec.error(
            "UNKNOWN_COST_CENTER",
            f"Centro de custo '{d.get('cost_center_name') or ''}' não encontrado no cadastro",
            "CENTRO DE CUSTO",
            d.get("cost_center_name"),
        )
    elif dims.cost_centers.get((company_id, cc)) is None and cc not in file_ccs:
        rec.error("UNKNOWN_COST_CENTER", f"Centro de custo {cc} não cadastrado", "CENTRO DE CUSTO", cc)
    for code in ("6010301011", "6010301036", "6010301001"):
        if code not in dims.accounts:
            rec.error("UNKNOWN_ACCOUNT", f"Conta de viagem {code} não cadastrada", "Conta", code)
    if d.get("branch") is None and d.get("branch_name"):
        d["branch"] = _branch_by_name(dims, company_id, d["branch_name"])
    if d.get("amounts") is None:
        rec.warn("TRAVEL_RECALCULATED", "Valores da viagem não vieram no arquivo: calculados pelas tarifas do ciclo")
    elif not d["amounts"]:
        rec.warn("TRAVEL_NO_VALUES", "Viagem sem valores (tarifa não encontrada na planilha)")
    else:
        t = d.get("travel") or {}
        ticket = Decimal(d["amounts"].get(TRAVEL_TICKET_ACCOUNT, "0"))
        warning = missing_fare_warning(t.get("origin"), t.get("destination"), ticket)
        if warning:
            rec.warn("TRAVEL_NO_FARE", warning, "DESPESAS COM PASSAGENS")


def validate(result: ParseResult, dims: Dimensions, options: dict) -> None:
    if result.dataset_type == "OPEX_TEMPLATE":
        return validate_opex_template(result, dims, options)
    if result.dataset_type == "CAPEX_TEMPLATE":
        return validate_capex_template(result, dims, options)
    if result.dataset_type in ("MASTER_DATA", "COST_CENTERS", "ACCOUNTS"):
        validate_master(result, dims, options)
    elif result.dataset_type in ("ACTUAL", "REFERENCE_BUDGET", "PROJECTION"):
        validate_financial(result, dims, options)
    elif result.dataset_type == "EMPLOYEES":
        validate_employees(result, dims, options)
    else:
        _mark_duplicates(result.records)


def final_status(rec: Record) -> str:
    if rec.status == "ERROR":
        return "ERROR"
    if rec.data.get("_duplicate"):
        return "DUPLICATE"
    return rec.status
