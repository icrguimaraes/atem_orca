from datetime import date
from decimal import Decimal

from sqlalchemy import Boolean, Date, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money, Rate, TimestampMixin


class ContractType(Base):
    """Regime de contratação. CLT aplica multiplicador; PJ não."""

    __tablename__ = "contract_types"

    code: Mapped[str] = mapped_column(String(20), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    apply_multiplier: Mapped[bool] = mapped_column(Boolean, default=True)
    default_multiplier: Mapped[Decimal] = mapped_column(Rate, default=Decimal("1"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class JobPosition(Base):
    __tablename__ = "job_positions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    level: Mapped[str | None] = mapped_column(String(60))


class Employee(TimestampMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (UniqueConstraint("company_id", "registration"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    registration: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(200))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id"))
    position_id: Mapped[int | None] = mapped_column(ForeignKey("job_positions.id"))
    contract_type_code: Mapped[str] = mapped_column(ForeignKey("contract_types.code"), default="CLT")
    base_salary: Mapped[Decimal] = mapped_column(Money)
    admission_date: Mapped[date | None] = mapped_column(Date)
    termination_date: Mapped[date | None] = mapped_column(Date)
    is_csc: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id"))
    # pendências da importação (ex.: "pending_cc": CC vazio na planilha, definido pelo cargo — confirmar)
    attributes: Mapped[dict | None] = mapped_column(JSONB)


class BenefitType(Base):
    __tablename__ = "benefit_types"

    code: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    calc_mode: Mapped[str] = mapped_column(String(20), default="FLAG")  # FLAG | PER_DEPENDENT | AMOUNT
    default_amount: Mapped[Decimal | None] = mapped_column(Money)


class EmployeeBenefit(Base):
    __tablename__ = "employee_benefits"

    employee_id: Mapped[int] = mapped_column(ForeignKey("employees.id", ondelete="CASCADE"), primary_key=True)
    benefit_code: Mapped[str] = mapped_column(ForeignKey("benefit_types.code"), primary_key=True)
    quantity: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)


class PersonnelMovement(TimestampMixin, Base):
    """Manter, promoção, reajuste, admissão, desligamento, transferência."""

    __tablename__ = "personnel_movements"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("budget_submissions.id", ondelete="CASCADE"), index=True)
    employee_id: Mapped[int | None] = mapped_column(ForeignKey("employees.id"))
    movement_type: Mapped[str] = mapped_column(String(30))
    effective_month: Mapped[int] = mapped_column(SmallInteger)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    position_id: Mapped[int | None] = mapped_column(ForeignKey("job_positions.id"))
    new_salary: Mapped[Decimal | None] = mapped_column(Money)
    contract_type_code: Mapped[str | None] = mapped_column(ForeignKey("contract_types.code"))
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id"))
    multiplier_override: Mapped[Decimal | None] = mapped_column(Rate)
    severance_cost: Mapped[Decimal | None] = mapped_column(Money)
    reason: Mapped[str | None] = mapped_column(Text)
    # vaga: cargo/descrição livre; origem (TEMPLATE/SYSTEM), linha do arquivo; CC de destino da transferência
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    target_cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class PersonnelScenario(TimestampMixin, Base):
    __tablename__ = "personnel_scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("budget_cycles.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(120))
    is_baseline: Mapped[bool] = mapped_column(Boolean, default=False)
    salary_adjustment_pct: Mapped[Decimal] = mapped_column(Rate, default=Decimal("0"))
    adjustment_month: Mapped[int] = mapped_column(SmallInteger, default=1)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class ScenarioMultiplier(Base):
    __tablename__ = "scenario_multipliers"

    scenario_id: Mapped[int] = mapped_column(ForeignKey("personnel_scenarios.id", ondelete="CASCADE"), primary_key=True)
    contract_type_code: Mapped[str] = mapped_column(ForeignKey("contract_types.code"), primary_key=True)
    multiplier: Mapped[Decimal] = mapped_column(Rate)
