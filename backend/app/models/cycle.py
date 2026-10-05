from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money, TimestampMixin


class BudgetCycle(TimestampMixin, Base):
    __tablename__ = "budget_cycles"

    id: Mapped[int] = mapped_column(primary_key=True)
    fiscal_year: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")
    actual_reference_year: Mapped[int] = mapped_column(Integer)
    opex_deadline: Mapped[date | None] = mapped_column(Date)
    capex_deadline: Mapped[date | None] = mapped_column(Date)
    personnel_deadline: Mapped[date | None] = mapped_column(Date)


class CycleParameter(Base):
    """Parâmetros do ciclo (limites de alerta, fatores de cálculo...)."""

    __tablename__ = "cycle_parameters"

    cycle_id: Mapped[int] = mapped_column(ForeignKey("budget_cycles.id", ondelete="CASCADE"), primary_key=True)
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB)
    description: Mapped[str | None] = mapped_column(Text)


class LookupValue(Base):
    """Listas de domínio parametrizáveis (tipo de viagem, cargo, tipo de obra, tipo de projeto CAPEX...)."""

    __tablename__ = "lookup_values"
    __table_args__ = (UniqueConstraint("domain", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    domain: Mapped[str] = mapped_column(String(40), index=True)
    code: Mapped[str] = mapped_column(String(80))
    label: Mapped[str] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict | None] = mapped_column(JSONB)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class TravelFare(Base):
    """Matriz de passagens (ida e volta) destino × origem."""

    __tablename__ = "travel_fares"
    __table_args__ = (UniqueConstraint("cycle_id", "origin", "destination"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("budget_cycles.id", ondelete="CASCADE"))
    origin: Mapped[str] = mapped_column(String(60))
    destination: Mapped[str] = mapped_column(String(60))
    trip_type: Mapped[str] = mapped_column(String(30))
    round_trip_amount: Mapped[Decimal] = mapped_column(Money)


class TravelRate(Base):
    """Valores diários por tipo de viagem × cargo (hospedagem, diária, aluguel de veículo)."""

    __tablename__ = "travel_rates"
    __table_args__ = (UniqueConstraint("cycle_id", "rate_type", "trip_type", "job_level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("budget_cycles.id", ondelete="CASCADE"))
    rate_type: Mapped[str] = mapped_column(String(20))  # LODGING | PER_DIEM | CAR_RENTAL
    trip_type: Mapped[str] = mapped_column(String(30))
    job_level: Mapped[str] = mapped_column(String(60))
    daily_amount: Mapped[Decimal] = mapped_column(Money)


class BudgetVersion(Base):
    __tablename__ = "budget_versions"
    __table_args__ = (UniqueConstraint("cycle_id", "major", "minor"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("budget_cycles.id", ondelete="CASCADE"), index=True)
    major: Mapped[int] = mapped_column(Integer, default=1)
    minor: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="WORKING")
    parent_version_id: Mapped[int | None] = mapped_column(ForeignKey("budget_versions.id"))
    reason: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def label(self) -> str:
        return f"{self.major}.{self.minor}"
