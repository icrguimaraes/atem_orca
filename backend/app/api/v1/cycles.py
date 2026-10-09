from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user, require_roles
from app.db import get_db
from app.models import BudgetCycle, BudgetVersion, CycleParameter, User
from app.models.base import Role
from app.schemas.common import CycleIn, CycleOut, CycleUpdate, ParameterIn, ParameterOut, VersionOut
from app.services import audit

router = APIRouter(prefix="/cycles", tags=["ciclos"])
admin_only = require_roles(Role.ADMIN)

TRANSITIONS = {"open": ("DRAFT", "CLOSED"), "close": ("OPEN",)}


def _cycle(db: Session, cycle_id: int) -> BudgetCycle:
    cycle = db.get(BudgetCycle, cycle_id)
    if cycle is None:
        raise HTTPException(404, "Ciclo não encontrado")
    return cycle


@router.get("", response_model=list[CycleOut])
def list_cycles(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    return list(db.scalars(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc())))


@router.post("", response_model=CycleOut, status_code=201)
def create_cycle(payload: CycleIn, request: Request, db: Session = Depends(get_db), actor: User = Depends(admin_only)):
    if db.scalar(select(BudgetCycle).where(BudgetCycle.fiscal_year == payload.fiscal_year)):
        raise HTTPException(409, "Já existe ciclo para este ano")
    cycle = BudgetCycle(**payload.model_dump(), status="DRAFT")
    db.add(cycle)
    db.flush()
    db.add(
        BudgetVersion(
            cycle_id=cycle.id, major=1, minor=0, status="WORKING", reason="Versão inicial", created_by=actor.id
        )
    )
    # herda parâmetros do ciclo anterior mais recente (evita reconfigurar tudo a cada ano)
    previous = db.scalar(select(BudgetCycle).where(BudgetCycle.id != cycle.id).order_by(BudgetCycle.fiscal_year.desc()))
    if previous:
        for p in db.scalars(select(CycleParameter).where(CycleParameter.cycle_id == previous.id)):
            db.add(CycleParameter(cycle_id=cycle.id, key=p.key, value=p.value, description=p.description))
    audit.record(
        db,
        user_id=actor.id,
        action="CREATE",
        entity_type="budget_cycle",
        entity_id=cycle.id,
        after=audit.snapshot(cycle),
        ip=client_ip(request),
    )
    db.commit()
    return cycle


@router.patch("/{cycle_id}", response_model=CycleOut)
def update_cycle(
    cycle_id: int,
    payload: CycleUpdate,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(admin_only),
):
    cycle = _cycle(db, cycle_id)
    before = audit.snapshot(cycle)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(cycle, key, value)
    audit.record(
        db,
        user_id=actor.id,
        action="UPDATE",
        entity_type="budget_cycle",
        entity_id=cycle.id,
        before=before,
        after=audit.snapshot(cycle),
        ip=client_ip(request),
    )
    db.commit()
    return cycle


@router.post("/{cycle_id}/{action}", response_model=CycleOut, summary="Abrir (open) ou fechar (close) o ciclo")
def change_status(
    cycle_id: int, action: str, request: Request, db: Session = Depends(get_db), actor: User = Depends(admin_only)
):
    if action not in TRANSITIONS:
        raise HTTPException(404, "Ação inválida")
    cycle = _cycle(db, cycle_id)
    if cycle.status not in TRANSITIONS[action]:
        raise HTTPException(409, f"Ciclo em status {cycle.status} não permite '{action}'")
    before = cycle.status
    cycle.status = "OPEN" if action == "open" else "CLOSED"
    audit.record(
        db,
        user_id=actor.id,
        action=f"CYCLE_{action.upper()}",
        entity_type="budget_cycle",
        entity_id=cycle.id,
        before={"status": before},
        after={"status": cycle.status},
        ip=client_ip(request),
    )
    db.commit()
    return cycle


@router.get("/{cycle_id}/parameters", response_model=list[ParameterOut])
def list_parameters(cycle_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _cycle(db, cycle_id)
    return list(
        db.scalars(select(CycleParameter).where(CycleParameter.cycle_id == cycle_id).order_by(CycleParameter.key))
    )


def _check_split(value: Any) -> dict:
    """Rateio de encargos {código da conta: peso}: pesos numéricos ≥ 0 (normalizados no cálculo); {} = conta única."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise HTTPException(422, "Informe o rateio como objeto JSON {conta: peso}")
    out = {}
    for code, weight in value.items():
        code = str(code).strip()
        if not code.isdigit():
            raise HTTPException(422, f"Código de conta inválido no rateio: {code or '(vazio)'}")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight < 0:
            raise HTTPException(422, f"Peso inválido para a conta {code}: use um número maior ou igual a zero")
        out[code] = weight
    if out and not any(out.values()):
        raise HTTPException(422, "O rateio precisa de ao menos uma conta com peso maior que zero")
    return out


@router.put("/{cycle_id}/parameters/{key}", response_model=ParameterOut)
def set_parameter(
    cycle_id: int,
    key: str,
    payload: ParameterIn,
    request: Request,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(Role.CONTROLLER)),
):
    _cycle(db, cycle_id)
    param = apply_parameter(
        db, cycle_id, key, payload.value, user_id=actor.id, ip=client_ip(request), description=payload.description
    )
    db.commit()
    return param


def apply_parameter(
    db: Session,
    cycle_id: int,
    key: str,
    value: Any,
    *,
    user_id: int | None,
    ip: str | None,
    description: str | None = None,
    reason: str | None = None,
) -> CycleParameter:
    """Grava um parâmetro do ciclo com a mesma validação e auditoria do PUT (usado também por "Retirar do rateio"
    nas Premissas de pessoal). Não faz commit."""
    if key == "personnel.charges_split":
        value = _check_split(value)
    param = db.get(CycleParameter, (cycle_id, key))
    before = None if param is None else {"value": param.value}
    if param is None:
        param = CycleParameter(cycle_id=cycle_id, key=key)
        db.add(param)
    param.value = value
    if description is not None:
        param.description = description
    audit.record(
        db,
        user_id=user_id,
        action="SET_PARAMETER",
        entity_type="cycle_parameter",
        entity_id=f"{cycle_id}:{key}",
        before=before,
        after={"value": value},
        reason=reason,
        ip=ip,
    )
    return param


@router.get("/{cycle_id}/versions", response_model=list[VersionOut])
def list_versions(cycle_id: int, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    _cycle(db, cycle_id)
    return list(
        db.scalars(
            select(BudgetVersion)
            .where(BudgetVersion.cycle_id == cycle_id)
            .order_by(BudgetVersion.major, BudgetVersion.minor)
        )
    )
