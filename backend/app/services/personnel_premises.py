"""Premissas de pessoal (rateio × abono/bônus) e what-if das premissas (nada é gravado).

Saiu de `services/consolidation.py` em 13/10/2026, sem mudar comportamento: a consolidação continua dona de
`personnel_amounts`; aqui ficam as leituras e simulações sobre ela (página Premissas de pessoal).
"""

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.domain.rules.personnel import normalize_split
from app.models import (
    Area,
    CostCenter,
    PersonnelScenario,
)
from app.services import personnel as personnel_svc
from app.services.consolidation import (
    CHARGES_SPLIT_KEY,
    ZERO,
    charges_split,
    personnel_accounts,
    personnel_amounts,
    snapshot_rows,
)
from app.services.opex import Context

# ------------------------------------------------------------------ premissas de pessoal (rateio × abono/bônus)

OVERLAP_LABELS = {"bonus": "abono anual do CLT", "cc_bonus": "bônus CLT por CC"}


def _positive(raw) -> Decimal:
    try:
        value = Decimal(str(raw or 0))
    except ArithmeticError:
        return ZERO
    return value if value.is_finite() and value > 0 else ZERO


def split_overlap(db: Session, ctx: Context) -> list[dict]:
    """Contas do rateio de encargos (`personnel.charges_split`, peso > 0) que já recebem o abono anual do CLT
    (`personnel.annual_bonus_account`, com `annual_bonus_clt` > 0) ou o bônus CLT por CC (`personnel.bonus_account`,
    com algum valor em `bonus_by_cc`): o rateio manda parte do multiplicador para elas e conta em dobro.
    → [{code, name, kind, label, share}] (sem cálculo do quadro; barato)."""
    split = dict(normalize_split(ctx.params.get(CHARGES_SPLIT_KEY)))
    if not split:
        return []
    pacc = personnel_accounts(db, ctx)
    bonus_by_cc = ctx.params.get("personnel.bonus_by_cc") or {}
    active = {
        "bonus": _positive(ctx.params.get("personnel.annual_bonus_clt", 0)) > 0,
        "cc_bonus": isinstance(bonus_by_cc, dict) and any(_positive(v) > 0 for v in bonus_by_cc.values()),
    }
    out = []
    for kind in ("bonus", "cc_bonus"):
        code, name = pacc[kind]
        if active[kind] and code in split:
            out.append({"code": code, "name": name, "kind": kind, "label": OVERLAP_LABELS[kind], "share": split[code]})
    return out


def overlap_amounts(db: Session, ctx: Context, overlap: list[dict]) -> dict[str, Decimal]:
    """R$ do ano que o rateio do multiplicador manda para as contas sobrepostas (por conta)."""
    codes = {o["code"] for o in overlap}
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for _cc, kind, code, _name, _m, amount in personnel_amounts(db, ctx):
        if kind == "charges" and code in codes:
            out[code] += amount
    return {c: money(out[c]) for c in codes}


@dataclass
class PersonnelBreakdown:
    """Pessoal do ano agregado a partir de `personnel_amounts` (mesmo caminho da consolidação)."""

    by_kind: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    # conta → {code, name, total, components: {componente: valor}}
    by_account: dict[str, dict] = field(default_factory=dict)
    # CC → {total, components}
    by_cc: dict[int, dict] = field(default_factory=dict)
    monthly: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)

    @property
    def total(self) -> Decimal:
        return money(sum(self.monthly, ZERO))


def personnel_breakdown(
    db: Session,
    ctx: Context,
    *,
    scenario: personnel_svc.ScenarioInfo | None = None,
    positions: dict[int, list[personnel_svc.Position]] | None = None,
    include_severance: bool = True,
) -> PersonnelBreakdown:
    """Totais de pessoal por componente, conta, CC e mês. Sem argumentos é exatamente o que a consolidação mostra."""
    out = PersonnelBreakdown()
    for cc_id, kind, code, name, m, amount in personnel_amounts(
        db, ctx, scenario=scenario, positions=positions, include_severance=include_severance
    ):
        out.by_kind[kind] += amount
        acc = out.by_account.setdefault(
            code, {"code": code, "name": name, "total": ZERO, "components": defaultdict(lambda: ZERO)}
        )
        acc["total"] += amount
        acc["components"][kind] += amount
        cc = out.by_cc.setdefault(cc_id, {"total": ZERO, "components": defaultdict(lambda: ZERO)})
        cc["total"] += amount
        cc["components"][kind] += amount
        out.monthly[m - 1] += amount
    return out


