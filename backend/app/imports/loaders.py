"""Carga dos registros validados. Executa dentro de uma transação; nunca apaga histórico:
fatos ganham nova `dataset_version` e a anterior apenas deixa de ser a corrente."""

import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, insert, select, update
from sqlalchemy.orm import Session

from app.models import (
    Account,
    AccountDetail,
    ActualEntry,
    Area,
    Branch,
    BudgetPackage,
    CostCenter,
    DatasetVersion,
    Department,
    Employee,
    EmployeeBenefit,
    ImportBatch,
    ImportRow,
    JobPosition,
    MacroAssumption,
    ReferenceBudgetEntry,
    User,
)
from app.services import audit


def _slug(text: str) -> str:
    value = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", "_", value).strip("_")[:40]


def infer_nature(account_code: str, dre_group: str | None, package: str | None) -> str:
    if (package or "").strip().upper() == "CAPEX" or account_code.startswith("1"):
        return "CAPEX"
    if (dre_group or "").upper().startswith("RESULTADO FINANCEIRO") or account_code.startswith("603"):
        return "FINANCEIRO"
    if account_code.startswith("60101"):
        return "PESSOAL"
    return "OPEX"


def new_version(
    db: Session, batch: ImportBatch, dataset_type: str, scope_key: str, user_id: int | None, source: str
) -> DatasetVersion:
    current = db.scalars(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_type == dataset_type, DatasetVersion.scope_key == scope_key)
        .with_for_update()
    ).all()
    number = max((v.version_number for v in current), default=0) + 1
    now = datetime.utcnow()
    for v in current:
        if v.is_current:
            v.is_current = False
            v.superseded_at = now
    version = DatasetVersion(
        dataset_type=dataset_type,
        scope_key=scope_key,
        version_number=number,
        import_batch_id=batch.id,
        source=source,
        is_current=True,
        created_by=user_id,
    )
    db.add(version)
    db.flush()
    return version


def _rows(db: Session, batch_id: int, record_type: str | None = None):
    stmt = select(ImportRow).where(ImportRow.batch_id == batch_id, ImportRow.status.in_(("VALID", "WARNING")))
    if record_type:
        stmt = stmt.where(ImportRow.record_type == record_type)
    return db.scalars(stmt.order_by(ImportRow.row_number)).all()


# ------------------------------------------------------------------ cadastros


