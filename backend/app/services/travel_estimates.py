"""Passagens estimadas pela Controladoria (08/10/2026).

Os templates vieram com a tabela de tarifas vazia: todas as viagens entraram sem passagem. A Controladoria estima a
passagem por rota (`travel.route_estimates`, ida e volta) ou com um valor fixo por viagem (`travel.flat_estimate`),
e pode trocar de um modo para o outro com um clique. Só viagens sem passagem real são tocadas: uma passagem informada
pelo gestor (sem a marca `ticket_estimate`) nunca é sobrescrita. A linha fica marcada como estimativa, não orçada no
template, e cada aplicação vai para a auditoria e para Apontamentos.
"""

from collections import defaultdict
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.models import BudgetLine, BudgetSubmission, CostCenter, FindingReview
from app.services import opex as opex_svc

ROUTE_KEY = "travel.route_estimates"
FLAT_KEY = "travel.flat_estimate"
MODES = ("route", "flat")
MODE_LABELS = {"route": "média por rota", "flat": "valor fixo por viagem"}
NOTE = "Passagem estimada pela Controladoria ({how}) — não orçada no template"
# estimativas de referência (ida e volta, econômica, compra antecipada), chave "ORIGEM>DESTINO" em maiúsculas
DEFAULT_ROUTES = {
    "AM>SP": 3200,
    "SP>AM": 3200,
    "CE>AM": 2900,
    "AM>PA": 2200,
    "AM>RJ": 3500,
    "AM>RO": 2200,
    "AM>AP": 2500,
    "AM>GO": 3200,
    "AM>MT": 3500,
    "AM>PR": 3800,
    "AM>SC": 4000,
    "CE>SP": 2200,
    "AM>PERU (LIMA)": 5500,
}
DEFAULT_FLAT = 4000


def route_key(origin: str | None, destination: str | None) -> str:
    return f"{(origin or '').strip().upper()}>{(destination or '').strip().upper()}"


def tables(ctx: opex_svc.Context) -> tuple[dict[str, Decimal], Decimal]:
    raw = ctx.params.get(ROUTE_KEY) or {}
    routes = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            try:
                amount = money(Decimal(str(v)))
            except ArithmeticError:
                continue
            if amount > 0:
                routes[str(k).strip().upper()] = amount
    flat = ctx.params.get(FLAT_KEY)
    try:
        flat_amount = money(Decimal(str(flat))) if flat is not None else money(Decimal(DEFAULT_FLAT))
    except ArithmeticError:
        flat_amount = money(Decimal(DEFAULT_FLAT))
    return routes, flat_amount


def _trips(db: Session, ctx: opex_svc.Context) -> list[tuple[BudgetSubmission, str, list[BudgetLine]]]:
    subs = {
        s.id: s
        for s in db.scalars(
            select(BudgetSubmission).where(
                BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.module == "OPEX"
            )
        )
    }
    groups: dict[tuple[int, str], list[BudgetLine]] = defaultdict(list)
    for line in db.scalars(
        select(BudgetLine).where(BudgetLine.submission_id.in_(subs or {-1}), BudgetLine.line_type == "TRAVEL")
    ):
        if line.group_ref:
            groups[(line.submission_id, line.group_ref)].append(line)
    return [(subs[sid], ref, lines) for (sid, ref), lines in groups.items()]


def estimate_for(mode: str, attrs: dict, routes: dict[str, Decimal], flat: Decimal) -> tuple[Decimal, str]:
    """(valor, como foi estimado). Por rota sem tarifa cadastrada cai no valor fixo."""
    if mode == "route":
        key = route_key(attrs.get("origin"), attrs.get("destination"))
        if key in routes:
            return routes[key], f"média por rota {key.replace('>', ' → ')}"
        return flat, f"rota {key.replace('>', ' → ')} sem tarifa: valor fixo"
    return flat, "valor fixo por viagem"


def status(db: Session, ctx: opex_svc.Context) -> dict:
    """Situação das passagens no ciclo e o total que cada modo daria."""
    routes, flat = tables(ctx)
    counts = {"real": 0, "route": 0, "flat": 0, "none": 0}
    current = Decimal(0)
    projected = {"route": Decimal(0), "flat": Decimal(0)}
    missing_routes: set[str] = set()
    for _sub, _ref, lines in _trips(db, ctx):
        attrs = lines[0].attributes or {}
        ticket = money(Decimal(str(attrs.get("ticket_amount") or 0)))
        est = attrs.get("ticket_estimate")
        if ticket > 0 and not est:
            counts["real"] += 1
            current += ticket
            continue
        counts[est["mode"] if est else "none"] += 1
        current += ticket
        for mode in MODES:
            amount, _how = estimate_for(mode, attrs, routes, flat)
            projected[mode] += amount
        if route_key(attrs.get("origin"), attrs.get("destination")) not in routes:
            missing_routes.add(route_key(attrs.get("origin"), attrs.get("destination")))
    mode = (
        "route"
        if counts["route"] and not counts["flat"]
        else "flat"
        if counts["flat"] and not counts["route"]
        else None
    )
    return {
        "trips": sum(counts.values()),
        "counts": counts,
        "mode": mode,
        "current_total": str(current),
        "projected": {k: str(v) for k, v in projected.items()},
        "routes": {k: str(v) for k, v in sorted(routes.items())},
        "flat": str(flat),
        "missing_routes": sorted(missing_routes),
    }


def apply(db: Session, ctx: opex_svc.Context, mode: str, user_id: int) -> dict:
    """Aplica a estimativa a toda viagem sem passagem real. Devolve o resumo para a auditoria."""
    if mode not in MODES:
        raise opex_svc.OpexError("Modo inválido: use 'route' ou 'flat'")
    routes, flat = tables(ctx)
    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    changed, total = 0, Decimal(0)
    per_cc: dict[str, Decimal] = defaultdict(lambda: Decimal(0))
    for sub, ref, lines in _trips(db, ctx):
        attrs = dict(lines[0].attributes or {})
        ticket = money(Decimal(str(attrs.get("ticket_amount") or 0)))
        if ticket > 0 and not attrs.get("ticket_estimate"):
            continue  # passagem real do gestor: não mexe
        amount, how = estimate_for(mode, attrs, routes, flat)
        if not attrs.get("departure_month"):
            continue
        updated = opex_svc.set_trip_ticket(db, sub, ref, amount, user_id)
        note = NOTE.format(how=how)
        for line in updated:
            a = dict(line.attributes or {})
            a["ticket_estimate"] = {
                "mode": mode,
                "amount": str(amount),
                "how": how,
                "by": user_id,
                "at": datetime.utcnow().isoformat(timespec="seconds"),
            }
            a["warnings"] = [w for w in a.get("warnings") or [] if not w.startswith("Passagem estimada")] + [note]
            line.attributes = a
        db.add(
            FindingReview(
                submission_id=sub.id,
                finding_key=f"{sub.id}:TRAVEL:{ref}:TRAVEL_NO_FARE",
                kind="TRAVEL_NO_FARE",
                action="CORRECTED",
                severity="WARNING",
                subject=(lines[0].description or "Viagem")[:300],
                message="Passagem zerada no template",
                note=f"Passagem {money(amount)}: {note}",
                user_id=user_id,
            )
        )
        cc = ccs.get(sub.cost_center_id)
        per_cc[cc.code if cc else str(sub.cost_center_id)] += amount
        changed += 1
        total += amount
    db.flush()
    return {
        "mode": mode,
        "trips": changed,
        "total": str(total),
        "per_cost_center": {k: str(v) for k, v in sorted(per_cc.items())},
    }
