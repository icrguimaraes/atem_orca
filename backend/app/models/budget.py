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


class FindingReview(Base):
    """Apontamento analisado na página Apontamentos: corrigido ali (CORRECTED) ou mantido com justificativa (KEPT).

    Registro da análise para o relatório; um aviso mantido sai da lista de pendentes da versão."""

    __tablename__ = "finding_reviews"

    id: Mapped[int] = mapped_column(primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey("budget_submissions.id", ondelete="CASCADE"), index=True)
    finding_key: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(40))
    action: Mapped[str] = mapped_column(String(20))  # CORRECTED | KEPT
    severity: Mapped[str] = mapped_column(String(20))
    subject: Mapped[str] = mapped_column(String(300))
    message: Mapped[str] = mapped_column(Text)
    note: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)


class BudgetQuestion(Base):
    """Pergunta sobre o orçamento (defesa do orçamento): quem analisa (ex.: VP) questiona uma linha do Painel — área,
    setor, pacote, conta ou CC — e o gestor da área responde. OPEN → ANSWERED → CLOSED (quem perguntou encerra ou
    pergunta de novo). `scope` guarda o recorte do Painel (filtros e linha) para reabrir o "por quê?" no mesmo ponto."""

    __tablename__ = "budget_questions"
    __table_args__ = (Index("ix_budget_questions_version_status", "version_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("budget_versions.id", ondelete="CASCADE"))
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"), index=True)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"))
    subject: Mapped[str] = mapped_column(String(300))
    scope: Mapped[dict | None] = mapped_column(JSONB)
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="OPEN")
    asked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    answer: Mapped[str | None] = mapped_column(Text)
    answered_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    attributes: Mapped[dict | None] = mapped_column(JSONB)  # origem (template/sistema), linha do arquivo
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    items: Mapped[list["CapexItem"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="CapexItem.id"
    )


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

    values: Mapped[list["CapexItemValue"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="CapexItemValue.month"
    )


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


class BudgetSnapshotLine(Base):
    """Fotografia da versão congelada: orçamento consolidado (OPEX, CAPEX e Pessoal) por chave × mês.

    Gravada ao congelar; a partir daí os relatórios da versão leem só daqui (imutável, mesmo que o
    quadro de pessoal ou os cadastros mudem depois)."""

    __tablename__ = "budget_snapshot_lines"
    __table_args__ = (Index("ix_budget_snapshot_lines_version", "version_id", "module"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    version_id: Mapped[int] = mapped_column(ForeignKey("budget_versions.id", ondelete="CASCADE"))
    module: Mapped[str] = mapped_column(String(20))
    company_code: Mapped[str] = mapped_column(String(10))
    branch_code: Mapped[str | None] = mapped_column(String(10))
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"))
    cost_center_code: Mapped[str] = mapped_column(String(20))
    cost_center_name: Mapped[str | None] = mapped_column(String(200))
    account_code: Mapped[str] = mapped_column(String(20))
    account_name: Mapped[str | None] = mapped_column(String(200))
    package: Mapped[str | None] = mapped_column(String(120))
    values: Mapped[list] = mapped_column(JSONB)  # 12 valores (texto decimal)
    total: Mapped[Decimal] = mapped_column(Money, default=0)
