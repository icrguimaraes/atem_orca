from sqlalchemy import Boolean, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.models.base import Base, TimestampMixin


class Company(TimestampMixin, Base):
    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(10), unique=True)  # código SAP (1001 = ATEM)
    name: Mapped[str] = mapped_column(String(200))
    short_name: Mapped[str | None] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Branch(TimestampMixin, Base):
    """Filial / local de negócios SAP."""

    __tablename__ = "branches"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(10))
    name: Mapped[str] = mapped_column(String(200))
    uf: Mapped[str | None] = mapped_column(String(2))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(lazy="joined")


class Department(Base):
    __tablename__ = "departments"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)


class Area(Base):
    __tablename__ = "areas"
    __table_args__ = (UniqueConstraint("name", "department_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))

    @validates("name")
    def _upper_name(self, _key, value):
        """Nomes sempre em MAIÚSCULAS (10/10/2026), venham da tela, da importação ou do seed."""
        return value.strip().upper() if isinstance(value, str) else value

    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))


class CostCenter(TimestampMixin, Base):
    __tablename__ = "cost_centers"
    __table_args__ = (UniqueConstraint("company_id", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("companies.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(200))

    @validates("name")
    def _upper_name(self, _key, value):
        """Nomes sempre em MAIÚSCULAS (10/10/2026), venham da tela, da importação ou do seed."""
        return value.strip().upper() if isinstance(value, str) else value

    manager_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    manager_name: Mapped[str | None] = mapped_column(String(200))
    department_id: Mapped[int | None] = mapped_column(ForeignKey("departments.id"))
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id"))
    is_csc: Mapped[bool] = mapped_column(Boolean, default=False)
    is_backoffice: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(lazy="joined")
    # co-gestores (19/11/2026): além do gestor principal (`manager_user_id`), outras pessoas com o acesso de gestor
    co_manager_links: Mapped[list["CostCenterManager"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin", order_by="CostCenterManager.user_id"
    )
    __audit_extra__ = ("co_manager_user_ids",)

    @property
    def co_manager_user_ids(self) -> list[int]:
        return [link.user_id for link in self.co_manager_links]

    @co_manager_user_ids.setter
    def co_manager_user_ids(self, ids: list[int] | None) -> None:
        wanted = [i for i in dict.fromkeys(ids or []) if i != self.manager_user_id]
        keep = [link for link in self.co_manager_links if link.user_id in wanted]
        have = {link.user_id for link in keep}
        self.co_manager_links = keep + [CostCenterManager(user_id=i) for i in wanted if i not in have]

    def is_managed_by(self, user_id: int) -> bool:
        """Gestor principal ou co-gestor."""
        return self.manager_user_id == user_id or user_id in self.co_manager_user_ids


class CostCenterManager(Base):
    """Co-gestor de um centro de custo (mesmo acesso do gestor principal)."""

    __tablename__ = "cost_center_managers"

    cost_center_id: Mapped[int] = mapped_column(ForeignKey("cost_centers.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)


class BudgetPackage(TimestampMixin, Base):
    """Pacote GMD (Gestão Matricial de Despesas)."""

    __tablename__ = "budget_packages"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    roman: Mapped[str | None] = mapped_column(String(6))
    package_type: Mapped[int] = mapped_column(Integer, default=2)  # 1 = validação obrigatória; 2 = consultivo
    nature: Mapped[str] = mapped_column(String(20), default="OPEX")
    form_type: Mapped[str] = mapped_column(String(20), default="GENERIC")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Account(TimestampMixin, Base):
    """Conta contábil (conta do razão). Não é amarrada a um único CC."""

    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    dre_group: Mapped[str | None] = mapped_column(String(100))
    nature: Mapped[str] = mapped_column(String(20), default="OPEX")
    package_id: Mapped[int | None] = mapped_column(ForeignKey("budget_packages.id"), index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    package: Mapped[BudgetPackage | None] = relationship(lazy="joined")


class AccountDetail(Base):
    """'Detalhamento' (ex.: DTI: Links de Internet → 6010301002)."""

    __tablename__ = "account_details"
    __table_args__ = (UniqueConstraint("account_id", "name"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(200))


class PackageManager(Base):
    """Gestor de pacote por ciclo (e opcionalmente por empresa: Infra ATEM/REAM/NAVE)."""

    __tablename__ = "package_managers"

    id: Mapped[int] = mapped_column(primary_key=True)
    cycle_id: Mapped[int] = mapped_column(ForeignKey("budget_cycles.id", ondelete="CASCADE"), index=True)
    package_id: Mapped[int] = mapped_column(ForeignKey("budget_packages.id"))
    company_id: Mapped[int | None] = mapped_column(ForeignKey("companies.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    manager_name: Mapped[str] = mapped_column(String(200))
    scope_label: Mapped[str | None] = mapped_column(String(100))
