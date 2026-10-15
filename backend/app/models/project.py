"""Projetos (14/10/2026): cadastro, cronograma, riscos e diário de cada projeto (ex.: 600561 TDC, 600801 Reforma
Tributária). Os números não são digitados: vêm do que já está no sistema pelos vínculos — realizado/projeção e orçado
de referência por centro de custo × conta (`ProjectFactKey`) e orçamento do ciclo pelas solicitações de CAPEX
(`ProjectCapexLink`, CC + código da solicitação, resolvido na versão vigente). O orçamento em si nunca é alterado."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, ForeignKey, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, Money, TimestampMixin

PROJECT_STATUS = ("PLANNED", "IN_PROGRESS", "PAUSED", "DONE", "CANCELLED")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH")
RISK_STATUS = ("OPEN", "MITIGATED", "CLOSED")


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(30), unique=True)  # ordem/PEP do SAP (ex.: 600561)
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="IN_PROGRESS")
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"))  # CC responsável
    owner_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))  # responsável (vê e edita)
    owner_name: Mapped[str | None] = mapped_column(String(200))
    sponsor: Mapped[str | None] = mapped_column(String(200))
    start_planned: Mapped[date | None] = mapped_column(Date)
    end_planned: Mapped[date | None] = mapped_column(Date)
    start_actual: Mapped[date | None] = mapped_column(Date)
    end_actual: Mapped[date | None] = mapped_column(Date)
    objective: Mapped[str | None] = mapped_column(Text)
    scope: Mapped[str | None] = mapped_column(Text)
    benefits: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))

    fact_keys: Mapped[list["ProjectFactKey"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ProjectFactKey.id"
    )
    capex_links: Mapped[list["ProjectCapexLink"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ProjectCapexLink.id"
    )
    milestones: Mapped[list["ProjectMilestone"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ProjectMilestone.id"
    )
    risks: Mapped[list["ProjectRisk"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ProjectRisk.id"
    )
    links: Mapped[list["ProjectLink"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ProjectLink.id"
    )
    updates: Mapped[list["ProjectUpdate"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ProjectUpdate.id"
    )
    budget: Mapped[list["ProjectBudgetEntry"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="ProjectBudgetEntry.id"
    )


class ProjectFactKey(Base):
    """Centro de custo × conta cujos lançamentos (realizado, projeção, orçado de referência) são do projeto."""

    __tablename__ = "project_fact_keys"
    __table_args__ = (UniqueConstraint("project_id", "cost_center_id", "account_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"))
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"))


class ProjectCapexLink(Base):
    """Solicitação de CAPEX do ciclo (CC + código "CPX-001") que é o orçamento do projeto; a versão é a vigente."""

    __tablename__ = "project_capex_links"
    __table_args__ = (UniqueConstraint("project_id", "cost_center_id", "capex_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id"))
    capex_code: Mapped[str] = mapped_column(String(20))


class ProjectMilestone(Base):
    __tablename__ = "project_milestones"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    due_date: Mapped[date | None] = mapped_column(Date)
    done_date: Mapped[date | None] = mapped_column(Date)
    progress_pct: Mapped[int] = mapped_column(SmallInteger, default=0)
    owner_name: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)


class ProjectRisk(Base):
    __tablename__ = "project_risks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    description: Mapped[str] = mapped_column(Text)
    impact: Mapped[str] = mapped_column(String(10), default="MEDIUM")
    probability: Mapped[str] = mapped_column(String(10), default="MEDIUM")
    mitigation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10), default="OPEN")


class ProjectLink(Base):
    """Documentos e links do projeto (contrato, cronograma, pasta no SharePoint…)."""

    __tablename__ = "project_links"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(String(1000))


class ProjectUpdate(Base):
    """Diário do projeto: atualizações de status escritas pela equipe (com autor e data)."""

    __tablename__ = "project_updates"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    text: Mapped[str] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectBudgetEntry(Base):
    """Orçado do próprio projeto por ano × mês (15/10/2026): o orçamento aprovado do projeto, que pode começar antes do
    ano de referência (TDC aprovado em dez/2025). Fica só no projeto — não entra no orçado do Painel. Quando existe,
    substitui o orçado de referência dos vínculos CC × conta nos números do projeto."""

    __tablename__ = "project_budget_entries"
    __table_args__ = (UniqueConstraint("project_id", "fiscal_year", "period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    fiscal_year: Mapped[int] = mapped_column(SmallInteger)
    period: Mapped[int] = mapped_column(SmallInteger)  # 1..12
    amount: Mapped[Decimal] = mapped_column(Money)
    source: Mapped[str | None] = mapped_column(String(300))  # de onde veio (planilha da área, data)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
