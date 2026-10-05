"""Compara o arquivo validado com a base vigente antes da confirmação.

Responde à pergunta "o que essa carga vai mudar?": registros novos, alterados, idênticos
e (no modo substituir) os que deixariam de existir. Também identifica cargas sem nenhuma
alteração, que exigem confirmação explícita para não gerar versões redundantes.
"""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.imports.base import ParseResult
from app.imports.resolver import final_status
from app.models import (
    Account,
    ActualEntry,
    Branch,
    CostCenter,
    DatasetVersion,
    Employee,
    MacroAssumption,
    ReferenceBudgetEntry,
)

TOLERANCE = Decimal("0.005")
MODES = ("MERGE", "REPLACE")


def financial_scope(dataset_type: str, scenario: str, year: int, company_code: str) -> str:
    if dataset_type == "ACTUAL":
        return f"ACTUAL:{year}:{company_code}"
    return f"{dataset_type}:{scenario}:{year}:{company_code}"


def current_version(db: Session, dataset_type: str, scope: str) -> DatasetVersion | None:
    return db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.dataset_type == dataset_type,
            DatasetVersion.scope_key == scope,
            DatasetVersion.is_current,
        )
    )


def _valid(result: ParseResult):
    return [r for r in result.records if final_status(r) in ("VALID", "WARNING")]


