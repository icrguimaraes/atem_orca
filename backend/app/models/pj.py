"""Contratos PJ (pessoa jurídica): cadastro confidencial, visível só a quem tem `users.can_view_pj`."""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, Money, TimestampMixin


class PjContract(TimestampMixin, Base):
    """Nada é obrigatório (migração 0011): campos importantes vazios viram pendência ("Falta preencher")."""

    __tablename__ = "pj_contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))  # pessoa
    company_name: Mapped[str | None] = mapped_column(String(200))  # razão social
    cnpj: Mapped[str | None] = mapped_column(String(14), index=True)  # sem pontuação (sem bloqueio de duplicidade)
    role: Mapped[str | None] = mapped_column(String(150))  # função (texto livre)
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"), index=True)
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(30))
    monthly_value: Mapped[Decimal | None] = mapped_column(Money)
    annual_bonus: Mapped[Decimal | None] = mapped_column(Money)
    start_date: Mapped[date | None] = mapped_column(Date)  # admissão
    end_date: Mapped[date | None] = mapped_column(Date)  # término ("encerrar" = informar)
    notes: Mapped[str | None] = mapped_column(Text)
    photo_blurred: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)  # exclusão lógica
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class PjPhoto(Base):
    """Foto do contratado (JPEG/PNG/WebP até 400 KB; a tela recorta e grava JPEG 280×280)."""

    __tablename__ = "pj_photos"

    contract_id: Mapped[int] = mapped_column(ForeignKey("pj_contracts.id", ondelete="CASCADE"), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    mime: Mapped[str] = mapped_column(String(30), default="image/jpeg")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