def personnel_premises(db: Session, ctx: Context) -> dict:
    """Premissas do custo de pessoal do ciclo (página "Premissas de pessoal"): dissídio e multiplicadores do cenário
    base, abono anual do CLT, bônus por CC, rateio da parte do multiplicador com o R$ do ano por conta, total de
    pessoal por conta e as contas do rateio que já recebem abono/bônus explícito (contagem em dobro)."""
    scenario = personnel_svc.baseline(db, ctx)
    contracts = personnel_svc.contract_types(db)
    pacc = personnel_accounts(db, ctx)
    breakdown = personnel_breakdown(db, ctx, scenario=scenario)
    by_kind = breakdown.by_kind
    by_account = breakdown.by_account
    charges_by_account: dict[str, Decimal] = defaultdict(
        lambda: ZERO, {code: a["components"].get("charges", ZERO) for code, a in by_account.items()}
    )
    bonus_by_cc_id: dict[int, Decimal] = defaultdict(
        lambda: ZERO, {cc: c["components"].get("cc_bonus", ZERO) for cc, c in breakdown.by_cc.items()}
    )
    if ctx.version.status == "FROZEN":  # versão congelada: total por conta da fotografia
        frozen: dict[str, dict] = {}
        for r in snapshot_rows(db, ctx.version):
            if r.module != "PERSONNEL":
                continue
            acc = frozen.setdefault(
                r.account_code, {"code": r.account_code, "name": r.account_name, "total": ZERO, "components": {}}
            )
            acc["total"] += r.total
        by_account = frozen
    accounts = sorted(by_account.values(), key=lambda a: (-a["total"], a["code"]))
    total = money(sum((a["total"] for a in accounts), ZERO))

    overlap = split_overlap(db, ctx)
    overlap_codes = {o["code"] for o in overlap}
    raw_split = ctx.params.get(CHARGES_SPLIT_KEY)
    raw_weights = {str(k).strip(): v for k, v in raw_split.items()} if isinstance(raw_split, dict) else {}
    split_rows = [
        {
            "code": code,
            "name": name,
            "weight": str(raw_weights.get(code, "")),
            "share": str(share),
            "amount": str(money(charges_by_account[code])),
            "overlap": code in overlap_codes,
        }
        for code, name, share in charges_split(db, ctx)
    ]
    overlap_out = [
        {
            "code": o["code"],
            "name": o["name"],
            "kind": o["kind"],
            "label": o["label"],
            "share": str(o["share"]),
            "amount": str(money(charges_by_account[o["code"]])),
        }
        for o in overlap
    ]

    raw_cc = ctx.params.get("personnel.bonus_by_cc") or {}
    raw_cc = raw_cc if isinstance(raw_cc, dict) else {}
    owner_ids = personnel_svc.bonus_owners(db, ctx)  # o CC que de fato recebe cada código
    ccs = {c.code: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(set(owner_ids) or {-1})))}
    bonus_rows = []
    for cc_code, value in sorted(raw_cc.items()):
        cc = ccs.get(str(cc_code))
        bonus_rows.append(
            {
                "cost_center_id": cc.id if cc else None,
                "code": str(cc_code),
                "name": cc.name if cc else None,
                "annual": str(money(_positive(value))),
                "booked": str(money(bonus_by_cc_id[cc.id])) if cc else "0.00",
            }
        )

    def acc_out(kind: str) -> dict:
        return {"code": pacc[kind][0], "name": pacc[kind][1]}

    return {
        "cycle_id": ctx.cycle.id,
        "cycle_status": ctx.cycle.status,
        "target_year": ctx.target_year,
        "version": ctx.version.label,
        "frozen": ctx.frozen,
        "scenario": {
            "name": scenario.name,
            "salary_adjustment_pct": str(scenario.salary_adjustment_pct),
            "adjustment_month": scenario.adjustment_month,
        },
        "multipliers": [
            {
                "code": code,
                "name": c.name,
                "multiplier": str(scenario.multipliers.get(code, Decimal(c.default_multiplier))),
                "apply_multiplier": bool(c.apply_multiplier),
                "is_active": bool(c.is_active),
            }
            for code, c in sorted(contracts.items())
        ],
        "salary": {"account": acc_out("salary"), "total": str(money(by_kind["salary"]))},
        "severance": {"account": acc_out("severance"), "total": str(money(by_kind["severance"]))},
        "abono": {
            "value": str(money(scenario.annual_bonus)),
            "contracts": list(scenario.bonus_contracts),
            "account": acc_out("bonus"),
            "total": str(money(by_kind["bonus"])),
        },
        "bonus_by_cc": {
            "rows": bonus_rows,
            "total": str(money(sum((_positive(v) for v in raw_cc.values()), ZERO))),
            "booked": str(money(by_kind["cc_bonus"])),
            "account": acc_out("cc_bonus"),
        },
        "charges": {
            "default_account": acc_out("charges"),
            "has_split": bool(normalize_split(raw_split)),
            "total": str(money(by_kind["charges"])),
            "rows": split_rows,
        },
        "accounts": [
            {
                "code": a["code"],
                "name": a["name"],
                "total": str(money(a["total"])),
                "components": {k: str(money(v)) for k, v in a["components"].items()},
            }
            for a in accounts
        ],
        "total": str(total),
        "overlap": overlap_out,
        "overlap_amount": str(money(sum((charges_by_account[c] for c in overlap_codes), ZERO))),
    }


