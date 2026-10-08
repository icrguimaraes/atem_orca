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
            if d.get("nature"):  # natureza inferida só vale para contas novas; ajuste manual prevalece
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
                name=d.get("cost_center_name") or d["cost_center"],
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
                name=d.get("account_name") or d["account"],
                package_id=package_id,
                nature=infer_nature(d["account"], None, d.get("package")),
            )
            db.add(acc)
            db.flush()
            stats["accounts_created"] += 1
        d["account_id"] = acc.id


# ------------------------------------------------------------------ fatos financeiros


def load_financial(db: Session, batch: ImportBatch, user_id: int | None, dataset: str | None = None) -> dict:
    rows = _rows(db, batch.id, "FACT")
    stats: dict = defaultdict(int)
    dataset = dataset or batch.dataset_type
    scenario = (batch.options or {}).get("scenario", "ORC")
    source = "SAP_KSB1" if batch.layout == "SAP_KSB1" else "EXCEL"
    mode = (batch.options or {}).get("mode", "MERGE")
    if dataset == "PROJECTION":
        mode = "REPLACE"  # a projeção substitui a anterior inteira; o realizado (KSB1) nunca é tocado
    model = ActualEntry if dataset in ("ACTUAL", "PROJECTION") else ReferenceBudgetEntry
    # uma versão por empresa × ano: cargas parciais de outra empresa não substituem esta
    groups: dict[tuple[str, int], list[ImportRow]] = defaultdict(list)
    for row in rows:
        groups[(row.data["company"], int(row.data["year"]))].append(row)
    versions = []
    for (company_code, year), group in sorted(groups.items()):
        scope = (
            f"ACTUAL:{year}:{company_code}"
            if dataset == "ACTUAL"
            else f"PROJECTION:{year}:{company_code}"
            if dataset == "PROJECTION"
            else f"{dataset}:{scenario}:{year}:{company_code}"
        )
        # meses com realizado (KSB1) não são tocados pela projeção
        closed_actual = (
            db.scalar(
                select(DatasetVersion.last_closed_period).where(
                    DatasetVersion.dataset_type == "ACTUAL",
                    DatasetVersion.is_current,
                    DatasetVersion.scope_key == f"ACTUAL:{year}:{company_code}",
                )
            )
            or 0
            if dataset == "PROJECTION"
            else 0
        )
        previous = db.scalar(
            select(DatasetVersion).where(
                DatasetVersion.dataset_type == dataset, DatasetVersion.scope_key == scope, DatasetVersion.is_current
            )
        )
        version = new_version(db, batch, dataset, scope, user_id, source)
        payload = []
        file_keys: set[tuple] = set()
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
            file_keys.add((d.get("branch_id"), d["cost_center_id"], d["account_id"]))
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
                    if value == 0 or int(month) <= closed_actual:
                        continue
                    entry = base | {"fiscal_year": year, "period": int(month), "amount": value}
                    if dataset in ("ACTUAL", "PROJECTION"):
                        entry |= {"source": source, "currency": "BRL"}
                    if dataset == "PROJECTION":
                        entry["projected"] = True
                    else:
                        entry |= {"scenario": scenario}
                    payload.append(entry)
                    max_period = max(max_period, int(month))
        carried = 0
        if dataset == "PROJECTION" and previous is not None:
            # a projeção só substitui as naturezas de conta que o arquivo cobre: a do CAPEX não apaga a do OPEX
            file_natures = set(
                db.scalars(
                    select(Account.nature).where(Account.id.in_({p["account_id"] for p in payload} or {-1})).distinct()
                )
            )
            columns = [c for c in model.__table__.columns if c.name not in ("id", "dataset_version_id")]
            old = db.execute(
                select(*[getattr(model, c.name) for c in columns])
                .join(Account, Account.id == model.account_id)
                .where(model.dataset_version_id == previous.id, Account.nature.not_in(file_natures or {"-"}))
            ).mappings()
            for row in old:
                payload.append(dict(row) | {"dataset_version_id": version.id})
                max_period = max(max_period, int(row["period"]))
                carried += 1
        if mode == "MERGE" and previous is not None:
            # mantém as combinações filial × CC × conta que não vieram neste arquivo
            columns = [c for c in model.__table__.columns if c.name not in ("id", "dataset_version_id")]
            for row in db.execute(select(*columns).where(model.dataset_version_id == previous.id)).mappings():
                if (row["branch_id"], row["cost_center_id"], row["account_id"]) in file_keys:
                    continue
                payload.append(dict(row) | {"dataset_version_id": version.id})
                max_period = max(max_period, int(row["period"]))
                carried += 1
        for start in range(0, len(payload), 5000):
            db.execute(insert(model), payload[start : start + 5000])
        version.row_count = len(payload)
        version.last_closed_period = max_period or None
        versions.append(
            {"scope": scope, "version": version.version_number, "entries": len(payload), "kept_from_previous": carried}
        )
        stats["entries"] += len(payload) - carried
        stats["entries_kept_from_previous"] += carried
    return dict(stats) | {"versions": versions}


