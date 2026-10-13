"""Pessoal — cenários, what-if e premissas (Controladoria). Saiu de `api/v1/personnel.py` em 13/10/2026, sem mudar
comportamento; mesmo prefixo `/personnel`, router próprio incluído em `main.py`."""

from collections import defaultdict
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.cycles import apply_parameter
from app.api.v1.personnel import (
    ZERO,
    _ctx,
    _visible,
    planner,
)
from app.core.deps import client_ip, get_current_user, require_roles
from app.db import get_db
from app.domain.rules.common import money
from app.models import (
    CostCenter,
    PersonnelScenario,
    ScenarioMultiplier,
    User,
)
from app.models.base import Role
from app.services import audit, painel_figures
from app.services import consolidation as consolidation_svc
from app.services import personnel as svc
from app.services import personnel_premises as premises_svc

router = APIRouter(prefix="/personnel", tags=["Pessoal"])


# ------------------------------------------------------------------ cenários e what-if


class ScenarioIn(BaseModel):
    name: str | None = None
    multipliers: dict[str, Decimal] = Field(default_factory=dict, description="Ex.: {'CLT': 2.0}")
    salary_adjustment_pct: Decimal | None = None
    adjustment_month: int | None = Field(None, ge=1, le=12)


class WhatIfIn(ScenarioIn):
    cost_center_ids: list[int] | None = None


@router.post("/what-if", summary="Cenário base × simulado (multiplicador por contrato, reajuste e data-base)")
def what_if(payload: WhatIfIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    visible = _visible(db, user)
    ids = set(payload.cost_center_ids or []) or None
    if visible is not None:
        ids = (ids or visible) & visible
    base = svc.baseline(db, ctx)
    base_row = db.get(PersonnelScenario, base.id) if base.id else None
    try:
        sim = svc.scenario_info(
            db,
            base_row,
            multipliers=payload.multipliers,
            salary_adjustment_pct=payload.salary_adjustment_pct,
            adjustment_month=payload.adjustment_month,
            name=payload.name or "Simulação",
        )
        sim = svc.with_cycle_bonus(sim, ctx)  # o abono do CLT vale nos dois cenários
    except svc.PersonnelError as exc:
        raise HTTPException(422, str(exc)) from exc
    positions = svc.build_positions(db, ctx, ids)
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(list(positions) or [-1])))}
    total_base, total_sim = svc.Totals(), svc.Totals()
    by_cc = []
    by_contract: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"base": ZERO, "sim": ZERO, "people": ZERO})
    for cc_id, pos in positions.items():
        tb, ts = svc.totals_for(pos, base), svc.totals_for(pos, sim)
        total_base.add(tb)
        total_sim.add(ts)
        for p in pos:
            c = by_contract[p.plan.contract_type]
            c["base"] += sum(svc.position_cost(p, base)[0], ZERO)
            c["sim"] += sum(svc.position_cost(p, sim)[0], ZERO)
            c["people"] += p.plan.quantity
        cc = ccs.get(cc_id)
        by_cc.append(
            {
                "cost_center_id": cc_id,
                "code": cc.code if cc else None,
                "name": cc.name if cc else "—",
                "base": str(tb.annual),
                "simulated": str(ts.annual),
                "difference": str(money(ts.annual - tb.annual)),
            }
        )
    by_cc.sort(key=lambda r: -abs(Decimal(r["difference"])))
    diff = money(total_sim.annual - total_base.annual)
    return {
        "baseline": svc.scenario_out(base),
        "simulated": svc.scenario_out(sim),
        "base": svc.totals_out(total_base),
        "simulation": svc.totals_out(total_sim),
        "difference": str(diff),
        "difference_pct": str((diff / total_base.annual).quantize(Decimal("0.0001"))) if total_base.annual else None,
        "monthly_impact": [str(money(total_sim.monthly[i] - total_base.monthly[i])) for i in range(12)],
        "by_cost_center": by_cc,
        "by_contract": [
            {
                "contract": k,
                "people": int(v["people"]),
                "base": str(money(v["base"])),
                "simulated": str(money(v["sim"])),
                "difference": str(money(v["sim"] - v["base"])),
            }
            for k, v in sorted(by_contract.items())
        ],
    }


