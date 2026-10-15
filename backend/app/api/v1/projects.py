"""Projetos: rotas finas sobre `services.projects` (números lidos do sistema; o orçamento não é alterado)."""

from collections.abc import Callable
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user
from app.db import get_db
from app.models import User
from app.services import projects as S

router = APIRouter(prefix="/projects", tags=["projetos"])

Kind = Literal["milestones", "risks", "links", "updates"]


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except S.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except S.Forbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except S.Invalid as exc:
        raise HTTPException(422, str(exc)) from exc


class FactKeyIn(BaseModel):
    cost_center_id: int
    account_id: int


class CapexLinkIn(BaseModel):
    cost_center_id: int
    capex_code: str = Field(max_length=20)


class ProjectIn(BaseModel):
    """Na edição, só os campos enviados mudam. Vínculos enviados substituem a lista inteira."""

    code: str | None = Field(default=None, max_length=30)
    name: str | None = Field(default=None, max_length=200)
    status: str | None = Field(default=None, max_length=20)
    cost_center_id: int | None = None
    owner_user_id: int | None = None
    owner_name: str | None = Field(default=None, max_length=200)
    sponsor: str | None = Field(default=None, max_length=200)
    start_planned: date | None = None
    end_planned: date | None = None
    start_actual: date | None = None
    end_actual: date | None = None
    objective: str | None = Field(default=None, max_length=8000)
    scope: str | None = Field(default=None, max_length=8000)
    benefits: str | None = Field(default=None, max_length=8000)
    notes: str | None = Field(default=None, max_length=8000)
    fact_keys: list[FactKeyIn] | None = None
    capex_links: list[CapexLinkIn] | None = None


class ChildIn(BaseModel):
    """Marco, risco, link ou atualização do diário; os campos aceitos dependem do tipo."""

    title: str | None = Field(default=None, max_length=300)
    due_date: date | None = None
    done_date: date | None = None
    progress_pct: int | None = Field(default=None, ge=0, le=100)
    owner_name: str | None = Field(default=None, max_length=200)
    notes: str | None = Field(default=None, max_length=4000)
    description: str | None = Field(default=None, max_length=4000)
    impact: str | None = Field(default=None, max_length=10)
    probability: str | None = Field(default=None, max_length=10)
    mitigation: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, max_length=10)
    url: str | None = Field(default=None, max_length=1000)
    text: str | None = Field(default=None, max_length=8000)


@router.get("", summary="Projetos visíveis com os totais lidos do sistema e o farol")
def list_projects(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return S.list_projects(db, user)


@router.get("/options", summary="CCs, contas de CAPEX, solicitações de CAPEX da versão vigente e usuários")
def options(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return S.options(db, user)


@router.get("/chart", summary="Colunas realizado × orçado por mês (ou por ano) de um projeto ou de todos")
def chart(
    project_id: int | None = None,
    year: int | None = None,
    view: Literal["month", "year"] = "month",
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return S.chart(db, user, project_id, year, view)


@router.post("", status_code=201, summary="Novo projeto (Controladoria)")
def create(body: ProjectIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(S.create, db, user, body.model_dump(exclude_unset=True, mode="python"), client_ip(request))


@router.get("/{project_id}", summary="Projeto completo: cadastro, números, marcos, riscos, links, diário e lançamentos")
def detail(project_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return _call(S.detail, db, user, project_id)


@router.get("/{project_id}/figure", summary="Colunas mensais do ano (realizado, projeção e orçado)")
def figure(
    project_id: int, year: int | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    p = _call(S._get, db, user, project_id)
    return S.figure(db, p, year)


@router.patch("/{project_id}", summary="Editar o projeto (auditado)")
def update(
    project_id: int,
    body: ProjectIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _call(S.update, db, user, project_id, body.model_dump(exclude_unset=True, mode="python"), client_ip(request))


class BudgetEntryIn(BaseModel):
    year: int
    month: int = Field(ge=1, le=12)
    amount: str = Field(max_length=30)


class BudgetIn(BaseModel):
    """Orçado do próprio projeto por ano × mês; substitui o anterior. Fica só no projeto (não muda o Painel)."""

    source: str = Field(max_length=300, description="De onde veio (ex.: planilha da área e data)")
    reason: str = Field(min_length=3, max_length=1000)
    entries: list[BudgetEntryIn]


@router.put("/{project_id}/budget", summary="Gravar o orçado do projeto (Controladoria; motivo obrigatório; auditado)")
def set_budget(
    project_id: int,
    body: BudgetIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    entries = [e.model_dump() for e in body.entries]
    return _call(S.set_budget, db, user, project_id, entries, body.source, body.reason, client_ip(request))


@router.delete("/{project_id}", status_code=204, summary="Excluir o projeto (Controladoria; auditado)")
def delete(project_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    _call(S.delete, db, user, project_id, client_ip(request))


@router.post("/{project_id}/{kind}", status_code=201, summary="Acrescentar marco, risco, link ou atualização")
def add_child(
    project_id: int,
    kind: Kind,
    body: ChildIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = body.model_dump(exclude_unset=True, mode="python")
    return _call(S.add_child, db, user, project_id, kind, data, client_ip(request))


@router.patch("/{project_id}/{kind}/{child_id}", summary="Editar marco, risco ou link")
def update_child(
    project_id: int,
    kind: Kind,
    child_id: int,
    body: ChildIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = body.model_dump(exclude_unset=True, mode="python")
    return _call(S.update_child, db, user, project_id, kind, child_id, data, client_ip(request))


@router.delete("/{project_id}/{kind}/{child_id}", summary="Excluir marco, risco, link ou atualização")
def delete_child(
    project_id: int,
    kind: Kind,
    child_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _call(S.delete_child, db, user, project_id, kind, child_id, client_ip(request))
