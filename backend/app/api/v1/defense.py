"""Defesa do orçamento (08/10/2026): "por quê?" de uma linha do Painel e perguntas ao gestor da área.

O "por quê?" compara sempre o **orçamento do ciclo** com o **realizado do ano anterior anualizado** (até o último mês
fechado × 12/mês), nos filtros do Painel (empresa, área, CC, pacote, conta, tipo) e no recorte da linha (parent_*,
como no drill-down). Traz a decomposição (contas e CCs que mais explicam a variação), o que o gestor justificou
(OPEX, Pessoal, CAPEX), alertas e as perguntas do recorte.
"""

import re
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.dashboard import Facts, _ints, _last_closed, _period
from app.core.deps import client_ip, get_current_user, is_global, visible_cost_center_ids
from app.db import get_db
from app.models import (
    Account,
    ActualEntry,
    BudgetPackage,
    BudgetQuestion,
    CostCenter,
    ReferenceBudgetEntry,
    User,
)
from app.services import audit
from app.services import consolidation as cons
from app.services import justifications as just_svc
from app.services import opex as opex_svc
from app.services import questions as q_svc

router = APIRouter(tags=["defesa do orçamento"])
ZERO = Decimal("0")
MODULES = ("PERSONNEL", "OPEX", "CAPEX")
MODULE_LABELS = {"OPEX": "OPEX", "PERSONNEL": "Pessoal", "CAPEX": "CAPEX"}
SOFTWARE = re.compile(r"software|licen|inform", re.IGNORECASE)


def _m(v: Decimal) -> str:
    return str(v.quantize(Decimal("0.01")))


def _pct(new: Decimal, base: Decimal) -> str | None:
    return str(((new - base) / base).quantize(Decimal("0.0001"))) if base else None


def _ctx(db: Session) -> opex_svc.Context:
    try:
        return opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc


def _scope_ccs(db: Session, user: User, company_id, cost_center_id, department_id, parent: dict) -> list[int]:
    """CCs do recorte (filtros do Painel + linha), dentro do que o usuário enxerga."""
    stmt = select(CostCenter.id)
    visible = visible_cost_center_ids(db, user)
    if visible is not None:
        stmt = stmt.where(CostCenter.id.in_(visible or {-1}))
    if _ints(company_id):
        stmt = stmt.where(CostCenter.company_id.in_(_ints(company_id)))
    if cost_center_id:
        stmt = stmt.where(CostCenter.id == cost_center_id)
    if _ints(department_id):
        stmt = stmt.where(CostCenter.department_id.in_(_ints(department_id)))
    for key in ("department_id", "area_id"):
        column = getattr(CostCenter, key)
        if parent.get(key) is not None:
            stmt = stmt.where(column == parent[key])
        elif parent.get(f"no_{key}"):
            stmt = stmt.where(column.is_(None))
    return list(db.scalars(stmt))


