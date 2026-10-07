"""Cadastros mestres com CRUD auditado (leitura para todos os autenticados; escrita só ADMIN)."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user, require_roles, visible_cost_center_ids
from app.db import get_db
from app.models import (
    Account,
    AccountDetail,
    Area,
    AssetClass,
    AssetItem,
    Branch,
    BudgetPackage,
    Company,
    ContractType,
    CostCenter,
    Department,
    LookupValue,
    PackageManager,
    User,
)
from app.models.base import Role
from app.schemas import common as s
from app.services import audit

router = APIRouter(tags=["cadastros"])
admin_only = require_roles(Role.ADMIN)


def _register_crud(
    path: str,
    model: Any,
    schema_in: type[BaseModel],
    schema_out: type[BaseModel],
    entity: str,
    *,
    pk: str = "id",
    order_by: Any = None,
    filters: dict[str, Any] | None = None,
    list_endpoint: bool = True,
) -> None:
    """Gera GET lista / GET item / POST / PATCH com auditoria para um cadastro simples."""
    filters = filters or {}
    tag = [path.strip("/")]

    if list_endpoint:

        def list_items(request: Request, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
            stmt = select(model)
            for param, column in filters.items():
                value = request.query_params.get(param)
                if value not in (None, ""):
                    stmt = stmt.where(
                        column == (value if column.type.python_type is str else column.type.python_type(value))
                    )
            if order_by is not None:
                stmt = stmt.order_by(order_by)
            return list(db.scalars(stmt))

        router.add_api_route(
            path, list_items, methods=["GET"], response_model=list[schema_out], tags=tag, summary=f"Listar {entity}"
        )

    def get_item(item_id: str, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
        return _load(db, model, pk, item_id)

    def create_item(
        payload: schema_in,
        request: Request,
        db: Session = Depends(get_db),  # type: ignore[valid-type]
        actor: User = Depends(admin_only),
    ):
        obj = model(**payload.model_dump())
        db.add(obj)
        _flush(db)
        audit.record(
            db,
            user_id=actor.id,
            action="CREATE",
            entity_type=entity,
            entity_id=getattr(obj, pk),
            after=audit.snapshot(obj),
            ip=client_ip(request),
        )
        db.commit()
        return obj

    def update_item(
        item_id: str,
        payload: dict[str, Any],
        request: Request,
        db: Session = Depends(get_db),
        actor: User = Depends(admin_only),
    ):
        obj = _load(db, model, pk, item_id)
        before = audit.snapshot(obj)
        try:
            merged = schema_in.model_validate(before | payload)  # valida o estado resultante
        except ValidationError as exc:
            raise RequestValidationError(exc.errors(include_url=False)) from exc
        for key in payload:
            if key in schema_in.model_fields and key != pk:
                setattr(obj, key, getattr(merged, key))
        _flush(db)
        audit.record(
            db,
            user_id=actor.id,
            action="UPDATE",
            entity_type=entity,
            entity_id=item_id,
            before=before,
            after=audit.snapshot(obj),
            ip=client_ip(request),
        )
        db.commit()
        return obj

    router.add_api_route(f"{path}/{{item_id}}", get_item, methods=["GET"], response_model=schema_out, tags=tag)
    router.add_api_route(path, create_item, methods=["POST"], response_model=schema_out, status_code=201, tags=tag)
    router.add_api_route(f"{path}/{{item_id}}", update_item, methods=["PATCH"], response_model=schema_out, tags=tag)


def _load(db: Session, model: Any, pk: str, item_id: str):
    column = getattr(model, pk)
    value = item_id if column.type.python_type is str else column.type.python_type(item_id)
    obj = db.scalar(select(model).where(column == value))
    if obj is None:
        raise HTTPException(404, "Registro não encontrado")
    return obj


def _flush(db: Session) -> None:
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Registro duplicado ou referência inválida") from exc


_register_crud("/companies", Company, s.CompanyIn, s.CompanyOut, "company", order_by=Company.code)
_register_crud(
    "/branches",
    Branch,
    s.BranchIn,
    s.BranchOut,
    "branch",
    order_by=Branch.code,
    filters={"company_id": Branch.company_id},
)
_register_crud(
    "/packages",
    BudgetPackage,
    s.PackageIn,
    s.PackageOut,
    "budget_package",
    order_by=BudgetPackage.sort_order,
    filters={"nature": BudgetPackage.nature},
)
_register_crud(
    "/account-details",
    AccountDetail,
    s.AccountDetailIn,
    s.AccountDetailOut,
    "account_detail",
    order_by=AccountDetail.name,
    filters={"account_id": AccountDetail.account_id},
)
_register_crud(
    "/lookups",
    LookupValue,
    s.LookupIn,
    s.LookupOut,
    "lookup_value",
    order_by=(LookupValue.sort_order),
    filters={"domain": LookupValue.domain},
)
_register_crud(
    "/contract-types",
    ContractType,
    s.ContractTypeIn,
    s.ContractTypeOut,
    "contract_type",
    pk="code",
    order_by=ContractType.code,
)
_register_crud("/departments", Department, s.NamedIn, s.NamedOut, "department", order_by=Department.name)
# no Painel: "Área" = departments (Controladoria, Tributos…) e "Setor" = areas (Fiscal, Contabilidade…)
_register_crud(
    "/areas", Area, s.AreaIn, s.AreaOut, "area", order_by=Area.name, filters={"department_id": Area.department_id}
)
_register_crud(
    "/package-managers",
    PackageManager,
    s.PackageManagerIn,
    s.PackageManagerOut,
    "package_manager",
    filters={"cycle_id": PackageManager.cycle_id, "package_id": PackageManager.package_id},
)
_register_crud("/asset-classes", AssetClass, s.AssetClassIn, s.AssetClassOut, "asset_class", order_by=AssetClass.name)
_register_crud(
    "/asset-items",
    AssetItem,
    s.AssetItemIn,
    s.AssetItemOut,
    "asset_item",
    order_by=AssetItem.name,
    filters={"asset_class_id": AssetItem.asset_class_id},
)
_register_crud("/accounts", Account, s.AccountIn, s.AccountOut, "account", list_endpoint=False)
_register_crud("/cost-centers", CostCenter, s.CostCenterIn, s.CostCenterOut, "cost_center", list_endpoint=False)


@router.get("/accounts", response_model=list[s.AccountOut], tags=["accounts"], summary="Listar contas")
def list_accounts(
    q: str | None = Query(None, description="Busca por código ou descrição"),
    package_id: int | None = None,
    nature: str | None = None,
    active: bool | None = True,
    include_inactive: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_user),
):
    stmt = select(Account).order_by(Account.code)
    if q:
        stmt = stmt.where(or_(Account.code.startswith(q), Account.name.ilike(f"%{q}%")))
    if package_id:
        stmt = stmt.where(Account.package_id == package_id)
    if nature:
        stmt = stmt.where(Account.nature == nature)
    if active is not None and not include_inactive:
        stmt = stmt.where(Account.is_active == active)
    return list(db.scalars(stmt))


@router.get(
    "/cost-centers",
    response_model=list[s.CostCenterOut],
    tags=["cost-centers"],
    summary="Listar centros de custo visíveis ao usuário",
)
def list_cost_centers(
    q: str | None = None,
    company_id: int | None = None,
    mine: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    stmt = select(CostCenter).order_by(CostCenter.code)
    visible = visible_cost_center_ids(db, user)
    if visible is not None:
        stmt = stmt.where(CostCenter.id.in_(visible))
    if mine:
        stmt = stmt.where(CostCenter.manager_user_id == user.id)
    if company_id:
        stmt = stmt.where(CostCenter.company_id == company_id)
    if q:
        stmt = stmt.where(or_(CostCenter.code.startswith(q), CostCenter.name.ilike(f"%{q}%")))
    return list(db.scalars(stmt))
