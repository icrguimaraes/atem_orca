from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, TimestampMixin


class BudgetSubmission(TimestampMixin, Base):
    """Unidade do workflow: versão × centro de custo × módulo."""

    __tablename__ = "budget_submissions"
    __table_args__ = (UniqueConstraint("version_id", "cost_center_id", "module"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("budget_versions.id", ondelete="CASCADE"), index=True)
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"), index=True)
    module: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consolidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BudgetLine(TimestampMixin, Base):
    __tablename__ = "budget_lines"
    __table_args__ = (Index("ix_budget_lines_dims", "submission_id", "account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("budget_submissions.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    package_id: Mapped[int | None] = mapped_column(ForeignKey("budget_packages.id"))
    account_detail_id: Mapped[int | None] = mapped_column(ForeignKey("account_details.id"))
    line_type: Mapped[str] = mapped_column(String(20), default="GENERIC")
    group_ref: Mapped[str | None] = mapped_column(String(40))  # agrupa linhas geradas juntas (ex.: viagem → 3 contas)
    description: Mapped[str | None] = mapped_column(Text)
    justification: Mapped[str | None] = mapped_column(Text)
    assumption: Mapped[str | None] = mapped_column(Text)
    supplier: Mapped[str | None] = mapped_column(String(200))
    contract_manager: Mapped[str | None] = mapped_column(String(200))
    attributes: Mapped[dict | None] = mapped_column(JSONB)
    total_amount: Mapped[Decimal] = mapped_column(Money, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    values: Mapped[list["BudgetLineValue"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="BudgetLineValue.month"
    )


class BudgetLineValue(Base):
    __tablename__ = "budget_line_values"

    line_id: Mapped[int] = mapped_column(ForeignKey("budget_lines.id", ondelete="CASCADE"), primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Money, default=0)


class AccountJustification(Base):
    __tablename__ = "account_justifications"

    submission_id: Mapped[int] = mapped_column(
        ForeignKey("budget_submissions.id", ondelete="CASCADE"), primary_key=True
    )
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class PackageReview(Base):
    """Validação GMD do gestor de pacote (Tipo 1 obrigatória, Tipo 2 consultiva)."""

    __tablename__ = "package_reviews"
    __table_args__ = (UniqueConstraint("submission_id", "package_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("budget_submissions.id", ondelete="CASCADE"))
    package_id: Mapped[int] = mapped_column(ForeignKey("budget_packages.id"))
    reviewer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    status: Mapped[str] = mapped_column(String(30), default="PENDING")
    comment: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("budget_submissions.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(30))
    to_status: Mapped[str] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(40))
    comment: Mapped[str | None] = mapped_column(Text)
    version_label: Mapped[str | None] = mapped_column(String(10))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class CapexProject(TimestampMixin, Base):
    __tablename__ = "capex_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("budget_submissions.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"))
    is_project: Mapped[bool] = mapped_column(default=False)
    project_type_code: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    justification: Mapped[str | None] = mapped_column(Text)
    expected_cost_reduction: Mapped[Decimal | None] = mapped_column(Money)
    expected_revenue: Mapped[Decimal | None] = mapped_column(Money)
    priority: Mapped[str | None] = mapped_column(String(30))
    budget_prev_year: Mapped[Decimal | None] = mapped_column(Money)
    observations: Mapped[str | None] = mapped_column(Text)

    items: Mapped[list["CapexItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class CapexItem(Base):
    __tablename__ = "capex_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("capex_projects.id", ondelete="CASCADE"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    asset_item_id: Mapped[int | None] = mapped_column(ForeignKey("asset_items.id"))
    item_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(Text)
    unit_value: Mapped[Decimal] = mapped_column(Money)
    quantity: Mapped[Decimal] = mapped_column(Money)
    total_value: Mapped[Decimal] = mapped_column(Money)
    useful_life_months: Mapped[int | None] = mapped_column(Integer)

    values: Mapped[list["CapexItemValue"]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class CapexItemValue(Base):
    __tablename__ = "capex_item_values"

    item_id: Mapped[int] = mapped_column(ForeignKey("capex_items.id", ondelete="CASCADE"), primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    amount: Mapped[Decimal] = mapped_column(Money, default=0)


class AssetClass(Base):
    __tablename__ = "asset_classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))


class AssetItem(Base):
    __tablename__ = "asset_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    asset_class_id: Mapped[int] = mapped_column(ForeignKey("asset_classes.id"))