@router.get(
    "/dashboard/why", summary='"Por quê?": orçamento do ciclo × realizado anterior anualizado, com justificativas'
)
def why(
    parent_department_id: int | None = None,
    parent_no_department: bool = False,
    parent_area_id: int | None = None,
    parent_no_area: bool = False,
    parent_package_id: int | None = None,
    parent_no_package: bool = False,
    parent_account_id: int | None = None,
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    modules: str | None = None,
    label: str | None = Query(None, max_length=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = _ctx(db)
    target = ctx.target_year
    ref = target - 1
    P = _period(db, str(target), None, modules, False, True)
    parent = {
        "package_id": parent_package_id,
        "no_package": parent_no_package,
        "account_id": parent_account_id,
        "department_id": parent_department_id,
        "no_department_id": parent_no_department,
        "area_id": parent_area_id,
        "no_area_id": parent_no_area,
    }
    f = Facts(
        db, user, company_id, cost_center_id, package_id, P, parent, account_id=account_id, department_id=department_id
    )
    closed = _last_closed(db, ref)
    factor = Decimal(12) / Decimal(closed) if closed else Decimal(1)
    allowed = P.modules or set(MODULES)

    # totais por tipo
    prop_mod = f.by_module(ReferenceBudgetEntry, [target])
    act_mod = f.by_module(ActualEntry, [ref])
    by_module = []
    for m in MODULES:
        if m not in allowed:
            continue
        b, a = prop_mod.get(m, ZERO), act_mod.get(m, ZERO) * factor
        if a or b:
            by_module.append(
                {
                    "module": m,
                    "label": MODULE_LABELS[m],
                    "base": _m(a),
                    "proposed": _m(b),
                    "var": _m(b - a),
                    "var_pct": _pct(b, a),
                }
            )
    total_b = sum((Decimal(x["proposed"]) for x in by_module), ZERO)
    total_a = sum((Decimal(x["base"]) for x in by_module), ZERO)

    # decomposição por conta e por CC
    prop = {(cc, acc): v for cc, acc, v in f.sums(ReferenceBudgetEntry, [target], "cost_center_id", "account_id")}
    act = {(cc, acc): v * factor for cc, acc, v in f.sums(ActualEntry, [ref], "cost_center_id", "account_id")}
    keys = set(prop) | set(act)
    by_acc: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    by_cc: dict[int, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for k in keys:
        a, b = act.get(k, ZERO), prop.get(k, ZERO)
        by_acc[k[1]][0] += a
        by_acc[k[1]][1] += b
        by_cc[k[0]][0] += a
        by_cc[k[0]][1] += b
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_({k for k in by_acc if k} or {-1})))}
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_({k for k in by_cc if k} or {-1})))}

    def driver(name, sub, a, b, module=None, **extra):
        return {
            "name": name,
            "sub": sub,
            "base": _m(a),
            "proposed": _m(b),
            "var": _m(b - a),
            "var_pct": _pct(b, a),
            "module": module,
        } | extra

    acc_drivers = []
    for acc_id, (a, b) in by_acc.items():
        acc = accounts.get(acc_id)
        module = cons.module_of_nature(acc.nature) if acc else None
        name = f"{acc.code} {acc.name}" if acc else "Sem conta"
        acc_drivers.append(driver(name, packages.get(acc.package_id) if acc else None, a, b, module, account_id=acc_id))
    acc_drivers.sort(key=lambda d: -abs(Decimal(d["var"])))
    cc_drivers = [
        driver(f"{ccs[c].code} · {ccs[c].name}" if c in ccs else "Sem CC", None, a, b, cost_center_id=c)
        for c, (a, b) in by_cc.items()
    ]
    cc_drivers.sort(key=lambda d: -abs(Decimal(d["var"])))

    # o que o gestor disse (mesmo recorte)
    scope = _scope_ccs(db, user, company_id, cost_center_id, department_id, parent)
    in_scope_accounts = {k[1] for k in keys}
    pkg_natures: set[str] = set()
    if package_id or parent_package_id:
        pid = package_id or parent_package_id
        pkg_natures = set(db.scalars(select(Account.nature).where(Account.package_id == pid)))
    narrow_account = bool(account_id or parent_account_id)

    def module_ok(module: str) -> bool:
        if module not in allowed:
            return False
        if module == "OPEX":
            return True
        if narrow_account:
            return False
        if pkg_natures:
            return bool(pkg_natures & set(cons.MODULE_NATURES[module]))
        return True

    items = just_svc.collect(db, ctx, set(scope)) if scope else []
    opex_items, personnel_items, capex_items = [], [], []
    for i in items:
        if not module_ok(i["module"]):
            continue
        if i["module"] == "OPEX":
            if i["entity_id"] not in in_scope_accounts:
                continue
            k = (i["cost_center_id"], i["entity_id"])
            i = i | {
                "var": _m(prop.get(k, ZERO) - act.get(k, ZERO)),
                "base": _m(act.get(k, ZERO)),
                "proposed": _m(prop.get(k, ZERO)),
            }
            opex_items.append(i)
        elif i["module"] == "PERSONNEL":
            personnel_items.append(i)
        else:
            capex_items.append(i)
    opex_items.sort(key=lambda i: -abs(Decimal(i["var"])))
    moves = defaultdict(int)
    for i in personnel_items:
        moves[i["group"]] += 1

    # alertas
    alerts = []
    capex_var = next((Decimal(x["var"]) for x in by_module if x["module"] == "CAPEX"), ZERO)
    soft = [
        d for d in acc_drivers if d["module"] == "OPEX" and SOFTWARE.search(d["name"]) and Decimal(d["var"]) <= -50000
    ]
    if soft and capex_var > 0:
        drop = -sum((Decimal(d["var"]) for d in soft), ZERO)
        if capex_var >= drop * Decimal("0.5"):
            alerts.append(
                {
                    "tone": "warn",
                    "text": (
                        f"Possível reclassificação: {', '.join(d['name'] for d in soft[:2])} cai R$ {drop:,.0f} no "
                        f"OPEX e o CAPEX sobe R$ {capex_var:,.0f}. Confirmar com o gestor."
                    ).replace(",", "."),
                }
            )
    missing = sum(1 for i in opex_items + personnel_items + capex_items if not i["justified"])
    if missing:
        alerts.append(
            {"tone": "bad", "text": f"{missing} item(ns) do recorte sem justificativa (obrigatória para enviar)."}
        )

    # perguntas do recorte
    qs = []
    if scope:
        stmt = (
            select(BudgetQuestion)
            .where(BudgetQuestion.version_id == ctx.version.id)
            .order_by(BudgetQuestion.asked_at.desc())
        )
        visible = visible_cost_center_ids(db, user)
        scope_set = set(scope)
        for q in db.scalars(stmt):
            if not (q_svc.scope_ccs(q) & scope_set) or not q_svc.can_view(db, user, q, visible):
                continue
            if narrow_account and q.account_id and q.account_id != (account_id or parent_account_id):
                continue
            qs.append(q)
    names = q_svc.user_names(db, {x for q in qs for x in (q.asked_by, q.answered_by)})
    single = ccs.get(scope[0]) if len(scope) == 1 else None
    if single is None and len(scope) == 1:
        single = db.get(CostCenter, scope[0])
    return {
        "title": label or (f"{single.code} · {single.name}" if single else "Recorte do Painel"),
        "target_year": target,
        "ref_year": ref,
        "closed": closed,
        "base_label": f"Realizado {ref} anualizado"
        + (f" (até {closed:02d} × 12/{closed})" if closed and closed < 12 else ""),
        "total": {
            "base": _m(total_a),
            "proposed": _m(total_b),
            "var": _m(total_b - total_a),
            "var_pct": _pct(total_b, total_a),
        },
        "by_module": by_module,
        "drivers": {"accounts": acc_drivers[:10], "cost_centers": cc_drivers[:10] if len(cc_drivers) > 1 else []},
        "alerts": alerts,
        "opex": opex_items[:20],
        "opex_count": len(opex_items),
        "personnel": personnel_items[:30],
        "personnel_summary": dict(moves),
        "capex": capex_items[:20],
        "missing": missing,
        "questions": [q_svc.serialize(db, user, q, names) for q in qs[:30]],
        "scope": {
            "cost_center_ids": scope,
            "single_cost_center": {"id": single.id, "code": single.code, "name": single.name} if single else None,
            "account_id": account_id or parent_account_id,
            "department_id": (next(iter(_ints(department_id))) if len(_ints(department_id)) == 1 else None)
            or parent_department_id,
        },
    }


