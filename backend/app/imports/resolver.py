"""Validação dos registros lidos contra os cadastros (empresa, filial, CC, conta, contrato)."""

from collections import Counter
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.imports.base import ParseResult, Record
from app.models import Account, Branch, BudgetPackage, Company, ContractType, CostCenter


class Dimensions:
    def __init__(self, db: Session) -> None:
        self.companies = {c.code: c.id for c in db.scalars(select(Company))}
        self.branches: dict[tuple[int, str], int] = {}
        self.branch_names: dict[tuple[int, str], int] = {}
        for b in db.scalars(select(Branch)):
            self.branches[(b.company_id, b.code)] = b.id
            self.branch_names[(b.company_id, b.name.upper())] = b.id
        self.cost_centers = {(c.company_id, c.code): c for c in db.scalars(select(CostCenter))}
        self.accounts = {a.code: a for a in db.scalars(select(Account))}
        self.packages = {p.name.upper(): p.id for p in db.scalars(select(BudgetPackage))}
        self.contracts = {c.code for c in db.scalars(select(ContractType))}

    def branch_id(self, company_id: int, value: str | None) -> int | None:
        if not value:
            return None
        if value.isdigit():
            return self.branches.get((company_id, value.zfill(4)))
        return self.branch_names.get((company_id, value.upper()))


def _company(rec: Record, dims: Dimensions, default_code: str | None) -> int | None:
    code = rec.data.get("company") or default_code
    if not code:
        rec.error("REQUIRED", "Empresa não informada (coluna Empresa ou opção company_code)", "Empresa")
        return None
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


def validate_master(result: ParseResult, dims: Dimensions, options: dict) -> None:
    default_company = options.get("company_code")
    for rec in result.records:
        rt = rec.record_type
        if rt in ("BRANCH", "COST_CENTER"):
            company_id = _company(rec, dims, default_company)
            rec.data["company_id"] = company_id
            rec.natural_key = f"{rt}|{rec.data['company']}|{rec.data['code']}"
            if company_id is not None:
                exists = (
                    (company_id, rec.data["code"]) in dims.branches
                    if rt == "BRANCH"
                    else (company_id, rec.data["code"]) in dims.cost_centers
                )
                rec.data["_action"] = "UPDATE" if exists else "CREATE"
        elif rt == "ACCOUNT":
            rec.natural_key = f"ACCOUNT|{rec.data['code']}"
            rec.data["_action"] = "UPDATE" if rec.data["code"] in dims.accounts else "CREATE"
            package = rec.data.get("package")
            if package and package.upper() not in dims.packages:
                rec.warn("NEW_PACKAGE", f"Pacote '{package}' não cadastrado: será criado", "Pacote GMD", package)
    _mark_duplicates(result.records)


def validate_financial(result: ParseResult, dims: Dimensions, options: dict) -> None:
    default_company = options.get("company_code")
    create_missing = bool(options.get("create_missing_dimensions"))
    for rec in result.records:
        d = rec.data
        company_id = _company(rec, dims, default_company)
        d["company_id"] = company_id
        if company_id is None:
            continue
        if d.get("branch"):
            d["branch_id"] = dims.branch_id(company_id, d["branch"])
            if d["branch_id"] is None:
                rec.warn(
                    "UNKNOWN_BRANCH", f"Filial {d['branch']} não cadastrada (ficará sem filial)", "Filial", d["branch"]
                )
        cc_code = d.get("cost_center")
        if not cc_code:
            rec.error("REQUIRED", "Centro de custo não informado", "Centro de Custo")
        else:
            cc = dims.cost_centers.get((company_id, cc_code))
            d["cost_center_id"] = cc.id if cc else None
            if cc is None:
                if create_missing and d.get("cost_center_name"):
                    rec.warn("NEW_COST_CENTER", f"Centro de custo {cc_code} será criado", "Centro de Custo", cc_code)
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
                    rec.warn("NEW_ACCOUNT", f"Conta {acc_code} será criada", "Conta", acc_code)
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
            rec.warn("NO_COST_CENTER", "Colaborador sem centro de custo", "Centro de Custo")
        if rec.natural_key:
            rec.natural_key = f"{d['company']}|{d['registration']}"
    _mark_duplicates(result.records)


def validate(result: ParseResult, dims: Dimensions, options: dict) -> None:
    if result.dataset_type in ("MASTER_DATA", "COST_CENTERS", "ACCOUNTS"):
        validate_master(result, dims, options)
    elif result.dataset_type in ("ACTUAL", "REFERENCE_BUDGET"):
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