# ------------------------------------------------------------------ pessoal


def load_employees(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    stats: dict = defaultdict(int)
    rows = _rows(db, batch.id, "EMPLOYEE")
    companies = sorted({r.data["company"] for r in rows})
    version = new_version(db, batch, "EMPLOYEES", "EMPLOYEES:" + ",".join(companies), user_id, "EXCEL")
    positions = {p.name.upper(): p.id for p in db.scalars(select(JobPosition))}
    pending_ccs: set[int] = set()
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
        attrs = dict((emp.attributes if emp is not None else None) or {})
        attrs.pop("pending_cc", None)
        if d.get("cc_from_position"):  # CC vazio na planilha, definido pelo cargo: confirmar em Apontamentos
            attrs["pending_cc"] = {"sector": d["cc_from_position"], "position": pos_name, "import_batch": batch.id}
            pending_ccs.add(d["cost_center_id"])
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
            "attributes": attrs or None,
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
    db.flush()
    stats["movements"] = load_personnel_actions(db, batch, user_id)
    if pending_ccs:
        stats["cost_center_from_position"] = sum(1 for r in rows if r.data.get("cc_from_position"))
        _open_personnel_budgets(db, pending_ccs, user_id)
    version.row_count = len(rows)
    return dict(stats)


def _open_personnel_budgets(db: Session, cc_ids: set[int], user_id: int | None) -> None:
    """Orçamentos de pessoal dos CCs com colaborador pendente passam a "em preenchimento" (aparecem em Apontamentos)."""
    from app.services import opex as opex_svc

    try:
        ctx = opex_svc.context(db)
    except opex_svc.OpexError:
        return
    if ctx.frozen:
        return
    for cc_id in cc_ids:
        sub = opex_svc.get_submission(db, ctx, cc_id, module="PERSONNEL")
        opex_svc.mark_in_progress(db, sub, ctx, user_id)
    db.flush()


def load_personnel_actions(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    """Ações do quadro (PROMOVER, REMOVER, INCLUIR) e vagas viram movimentações no orçamento de pessoal do CC.

    Substituem só as que vieram de importação anterior **para as pessoas deste arquivo** (e as vagas, nos CCs em que
    o arquivo traz vagas): o quadro de um CC pode chegar em vários arquivos (ex.: Controladoria e Contabilidade), e um
    não apaga as promoções do outro. As lançadas no sistema são preservadas."""
    from app.domain.workflow import EDITABLE
    from app.models import BudgetSubmission, PersonnelMovement
    from app.services import opex as opex_svc
    from app.services import personnel as personnel_svc

    all_rows = _rows(db, batch.id, "EMPLOYEE")
    rows = [r for r in all_rows if r.data.get("action") not in (None, "KEEP")]
    vacancies = _rows(db, batch.id, "VACANCY")
    if not all_rows and not vacancies:
        return {}
    try:
        ctx = opex_svc.context(db)
    except opex_svc.OpexError:
        return {"skipped": "sem ciclo orçamentário"}
    if ctx.frozen:
        return {"skipped": f"versão {ctx.version.label} congelada"}
    stats: dict = defaultdict(int)
    # movimentações vindas de planilha das pessoas deste arquivo (inclusive quem agora "mantém"): substituídas
    file_employee_ids = {
        emp_id
        for r in all_rows
        if (
            emp_id := db.scalar(
                select(Employee.id).where(
                    Employee.company_id == r.data["company_id"], Employee.registration == r.data["registration"]
                )
            )
        )
    }
    if file_employee_ids:
        for old, old_sub in db.execute(
            select(PersonnelMovement, BudgetSubmission)
            .join(BudgetSubmission, BudgetSubmission.id == PersonnelMovement.submission_id)
            .where(
                BudgetSubmission.version_id == ctx.version.id,
                PersonnelMovement.employee_id.in_(file_employee_ids),
            )
        ).all():
            if (old.attributes or {}).get("source") == "TEMPLATE" and old_sub.status in EDITABLE:
                db.delete(old)
                stats["replaced"] += 1
        db.flush()
    by_cc: dict[int, list[ImportRow]] = defaultdict(list)
    for row in rows + vacancies:
        if row.data.get("cost_center_id"):
            by_cc[row.data["cost_center_id"]].append(row)
        else:
            stats["without_cost_center"] += 1
    for cc_id, items in by_cc.items():
        sub = opex_svc.get_submission(db, ctx, cc_id, module="PERSONNEL")
        if sub.status not in EDITABLE or ctx.frozen:
            stats["cost_centers_locked"] += 1
            continue
        if any(r.record_type == "VACANCY" for r in items):  # vagas do arquivo substituem as vagas de planilha do CC
            for old in db.scalars(
                select(PersonnelMovement).where(
                    PersonnelMovement.submission_id == sub.id, PersonnelMovement.employee_id.is_(None)
                )
            ):
                if (old.attributes or {}).get("source") == "TEMPLATE":
                    db.delete(old)
                    stats["replaced"] += 1
            db.flush()
        for row in items:
            d = row.data
            try:
                if row.record_type == "VACANCY":
                    personnel_svc.save_hire(
                        db,
                        sub,
                        {
                            "position_name": d.get("new_position") or d.get("position") or d.get("name") or "Vaga",
                            "month": d.get("action_month") or 1,
                            "new_salary": d.get("new_salary") or d.get("salary"),
                            "contract_type_code": d.get("contract") or "CLT",
                            "reason": d.get("reason"),  # o template não traz justificativa: o gestor informa na tela
                            "source": "TEMPLATE",
                            "pending_ok": True,  # vaga sem salário entra pendente (Apontamentos)
                        },
                        user_id,
                    )
                    stats["hires"] += 1
                else:
                    emp = db.scalar(
                        select(Employee).where(
                            Employee.company_id == d["company_id"], Employee.registration == d["registration"]
                        )
                    )
                    personnel_svc.set_employee_movement(
                        db,
                        sub,
                        emp,
                        {
                            "type": d["action"],
                            "month": d.get("action_month"),
                            "new_salary": d.get("new_salary"),
                            "new_position": d.get("new_position"),
                            "source": "TEMPLATE",
                            "pending_ok": True,  # promoção sem novo salário entra pendente (Apontamentos)
                        },
                        user_id,
                    )
                    stats[d["action"].lower()] += 1
            except personnel_svc.PersonnelError as exc:
                stats["errors"] += 1
                stats.setdefault("messages", []).append(f"linha {row.row_number}: {exc}")
        opex_svc.mark_in_progress(db, sub, ctx, user_id)
        stats["cost_centers"] += 1
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


def _detail_id(db: Session, account_id: int, name: str | None) -> int | None:
    if not name:
        return None
    from app.models import AccountDetail

    return db.scalar(select(AccountDetail.id).where(AccountDetail.account_id == account_id, AccountDetail.name == name))


def _has_errors(db: Session, batch_id: int, cc_code: str) -> bool:
    rows = db.scalars(select(ImportRow).where(ImportRow.batch_id == batch_id, ImportRow.status == "ERROR")).all()
    return any((r.data or {}).get("cost_center") == cc_code for r in rows)


def _full_replace(batch: ImportBatch, cc_code: str) -> bool:
    """Planilha exportada pelo sistema: substitui todos os lançamentos do CC exportado (não só os de
    template). A prévia (`compare._full_replace_ok`) já barrou erros e versão divergente; aqui só se confere
    que o CC é o do marcador e que nenhuma linha dele ficou em erro."""
    export = ((batch.summary or {}).get("meta") or {}).get("system_export") or {}
    return export.get("cost_center") == cc_code


def _template_guard(db: Session, batch: ImportBatch) -> dict:
    """Template de gestor não é fonte de verdade de cadastros nem de realizado:
    - cadastros existentes (BD-Novo) não são alterados — só registros novos são criados;
    - a aba Realizado só é carregada se ainda não houver realizado daquela empresa/ano na base
      (a Controladoria atualiza o realizado pela importação própria)."""
    skipped = {"master_updates_ignored": 0, "actual_skipped": []}
    for row in _rows(db, batch.id):
        if row.record_type in ("BRANCH", "COST_CENTER", "ACCOUNT") and row.data.get("_action") == "UPDATE":
            row.status = "SKIPPED"
            skipped["master_updates_ignored"] += 1
    facts = _rows(db, batch.id, "FACT")
    if facts:
        scopes = {f"ACTUAL:{int(r.data['year'])}:{r.data['company']}" for r in facts}
        existing = set(
            db.scalars(
                select(DatasetVersion.scope_key).where(
                    DatasetVersion.dataset_type == "ACTUAL",
                    DatasetVersion.scope_key.in_(scopes),
                    DatasetVersion.is_current,
                )
            )
        )
        for row in facts:
            if f"ACTUAL:{int(row.data['year'])}:{row.data['company']}" in existing:
                row.status = "SKIPPED"
        skipped["actual_skipped"] = sorted(existing)
    db.flush()
    return skipped


def load_opex_template(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    """Cadastros novos → realizado (só se ainda não houver) → orçamento 2027, na mesma transação."""
    guard = _template_guard(db, batch)
    stats = {"master": load_master(db, batch, user_id)}
    if _rows(db, batch.id, "FACT"):
        stats["actual"] = load_financial(db, batch, user_id, dataset="ACTUAL")
    elif guard["actual_skipped"]:
        stats["actual"] = {"skipped": "realizado já carregado na base; não substituído pelo template"}
    stats["budget"] = load_budget_lines(db, batch, user_id)
    if guard["master_updates_ignored"]:
        stats["master"]["updates_ignored"] = guard["master_updates_ignored"]
    return stats


def load_budget_lines(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    """Linhas do orçamento vindas do template: substituem as que vieram de template antes (no mesmo CC);
    linhas digitadas no sistema são preservadas."""
    from app.domain.rules.common import normalize_months
    from app.domain.workflow import EDITABLE
    from app.models import BudgetLine, Company
    from app.services import opex as opex_svc

    rows = _rows(db, batch.id, "BUDGET_LINE") + _rows(db, batch.id, "TRAVEL")
    if not rows:
        return {}
    ctx = opex_svc.context(db)
    if ctx.frozen:
        return {"skipped": f"versão {ctx.version.label} congelada"}
    companies = {c.code: c.id for c in db.scalars(select(Company))}
    groups: dict[tuple, list[ImportRow]] = defaultdict(list)
    for row in rows:
        groups[(row.data["company"], row.data["cost_center"])].append(row)
    stats: dict = defaultdict(int)
    for (company, cc_code), items in groups.items():
        company_id = companies[company]
        cc = db.scalar(select(CostCenter).where(CostCenter.company_id == company_id, CostCenter.code == cc_code))
        sub = opex_svc.get_submission(db, ctx, cc.id)
        if sub.status not in EDITABLE or ctx.frozen:
            stats["cost_centers_locked"] += 1
            continue
        full_replace = _full_replace(batch, cc_code) and not _has_errors(db, batch.id, cc_code)
        for old in db.scalars(select(BudgetLine).where(BudgetLine.submission_id == sub.id)):
            # exportação do sistema: substitui lançamentos e viagens; eventos (calculados) ficam, porque a
            # planilha os traz só para leitura (aba "Eventos (leitura)")
            if (full_replace and old.line_type != "EVENT") or (old.attributes or {}).get("source") == "TEMPLATE":
                db.delete(old)
                stats["lines_replaced"] += 1
        total = Decimal("0")
        for row in items:
            d = row.data
            if row.record_type == "TRAVEL":
                for line in _travel_from_template(db, ctx, batch, row, sub, cc, company_id, user_id):
                    db.add(line)
                    total += line.total_amount
                    stats["lines_created"] += 1
                stats["trips"] += 1
                continue
            acc = db.scalar(select(Account).where(Account.code == d["account"]))
            branch_id = None
            if d.get("branch"):
                branch_id = db.scalar(
                    select(Branch.id).where(Branch.company_id == company_id, Branch.code == d["branch"])
                )
            line = BudgetLine(
                submission_id=sub.id,
                company_id=company_id,
                branch_id=branch_id,
                cost_center_id=cc.id,
                account_id=acc.id,
                package_id=acc.package_id,
                line_type="GENERIC",
                description=d.get("description"),
                supplier=d.get("supplier"),
                justification=d.get("justification"),
                contract_manager=d.get("contract_manager"),
                assumption=d.get("assumption"),
                account_detail_id=_detail_id(db, acc.id, d.get("account_detail")),
                attributes={"source": "TEMPLATE", "sheet": row.sheet, "row": row.row_number, "import_batch": batch.id},
                created_by=user_id,
                updated_by=user_id,
            )
            opex_svc._set_values(line, normalize_months({int(m): v for m, v in d["values"].items()}))
            db.add(line)
            total += line.total_amount
            stats["lines_created"] += 1
        opex_svc.mark_in_progress(db, sub, ctx, user_id)
        audit.record(
            db,
            user_id=user_id,
            action="IMPORT_BUDGET",
            entity_type="budget_submission",
            entity_id=sub.id,
            after={"lines": len(items), "total": str(total), "import_batch": batch.id},
        )
        stats["cost_centers"] += 1
    db.flush()
    return dict(stats)


def load_capex_template(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    """Cadastros novos → catálogo de ativos → solicitações CAPEX, na mesma transação."""
    guard = _template_guard(db, batch)
    stats = {"master": load_master(db, batch, user_id), "catalog": load_asset_catalog(db, batch)}
    if guard["master_updates_ignored"]:
        stats["master"]["updates_ignored"] = guard["master_updates_ignored"]
    stats["capex"] = load_capex_items(db, batch, user_id)
    return stats


def load_asset_catalog(db: Session, batch: ImportBatch) -> dict:
    from app.models import AssetClass, AssetItem

    stats: dict = defaultdict(int)
    classes = {c.name.upper(): c for c in db.scalars(select(AssetClass))}
    items = {i.name.upper(): i for i in db.scalars(select(AssetItem))}
    accounts = {a.code: a.id for a in db.scalars(select(Account))}
    for row in _rows(db, batch.id, "ASSET_ITEM"):
        d = row.data
        klass = classes.get(d["asset_class"].upper())
        if klass is None:
            klass = AssetClass(name=d["asset_class"], account_id=accounts.get(d.get("account") or ""))
            db.add(klass)
            db.flush()
            classes[klass.name.upper()] = klass
            stats["classes_created"] += 1
        elif klass.account_id is None and d.get("account") in accounts:
            klass.account_id = accounts[d["account"]]
        item = items.get(d["name"])
        if item is None:
            item = AssetItem(name=d["name"], asset_class_id=klass.id)
            db.add(item)
            items[d["name"]] = item
            stats["items_created"] += 1
        elif item.asset_class_id != klass.id:
            item.asset_class_id = klass.id
            stats["items_updated"] += 1
    db.flush()
    return dict(stats)


def load_capex_items(db: Session, batch: ImportBatch, user_id: int | None) -> dict:
    """Solicitações vindas do template substituem as que vieram de template antes (no mesmo CC);
    as cadastradas no sistema são preservadas. Linhas de projeto com o mesmo tipo e justificativa viram
    uma única solicitação com vários itens; aquisições avulsas, uma solicitação por linha."""
    from app.domain.workflow import EDITABLE
    from app.models import AssetItem, CapexProject, Company
    from app.services import capex as capex_svc
    from app.services import opex as opex_svc

    rows = _rows(db, batch.id, "CAPEX_ITEM")
    if not rows:
        return {}
    ctx = opex_svc.context(db)
    if ctx.frozen:
        return {"skipped": f"versão {ctx.version.label} congelada"}
    companies = {c.code: c.id for c in db.scalars(select(Company))}
    accounts = {a.code: a.id for a in db.scalars(select(Account))}
    assets = {i.name.upper(): i.id for i in db.scalars(select(AssetItem))}
    by_cc: dict[tuple, list[ImportRow]] = defaultdict(list)
    for row in rows:
        by_cc[(row.data["company"], row.data["cost_center"])].append(row)
    stats: dict = defaultdict(int)
    for (company, cc_code), cc_rows in by_cc.items():
        company_id = companies[company]
        cc = db.scalar(select(CostCenter).where(CostCenter.company_id == company_id, CostCenter.code == cc_code))
        sub = opex_svc.get_submission(db, ctx, cc.id, module="CAPEX")
        if sub.status not in EDITABLE or ctx.frozen:
            stats["cost_centers_locked"] += 1
            continue
        full_replace = _full_replace(batch, cc_code) and not _has_errors(db, batch.id, cc_code)
        for old in db.scalars(select(CapexProject).where(CapexProject.submission_id == sub.id)):
            if full_replace or (old.attributes or {}).get("source") == "TEMPLATE":
                db.delete(old)
                stats["requests_replaced"] += 1
        db.flush()
        groups: dict[tuple, list[ImportRow]] = defaultdict(list)
        for row in cc_rows:
            d = row.data
            if d.get("request"):  # planilha exportada pelo sistema: a coluna SOLICITAÇÃO preserva o agrupamento
                key = ("R", d["request"].strip().upper())
            elif d["is_project"]:
                key = ("P", d.get("branch"), d.get("project_type"), (d.get("justification") or "").strip().upper())
            else:
                key = ("A", row.row_number)
            groups[key].append(row)
        total = Decimal("0")
        for group in groups.values():
            first = group[0].data
            branch_id = None
            if first.get("branch"):
                branch_id = db.scalar(
                    select(Branch.id).where(Branch.company_id == company_id, Branch.code == first["branch"])
                )
            title = (first.get("request") or "").split(" · ", 1)[-1] or first.get("description") or first["item"]
            project = capex_svc.create_project(
                db,
                ctx,
                sub,
                {
                    "title": title[:200],
                    "is_project": first["is_project"],
                    "project_type_code": first.get("project_type"),
                    "branch_id": branch_id,
                    "description": first.get("description"),
                    "justification": first.get("justification"),
                },
                user_id,
                attributes={
                    "source": "TEMPLATE",
                    "sheet": group[0].sheet,
                    "rows": [r.row_number for r in group],
                    "import_batch": batch.id,
                },
            )
            for row in group:
                d = row.data
                item = capex_svc.add_item(
                    db,
                    ctx,
                    project,
                    {
                        "account_id": accounts[d["account"]],
                        "asset_item_id": assets.get((d["item"] or "").upper()),
                        "item_name": d["item"],
                        "description": d.get("description"),
                        "unit_value": Decimal(d["unit_value"]),
                        "quantity": Decimal(d["quantity"]),
                        "useful_life_months": d.get("useful_life_months"),
                        "values": d["values"],
                    },
                )
                total += item.total_value
                stats["items_created"] += 1
            stats["requests_created"] += 1
        audit.record(
            db,
            user_id=user_id,
            action="IMPORT_CAPEX",
            entity_type="budget_submission",
            entity_id=sub.id,
            after={"items": len(cc_rows), "total": str(total), "import_batch": batch.id},
        )
        stats["cost_centers"] += 1
    db.flush()
    return dict(stats)


def _travel_from_template(db, ctx, batch, row, sub, cc, company_id, user_id) -> list:
    """Uma viagem da planilha → até 3 linhas TRAVEL (passagem, diária, hospedagem) no mês de ida,
    com os valores calculados pela planilha; sem eles, recalcula pelas tarifas do ciclo."""
    import uuid

    from app.models import BudgetLine
    from app.services import opex as opex_svc

    d = row.data
    t = d["travel"]
    branch_id = None
    if d.get("branch"):
        branch_id = db.scalar(select(Branch.id).where(Branch.company_id == company_id, Branch.code == d["branch"]))
    attributes = {
        k: t.get(k)
        for k in ("trip_type", "job_level", "origin", "destination", "departure_month", "return_month", "days")
    } | {
        "purpose": t.get("purpose"),
        "warnings": [],
        "source": "TEMPLATE",
        "sheet": row.sheet,
        "row": row.row_number,
        "import_batch": batch.id,
    }
    common = {
        "submission_id": sub.id,
        "company_id": company_id,
        "branch_id": branch_id,
        "cost_center_id": cc.id,
        "description": t.get("purpose"),
        "created_by": user_id,
        "updated_by": user_id,
    }
    if d.get("amounts") is None:
        lines = opex_svc._travel_lines(db, ctx, {"travel": t}, common)
        for line in lines:
            line.attributes = (line.attributes or {}) | attributes
        return lines
    from app.domain.rules.opex import TRAVEL_TICKET_ACCOUNT, missing_fare_warning

    ticket = Decimal(d["amounts"].get(TRAVEL_TICKET_ACCOUNT, "0"))
    warning = missing_fare_warning(t.get("origin"), t.get("destination"), ticket)
    if warning:
        attributes["warnings"] = [warning]
    group = uuid.uuid4().hex[:12]
    lines = []
    for code, amount in d["amounts"].items():
        acc = db.scalar(select(Account).where(Account.code == code))
        line = BudgetLine(
            **common,
            account_id=acc.id,
            package_id=acc.package_id,
            line_type="TRAVEL",
            group_ref=group,
            attributes=attributes,
        )
        opex_svc._set_values(line, {t["departure_month"]: Decimal(amount)})
        lines.append(line)
    return lines


LOADERS = {
    "PROJECTION": load_financial,
    "OPEX_TEMPLATE": load_opex_template,
    "CAPEX_TEMPLATE": load_capex_template,
    "MASTER_DATA": load_master,
    "COST_CENTERS": load_master,
    "ACCOUNTS": load_master,
    "ACTUAL": load_financial,
    "REFERENCE_BUDGET": load_financial,
    "EMPLOYEES": load_employees,
    "MACRO_ASSUMPTIONS": load_macro,
}
