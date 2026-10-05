from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, require_roles
from app.core.security import hash_password
from app.db import get_db
from app.models import RoleDef, User, UserRole, UserScope
from app.models.base import Role
from app.schemas.common import ScopeIn, UserCreate, UserOut, UserUpdate
from app.services import audit

router = APIRouter(prefix="/users", tags=["usuários"])
admin_only = require_roles(Role.ADMIN)


def _check_roles(db: Session, roles: list[str]) -> None:
    valid = set(db.scalars(select(RoleDef.code)))
    unknown = set(roles) - valid
    if unknown:
        raise HTTPException(422, f"Perfis inválidos: {', '.join(sorted(unknown))}")


@router.get("", response_model=list[UserOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_roles(Role.CONTROLLER))) -> list[UserOut]:
    return [UserOut.build(u) for u in db.scalars(select(User).order_by(User.name))]


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate, request: Request, db: Session = Depends(get_db), actor: User = Depends(admin_only)
) -> UserOut:
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(409, "E-mail já cadastrado")
    _check_roles(db, payload.roles)
    user = User(email=payload.email.lower(), name=payload.name, password_hash=hash_password(payload.password))
    user.roles = [UserRole(role_code=r) for r in set(payload.roles)]
    db.add(user)
    db.flush()
    audit.record(
        db,
        user_id=actor.id,
        action="CREATE",
        entity_type="user",
        entity_id=user.id,
        after=audit.snapshot(user) | {"roles": payload.roles},
        ip=client_ip(request),
    )
    db.commit()
    return UserOut.build(user)


@router.patch("/{user_id}", response_model=UserOut)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_only),
) -> UserOut:
    user = db.get(User, user_id) or _not_found()
    before = audit.snapshot(user)
    data = payload.model_dump(exclude_unset=True)
    if "password" in data:
        user.password_hash = hash_password(data.pop("password"))
    for key, value in data.items():
        setattr(user, key, value)
    audit.record(
        db,
        user_id=actor.id,
        action="UPDATE",
        entity_type="user",
        entity_id=user.id,
        before=before,
        after=audit.snapshot(user),
        ip=client_ip(request),
    )
    db.commit()
    return UserOut.build(user)


@router.put("/{user_id}/roles", response_model=UserOut)
def set_roles(
    user_id: int, roles: list[str], request: Request, db: Session = Depends(get_db), actor: User = Depends(admin_only)
) -> UserOut:
    user = db.get(User, user_id) or _not_found()
    _check_roles(db, roles)
    before = sorted(user.role_codes)
    user.roles = [UserRole(role_code=r) for r in sorted(set(roles))]
    audit.record(
        db,
        user_id=actor.id,
        action="SET_ROLES",
        entity_type="user",
        entity_id=user.id,
        before={"roles": before},
        after={"roles": sorted(set(roles))},
        ip=client_ip(request),
    )
    db.commit()
    db.refresh(user)
    return UserOut.build(user)


@router.put("/{user_id}/scopes", response_model=list[ScopeIn])
def set_scopes(
    user_id: int,
    scopes: list[ScopeIn],
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_only),
) -> list[ScopeIn]:
    user = db.get(User, user_id) or _not_found()
    before = [{"company_id": s.company_id, "cost_center_id": s.cost_center_id} for s in user.scopes]
    user.scopes = [UserScope(company_id=s.company_id, cost_center_id=s.cost_center_id) for s in scopes]
    audit.record(
        db,
        user_id=actor.id,
        action="SET_SCOPES",
        entity_type="user",
        entity_id=user.id,
        before={"scopes": before},
        after={"scopes": [s.model_dump() for s in scopes]},
        ip=client_ip(request),
    )
    db.commit()
    return scopes


def _not_found():
    raise HTTPException(404, "Usuário não encontrado")