def _scenario_row(db: Session, row: PersonnelScenario) -> dict:
    info = svc.scenario_info(db, row)
    return svc.scenario_out(info) | {"is_baseline": row.is_baseline}


@router.get("/scenarios")
def list_scenarios(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    ctx = _ctx(db)
    rows = db.scalars(
        select(PersonnelScenario).where(PersonnelScenario.cycle_id == ctx.cycle.id).order_by(PersonnelScenario.id)
    )
    return [_scenario_row(db, r) for r in rows]


def _apply_scenario(db: Session, row: PersonnelScenario, payload: ScenarioIn) -> None:
    info = svc.scenario_info(
        db,
        row if row.id else None,
        multipliers=payload.multipliers,
        salary_adjustment_pct=payload.salary_adjustment_pct,
        adjustment_month=payload.adjustment_month,
    )
    row.salary_adjustment_pct = info.salary_adjustment_pct
    row.adjustment_month = info.adjustment_month
    if payload.name:
        row.name = payload.name.strip()
    if row.id is None:
        db.add(row)
        db.flush()
    db.execute(ScenarioMultiplier.__table__.delete().where(ScenarioMultiplier.scenario_id == row.id))
    for code, mult in info.multipliers.items():
        db.add(ScenarioMultiplier(scenario_id=row.id, contract_type_code=code, multiplier=mult))


@router.post("/scenarios", status_code=201, summary="Salvar cenário (não altera o base)")
def create_scenario(
    payload: ScenarioIn, request: Request, db: Session = Depends(get_db), user: User = Depends(planner)
):
    ctx = _ctx(db)
    if not (payload.name or "").strip():
        raise HTTPException(422, "Informe o nome do cenário")
    row = PersonnelScenario(cycle_id=ctx.cycle.id, name=payload.name.strip(), is_baseline=False, created_by=user.id)
    base = svc.baseline(db, ctx)
    payload = payload.model_copy(
        update={
            "multipliers": base.multipliers | payload.multipliers,
            "salary_adjustment_pct": base.salary_adjustment_pct
            if payload.salary_adjustment_pct is None
            else payload.salary_adjustment_pct,
            "adjustment_month": payload.adjustment_month or base.adjustment_month,
        }
    )
    try:
        _apply_scenario(db, row, payload)
    except svc.PersonnelError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        user_id=user.id,
        action="CREATE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        after=_scenario_row(db, row),
        ip=client_ip(request),
    )
    db.commit()
    return _scenario_row(db, row)


@router.patch("/scenarios/{scenario_id}", summary="Alterar cenário (o base afeta todos os cálculos)")
def update_scenario(
    scenario_id: int,
    payload: ScenarioIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(planner),
):
    row = db.get(PersonnelScenario, scenario_id)
    if row is None or row.cycle_id != _ctx(db).cycle.id:
        raise HTTPException(404, "Cenário não encontrado")
    before = _scenario_row(db, row)
    try:
        _apply_scenario(db, row, payload)
    except svc.PersonnelError as exc:
        db.rollback()
        raise HTTPException(422, str(exc)) from exc
    db.flush()
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        before=before,
        after=_scenario_row(db, row),
        ip=client_ip(request),
    )
    db.commit()
    return _scenario_row(db, row)


@router.post("/scenarios/{scenario_id}/baseline", summary="Tornar o cenário a base do orçamento de pessoal")
def set_baseline(scenario_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(planner)):
    ctx = _ctx(db)
    row = db.get(PersonnelScenario, scenario_id)
    if row is None or row.cycle_id != ctx.cycle.id:
        raise HTTPException(404, "Cenário não encontrado")
    for other in db.scalars(select(PersonnelScenario).where(PersonnelScenario.cycle_id == ctx.cycle.id)):
        other.is_baseline = other.id == row.id
    audit.record(
        db,
        user_id=user.id,
        action="SET_BASELINE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        after={"name": row.name},
        ip=client_ip(request),
    )
    db.commit()
    return _scenario_row(db, row)


