from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money


class DatasetVersion(Base):
    """Versão publicada de um conjunto de dados importado (Importação → Versão → Data → Usuário → Fonte)."""

    __tablename__ = "dataset_versions"
    __table_args__ = (Index("ix_dataset_versions_scope", "dataset_type", "scope_key", "is_current"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_type: Mapped[str] = mapped_column(String(30))
    scope_key: Mapped[str] = mapped_column(String(80))  # ex.: ACTUAL:2026, REFERENCE_BUDGET:ORC:2026
    version_number: Mapped[int] = mapped_column(Integer)
    import_batch_id: Mapped[int | None] = mapped_column(ForeignKey("import_batches.id"))
    source: Mapped[str] = mapped_column(String(30), default="EXCEL")
    is_current: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    last_closed_period: Mapped[int | None] = mapped_column(SmallInteger)


class ActualEntry(Base):
    """Realizado no grão de documento (estrutura SAP/KSB1) ou agregado mensal (planilha)."""

    __tablename__ = "actual_entries"
    __table_args__ = (
        Index("ix_actual_dims", "dataset_version_id", "fiscal_year", "cost_center_id", "account_id", "period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    fiscal_year: Mapped[int] = mapped_column(SmallInteger)
    period: Mapped[int] = mapped_column(SmallInteger)  # 1..12
    posting_date: Mapped[date | None] = mapped_column(Date)
    document_number: Mapped[str | None] = mapped_column(String(30))
    document_type: Mapped[str | None] = mapped_column(String(10))
    vendor_code: Mapped[str | None] = mapped_column(String(30))
    vendor_name: Mapped[str | None] = mapped_column(String(200))
    text: Mapped[str | None] = mapped_column(Text)
    currency: Mapped[str] = mapped_column(String(3), default="BRL")
    amount: Mapped[Decimal] = mapped_column(Money)
    source: Mapped[str] = mapped_column(String(20), default="EXCEL")
    # linha veio da projeção do gestor (meses sem KSB1): aparece em vermelho no Painel
    projected: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class ReferenceBudgetEntry(Base):
    """Orçamentos de anos anteriores (ex.: Orçamento 2026) para comparação."""

    __tablename__ = "reference_budget_entries"
    __table_args__ = (
        Index("ix_refbudget_dims", "dataset_version_id", "fiscal_year", "cost_center_id", "account_id", "period"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_version_id: Mapped[int] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    scenario: Mapped[str] = mapped_column(String(20), default="ORC")
    fiscal_year: Mapped[int] = mapped_column(SmallInteger)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"))
    branch_id: Mapped[int | None] = mapped_column(ForeignKey("branches.id"))
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))
    period: Mapped[int] = mapped_column(SmallInteger)
    amount: Mapped[Decimal] = mapped_column(Money)


class MacroAssumption(Base):
    __tablename__ = "macro_assumptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_version_id: Mapped[int | None] = mapped_column(ForeignKey("dataset_versions.id", ondelete="CASCADE"))
    category: Mapped[str] = mapped_column(String(40), default="MACRO")  # MACRO | NEGOCIO
    indicator: Mapped[str] = mapped_column(String(120))
    segment: Mapped[str | None] = mapped_column(String(80))
    source: Mapped[str | None] = mapped_column(String(300))
    year: Mapped[int] = mapped_column(SmallInteger)
    value: Mapped[Decimal] = mapped_column(Numeric(24, 10))
    reference_date: Mapped[date | None] = mapped_column(Date)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
