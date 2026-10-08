"""Consolidação: painel executivo (OPEX + CAPEX + Pessoal), variações, pontos de atenção, versões e exportação."""

from collections import defaultdict
from datetime import date
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user, require_roles, visible_cost_center_ids
from app.db import get_db
from app.domain.rules.common import money
from app.models import Account, BudgetPackage, BudgetSubmission, BudgetVersion, CostCenter, User
from app.models.base import Role
from app.services import audit, exports
from app.services import capex as capex_svc
from app.services import consolidation as svc
from app.services import opex as opex_svc
from app.services import personnel as personnel_svc

router = APIRouter(prefix="/consolidation", tags=["Consolidação"])
ZERO = Decimal("0")
controller = require_roles(Role.CONTROLLER)
KIND_LABELS = {"salary": "salários", "charges": "encargos e benefícios", "severance": "verbas rescisórias"}


def _ctx(db: Session) -> opex_svc.Context:
    try:
        return opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


def _version(db: Session, ctx: opex_svc.Context, version_id: int | None) -> BudgetVersion:
    if version_id is None:
        return ctx.version
    version = db.get(BudgetVersion, version_id)
    if version is None or version.cycle_id != ctx.cycle.id:
        raise HTTPException(404, "Versão não encontrada")
    return version


def _scope(db: Session, user: User, company_id: int | None, cost_center_id: int | None) -> set[int] | None:
    visible = visible_cost_center_ids(db, user)
    if company_id is None and cost_center_id is None:
        return visible
    stmt = select(CostCenter.id)
    if company_id:
        stmt = stmt.where(CostCenter.company_id == company_id)
    if cost_center_id:
        stmt = stmt.where(CostCenter.id == cost_center_id)
    ids = set(db.scalars(stmt))
    return ids if visible is None else ids & visible


def _versions(db: Session, ctx: opex_svc.Context) -> list[dict]:
    return [
        {
            "id": v.id,
            "label": v.label,
            "status": v.status,
            "reason": v.reason,
            "frozen_at": v.frozen_at.isoformat() if v.frozen_at else None,
            "created_at": v.created_at.isoformat() if v.created_at else None,
            "current": v.id == ctx.version.id,
        }
        for v in db.scalars(
            select(BudgetVersion)
            .where(BudgetVersion.cycle_id == ctx.cycle.id)
            .order_by(BudgetVersion.major, BudgetVersion.minor)
        )
    ]


def _deadline(ctx: opex_svc.Context, module: str) -> date | None:
    return {
        "OPEX": ctx.cycle.opex_deadline,
        "CAPEX": ctx.cycle.capex_deadline,
        "PERSONNEL": ctx.cycle.personnel_deadline,
    }[module]


