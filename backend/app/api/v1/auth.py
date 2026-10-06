import threading
import time
from collections import deque
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user
from app.core.security import create_access_token, hash_password, verify_password
from app.db import get_db
from app.models import User
from app.schemas.common import LoginIn, Token, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])

# Limite de tentativas de login: 5 falhas por e-mail ou IP em 15 min → bloqueio de 15 min (em memória, por processo)
MAX_FAILURES, WINDOW_SECONDS = 5, 15 * 60
_failures: dict[str, deque[float]] = {}
_lock = threading.Lock()


def _blocked(*keys: str) -> bool:
    now = time.time()
    with _lock:
        for key in keys:
            q = _failures.get(key)
            if q is None:
                continue
            while q and now - q[0] > WINDOW_SECONDS:
                q.popleft()
            if len(q) >= MAX_FAILURES:
                return True
    return False


def _register_failure(*keys: str) -> None:
    now = time.time()
    with _lock:
        for key in keys:
            _failures.setdefault(key, deque()).append(now)


def _clear_failures(*keys: str) -> None:
    with _lock:
        for key in keys:
            _failures.pop(key, None)


def reset_rate_limit() -> None:
    """Zera o limite de tentativas (usado pelos testes, que compartilham o processo)."""
    with _lock:
        _failures.clear()


def _authenticate(db: Session, email: str, password: str, request: Request) -> Token:
    email = email.lower().strip()
    keys = (f"email:{email}", f"ip:{client_ip(request) or '-'}")
    if _blocked(*keys):
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS, "Muitas tentativas de login; aguarde 15 minutos e tente de novo"
        )
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        _register_failure(*keys)
        audit.record(
            db,
            user_id=user.id if user else None,
            action="LOGIN_FAILED",
            entity_type="user",
            entity_id=email.lower(),
            ip=client_ip(request),
        )
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "E-mail ou senha inválidos")
    _clear_failures(*keys)
    user.last_login_at = datetime.utcnow()
    audit.record(db, user_id=user.id, action="LOGIN", entity_type="user", entity_id=user.id, ip=client_ip(request))
    db.commit()
    return Token(access_token=create_access_token(user.id, sorted(user.role_codes)))


@router.post("/token", response_model=Token, summary="Login (formulário OAuth2 — usado pelo Swagger)")
def token(request: Request, form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)) -> Token:
    return _authenticate(db, form.username, form.password, request)


@router.post("/login", response_model=Token)
def login(payload: LoginIn, request: Request, db: Session = Depends(get_db)) -> Token:
    return _authenticate(db, payload.email, payload.password, request)


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.build(user)


class PasswordChangeIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


@router.post("/change-password", status_code=204, summary="Troca da própria senha")
def change_password(
    payload: PasswordChangeIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Senha atual incorreta")
    if payload.new_password == payload.current_password:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A nova senha deve ser diferente da atual")
    if payload.new_password.lower() in (user.email.lower(), user.email.split("@")[0].lower()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "A senha não pode ser o seu e-mail")
    user.password_hash = hash_password(payload.new_password)
    audit.record(
        db, user_id=user.id, action="PASSWORD_CHANGE", entity_type="user", entity_id=user.id, ip=client_ip(request)
    )
    db.commit()