def split_without_overlap(db: Session, ctx: Context) -> tuple[dict, dict, list[dict]]:
    """Rateio sem as contas sobrepostas → (rateio atual, novo rateio, sobreposição). Os pesos restantes ficam como
    estão: `normalize_split` renormaliza no cálculo, então o total de encargos não muda."""
    overlap = split_overlap(db, ctx)
    codes = {o["code"] for o in overlap}
    raw = ctx.params.get(CHARGES_SPLIT_KEY)
    current = dict(raw) if isinstance(raw, dict) else {}
    return current, {k: v for k, v in current.items() if str(k).strip() not in codes}, overlap


# ------------------------------------------------------------------ what-if das premissas (nada é gravado)

WHAT_IF_TOP_CC = 15
WHAT_IF_SUMMARY = (
    ("total", "Total de pessoal"),
    ("salary", "Salários (com dissídio)"),
    ("charges", "Parte do multiplicador"),
    ("bonus", "Abono anual do CLT"),
    ("cc_bonus", "Bônus CLT por CC"),
    ("severance", "Verbas rescisórias"),
)


@dataclass
class PremisesWhatIf:
    """Premissas simuladas; None/False/0 = como está no ciclo."""

    salary_adjustment_pct: Decimal | None = None  # fração (0,05 = 5%)
    adjustment_month: int | None = None
    multipliers: dict[str, Decimal] = field(default_factory=dict)
    annual_bonus: Decimal | None = None  # abono anual do CLT, R$ por pessoa no ano
    cc_bonus_pct: Decimal = ZERO  # ajuste sobre o bônus por CC do parâmetro (fração: 0,05 = +5%)
    include_cc_bonus: bool = True
    remove_overlap: bool = False  # simula "Retirar do rateio"
    include_severance: bool = True


def what_if_context(db: Session, ctx: Context, w: PremisesWhatIf) -> tuple[Context, list[str]]:
    """Contexto com os parâmetros simulados (abono, bônus por CC, rateio) → (contexto, contas retiradas do rateio).
    Cópia de `ctx.params`: nada é gravado."""
    params = dict(ctx.params)
    if w.annual_bonus is not None:
        params["personnel.annual_bonus_clt"] = str(money(w.annual_bonus))
    raw_cc = params.get("personnel.bonus_by_cc") or {}
    raw_cc = raw_cc if isinstance(raw_cc, dict) else {}
    if not w.include_cc_bonus:
        params["personnel.bonus_by_cc"] = {}
    elif w.cc_bonus_pct:
        params["personnel.bonus_by_cc"] = {
            k: str(money(_positive(v) * (1 + w.cc_bonus_pct))) for k, v in raw_cc.items()
        }
    sim = dataclasses.replace(ctx, params=params)
    removed: list[str] = []
    if w.remove_overlap:
        # as contas sobrepostas hoje e as que a simulação sobrepõe (ex.: abono que passa a existir)
        _current, new_split, overlap = split_without_overlap(db, ctx)
        codes = {o["code"] for o in overlap} | {o["code"] for o in split_overlap(db, sim)}
        new_split = {k: v for k, v in new_split.items() if str(k).strip() not in codes}
        removed = sorted(codes & {str(k).strip() for k in (_current or {})})
        params[CHARGES_SPLIT_KEY] = new_split
    return sim, removed


def _cmp(current: Decimal, simulated: Decimal) -> dict:
    current, simulated = money(current), money(simulated)
    diff = simulated - current
    return {
        "current": str(current),
        "simulated": str(simulated),
        "difference": str(diff),
        "difference_pct": str((diff / abs(current)).quantize(Decimal("0.0001"))) if current else None,
    }


def _ranked(rows: list[dict]) -> list[dict]:
    """Maior diferença primeiro (em módulo); empate (ex.: sem simulação) pelo maior valor atual."""
    return sorted(rows, key=lambda r: (-abs(Decimal(r["difference"])), -Decimal(r["current"]), str(r.get("label"))))


