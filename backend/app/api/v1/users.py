from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, require_roles
from app.core.security import hash_password
from app.db import get_db
from app.models import CostCenter, RoleDef, User, UserRole, UserScope
from app.models.base import Role
from app.schemas.common import (
    ScopeIn,
    UserAccessOut,
    UserCostCentersIn,
    UserCreate,
    UserListOut,
    UserOut,
    UserUpdate,
)
from app.services import audit
from app.services import user_access as access_svc

router = APIRouter(prefix="/users", tags=["usuários"])
admin_only = require_roles(Role.ADMIN)
controller = require_roles(Role.CONTROLLER)  # Controladoria (e Administrador) atribui acesso a CCs


def _check_roles(db: Session, roles: list[str]) -> None:
    valid = set(db.scalars(select(RoleDef.code)))
    unknown = set(roles) - valid
    if unknown:
        raise HTTPException(422, f"Perfis inválidos: {', '.join(sorted(unknown))}")


@router.get("", response_model=list[UserListOut])
def list_users(db: Session = Depends(get_db), _: User = Depends(controller)) -> list[UserListOut]:
    users = list(db.scalars(select(User).order_by(User.name)))
    access = access_svc.summaries(db, users)
    return [UserListOut(**UserOut.build(u).model_dump(), access=access[u.id]) for u in users]


@router.post("", response_model=UserOut, status_code=201)
def create_user(
    payload: UserCreate, request: Request, db: Session = Depends(get_db), actor: User = Depends(admin_only)
) -> UserOut:
    if db.scalar(select(User).where(User.email == payload.email.lower())):
        raise HTTPException(409, "E-mail já cadastrado")
    _check_roles(db, payload.roles)
    user = User(
        email=payload.email.lower(),
        name=payload.name,
        password_hash=hash_password(payload.password),
        can_view_pj=payload.can_view_pj,
        can_view_all_pj=payload.can_view_pj and payload.can_view_all_pj,
    )
    user.roles = [UserRole(role_code=r) for r in set(payload.roles)]
    db.add(user)
    db.flush()
    changes = {}
    if payload.cost_center_ids or payload.manager_of or payload.department_ids:
        try:
            changes = access_svc.set_cost_centers(
                db, user, payload.cost_center_ids, payload.manager_of, payload.department_ids
            )
        except access_svc.AccessError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
    audit.record(
        db,
        user_id=actor.id,
        action="CREATE",
        entity_type="user",
        entity_id=user.id,
        after=audit.snapshot(user) | {"roles": payload.roles, "scopes": access_svc.snapshot(db, user)} | changes,
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
    if data.get("email"):
        data["email"] = data["email"].lower()
        if db.scalar(select(User).where(User.email == data["email"], User.id != user.id)):
            raise HTTPException(409, "E-mail já cadastrado")
    if user.id == actor.id and data.get("is_active") is False:
        raise HTTPException(409, "Você não pode desativar o próprio usuário")
    if "password" in data:
        user.password_hash = hash_password(data.pop("password"))
    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)
    if not user.can_view_pj:  # "Vê contratos PJ: Não" limpa também o "Todos"
        user.can_view_all_pj = False
    if "name" in data:  # nome do gestor exibido nos CCs que ele gere
        for cc in db.scalars(select(CostCenter).where(CostCenter.manager_user_id == user.id)):
            cc.manager_name = user.name
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
    actor: User = Depends(controller),
) -> list[ScopeIn]:
    """Substitui todos os escopos (uso em lote); a tela usa os endpoints de adicionar/remover abaixo."""
    user = db.get(User, user_id) or _not_found()
    before = [
        {"company_id": s.company_id, "cost_center_id": s.cost_center_id, "department_id": s.department_id}
        for s in user.scopes
    ]
    user.scopes = [
        UserScope(company_id=s.company_id, cost_center_id=s.cost_center_id, department_id=s.department_id)
        for s in scopes
    ]
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


@router.get("/{user_id}/access", response_model=UserAccessOut)
def get_access(user_id: int, db: Session = Depends(get_db), _: User = Depends(controller)) -> dict:
    """CCs que o usuário acessa: como gestor (cadastro do CC) e por escopo atribuído (CC ou empresa inteira)."""
    user = db.get(User, user_id) or _not_found()
    return access_svc.detail(db, user)


@router.post("/{user_id}/scopes", response_model=UserAccessOut, status_code=201)
def add_scope(
    user_id: int,
    payload: ScopeIn,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(controller),
) -> dict:
    """Libera um centro de custo (`cost_center_id`) ou uma empresa inteira (`company_id`) para o usuário."""
    user = db.get(User, user_id) or _not_found()
    before = access_svc.snapshot(db, user)
    try:
        scope = access_svc.add_scope(
            db,
            user,
            cost_center_id=payload.cost_center_id,
            company_id=payload.company_id,
            department_id=payload.department_id,
        )
    except access_svc.AccessError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit.record(
        db,
        user_id=actor.id,
        action="ADD_SCOPE",
        entity_type="user",
        entity_id=user.id,
        before={"scopes": before},
        after={"scopes": access_svc.snapshot(db, user), "added": scope.id},
        ip=client_ip(request),
    )
    out = access_svc.detail(db, user)
    db.commit()
    return out


@router.delete("/{user_id}/scopes/{scope_id}", response_model=UserAccessOut)
def remove_scope(
    user_id: int,
    scope_id: int,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(controller),
) -> dict:
    """Retira um escopo atribuído. CCs em que o usuário é gestor saem só trocando o gestor no cadastro do CC."""
    user = db.get(User, user_id) or _not_found()
    before = access_svc.snapshot(db, user)
    try:
        access_svc.remove_scope(db, user, scope_id)
    except access_svc.AccessError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit.record(
        db,
        user_id=actor.id,
        action="REMOVE_SCOPE",
        entity_type="user",
        entity_id=user.id,
        before={"scopes": before},
        after={"scopes": access_svc.snapshot(db, user), "removed": scope_id},
        ip=client_ip(request),
    )
    out = access_svc.detail(db, user)
    db.commit()
    return out


@router.put("/{user_id}/cost-centers", response_model=UserAccessOut, summary="CCs do usuário em lote (escopo e gestor)")
def set_cost_centers(
    user_id: int,
    payload: UserCostCentersIn,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(controller),
) -> dict:
    user = db.get(User, user_id) or _not_found()
    before = access_svc.snapshot(db, user)
    try:
        changes = access_svc.set_cost_centers(
            db, user, payload.cost_center_ids, payload.manager_of, payload.department_ids
        )
    except access_svc.AccessError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit.record(
        db,
        user_id=actor.id,
        action="SET_COST_CENTERS",
        entity_type="user",
        entity_id=user.id,
        before={"scopes": before},
        after={"scopes": access_svc.snapshot(db, user)} | changes,
        ip=client_ip(request),
    )
    out = access_svc.detail(db, user)
    db.commit()
    return out


def _not_found():
    raise HTTPException(404, "Usuário não encontrado")
