"""Projeção digitada pelo gestor: rotas finas sobre `services.projection_entry` (grade conta × mês por CC)."""

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user
from app.db import get_db
from app.models import User
from app.services import projection_entry as S

router = APIRouter(prefix="/projection", tags=["projeção"])


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except S.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except S.Forbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except S.Conflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except S.Invalid as exc:
        raise HTTPException(422, str(exc)) from exc


class CellIn(BaseModel):
    account_id: int
    month: int = Field(ge=1, le=12)
    amount: str = Field(max_length=30)


class ProjectionIn(BaseModel):
    """Células dos meses abertos (as não enviadas ficam como estão) e a versão vigente lida na tela."""

    values: list[CellIn] = Field(max_length=20000)
    version_id: int | None = None
    comment: str | None = Field(default=None, max_length=2000)


@router.get("/cost-centers", summary="CCs visíveis com realizado, projeção, fechamento × orçado e última gravação")
def cost_centers(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return S.list_cost_centers(db, user)


@router.get("/accounts", summary="Contas de OPEX, Pessoal e CAPEX que podem entrar na grade")
def accounts(db: Session = Depends(get_db), _user: User = Depends(get_current_user)):
    return S.accounts(db)


@router.get("/{cc_id}", summary="Grade conta × mês do CC: realizado nos meses fechados, projeção/orçado nos abertos")
def grid(cc_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(S.grid, db, user, cc_id)


@router.post("/{cc_id}/compare", summary="Projeção antiga × nova, sem gravar")
def compare(cc_id: int, body: ProjectionIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(S.compare, db, user, cc_id, [v.model_dump() for v in body.values], body.version_id)


@router.put("/{cc_id}", summary="Grava a projeção do CC (vale na hora; auditoria com antes × depois)")
def save(
    cc_id: int,
    body: ProjectionIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _call(
        S.save,
        db,
        user,
        cc_id,
        [v.model_dump() for v in body.values],
        body.version_id,
        body.comment,
        client_ip(request),
    )


@router.get("/{cc_id}/history", summary="Gravações da projeção do CC, com as células alteradas")
def history(cc_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(S.history, db, user, cc_id)
