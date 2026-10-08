from collections.abc import Callable

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.security import decode_token
from app.db import get_db
from app.models import CostCenter, User, UserScope
from app.models.base import Role

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/token")


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED, "Credenciais inválidas", headers={"WWW-Authenticate": "Bearer"}
    )
    try:
        payload = decode_token(token)
    except jwt.PyJWTError:
        raise unauthorized from None
    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise unauthorized
    return user


def require_roles(*roles: Role) -> Callable[[User], User]:
    allowed = {r.value for r in roles} | {Role.ADMIN.value}

    def checker(user: User = Depends(get_current_user)) -> User:
        if not user.role_codes & allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Perfil sem permissão para esta operação")
        return user

    return checker


def require_pj(user: User = Depends(get_current_user)) -> User:
    """Contratos PJ: só quem tem "Vê contratos PJ" (dado pelo Administrador), qualquer que seja o perfil."""
    if not user.can_view_pj:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Sem acesso aos contratos PJ")
    return user


def is_global(user: User) -> bool:
    return bool(user.role_codes & {Role.ADMIN, Role.CONTROLLER})


def visible_cost_center_ids(db: Session, user: User) -> set[int] | None:
    """None = acesso a todos. Gestor: CCs onde é gestor + escopos atribuídos."""
    if is_global(user):
        return None
    return own_cost_center_ids(db, user)


def own_cost_center_ids(db: Session, user: User) -> set[int]:
    """CCs da própria pessoa (gestor + escopos atribuídos), sem o "vê tudo" dos perfis globais."""
    ids = set(db.scalars(select(CostCenter.id).where(CostCenter.manager_user_id == user.id)))
    for scope in db.scalars(select(UserScope).where(UserScope.user_id == user.id)):
        if scope.cost_center_id:
            ids.add(scope.cost_center_id)
        elif scope.company_id:
            ids.update(db.scalars(select(CostCenter.id).where(CostCenter.company_id == scope.company_id)))
        elif scope.department_id:  # área inteira (Tributos, Controladoria…)
            ids.update(db.scalars(select(CostCenter.id).where(CostCenter.department_id == scope.department_id)))
    return ids


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    return forwarded.split(",")[0].strip() if forwarded else (request.client.host if request.client else None)