def load_master(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    stats = defaultdict(int)
    users_by_name = {u.name.upper(): u.id for u in db.scalars(select(User))}
    for row in _rows(db, batch.id, "BRANCH"):
        d = row.data
        obj = db.scalar(select(Branch).where(Branch.company_id == d["company_id"], Branch.code == d["code"]))
        if obj is None:
            obj = Branch(company_id=d["company_id"], code=d["code"], name=d["name"], uf=d.get("uf"))
            db.add(obj)
            db.flush()
            audit.record(
                db,
                user_id=user_id,
                action="CREATE",
                entity_type="branch",
                entity_id=obj.id,
                after=audit.snapshot(obj),
                reason=f"import #{batch.id}",
            )
            stats["branches_created"] += 1
        else:
            before = audit.snapshot(obj)
            obj.name = d["name"]
            obj.uf = d.get("uf") or obj.uf
            after = audit.snapshot(obj)
            if audit.diff(before, after)[0]:
                audit.record(
                    db,
                    user_id=user_id,
                    action="UPDATE",
                    entity_type="branch",
                    entity_id=obj.id,
                    before=before,
                    after=after,
                    reason=f"import #{batch.id}",
                )
                stats["branches_updated"] += 1
    for row in _rows(db, batch.id, "COST_CENTER"):
        d = row.data
        dept_id = _get_or_create_department(db, d.get("department"))
        area_id = _get_or_create_area(db, d.get("area"), dept_id)
        obj = db.scalar(
            select(CostCenter).where(CostCenter.company_id == d["company_id"], CostCenter.code == d["code"])
        )
        manager_id = users_by_name.get((d.get("manager") or "").upper())
        if obj is None:
            obj = CostCenter(
                company_id=d["company_id"],
                code=d["code"],
                name=d["name"],
                manager_name=d.get("manager"),
                manager_user_id=manager_id,
                department_id=dept_id,
                area_id=area_id,
            )
            db.add(obj)
            db.flush()
            audit.record(
                db,
                user_id=user_id,
                action="CREATE",
                entity_type="cost_center",
                entity_id=obj.id,
                after=audit.snapshot(obj),
                reason=f"import #{batch.id}",
            )
            stats["cost_centers_created"] += 1
        else:
            before = audit.snapshot(obj)
            obj.name = d["name"]
            obj.manager_name = d.get("manager") or obj.manager_name
            obj.manager_user_id = manager_id or obj.manager_user_id
            obj.department_id = dept_id or obj.department_id
            obj.area_id = area_id or obj.area_id
            after = audit.snapshot(obj)
            if audit.diff(before, after)[0]:
                audit.record(
                    db,
                    user_id=user_id,
                    action="UPDATE",
                    entity_type="cost_center",
                    entity_id=obj.id,
                    before=before,
                    after=after,
                    reason=f"import #{batch.id}",
                )
                stats["cost_centers_updated"] += 1
    packages = {p.name.upper(): p for p in db.scalars(select(BudgetPackage))}
    for row in _rows(db, batch.id, "ACCOUNT"):
        d = row.data
        package = None
        if d.get("package"):
            package = packages.get(d["package"].upper())
            if package is None:
                nature = "CAPEX" if d["package"].upper() == "CAPEX" else "OPEX"
                package = BudgetPackage(
                    code=_slug(d["package"]),
                    name=d["package"],
                    nature=nature,
                    form_type="CAPEX" if nature == "CAPEX" else "GENERIC",
                )
                db.add(package)
                db.flush()
                packages[d["package"].upper()] = package
                stats["packages_created"] += 1
        obj = db.scalar(select(Account).where(Account.code == d["code"]))
        nature = d.get("nature") or infer_nature(d["code"], d.get("dre_group"), d.get("package"))
        if obj is None:
            obj = Account(
                code=d["code"],
                name=d["name"],
                dre_group=d.get("dre_group"),
                nature=nature,
                package_id=package.id if package else None,
            )
            db.add(obj)
            db.flush()
            audit.record(
                db,
                user_id=user_id,
                action="CREATE",
                entity_type="account",
                entity_id=obj.id,
                after=audit.snapshot(obj),
                reason=f"import #{batch.id}",
            )
            stats["accounts_created"] += 1
        else:
            before = audit.snapshot(obj)
            obj.name = d["name"]
            obj.dre_group = d.get("dre_group") or obj.dre_group
            obj.package_id = package.id if package else obj.package_id
            obj.nature = nature
            after = audit.snapshot(obj)
            if audit.diff(before, after)[0]:
                audit.record(
                    db,
                    user_id=user_id,
                    action="UPDATE",
                    entity_type="account",
                    entity_id=obj.id,
                    before=before,
                    after=after,
                    reason=f"import #{batch.id}",
                )
                stats["accounts_updated"] += 1
        if d.get("detail"):
            exists = db.scalar(
                select(AccountDetail).where(AccountDetail.account_id == obj.id, AccountDetail.name == d["detail"])
            )
            if exists is None:
                db.add(AccountDetail(account_id=obj.id, name=d["detail"]))
                stats["account_details_created"] += 1
    version = new_version(db, batch, "MASTER_DATA", "MASTER_DATA", user_id, "EXCEL")
    version.row_count = sum(stats.values())
    return dict(stats)


def _get_or_create_department(db: Session, name: str | None) -> int | None:
    if not name:
        return None
    obj = db.scalar(select(Department).where(func.upper(Department.name) == name.upper()))
    if obj is None:
        obj = Department(name=name)
        db.add(obj)
        db.flush()
    return obj.id


def _get_or_create_area(db: Session, name: str | None, department_id: int | None) -> int | None:
    if not name:
        return None
    obj = db.scalar(select(Area).where(func.upper(Area.name) == name.upper(), Area.department_id == department_id))
    if obj is None:
        obj = Area(name=name, department_id=department_id)
        db.add(obj)
        db.flush()
    return obj.id


def _ensure_dimensions(db: Session, d: dict, stats: dict) -> None:
    """Cria CC/conta ausentes quando a importação foi feita com create_missing_dimensions."""
    if d.get("cost_center_id") is None:
        cc = db.scalar(
            select(CostCenter).where(CostCenter.company_id == d["company_id"], CostCenter.code == d["cost_center"])
        )
        if cc is None:
            cc = CostCenter(
                company_id=d["company_id"],
                code=d["cost_center"],
                name=d["cost_center_name"],
                manager_name=d.get("manager"),
            )
            db.add(cc)
            db.flush()
            stats["cost_centers_created"] += 1
        d["cost_center_id"] = cc.id
    if d.get("account_id") is None:
        acc = db.scalar(select(Account).where(Account.code == d["account"]))
        if acc is None:
            package_id = None
            if d.get("package"):
                package_id = db.scalar(
                    select(BudgetPackage.id).where(func.upper(BudgetPackage.name) == d["package"].upper())
                )
            acc = Account(
                code=d["account"],
                name=d["account_name"],
                package_id=package_id,
                nature=infer_nature(d["account"], None, d.get("package")),
            )
            db.add(acc)
            db.flush()
            stats["accounts_created"] += 1
        d["account_id"] = acc.id


# ------------------------------------------------------------------ fatos financeiros


def load_financial(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    rows = _rows(db, batch.id)
    stats: dict = defaultdict(int)
    dataset = batch.dataset_type
    scenario = (batch.options or {}).get("scenario", "ORC")
    source = "SAP_KSB1" if batch.layout == "SAP_KSB1" else "EXCEL"
    # uma versão por empresa × ano: cargas parciais de outra empresa não substituem esta
    groups: dict[tuple[str, int], list[ImportRow]] = defaultdict(list)
    for row in rows:
        groups[(row.data["company"], int(row.data["year"]))].append(row)
    versions = []
    for (company_code, year), group in sorted(groups.items()):
        scope = (
            f"ACTUAL:{year}:{company_code}" if dataset == "ACTUAL" else f"{dataset}:{scenario}:{year}:{company_code}"
        )
        version = new_version(db, batch, dataset, scope, user_id, source)
        payload = []
        max_period = 0
        for row in group:
            d = dict(row.data)
            _ensure_dimensions(db, d, stats)
            base = {
                "dataset_version_id": version.id,
                "company_id": d["company_id"],
                "branch_id": d.get("branch_id"),
                "cost_center_id": d["cost_center_id"],
                "account_id": d["account_id"],
            }
            if batch.layout == "SAP_KSB1":
                period = int(d["period"])
                payload.append(
                    base
                    | {
                        "fiscal_year": year,
                        "period": period,
                        "amount": Decimal(d["amount"]),
                        "posting_date": date.fromisoformat(d["posting_date"]) if d.get("posting_date") else None,
                        "document_number": d.get("document"),
                        "document_type": d.get("document_type"),
                        "vendor_code": d.get("vendor_code"),
                        "vendor_name": d.get("vendor_name"),
                        "text": d.get("text"),
                        "currency": d.get("currency") or "BRL",
                        "source": source,
                    }
                )
                max_period = max(max_period, period)
            else:
                for month, amount in d.get("values", {}).items():
                    value = Decimal(amount)
                    if value == 0:
                        continue
                    entry = base | {"fiscal_year": year, "period": int(month), "amount": value}
                    if dataset == "ACTUAL":
                        entry |= {"source": source, "currency": "BRL"}
                    else:
                        entry |= {"scenario": scenario}
                    payload.append(entry)
                    max_period = max(max_period, int(month))
        model = ActualEntry if dataset == "ACTUAL" else ReferenceBudgetEntry
        for start in range(0, len(payload), 5000):
            db.execute(insert(model), payload[start : start + 5000])
        version.row_count = len(payload)
        version.last_closed_period = max_period or None
        versions.append({"scope": scope, "version": version.version_number, "entries": len(payload)})
        stats["entries"] += len(payload)
    return dict(stats) | {"versions": versions}


# ------------------------------------------------------------------ pessoal


def load_employees(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    stats: dict = defaultdict(int)
    rows = _rows(db, batch.id, "EMPLOYEE")
    companies = sorted({r.data["company"] for r in rows})
    version = new_version(db, batch, "EMPLOYEES", "EMPLOYEES:" + ",".join(companies), user_id, "EXCEL")
    positions = {p.name.upper(): p.id for p in db.scalars(select(JobPosition))}
    for row in rows:
        d = row.data
        pos_name = d.get("position")
        position_id = None
        if pos_name:
            position_id = positions.get(pos_name.upper())
            if position_id is None:
                pos = JobPosition(name=pos_name)
                db.add(pos)
                db.flush()
                position_id = positions[pos_name.upper()] = pos.id
        dept_id = _get_or_create_department(db, d.get("department"))
        area_id = _get_or_create_area(db, d.get("area"), dept_id)
        emp = db.scalar(
            select(Employee).where(Employee.company_id == d["company_id"], Employee.registration == d["registration"])
        )
        fields = {
            "name": d["name"],
            "branch_id": d.get("branch_id"),
            "cost_center_id": d.get("cost_center_id"),
            "department_id": dept_id,
            "area_id": area_id,
            "position_id": position_id,
            "contract_type_code": d["contract"],
            "base_salary": Decimal(d["salary"]),
            "admission_date": date.fromisoformat(d["admission"]) if d.get("admission") else None,
            "termination_date": date.fromisoformat(d["termination"]) if d.get("termination") else None,
            "dataset_version_id": version.id,
            "is_active": True,
        }
        if emp is None:
            emp = Employee(company_id=d["company_id"], registration=d["registration"], **fields)
            db.add(emp)
            db.flush()
            audit.record(
                db,
                user_id=user_id,
                action="CREATE",
                entity_type="employee",
                entity_id=emp.id,
                after=audit.snapshot(emp),
                reason=f"import #{batch.id}",
            )
            stats["employees_created"] += 1
        else:
            before = audit.snapshot(emp)
            for key, value in fields.items():
                setattr(emp, key, value)
            audit.record(
                db,
                user_id=user_id,
                action="UPDATE",
                entity_type="employee",
                entity_id=emp.id,
                before=before,
                after=audit.snapshot(emp),
                reason=f"import #{batch.id}",
            )
            stats["employees_updated"] += 1
        db.execute(EmployeeBenefit.__table__.delete().where(EmployeeBenefit.employee_id == emp.id))
        for code, qty in (d.get("benefits") or {}).items():
            db.add(EmployeeBenefit(employee_id=emp.id, benefit_code=code, quantity=Decimal(str(qty))))
    if (batch.options or {}).get("deactivate_missing"):
        loaded = {r.data["registration"] for r in rows}
        company_ids = {r.data["company_id"] for r in rows}
        result = db.execute(
            update(Employee)
            .where(Employee.company_id.in_(company_ids), Employee.registration.not_in(loaded), Employee.is_active)
            .values(is_active=False)
        )
        stats["employees_deactivated"] = result.rowcount
    stats["vacancies_pending"] = len(_rows(db, batch.id, "VACANCY"))
    version.row_count = len(rows)
    return dict(stats)


def load_macro(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    rows = _rows(db, batch.id)
    version = new_version(db, batch, "MACRO_ASSUMPTIONS", "MACRO_ASSUMPTIONS", user_id, "EXCEL")
    payload = [
        {
            "dataset_version_id": version.id,
            "category": r.data["category"],
            "indicator": r.data["indicator"],
            "segment": r.data.get("segment"),
            "source": r.data.get("source"),
            "year": r.data["year"],
            "value": Decimal(r.data["value"]),
            "reference_date": date.fromisoformat(r.data["reference_date"]) if r.data.get("reference_date") else None,
        }
        for r in rows
        if r.data.get("value") is not None
    ]
    if payload:
        db.execute(insert(MacroAssumption), payload)
    version.row_count = len(payload)
    return {"assumptions": len(payload)}


LOADERS = {
    "MASTER_DATA": load_master,
    "COST_CENTERS": load_master,
    "ACCOUNTS": load_master,
    "ACTUAL": load_financial,
    "REFERENCE_BUDGET": load_financial,
    "EMPLOYEES": load_employees,
    "MACRO_ASSUMPTIONS": load_macro,
}
