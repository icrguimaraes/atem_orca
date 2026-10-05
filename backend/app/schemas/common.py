from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------- auth / usuários


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(ORM):
    id: int
    email: str
    name: str
    is_active: bool
    roles: list[str] = []
    last_login_at: datetime | None = None

    @classmethod
    def build(cls, user) -> "UserOut":
        return cls(
            id=user.id,
            email=user.email,
            name=user.name,
            is_active=user.is_active,
            roles=sorted(user.role_codes),
            last_login_at=user.last_login_at,
        )


class UserCreate(BaseModel):
    email: EmailStr
    name: str = Field(min_length=2)
    password: str = Field(min_length=8)
    roles: list[str] = ["MANAGER"]


class UserUpdate(BaseModel):
    name: str | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8)


class ScopeIn(BaseModel):
    company_id: int | None = None
    cost_center_id: int | None = None


# ---------------------------------------------------------------- cadastros


class CompanyIn(BaseModel):
    code: str = Field(pattern=r"^\d{4}$")
    name: str
    short_name: str | None = None
    is_active: bool = True


class CompanyOut(CompanyIn, ORM):
    id: int


class BranchIn(BaseModel):
    company_id: int
    code: str = Field(pattern=r"^\d{1,10}$")
    name: str
    uf: str | None = Field(default=None, max_length=2)
    is_active: bool = True


class BranchOut(BranchIn, ORM):
    id: int


class CostCenterIn(BaseModel):
    company_id: int
    code: str = Field(pattern=r"^\d{1,20}$")
    name: str
    manager_user_id: int | None = None
    manager_name: str | None = None
    department_id: int | None = None
    area_id: int | None = None
    is_csc: bool = False
    is_backoffice: bool = False
    is_active: bool = True


class CostCenterOut(CostCenterIn, ORM):
    id: int


class PackageIn(BaseModel):
    code: str
    name: str
    roman: str | None = None
    package_type: int = Field(ge=1, le=2)
    nature: str = "OPEX"
    form_type: str = "GENERIC"
    sort_order: int = 0
    is_active: bool = True


class PackageOut(PackageIn, ORM):
    id: int


class AccountIn(BaseModel):
    code: str = Field(pattern=r"^\d{4,20}$")
    name: str
    dre_group: str | None = None
    nature: str = "OPEX"
    package_id: int | None = None
    is_active: bool = True


class AccountOut(AccountIn, ORM):
    id: int


class AccountDetailIn(BaseModel):
    account_id: int
    name: str


class AccountDetailOut(AccountDetailIn, ORM):
    id: int


class LookupIn(BaseModel):
    domain: str
    code: str
    label: str
    sort_order: int = 0
    extra: dict | None = None
    is_active: bool = True


class LookupOut(LookupIn, ORM):
    id: int


class ContractTypeIn(BaseModel):
    code: str
    name: str
    apply_multiplier: bool
    default_multiplier: Decimal = Field(gt=0)
    is_active: bool = True


class ContractTypeOut(ContractTypeIn, ORM):
    pass


class NamedIn(BaseModel):
    name: str
    department_id: int | None = None


class NamedOut(ORM):
    id: int
    name: str


class PackageManagerIn(BaseModel):
    cycle_id: int
    package_id: int
    company_id: int | None = None
    user_id: int | None = None
    manager_name: str
    scope_label: str | None = None


class PackageManagerOut(PackageManagerIn, ORM):
    id: int


# ---------------------------------------------------------------- ciclo


class CycleIn(BaseModel):
    fiscal_year: int = Field(ge=2020, le=2100)
    name: str
    actual_reference_year: int
    opex_deadline: date | None = None
    capex_deadline: date | None = None
    personnel_deadline: date | None = None


class CycleUpdate(BaseModel):
    name: str | None = None
    opex_deadline: date | None = None
    capex_deadline: date | None = None
    personnel_deadline: date | None = None


class CycleOut(CycleIn, ORM):
    id: int
    status: str


class ParameterIn(BaseModel):
    value: Any
    description: str | None = None


class ParameterOut(ORM):
    key: str
    value: Any
    description: str | None


class VersionOut(ORM):
    id: int
    cycle_id: int
    label: str
    status: str
    reason: str | None
    created_at: datetime
    frozen_at: datetime | None


# ---------------------------------------------------------------- importação


class ImportBatchOut(ORM):
    id: int
    dataset_type: str | None
    layout: str | None
    file_name: str
    file_size: int
    status: str
    options: dict | None
    total_rows: int
    valid_rows: int
    warning_rows: int
    error_rows: int
    duplicate_rows: int
    summary: dict | None
    error_message: str | None
    uploaded_by: int | None
    confirmed_by: int | None
    created_at: datetime
    validated_at: datetime | None
    completed_at: datetime | None


class ImportRowOut(ORM):
    row_number: int
    sheet: str | None
    record_type: str
    status: str
    natural_key: str | None
    data: dict


class ImportErrorOut(ORM):
    sheet: str | None
    row_number: int | None
    column: str | None
    code: str
    severity: str
    message: str
    value: str | None


class ImportPreview(BaseModel):
    batch: ImportBatchOut
    errors_by_code: list[dict]
    rows: list[ImportRowOut]


class DatasetVersionOut(ORM):
    id: int
    dataset_type: str
    scope_key: str
    version_number: int
    import_batch_id: int | None
    source: str
    is_current: bool
    created_by: int | None
    created_at: datetime
    superseded_at: datetime | None
    row_count: int
    last_closed_period: int | None


class AuditOut(ORM):
    id: int
    occurred_at: datetime
    user_id: int | None
    action: str
    entity_type: str
    entity_id: str | None
    before: dict | None
    after: dict | None
    reason: str | None


class Page(BaseModel):
    total: int
    items: list[Any]