@router.get("/overview", summary="Orçamento consolidado: totais por módulo, mês, pacote, empresa e status")
def overview(
    version_id: int | None = None,
    company_id: int | None = None,
    cost_center_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = _ctx(db)
    version = _version(db, ctx, version_id)
    vctx = svc.version_context(ctx, version)
    scope = _scope(db, user, company_id, cost_center_id)
    rows = svc.rows_for(db, ctx, version, scope)
    ref = svc.reference(db, vctx, scope)

    modules = {}
    for m in svc.MODULES:
        natures = svc.MODULE_NATURES[m]
        codes = [c for c, n in ref.natures.items() if n in natures]
        mrows = [r for r in rows if r.module == m]
        modules[m] = {
            "label": svc.MODULE_LABELS[m],
            "proposed": str(money(sum((r.total for r in mrows), ZERO))),
            "prev_actual": str(money(sum((ref.prev.get(c, ZERO) for c in codes), ZERO))),
            "ref_annualized": str(money(sum((ref.ref_annualized.get(c, ZERO) for c in codes), ZERO))),
            "ref_budget": str(money(sum((ref.ref_budget.get(c, ZERO) for c in codes), ZERO))),
            "monthly": [str(money(sum((r.values[i] for r in mrows), ZERO))) for i in range(12)],
            "unscheduled": str(money(sum((r.unscheduled for r in mrows), ZERO))),  # CAPEX sem cronograma
        }
    total = sum((Decimal(v["proposed"]) for v in modules.values()), ZERO)

    by_package: dict[str, dict[str, Decimal]] = defaultdict(lambda: {"proposed": ZERO, "ref": ZERO})
    for r in rows:
        by_package[r.package or "Sem pacote"]["proposed"] += r.total
    accounts = {a.code: a for a in db.scalars(select(Account))}
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}
    for code, amount in ref.ref_annualized.items():
        acc = accounts.get(code)
        if acc is None or svc.module_of_nature(acc.nature) is None:
            continue
        name = "Pessoas" if acc.nature == "PESSOAL" else packages.get(acc.package_id) or "Sem pacote"
        by_package[name]["ref"] += amount

    by_company: dict[str, Decimal] = defaultdict(lambda: ZERO)
    by_cc: dict[int, dict] = {}
    for r in rows:
        by_company[r.company_code] += r.total
        entry = by_cc.setdefault(
            r.cost_center_id,
            {"cost_center_id": r.cost_center_id, "code": r.cost_center_code, "name": r.cost_center_name}
            | {m: ZERO for m in svc.MODULES},
        )
        entry[r.module] += r.total

    # status por CC × módulo (versão escolhida)
    stmt = select(CostCenter).where(CostCenter.is_active).order_by(CostCenter.code)
    if scope is not None:
        stmt = stmt.where(CostCenter.id.in_(scope or {-1}))
    ccs = db.scalars(stmt).all()
    subs = defaultdict(dict)
    for s in db.scalars(select(BudgetSubmission).where(BudgetSubmission.version_id == version.id)):
        subs[s.cost_center_id][s.module] = s.status
    matrix = []
    counts = {m: defaultdict(int) for m in svc.MODULES}
    for cc in ccs:
        values = by_cc.get(cc.id) or {m: ZERO for m in svc.MODULES}
        statuses = {m: subs[cc.id].get(m, "DRAFT") for m in svc.MODULES}
        for m in svc.MODULES:
            counts[m][statuses[m]] += 1
        matrix.append(
            {
                "cost_center_id": cc.id,
                "code": cc.code,
                "name": cc.name,
                "company_code": cc.company.code,
                "manager_name": cc.manager_name,
                "status": statuses,
                "totals": {m: str(money(values[m])) for m in svc.MODULES},
                "total": str(money(sum((values[m] for m in svc.MODULES), ZERO))),
            }
        )
    variations = svc.account_variations(rows, ref, vctx)
    ref_total = sum((Decimal(v["ref_annualized"]) for v in modules.values()), ZERO)
    return {
        "cycle": {"id": ctx.cycle.id, "name": ctx.cycle.name, "status": ctx.cycle.status},
        "years": {"prev": ctx.prev_year, "ref": ctx.ref_year, "target": ctx.target_year},
        "version": {"id": version.id, "label": version.label, "status": version.status},
        "versions": _versions(db, ctx),
        "modules": modules,
        "total": str(money(total)),
        "ref_total": str(money(ref_total)),
        "prev_total": str(money(sum((Decimal(v["prev_actual"]) for v in modules.values()), ZERO))),
        "by_package": sorted(
            (
                {"label": k, "proposed": str(money(v["proposed"])), "ref_annualized": str(money(v["ref"]))}
                for k, v in by_package.items()
            ),
            key=lambda r: -max(Decimal(r["proposed"]), Decimal(r["ref_annualized"])),
        ),
        "by_company": [{"company": k, "total": str(money(v))} for k, v in sorted(by_company.items())],
        "status_counts": {m: dict(v) for m, v in counts.items()},
        "matrix": matrix,
        "variations": variations[:200],
        "flag_counts": {
            f: sum(1 for v in variations if f in v["flags"])
            for f in ("GROWTH_ABOVE", "REDUCTION_ABOVE", "NEW_ACCOUNT", "NO_BUDGET")
        },
        "personnel_accounts": {k: {"code": c, "name": n} for k, (c, n) in svc.personnel_accounts(db, vctx).items()},
        # rateio da parte do multiplicador (encargos e benefícios) entre contas
        "personnel_charges_split": [
            {"code": c, "name": n, "weight": str(w.quantize(Decimal("0.0001")))}
            for c, n, w in svc.charges_split(db, vctx)
        ],
    }


