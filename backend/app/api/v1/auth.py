from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user
from app.core.security import create_access_token, verify_password
from app.db import get_db
from app.models import User
from app.schemas.common import LoginIn, Token, UserOut
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])


def _authenticate(db: Session, email: str, password: str, request: Request) -> Token:
    user = db.scalar(select(User).where(User.email == email.lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
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