def _top(rows: list[dict], n: int, cur: Decimal, sim: Decimal) -> dict:
    top, rest = rows[:n], rows[n:]
    others = None
    if rest:
        others = {
            "label": f"Demais ({len(rest)})",
            **_cmp(
                sum((Decimal(r["current"]) for r in rest), ZERO), sum((Decimal(r["simulated"]) for r in rest), ZERO)
            ),
        }
    return {"rows": top, "others": others, "count": len(rows), "total": {"label": "Total", **_cmp(cur, sim)}}


def premises_what_if(db: Session, ctx: Context, w: PremisesWhatIf) -> dict:
    """Atual × simulado do custo de pessoal do ano (página Premissas de pessoal, Controladoria: todos os CCs). Os dois
    lados passam por `personnel_amounts` — o atual é exatamente o da consolidação; o quadro é montado uma vez só
    (cache do request). Só leitura: parâmetros e cenário simulados vivem em memória."""
    base = personnel_svc.baseline(db, ctx)
    row = db.get(PersonnelScenario, base.id) if base.id else None
    sim_ctx, removed = what_if_context(db, ctx, w)
    sim = personnel_svc.with_cycle_bonus(
        personnel_svc.scenario_info(
            db,
            row,
            multipliers=w.multipliers,
            salary_adjustment_pct=w.salary_adjustment_pct,
            adjustment_month=w.adjustment_month,
            name="Simulação",
        ),
        sim_ctx,
    )
    positions = personnel_svc.build_positions(db, ctx)
    cur = personnel_breakdown(db, ctx, scenario=base, positions=positions)
    simb = personnel_breakdown(db, sim_ctx, scenario=sim, positions=positions, include_severance=w.include_severance)

    summary = []
    for key, label in WHAT_IF_SUMMARY:
        a = cur.total if key == "total" else cur.by_kind.get(key, ZERO)
        b = simb.total if key == "total" else simb.by_kind.get(key, ZERO)
        summary.append({"key": key, "label": f"{label} {ctx.target_year}" if key == "total" else label, **_cmp(a, b)})

    accounts = []
    for code in set(cur.by_account) | set(simb.by_account):
        a, b = cur.by_account.get(code), simb.by_account.get(code)
        accounts.append(
            {
                "code": code,
                "label": (a or b)["name"],
                **_cmp(a["total"] if a else ZERO, b["total"] if b else ZERO),
            }
        )
    accounts.sort(key=lambda r: (-max(Decimal(r["current"]), Decimal(r["simulated"])), r["code"]))

    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(set(cur.by_cc) | set(simb.by_cc))))}
    areas = {a.id: a.name for a in db.scalars(select(Area))}
    cc_rows = []
    by_area: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for cc_id in set(cur.by_cc) | set(simb.by_cc):
        a = cur.by_cc.get(cc_id, {}).get("total", ZERO)
        b = simb.by_cc.get(cc_id, {}).get("total", ZERO)
        if not a and not b:
            continue
        cc = ccs.get(cc_id)
        cc_rows.append(
            {"cost_center_id": cc_id, "code": cc.code if cc else None, "label": cc.name if cc else "—", **_cmp(a, b)}
        )
        area = areas.get(cc.area_id) if cc and cc.area_id else None
        by_area[area or "Sem área"][0] += a
        by_area[area or "Sem área"][1] += b
    area_rows = [{"label": name, **_cmp(v[0], v[1])} for name, v in by_area.items()]

    monthly = [{"month": m, **_cmp(cur.monthly[m - 1], simb.monthly[m - 1])} for m in range(1, 13)]
    return {
        "target_year": ctx.target_year,
        "version": ctx.version.label,
        "frozen": ctx.frozen,
        "current_premises": {
            "salary_adjustment_pct": str(base.salary_adjustment_pct),
            "adjustment_month": base.adjustment_month,
            "multipliers": {k: str(v) for k, v in base.multipliers.items()},
            "annual_bonus": str(money(base.annual_bonus)),
        },
        "simulated_premises": {
            "salary_adjustment_pct": str(sim.salary_adjustment_pct),
            "adjustment_month": sim.adjustment_month,
            "multipliers": {k: str(v) for k, v in sim.multipliers.items()},
            "annual_bonus": str(money(sim.annual_bonus)),
            "cc_bonus_pct": str(w.cc_bonus_pct),
            "include_cc_bonus": w.include_cc_bonus,
            "remove_overlap": w.remove_overlap,
            "include_severance": w.include_severance,
        },
        "removed_from_split": removed,
        "summary": summary,
        "accounts": accounts,
        "cost_centers": _top(_ranked(cc_rows), WHAT_IF_TOP_CC, cur.total, simb.total),
        "areas": _top(_ranked(area_rows), 50, cur.total, simb.total),
        "monthly": monthly,
    }