def _file_cells(records) -> dict[tuple, dict[int, Decimal]]:
    """{(branch_id, cc_id, account_id): {mês: valor}} a partir dos registros do arquivo."""
    cells: dict[tuple, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    for r in records:
        d = r.data
        key = (
            d.get("branch_id"),
            d.get("cost_center_id") or f"novo:{d['cost_center']}",
            d.get("account_id") or f"novo:{d['account']}",
        )
        if "values" in d:
            for month, amount in d["values"].items():
                cells[key][int(month)] += Decimal(amount)
        elif d.get("amount") is not None:
            cells[key][int(d["period"])] += Decimal(d["amount"])
    return cells


def _db_cells(db: Session, model, version_id: int) -> dict[tuple, dict[int, Decimal]]:
    cells: dict[tuple, dict[int, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    rows = db.execute(
        select(model.branch_id, model.cost_center_id, model.account_id, model.period, func.sum(model.amount))
        .where(model.dataset_version_id == version_id)
        .group_by(model.branch_id, model.cost_center_id, model.account_id, model.period)
    )
    for branch_id, cc_id, acc_id, period, amount in rows:
        cells[(branch_id, cc_id, acc_id)][int(period)] += amount
    return cells


def _same(a: dict[int, Decimal], b: dict[int, Decimal]) -> bool:
    return all(abs(a.get(m, Decimal(0)) - b.get(m, Decimal(0))) <= TOLERANCE for m in set(a) | set(b))


def compare_financial(db: Session, result: ParseResult, options: dict) -> dict:
    mode = options.get("mode", "MERGE")
    scenario = options.get("scenario", "ORC")
    model = ActualEntry if result.dataset_type == "ACTUAL" else ReferenceBudgetEntry
    groups = defaultdict(list)
    for r in _valid(result):
        groups[(r.data["company"], int(r.data["year"]))].append(r)
    scopes = []
    for (company, year), records in sorted(groups.items()):
        scope = financial_scope(result.dataset_type, scenario, year, company)
        version = current_version(db, result.dataset_type, scope)
        file_cells = _file_cells(records)
        db_cells = _db_cells(db, model, version.id) if version else {}
        new = changed = unchanged = 0
        for key, months in file_cells.items():
            if key not in db_cells:
                new += 1
            elif _same(months, db_cells[key]):
                unchanged += 1
            else:
                changed += 1
        absent = [k for k in db_cells if k not in file_cells]
        file_total = sum((sum(m.values(), Decimal(0)) for m in file_cells.values()), Decimal(0))
        current_total = sum((sum(m.values(), Decimal(0)) for m in db_cells.values()), Decimal(0))
        kept_total = sum((sum(db_cells[k].values(), Decimal(0)) for k in absent), Decimal(0))
        after_total = file_total + (kept_total if mode == "MERGE" else 0)
        scopes.append(
            {
                "scope": scope,
                "year": year,
                "company": company,
                "current_version": version.version_number if version else None,
                "new": new,
                "changed": changed,
                "unchanged": unchanged,
                "absent": len(absent),
                "absent_action": "KEEP" if mode == "MERGE" else "REMOVE",
                "absent_total": str(kept_total),
                "current_total": str(current_total),
                "after_total": str(after_total),
                "difference": str(after_total - current_total),
                "absent_samples": _describe(db, absent[:10]),
            }
        )
    no_changes = bool(scopes) and all(
        s["new"] == 0 and s["changed"] == 0 and (mode == "MERGE" or s["absent"] == 0) for s in scopes
    )
    return {"kind": "FINANCIAL", "mode": mode, "scopes": scopes, "no_changes": no_changes}


def _describe(db: Session, keys: list[tuple]) -> list[str]:
    out = []
    for branch_id, cc_id, acc_id in keys:
        cc = db.get(CostCenter, cc_id)
        acc = db.get(Account, acc_id)
        branch = db.get(Branch, branch_id) if branch_id else None
        out.append(
            f"CC {cc.code if cc else cc_id} · conta {acc.code if acc else acc_id}"
            + (f" · filial {branch.code}" if branch else "")
        )
    return out


def compare_master(result: ParseResult) -> dict:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"CREATE": 0, "UPDATE": 0, "UNCHANGED": 0})
    for r in _valid(result):
        counts[r.record_type][r.data.get("_action", "CREATE")] += 1
    no_changes = bool(counts) and all(c["CREATE"] == 0 and c["UPDATE"] == 0 for c in counts.values())
    return {"kind": "MASTER", "by_type": dict(counts), "no_changes": no_changes}


def compare_employees(db: Session, result: ParseResult) -> dict:
    created = updated = unchanged = 0
    for r in _valid(result):
        if r.record_type != "EMPLOYEE":
            continue
        d = r.data
        emp = db.scalar(
            select(Employee).where(Employee.company_id == d["company_id"], Employee.registration == d["registration"])
        )
        if emp is None:
            created += 1
            d["_action"] = "CREATE"
        elif (
            emp.name == d["name"]
            and emp.base_salary == Decimal(d["salary"])
            and emp.cost_center_id == d.get("cost_center_id")
            and emp.contract_type_code == d["contract"]
        ):
            unchanged += 1
            d["_action"] = "UNCHANGED"
        else:
            updated += 1
            d["_action"] = "UPDATE"
    no_changes = (created + updated) == 0 and unchanged > 0
    return {"kind": "EMPLOYEES", "new": created, "changed": updated, "unchanged": unchanged, "no_changes": no_changes}


def compare_macro(db: Session, result: ParseResult) -> dict:
    version = current_version(db, "MACRO_ASSUMPTIONS", "MACRO_ASSUMPTIONS")
    current = {}
    if version:
        for m in db.scalars(select(MacroAssumption).where(MacroAssumption.dataset_version_id == version.id)):
            current[(m.indicator, m.segment, m.source, m.year)] = m.value
    new = changed = unchanged = 0
    for r in _valid(result):
        d = r.data
        key = (d["indicator"], d.get("segment"), d.get("source"), d["year"])
        if key not in current:
            new += 1
        elif d.get("value") is not None and abs(Decimal(d["value"]) - current[key]) <= Decimal("0.0000001"):
            unchanged += 1
        else:
            changed += 1
    no_changes = version is not None and new == 0 and changed == 0
    return {"kind": "MACRO", "new": new, "changed": changed, "unchanged": unchanged, "no_changes": no_changes}


def compare_opex_template(db: Session, result: ParseResult, options: dict) -> dict:
    """Template OPEX: compara cada parte e checa se o orçamento de cada CC ainda aceita alterações."""
    from app.domain.workflow import EDITABLE, STATUS_LABELS
    from app.models import BudgetLine, Company
    from app.services import opex as opex_svc

    def part(*types):
        return ParseResult(result.dataset_type, result.layout, [r for r in result.records if r.record_type in types])

    master = compare_master(part("BRANCH", "COST_CENTER", "ACCOUNT"))
    facts = part("FACT")
    facts.dataset_type = "ACTUAL"
    actual = compare_financial(db, facts, options) if facts.records else None

    budget = []
    lines = [r for r in result.records if r.record_type == "BUDGET_LINE"]
    if lines:
        try:
            ctx = opex_svc.context(db)
        except opex_svc.OpexError as exc:
            for r in lines:
                r.error("NO_CYCLE", str(exc))
            ctx = None
        groups = defaultdict(list)
        for r in lines:
            groups[(r.data["company"], r.data["cost_center"])].append(r)
        companies = {c.code: c.id for c in db.scalars(select(Company))}
        for (company, cc_code), recs in sorted(groups.items()):
            cc = db.scalar(
                select(CostCenter).where(CostCenter.company_id == companies.get(company), CostCenter.code == cc_code)
            )
            sub = (
                opex_svc.get_submission(db, ctx, cc.id, create=False) if (cc is not None and ctx is not None) else None
            )
            status = sub.status if sub else "DRAFT"
            editable = status in EDITABLE and (ctx is None or ctx.cycle.status != "CLOSED")
            if not editable:
                for r in recs:
                    r.error(
                        "BUDGET_LOCKED",
                        f"O orçamento do CC {cc_code} está '{STATUS_LABELS.get(status, status)}' "
                        "e não aceita alterações; peça à Controladoria para devolver para ajuste",
                    )
            replaced = []
            if sub is not None:
                replaced = [
                    line
                    for line in db.scalars(select(BudgetLine).where(BudgetLine.submission_id == sub.id))
                    if (line.attributes or {}).get("source") == "TEMPLATE"
                ]
            valid = [r for r in recs if final_status(r) in ("VALID", "WARNING")]
            new_total = sum((Decimal(v) for r in valid for v in r.data["values"].values()), Decimal(0))
            budget.append(
                {
                    "cost_center": cc_code,
                    "company": company,
                    "status": status,
                    "editable": editable,
                    "lines": len(valid),
                    "total": str(new_total),
                    "replaces_lines": len(replaced),
                    "replaces_total": str(sum((line.total_amount for line in replaced), Decimal(0))),
                }
            )
    no_changes = (
        (master["no_changes"] or not master["by_type"])
        and (actual is None or actual["no_changes"])
        and not any(b["lines"] for b in budget)
    )
    return {"kind": "TEMPLATE", "master": master, "actual": actual, "budget": budget, "no_changes": no_changes}


def compare(db: Session, result: ParseResult, options: dict) -> dict:
    if result.dataset_type == "OPEX_TEMPLATE":
        return compare_opex_template(db, result, options)
    if result.dataset_type in ("ACTUAL", "REFERENCE_BUDGET"):
        return compare_financial(db, result, options)
    if result.dataset_type in ("MASTER_DATA", "COST_CENTERS", "ACCOUNTS"):
        return compare_master(result)
    if result.dataset_type == "EMPLOYEES":
        return compare_employees(db, result)
    if result.dataset_type == "MACRO_ASSUMPTIONS":
        return compare_macro(db, result)
    return {"kind": "NONE", "no_changes": False}
