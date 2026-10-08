from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import DateTime, MetaData, Numeric, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

Money = Numeric(18, 2)
Rate = Numeric(12, 6)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map = {Decimal: Money}


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Role(StrEnum):
    ADMIN = "ADMIN"
    CONTROLLER = "CONTROLLER"
    MANAGER = "MANAGER"
    PACKAGE_MANAGER = "PACKAGE_MANAGER"
    HR = "HR"
    VIEWER = "VIEWER"


class Nature(StrEnum):
    OPEX = "OPEX"
    CAPEX = "CAPEX"
    PESSOAL = "PESSOAL"
    FINANCEIRO = "FINANCEIRO"
    CUSTO = "CUSTO"


class Module(StrEnum):
    OPEX = "OPEX"
    CAPEX = "CAPEX"
    PERSONNEL = "PERSONNEL"


class SubmissionStatus(StrEnum):
    DRAFT = "DRAFT"
    IN_PROGRESS = "IN_PROGRESS"
    SUBMITTED = "SUBMITTED"
    UNDER_REVIEW = "UNDER_REVIEW"
    ADJUSTMENT_REQUESTED = "ADJUSTMENT_REQUESTED"
    APPROVED = "APPROVED"
    CONSOLIDATED = "CONSOLIDATED"


class CycleStatus(StrEnum):
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    CLOSED = "CLOSED"


class VersionStatus(StrEnum):
    WORKING = "WORKING"
    FROZEN = "FROZEN"


class ImportStatus(StrEnum):
    UPLOADED = "UPLOADED"
    VALIDATING = "VALIDATING"
    VALIDATED = "VALIDATED"
    FAILED = "FAILED"
    CONFIRMED = "CONFIRMED"  # na fila para processamento
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"


class DatasetType(StrEnum):
    MASTER_DATA = "MASTER_DATA"
    COST_CENTERS = "COST_CENTERS"
    ACCOUNTS = "ACCOUNTS"
    ACTUAL = "ACTUAL"
    REFERENCE_BUDGET = "REFERENCE_BUDGET"
    EMPLOYEES = "EMPLOYEES"
    MACRO_ASSUMPTIONS = "MACRO_ASSUMPTIONS"
    OPEX_TEMPLATE = "OPEX_TEMPLATE"
    CAPEX_TEMPLATE = "CAPEX_TEMPLATE"
    PROJECTION = "PROJECTION"  # projeção do gestor para os meses que faltam do realizado (ex.: out–dez 2026)


class MovementType(StrEnum):
    KEEP = "KEEP"
    PROMOTION = "PROMOTION"
    SALARY_ADJUSTMENT = "SALARY_ADJUSTMENT"
    HIRE = "HIRE"
    TERMINATION = "TERMINATION"
    TRANSFER = "TRANSFER"
