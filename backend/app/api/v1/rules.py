"""Simuladores das regras de cálculo (sem gravação). Usados pelas telas para cálculo em tempo real."""

from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db import get_db
from app.domain.rules import capex as capex_rules
from app.domain.rules import opex as opex_rules
from app.domain.rules import personnel as pr
from app.domain.rules.common import normalize_months
from app.models import ContractType, CycleParameter, LookupValue, TravelFare, TravelRate, User

router = APIRouter(prefix="/rules", tags=["regras de cálculo"])


def _exact(value):
    """Valores monetários saem como string (sem perda de precisão de float)."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {k: _exact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_exact(v) for v in value]
    return value


def _param(db: Session, cycle_id: int, key: str, default):
    p = db.get(CycleParameter, (cycle_id, key))
    return default if p is None else p.value


class TravelIn(BaseModel):
    cycle_id: int
    trip_type: str
    job_level: str
    origin: str
    destination: str
    departure_month: int = Field(ge=1, le=12)
    return_month: int | None = Field(default=None, ge=1, le=12)
    days: int = Field(ge=0)


@router.post("/travel", summary="Viagem → passagem, diária e hospedagem no mês de ida")
def travel(payload: TravelIn, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    def rate(kind: str) -> Decimal:
        value = db.scalar(
            select(TravelRate.daily_amount).where(
                TravelRate.cycle_id == payload.cycle_id,
                TravelRate.rate_type == kind,
                TravelRate.trip_type == payload.trip_type,
                TravelRate.job_level == payload.job_level,
            )
        )
        return Decimal(value or 0)

    fare = db.scalar(
        select(TravelFare.round_trip_amount).where(
            TravelFare.cycle_id == payload.cycle_id,
            TravelFare.origin == payload.origin,
            TravelFare.destination == payload.destination,
        )
    )
    rates = opex_rules.TravelRates(
        round_trip_fare=fare,
        per_diem_daily=rate("PER_DIEM"),
        lodging_daily=rate("LODGING"),
        one_way_factor=Decimal(str(_param(db, payload.cycle_id, "travel.one_way_factor", 0.5))),
    )
    try:
        result = opex_rules.calculate_travel(
            opex_rules.TravelInput(payload.departure_month, payload.return_month, payload.days), rates
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _exact(
        {
            "ticket": result.ticket,
            "per_diem": result.per_diem,
            "lodging": result.lodging,
            "total": result.total,
            "month": result.month,
            "warnings": result.warnings,
            "by_account": result.by_account(),
        }
    )


class EventIn(BaseModel):
    event_type: str = Field(description="Interno | Externo")
    month: int = Field(ge=1, le=12)
    people: int = Field(ge=0)
    graphic_material: Decimal = Decimal("0")
    structure: Decimal = Decimal("0")
    gifts: Decimal = Decimal("0")
    transport: Decimal = Decimal("0")


@router.post("/event", summary="Evento (Comunicação e MKT) → total no mês do evento")
def event(payload: EventIn, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    lookup = db.scalar(
        select(LookupValue).where(LookupValue.domain == "EVENT_TYPE", LookupValue.code == payload.event_type)
    )
    if lookup is None:
        raise HTTPException(422, "Tipo de evento inválido")
    meal = Decimal(str((lookup.extra or {}).get("meal_per_person", 0)))
    total, months = opex_rules.calculate_event(
        opex_rules.EventInput(
            payload.month,
            payload.people,
            meal,
            payload.graphic_material,
            payload.structure,
            payload.gifts,
            payload.transport,
        )
    )
    return _exact({"meal_per_person": meal, "total": total, "months": months})


class CapexItemIn(BaseModel):
    cycle_id: int
    unit_value: Decimal
    quantity: Decimal
    schedule: dict[int, Decimal] = Field(description="{mês: valor}")
    useful_life_months: int | None = None
    is_project: bool = False
    project_type: str | None = None
    justification: str | None = None


@router.post("/capex-item", summary="Item CAPEX → total, conferência do cronograma e enquadramento")
def capex_item(payload: CapexItemIn, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    try:
        schedule = normalize_months(payload.schedule)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    check = capex_rules.check_item(
        payload.unit_value,
        payload.quantity,
        schedule,
        min_unit_value=Decimal(str(_param(db, payload.cycle_id, "capex.min_unit_value", 1200))),
        useful_life_months=payload.useful_life_months,
    )
    issues = check.issues + capex_rules.check_project(payload.is_project, payload.project_type, payload.justification)
    return _exact(
        {
            "total_value": check.total_value,
            "schedule_total": check.schedule_total,
            "difference": check.difference,
            "is_consistent": not any(i.severity == "CRITICAL" for i in issues),
            "issues": [i.__dict__ for i in issues],
        }
    )


class PositionIn(BaseModel):
    key: str
    base_salary: Decimal = Decimal("0")
    contract_type: str = "CLT"
    movement: str = "KEEP"
    effective_month: int | None = Field(default=None, ge=1, le=12)
    new_salary: Decimal | None = None
    quantity: int = Field(default=1, ge=1)
    group: dict[str, str] = {}


class WhatIfIn(BaseModel):
    positions: list[PositionIn]
    multipliers: dict[str, Decimal] = Field(default_factory=dict, description="Ex.: {'CLT': 2.0}")
    salary_adjustment_pct: Decimal = Decimal("0")
    adjustment_month: int = Field(default=1, ge=1, le=12)


@router.post("/personnel/what-if", summary="Simulação de multiplicador/reajuste (CLT × PJ)")
def personnel_what_if(payload: WhatIfIn, db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    contracts = {c.code: c for c in db.scalars(select(ContractType))}
    base_rules = {
        c.code: pr.ContractRule(c.apply_multiplier, Decimal(c.default_multiplier)) for c in contracts.values()
    }
    unknown = {p.contract_type for p in payload.positions} - base_rules.keys()
    if unknown:
        raise HTTPException(422, f"Tipos de contrato não parametrizados: {', '.join(sorted(unknown))}")
    sim_rules = {
        code: pr.ContractRule(rule.apply_multiplier, payload.multipliers.get(code, rule.multiplier))
        for code, rule in base_rules.items()
    }
    plans = [pr.PositionPlan(**p.model_dump()) for p in payload.positions]
    try:
        baseline = pr.Scenario(base_rules)
        simulated = pr.Scenario(sim_rules, payload.salary_adjustment_pct, payload.adjustment_month)
        result = pr.what_if(plans, baseline, simulated)
        projection = pr.project(plans, simulated)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return _exact(
        result.__dict__
        | {
            "headcount": projection.headcount,
            "terminations_by_month": pr.terminations_summary(plans, simulated),
            "ignored_multiplier_for": sorted(c for c, r in sim_rules.items() if not r.apply_multiplier),
        }
    )