@router.delete("/scenarios/{scenario_id}")
def delete_scenario(scenario_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(planner)):
    row = db.get(PersonnelScenario, scenario_id)
    if row is None or row.cycle_id != _ctx(db).cycle.id:
        raise HTTPException(404, "Cenário não encontrado")
    if row.is_baseline:
        raise HTTPException(409, "O cenário base não pode ser excluído; defina outro como base antes")
    audit.record(
        db,
        user_id=user.id,
        action="DELETE",
        entity_type="personnel_scenario",
        entity_id=row.id,
        before=_scenario_row(db, row),
        ip=client_ip(request),
    )
    db.delete(row)
    db.commit()
    return {"deleted": scenario_id}


# ------------------------------------------------------------------ premissas de pessoal (Controladoria)

controller = require_roles(Role.CONTROLLER)


@router.get(
    "/premises",
    summary="Premissas do custo de pessoal: dissídio, multiplicadores, abono, bônus por CC, rateio e total por conta",
)
def premises(db: Session = Depends(get_db), _: User = Depends(controller)):
    return premises_svc.personnel_premises(db, _ctx(db))


class PremisesWhatIfIn(BaseModel):
    """Premissas simuladas (frações: 0.05 = 5%). Campo ausente = como está no ciclo."""

    salary_adjustment_pct: Decimal | None = Field(None, ge=0, le=Decimal("0.5"), description="Dissídio, 0 a 50%")
    adjustment_month: int | None = Field(None, ge=1, le=12)
    multipliers: dict[str, Annotated[Decimal, Field(gt=0, le=5)]] = Field(
        default_factory=dict, description="Ex.: {'CLT': 1.9}"
    )
    annual_bonus: Decimal | None = Field(None, ge=0, description="Abono anual do CLT, R$ por pessoa no ano")
    cc_bonus_pct: Decimal = Field(Decimal(0), ge=-1, le=2, description="Ajuste do bônus por CC, -100% a +200%")
    include_cc_bonus: bool = True
    remove_overlap: bool = False
    include_severance: bool = True


@router.post(
    "/premises/what-if",
    summary="What-if das premissas de pessoal: atual × simulado (nada é gravado)",
)
def premises_what_if(payload: PremisesWhatIfIn, db: Session = Depends(get_db), _: User = Depends(controller)):
    ctx = _ctx(db)
    try:
        out = premises_svc.premises_what_if(db, ctx, premises_svc.PremisesWhatIf(**payload.model_dump()))
    except svc.PersonnelError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        db.rollback()  # só leitura: nada do cálculo pode ser gravado
    return out | {"figure": painel_figures.fig_personnel_what_if(out["monthly"], ctx.target_year)}


@router.post(
    "/premises/remove-overlap",
    summary="Retira do rateio de encargos as contas que já recebem abono/bônus explícito (total não muda)",
)
def remove_overlap(request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    ctx = _ctx(db)
    if ctx.frozen:
        raise HTTPException(409, "Versão congelada: só leitura. Abra uma revisão para alterar o rateio")
    if ctx.cycle.status == "CLOSED":
        raise HTTPException(409, "Ciclo encerrado: parâmetros só leitura")
    current, new, overlap = premises_svc.split_without_overlap(db, ctx)
    if not overlap:
        raise HTTPException(409, "O rateio não tem conta sobreposta ao abono ou ao bônus")
    before_total = premises_svc.personnel_premises(db, ctx)["total"]
    accounts = ", ".join(f"{o['code']} ({o['label']})" for o in overlap)
    apply_parameter(
        db,
        ctx.cycle.id,
        consolidation_svc.CHARGES_SPLIT_KEY,
        new,
        user_id=user.id,
        ip=client_ip(request),
        reason=f"Retirar do rateio de encargos as contas com valor explícito: {accounts} "
        "(apontamento PERSONNEL_SPLIT_OVERLAP)",
    )
    db.flush()
    after = premises_svc.personnel_premises(db, _ctx(db))
    if after["total"] != before_total:  # salvaguarda: só redistribui entre contas
        db.rollback()
        raise HTTPException(409, "A retirada mudaria o total de pessoal; nada foi alterado")
    db.commit()
    return {"removed": [o["code"] for o in overlap], "before": current, "after": new, "premises": after}