# ------------------------------------------------------------------ perguntas


class AskIn(BaseModel):
    subject: str = Field(min_length=1, max_length=300)
    question: str = Field(min_length=1, max_length=4000)
    scope: dict = Field(default_factory=dict)
    cost_center_id: int | None = None
    department_id: int | None = None
    account_id: int | None = None


class AnswerIn(BaseModel):
    answer: str = Field(min_length=1, max_length=4000)


def _question(db: Session, user: User, qid: int) -> BudgetQuestion:
    q = db.get(BudgetQuestion, qid)
    if q is None or not q_svc.can_view(db, user, q, visible_cost_center_ids(db, user)):
        raise HTTPException(404, "Pergunta não encontrada")
    return q


def _one(db: Session, user: User, q: BudgetQuestion) -> dict:
    return q_svc.serialize(db, user, q, q_svc.user_names(db, {q.asked_by, q.answered_by}))


@router.get("/questions", summary="Perguntas sobre o orçamento (as que o usuário fez e as que ele pode responder)")
def list_questions(
    status: str | None = Query(None, pattern="^(OPEN|ANSWERED|CLOSED)$"),
    to_answer: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    ctx = _ctx(db)
    visible = visible_cost_center_ids(db, user)
    stmt = select(BudgetQuestion).where(BudgetQuestion.version_id == ctx.version.id)
    if status:
        stmt = stmt.where(BudgetQuestion.status == status)
    qs = [q for q in db.scalars(stmt.order_by(BudgetQuestion.asked_at.desc())) if q_svc.can_view(db, user, q, visible)]
    out = [_one(db, user, q) for q in qs]
    if to_answer:
        out = [q for q in out if q["status"] == "OPEN" and q["can_answer"] and not q["mine"]]
    return {
        "items": out,
        "counts": {
            "to_answer": sum(1 for q in out if q["status"] == "OPEN" and q["can_answer"] and not q["mine"]),
            "answered_mine": sum(1 for q in out if q["status"] == "ANSWERED" and q["mine"]),
            "open": sum(1 for q in out if q["status"] == "OPEN"),
        },
        "can_review": is_global(user),
    }


@router.post("/questions", status_code=201, summary="Questionar o gestor da área sobre um recorte do orçamento")
def ask(body: AskIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    ctx = _ctx(db)
    try:
        q = q_svc.ask(db, ctx.version.id, user, body.model_dump())
    except q_svc.QuestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        user_id=user.id,
        action="QUESTION",
        entity_type="budget_question",
        entity_id=q.id,
        after={"subject": q.subject, "question": q.question, "cost_center_ids": sorted(q_svc.scope_ccs(q))},
        ip=client_ip(request),
    )
    db.commit()
    return _one(db, user, q)


@router.post("/questions/{qid}/answer", summary="Responder (gestor da área ou Controladoria)")
def answer(
    qid: int, body: AnswerIn, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)
):
    q = _question(db, user, qid)
    before = {"status": q.status, "answer": q.answer}
    try:
        q_svc.answer(db, user, q, body.answer)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except q_svc.QuestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        user_id=user.id,
        action="ANSWER",
        entity_type="budget_question",
        entity_id=q.id,
        before=before,
        after={"status": q.status, "answer": q.answer},
        ip=client_ip(request),
    )
    db.commit()
    return _one(db, user, q)


@router.post("/questions/{qid}/close", summary="Encerrar a pergunta (quem perguntou ou Controladoria)")
def close(qid: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    q = _question(db, user, qid)
    before = {"status": q.status}
    try:
        q_svc.close(db, user, q)
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except q_svc.QuestionError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.record(
        db,
        user_id=user.id,
        action="CLOSE",
        entity_type="budget_question",
        entity_id=q.id,
        before=before,
        after={"status": q.status},
        ip=client_ip(request),
    )
    db.commit()
    return _one(db, user, q)