@router.get("/attention-points", summary="Pendências e riscos para a Controladoria fechar o orçamento")
def attention_points(
    company_id: int | None = None,
    cost_center_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = _ctx(db)
    scope = _scope(db, user, company_id, cost_center_id)
    stmt = select(CostCenter).where(CostCenter.is_active)
    if scope is not None:
        stmt = stmt.where(CostCenter.id.in_(scope or {-1}))
    ccs = {c.id: c for c in db.scalars(stmt)}
    subs = defaultdict(dict)
    for s in db.scalars(
        select(BudgetSubmission).where(
            BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.cost_center_id.in_(list(ccs) or [-1])
        )
    ):
        subs[s.cost_center_id][s.module] = s
    points: list[dict] = []
    today = date.today()
    route = {"OPEX": "/orcamento", "CAPEX": "/capex", "PERSONNEL": "/pessoal"}

    def add(severity, module, cc, message, kind):
        points.append(
            {
                "severity": severity,
                "module": module,
                "module_label": svc.MODULE_LABELS.get(module, module),
                "kind": kind,
                "cost_center_id": cc.id if cc else None,
                "cost_center": f"{cc.code} · {cc.name}" if cc else None,
                "message": message,
                "link": f"{route[module]}/{cc.id}" if cc and module in route else None,
            }
        )

    if ctx.frozen:
        add("info", "OPEX", None, f"Versão {ctx.version.label} congelada: abra uma revisão para alterar", "FROZEN")
    for module in svc.MODULES:
        deadline = _deadline(ctx, module)
        late = deadline is not None and today > deadline
        not_started = [
            cc for cc_id, cc in ccs.items() if module not in subs[cc_id] or subs[cc_id][module].status == "DRAFT"
        ]
        if module == "OPEX" and not_started:
            sev = "high" if late else "medium"
            when = f" (prazo {deadline.strftime('%d/%m')}{' vencido' if late else ''})" if deadline else ""
            add(
                sev,
                module,
                None,
                f"{len(not_started)} CC(s) sem orçamento {svc.MODULE_LABELS[module]} iniciado{when}",
                "NOT_STARTED",
            )
        for cc_id, by_module in subs.items():
            sub = by_module.get(module)
            cc = ccs.get(cc_id)
            if sub is None or cc is None:
                continue
            if sub.status == "ADJUSTMENT_REQUESTED":
                add("medium", module, cc, "Ajuste solicitado aguardando o gestor", "ADJUSTMENT")
            elif sub.status == "SUBMITTED":
                add("medium", module, cc, "Enviado: aguardando análise da Controladoria", "AWAITING_REVIEW")
            elif sub.status == "IN_PROGRESS" and late:
                add(
                    "high",
                    module,
                    cc,
                    f"Prazo vencido em {deadline.strftime('%d/%m')} e ainda em preenchimento",
                    "LATE",
                )
            if sub.status in ("IN_PROGRESS", "ADJUSTMENT_REQUESTED", "SUBMITTED", "UNDER_REVIEW"):
                if module == "OPEX":
                    pending = opex_svc.account_view(db, ctx, sub)["pending_justifications"]
                    if pending:
                        add(
                            "medium",
                            module,
                            cc,
                            f"{pending} conta(s) com variação acima do limite sem justificativa",
                            "JUSTIFICATION",
                        )
                elif module == "CAPEX":
                    critical = capex_svc.blockers(db, ctx, sub)
                    if critical and critical != ["nenhum item de CAPEX lançado"]:
                        add("high", module, cc, f"Pendência crítica: {critical[0]}", "CAPEX_CRITICAL")
                elif module == "PERSONNEL":
                    for b in personnel_svc.blockers(db, ctx, sub):
                        add("medium", module, cc, b[0].upper() + b[1:], "JUSTIFICATION")
    # contas de pessoal da consolidação inexistentes no plano de contas
    configured = [(KIND_LABELS[kind], code) for kind, (code, _name) in svc.personnel_accounts(db, ctx).items()]
    configured += [("rateio de encargos e benefícios", code) for code, _n, _w in svc.charges_split(db, ctx)]
    seen: set[str] = set()
    for label, code in configured:
        if code in seen:
            continue
        seen.add(code)
        if db.scalar(select(Account.id).where(Account.code == code)) is None:
            add(
                "low",
                "PERSONNEL",
                None,
                f"Conta de {label} {code} não está no cadastro de contas: confirme em Ciclo e parâmetros",
                "ACCOUNT_CONFIG",
            )
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    points.sort(key=lambda p: (order[p["severity"]], p["module"], p["cost_center"] or ""))
    return {"points": points, "counts": {k: sum(1 for p in points if p["severity"] == k) for k in order}}


class VersionActionIn(BaseModel):
    reason: str | None = None
    major: bool = False


@router.post("/freeze", summary="Congelar a versão em elaboração (grava a fotografia do consolidado)")
def freeze(payload: VersionActionIn, request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    ctx = _ctx(db)
    try:
        result = svc.freeze(db, ctx, user.id, payload.reason)
    except svc.ConsolidationError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    audit.record(
        db,
        user_id=user.id,
        action="VERSION_FREEZE",
        entity_type="budget_version",
        entity_id=ctx.version.id,
        after=result,
        reason=payload.reason,
        ip=client_ip(request),
    )
    db.commit()
    return result | {"versions": _versions(db, _ctx(db))}


@router.post("/revise", summary="Abrir revisão: nova versão em elaboração a partir da congelada")
def revise(payload: VersionActionIn, request: Request, db: Session = Depends(get_db), user: User = Depends(controller)):
    ctx = _ctx(db)
    try:
        new = svc.revise(db, ctx, user.id, payload.reason or "", major=payload.major)
    except svc.ConsolidationError as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    audit.record(
        db,
        user_id=user.id,
        action="VERSION_REVISE",
        entity_type="budget_version",
        entity_id=new.id,
        after={"version": new.label, "from": ctx.version.label},
        reason=payload.reason,
        ip=client_ip(request),
    )
    db.commit()
    return {"version": new.label, "versions": _versions(db, _ctx(db))}


@router.get("/export.xlsx", summary="Exportação do orçamento consolidado (resumo, carga SAP, detalhes e variações)")
def export_xlsx(
    version_id: int | None = None,
    company_id: int | None = None,
    cost_center_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = _ctx(db)
    version = _version(db, ctx, version_id)
    scope = _scope(db, user, company_id, cost_center_id)
    content = exports.budget_workbook(db, ctx, version, scope, user)
    name = f"Orcamento_{ctx.target_year}_v{version.label}.xlsx"
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/compare", summary="O que mudou entre duas versões (módulo, CC e conta)")
def compare(
    from_version_id: int,
    to_version_id: int,
    company_id: int | None = None,
    cost_center_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = _ctx(db)
    a, b = _version(db, ctx, from_version_id), _version(db, ctx, to_version_id)
    if a.id == b.id:
        raise HTTPException(422, "Escolha duas versões diferentes")
    return svc.compare_versions(db, ctx, a, b, _scope(db, user, company_id, cost_center_id))
