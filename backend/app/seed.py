"""Seed idempotente: `python -m app.seed`. Cria/atualiza domínios sem sobrescrever edições de cadastro."""

import logging
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import seed_data as S
from app.config import get_settings
from app.core.security import hash_password
from app.db import SessionLocal
from app.models import (
    Account,
    AccountDetail,
    AssetClass,
    BenefitType,
    Branch,
    BudgetCycle,
    BudgetPackage,
    BudgetVersion,
    Company,
    ContractType,
    CycleParameter,
    LookupValue,
    PackageManager,
    PersonnelScenario,
    RoleDef,
    ScenarioMultiplier,
    TravelRate,
    User,
    UserRole,
)

log = logging.getLogger(__name__)


def _get(db: Session, model, **keys):
    return db.scalar(select(model).filter_by(**keys))


def seed(db: Session, *, fiscal_year: int = 2027) -> None:
    for code, name in S.ROLES.items():
        if db.get(RoleDef, code) is None:
            db.add(RoleDef(code=code, name=name))
    for code, name, short in S.COMPANIES:
        if _get(db, Company, code=code) is None:
            db.add(Company(code=code, name=name, short_name=short))
    db.flush()
    atem = _get(db, Company, code="1001")
    for code, name, uf in S.BRANCHES:
        if _get(db, Branch, company_id=atem.id, code=code) is None:
            db.add(Branch(company_id=atem.id, code=code, name=name, uf=uf))

    packages = {}
    for code, name, roman, ptype, nature, form, order in S.PACKAGES:
        pkg = _get(db, BudgetPackage, code=code)
        if pkg is None:
            pkg = BudgetPackage(
                code=code, name=name, roman=roman, package_type=ptype, nature=nature, form_type=form, sort_order=order
            )
            db.add(pkg)
        packages[name] = pkg
    db.flush()
    from app.imports.loaders import infer_nature

    accounts = {}
    for code, name, dre, pkg_name, detail in S.ACCOUNTS:
        acc = _get(db, Account, code=code)
        if acc is None:
            pkg = packages[pkg_name]
            nature = "PESSOAL" if pkg.code == "PESSOAS" else infer_nature(code, dre, pkg_name)
            acc = Account(code=code, name=name, dre_group=dre, nature=nature, package_id=pkg.id)
            db.add(acc)
            db.flush()
        accounts[code] = acc
        if detail and _get(db, AccountDetail, account_id=acc.id, name=detail) is None:
            db.add(AccountDetail(account_id=acc.id, name=detail))
    for name, account_code in S.ASSET_CLASSES:
        if _get(db, AssetClass, name=name) is None:
            db.add(AssetClass(name=name, account_id=accounts[account_code].id))

    for domain, code, label, order, extra in S.LOOKUPS:
        if _get(db, LookupValue, domain=domain, code=code) is None:
            db.add(LookupValue(domain=domain, code=code, label=label, sort_order=order, extra=extra))

    for code, name, apply, mult in S.CONTRACT_TYPES:
        if db.get(ContractType, code) is None:
            db.add(ContractType(code=code, name=name, apply_multiplier=apply, default_multiplier=Decimal(mult)))
    for code, name, account_code, mode in S.BENEFIT_TYPES:
        if db.get(BenefitType, code) is None:
            account_id = accounts[account_code].id if account_code else None
            db.add(BenefitType(code=code, name=name, account_id=account_id, calc_mode=mode))
    db.flush()

    cycle = _get(db, BudgetCycle, fiscal_year=fiscal_year)
    if cycle is None:
        cycle = BudgetCycle(
            fiscal_year=fiscal_year,
            name=f"Orçamento {fiscal_year}",
            status="DRAFT",
            actual_reference_year=fiscal_year - 1,
            opex_deadline=date(2026, 10, 9) if fiscal_year == 2027 else None,
            capex_deadline=date(2026, 10, 15) if fiscal_year == 2027 else None,
        )
        db.add(cycle)
        db.flush()
        db.add(BudgetVersion(cycle_id=cycle.id, major=1, minor=0, status="WORKING", reason="Versão inicial"))
        for key, (value, desc) in S.CYCLE_PARAMETERS.items():
            db.add(CycleParameter(cycle_id=cycle.id, key=key, value=value, description=desc))
        for rate_type, trip, job, value in S.TRAVEL_RATES:
            db.add(
                TravelRate(
                    cycle_id=cycle.id, rate_type=rate_type, trip_type=trip, job_level=job, daily_amount=Decimal(value)
                )
            )
        companies = {c.code: c.id for c in db.scalars(select(Company))}
        by_code = {p.code: p for p in packages.values()}
        for pkg_code, name, company_code, label in S.PACKAGE_MANAGERS:
            db.add(
                PackageManager(
                    cycle_id=cycle.id,
                    package_id=by_code[pkg_code].id,
                    manager_name=name,
                    company_id=companies.get(company_code),
                    scope_label=label,
                )
            )
        baseline = PersonnelScenario(
            cycle_id=cycle.id, name="Base", is_baseline=True, salary_adjustment_pct=Decimal("0.05"), adjustment_month=1
        )
        db.add(baseline)
        db.flush()
        for code, _, _apply, mult in S.CONTRACT_TYPES:
            db.add(ScenarioMultiplier(scenario_id=baseline.id, contract_type_code=code, multiplier=Decimal(mult)))

    # parâmetros novos chegam também aos ciclos já existentes (sem sobrescrever valores alterados)
    existing = set(db.scalars(select(CycleParameter.key).where(CycleParameter.cycle_id == cycle.id)))
    for key, (value, desc) in S.CYCLE_PARAMETERS.items():
        if key not in existing:
            db.add(CycleParameter(cycle_id=cycle.id, key=key, value=value, description=desc))

    settings = get_settings()
    if settings.admin_password and _get(db, User, email=settings.admin_email.lower()) is None:
        admin = User(
            email=settings.admin_email.lower(),
            name=settings.admin_name,
            password_hash=hash_password(settings.admin_password),
        )
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_code="ADMIN"))
    db.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    with SessionLocal() as db:
        seed(db)
    log.info("Seed concluído")


if __name__ == "__main__":
    main()
