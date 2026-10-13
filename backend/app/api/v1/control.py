"""Central de dados: rotas finas sobre `services.control`. Todas exigem o acesso pessoal "Central de dados"."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.v1.cycles import apply_parameter
from app.core.deps import client_ip, require_data_control
from app.db import get_db
from app.models import User
from app.services import control
from app.services import opex as opex_svc

router = APIRouter(prefix="/control", tags=["central de dados"])


@router.get("/lineage", summary="Como cada número é montado: fontes vigentes e componentes do orçamento")
def lineage(db: Session = Depends(get_db), _: User = Depends(require_data_control)):
    return control.lineage(db)


@router.get("/sources", summary="Todas as versões de base (vigentes e substituídas) com o arquivo de origem")
def sources(db: Session = Depends(get_db), _: User = Depends(require_data_control)):
    return control.sources(db)


@router.get("/steps", summary="Etapas do ETL por layout, colunas aceitas e última importação")
def steps(db: Session = Depends(get_db), _: User = Depends(require_data_control)):
    return control.steps(db)


@router.get("/parameters", summary="Parâmetros do ciclo com grupo, onde são usados e a última alteração")
def parameters(db: Session = Depends(get_db), _: User = Depends(require_data_control)):
    return control.parameters(db)


class ParameterChange(BaseModel):
    value: Any
    reason: str = Field(min_length=5, max_length=500)


@router.put("/parameters/{key}", summary="Altera um parâmetro do ciclo (motivo obrigatório; auditado)")
def set_parameter(
    key: str,
    payload: ParameterChange,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_data_control),
):
    ctx = opex_svc.context(db)
    if key not in control.S.CYCLE_PARAMETERS and key not in ctx.params:
        raise HTTPException(404, "Parâmetro não encontrado")
    apply_parameter(
        db, ctx.cycle.id, key, payload.value, user_id=actor.id, ip=client_ip(request), reason=payload.reason.strip()
    )
    db.commit()
    return next(p for p in control.parameters(db)["items"] if p["key"] == key)


@router.get("/history", summary="Histórico: parâmetros, importações, exclusões de base e versões")
def history(limit: int = 200, db: Session = Depends(get_db), _: User = Depends(require_data_control)):
    return control.history(db, min(limit, 1000))
