from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Contratos PJ (confidencial; migração 0009): "Vê contratos PJ: Não / Da área / Todos" — dado pelo Administrador,
    # vale para qualquer perfil (um Administrador sem o acesso pode concedê-lo, mas não vê os dados antes disso)
    can_view_pj: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    can_view_all_pj: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    roles: Mapped[list["UserRole"]] = relationship(cascade="all, delete-orphan", lazy="selectin")
    scopes: Mapped[list["UserScope"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

    @property
    def role_codes(self) -> set[str]:
        return {r.role_code for r in self.roles}


class RoleDef(Base):
    __tablename__ = "roles"

    code: Mapped[str] = mapped_column(String(30), primary_key=True)
    name: Mapped[str] = mapped_column(String(100))


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_code: Mapped[str] = mapped_column(ForeignKey("roles.code"), primary_key=True)


class UserScope(Base):
    """Escopo adicional de acesso (além de ser gestor do CC)."""

    __tablename__ = "user_scopes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    cost_center_id: Mapped[int | None] = mapped_column(ForeignKey("cost_centers.id"))
    # área inteira (Tributos, Controladoria…): todos os CCs da área, inclusive os criados depois (08/10/2026)
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
