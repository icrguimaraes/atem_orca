"""Defesa do orçamento (10/11/2026): página do VP para apresentar e defender o orçamento do ciclo contra o que o
Planejamento cortou — **base** (versão `is_base`, hoje a Revisão 1 · VP) × **proposta** (a versão em elaboração).

Tudo é lido do sistema: as duas versões pela consolidação (`rows_for`: foto da congelada, cálculo da em elaboração),
o realizado do ano anterior e do ano de referência por CC × conta (com a projeção do gestor; anualizado se faltar
mês), as justificativas do gestor, as perguntas em aberto e os apontamentos da revisão do Planejamento.

Para cada corte o sistema sugere a prioridade de pedir de volta e monta o argumento:
- Alta: obrigação legal ou contratual (termos de `defense.mandatory_terms` no nome da conta, ou contratos PJ) ou
  conta zerada com realizado relevante (`alert.min_relevant_amount`);
- Média: a proposta fica abaixo do realizado do ano de referência, ou o gestor justificou um corte relevante;
- Baixa: o resto (a proposta ainda cobre o realizado).
A Controladoria ajusta prioridade, argumento e valor a pedir (`BudgetDefenseItem`, auditoria `budget_defense`);
"Aceitar o corte" tira o item do pedido. Nada aqui altera o orçamento.
"""

from __future__ import annotations

import io
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from functools import partial

from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules.capex import brl
from app.domain.rules.common import money
from app.models import (
    Account,
    AccountJustification,
    ActualEntry,
    BudgetDefenseItem,
    BudgetLine,
    BudgetQuestion,
    BudgetSubmission,
    BudgetVersion,
    CapexProject,
    CostCenter,
    DatasetVersion,
)
from app.services import audit
from app.services import consolidation as cons
from app.services import findings as findings_svc
from app.services.exports import TITLE_FONT, _f, _sheet, _table
from app.services.opex import Context
from app.services.painel_facts import _last_closed
from app.services.planning_revision import SOURCE as PLANNING_SOURCE

ZERO = Decimal("0")
PRIORITIES = ("HIGH", "MEDIUM", "LOW", "ACCEPT")
PRIORITY_LABELS = {"HIGH": "Alta", "MEDIUM": "Média", "LOW": "Baixa", "ACCEPT": "Aceitar o corte"}
GROUPS = ("PERSONNEL", "PJ", "OPEX", "CAPEX")
GROUP_LABELS = {"PERSONNEL": "Pessoal", "PJ": "Contratos PJ", "OPEX": "OPEX", "CAPEX": "CAPEX"}
TERMS_PARAM = "defense.mandatory_terms"
DEFAULT_TERMS = ["juros", "auditoria", "parcelamento", "multa", "imposto", "tribut", "taxa"]
OPEN_QUESTIONS = ("OPEN", "ANSWERED")
QUESTION_LABELS = {"OPEN": "Sem resposta", "ANSWERED": "Respondida, não encerrada"}
TAG_LABELS = {
    "legal": "Obrigação legal",
    "contract": "Contrato",
    "zeroed": "Zerada",
    "absent": "Fora do arquivo do Planejamento",
    "below_actual": "Abaixo do realizado",
    "justified": "Justificada",
    "question": "Pergunta em aberto",
}


class DefenseError(Exception):
    """Pedido inválido (mensagem pronta para o usuário)."""


def _m(v: Decimal) -> str:
    return str(money(v))


def _pct(new: Decimal, base: Decimal) -> str | None:
    return str(((new - base) / abs(base)).quantize(Decimal("0.0001"))) if base else None


def _fold(text) -> str:
    return unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().lower()


def _terms(ctx: Context) -> list[str]:
    raw = ctx.params.get(TERMS_PARAM, DEFAULT_TERMS)
    if isinstance(raw, str):
        raw = raw.split(",")
    return [_fold(t).strip() for t in raw or [] if _fold(t).strip()]


# ------------------------------------------------------------------ versões


def pick_versions(
    db: Session, ctx: Context, base_id: int | None = None, version_id: int | None = None
) -> tuple[BudgetVersion, BudgetVersion]:
    """(base, proposta): por padrão a versão base do ciclo × a versão em elaboração."""
    versions = list(
        db.scalars(
            select(BudgetVersion)
            .where(BudgetVersion.cycle_id == ctx.cycle.id)
            .order_by(BudgetVersion.major, BudgetVersion.minor)
        )
    )
    by_id = {v.id: v for v in versions}
    if (version_id and version_id not in by_id) or (base_id and base_id not in by_id):
        raise DefenseError("Versão não encontrada no ciclo")
    proposal = by_id[version_id] if version_id else ctx.version
    if base_id:
        base = by_id[base_id]
    else:
        base = next((v for v in versions if v.is_base and v.id != proposal.id), None)
        if base is None:
            base = by_id.get(proposal.parent_version_id or -1)
    if base is None or base.id == proposal.id:
        raise DefenseError("Escolha duas versões diferentes: a base (o que o VP aprovou) e a proposta")
    return base, proposal


def _version_out(v: BudgetVersion, total: Decimal) -> dict:
    return {"id": v.id, "label": v.display_label, "status": v.status, "is_base": v.is_base, "total": _m(total)}


# ------------------------------------------------------------------ leituras


def _by_key(rows: list[cons.Row]) -> tuple[dict, dict]:
    totals: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
    meta: dict[tuple, cons.Row] = {}
    for r in rows:
        if r.cost_center_id is None:
            continue
        k = (r.cost_center_id, r.module, r.account_code)
        totals[k] += r.total
        meta.setdefault(k, r)
    return totals, meta


def _year_actuals(db: Session, year: int, cc_ids: set[int]) -> tuple[dict, int | None, Decimal]:
    """Realizado do ano por (CC, conta), com a projeção do gestor; anualizado (× 12/mês) se o ano não está completo."""
    closed = _last_closed(db, year)
    factor = Decimal(12) / Decimal(closed) if closed and closed < 12 else Decimal(1)
    rows = db.execute(
        select(ActualEntry.cost_center_id, Account.code, func.sum(ActualEntry.amount))
        .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
        .join(Account, Account.id == ActualEntry.account_id)
        .where(
            DatasetVersion.is_current,
            ActualEntry.fiscal_year == year,
            ActualEntry.cost_center_id.in_(cc_ids or {-1}),
        )
        .group_by(ActualEntry.cost_center_id, Account.code)
    )
    return {(cc, code): money((amount or ZERO) * factor) for cc, code, amount in rows}, closed, factor


def _year_label(db: Session, year: int, closed: int | None, factor: Decimal) -> str:
    if not closed:
        return f"Realizado {year} (sem dados)"
    if factor != 1:
        return f"Realizado {year} anualizado (até {closed:02d} × 12/{closed})"
    projected = db.scalar(
        select(func.count(DatasetVersion.id)).where(
            DatasetVersion.dataset_type == "PROJECTION",
            DatasetVersion.is_current,
            DatasetVersion.scope_key.like(f"PROJECTION:{year}:%"),
        )
    )
    return f"Realizado {year} + projeção" if projected else f"Realizado {year}"


def _texts(db: Session, base: BudgetVersion, proposal: BudgetVersion, cc_ids: set[int]) -> tuple[dict, set]:
    """Justificativas do gestor por (CC, conta) — da proposta e, se diferentes, da base — e as chaves zeradas por não
    terem vindo no arquivo do Planejamento."""
    subs = {
        sid: (cc, ver, module)
        for sid, cc, ver, module in db.execute(
            select(
                BudgetSubmission.id,
                BudgetSubmission.cost_center_id,
                BudgetSubmission.version_id,
                BudgetSubmission.module,
            ).where(
                BudgetSubmission.version_id.in_({base.id, proposal.id}),
                BudgetSubmission.module.in_(("OPEX", "CAPEX")),
                BudgetSubmission.cost_center_id.in_(cc_ids or {-1}),
            )
        )
    }
    codes = dict(db.execute(select(Account.id, Account.code)).all())
    found: dict[tuple, list[tuple[int, str]]] = defaultdict(list)
    absent: set[tuple] = set()

    def add(sid: int, account_id: int, text: str | None, prefix: str = "") -> None:
        text = (text or "").strip()
        if not text:
            return
        cc, ver, _module = subs[sid]
        found[(cc, codes.get(account_id))].append((0 if ver == proposal.id else 1, prefix + text))

    opex = [s for s, (_c, _v, m) in subs.items() if m == "OPEX"]
    for j in db.scalars(select(AccountJustification).where(AccountJustification.submission_id.in_(opex or {-1}))):
        add(j.submission_id, j.account_id, j.text)
    for sid, account_id, text, attrs in db.execute(
        select(BudgetLine.submission_id, BudgetLine.account_id, BudgetLine.justification, BudgetLine.attributes).where(
            BudgetLine.submission_id.in_(opex or {-1})
        )
    ):
        attrs = attrs or {}
        info = attrs.get("planning_revision") or {}
        if subs[sid][1] == proposal.id and info.get("zeroed") and info.get("absent"):
            absent.add((subs[sid][0], codes.get(account_id)))
        if attrs.get("source") != PLANNING_SOURCE:  # a linha de ajuste do Planejamento não é justificativa do gestor
            add(sid, account_id, text)
    capex = [s for s, (_c, _v, m) in subs.items() if m == "CAPEX"]
    for p in db.scalars(select(CapexProject).where(CapexProject.submission_id.in_(capex or {-1}))):
        for item in p.items:
            add(p.submission_id, item.account_id, p.justification, f"{p.code} · {p.title}: ")
    out = {}
    for k, texts in found.items():
        seen: list[str] = []
        for _order, t in sorted(texts):
            if t not in seen:
                seen.append(t)
        out[k] = seen[:4]
    return out, absent


def _questions(db: Session, base: BudgetVersion, proposal: BudgetVersion, cc_ids: set[int]) -> list[BudgetQuestion]:
    return list(
        db.scalars(
            select(BudgetQuestion)
            .where(
                BudgetQuestion.version_id.in_({base.id, proposal.id}),
                BudgetQuestion.status.in_(OPEN_QUESTIONS),
            )
            .order_by(BudgetQuestion.asked_at.desc())
        )
    )


def _planning_points(db: Session, ctx: Context, proposal: BudgetVersion, cc_ids: set[int]) -> list[dict]:
    """Apontamentos da revisão do Planejamento na proposta (os mesmos de Apontamentos, sem os avisos mantidos)."""
    pctx = cons.version_context(ctx, proposal)
    kept = findings_svc.kept_keys(db, pctx)
    items: list[dict] = []
    subs = db.scalars(
        select(BudgetSubmission).where(
            BudgetSubmission.version_id == proposal.id,
            BudgetSubmission.module == "OPEX",
            BudgetSubmission.status.in_(findings_svc.OPEN),
            BudgetSubmission.cost_center_id.in_(cc_ids or {-1}),
        )
    )
    for sub in subs:
        cc = db.get(CostCenter, sub.cost_center_id)
        findings_svc._planning(db, pctx, sub, partial(findings_svc._add, items, sub, cc))
    return [
        {
            k: i[k]
            for k in ("key", "kind", "kind_label", "cost_center_id", "cost_center", "subject", "message", "amount")
        }
        for i in items
        if i["key"] not in kept
    ]


# ------------------------------------------------------------------ sugestão


def _suggest(it: dict, ctx_terms: list[str], min_relevant: Decimal, years: tuple[int, int]) -> tuple[str, list, list]:
    """Prioridade sugerida, argumentos (frases) e marcadores de um corte."""
    prev_year, ref_year = years
    base, prop = Decimal(it["base"]), Decimal(it["proposal"])
    act_prev, act_ref = Decimal(it["actual_prev"]), Decimal(it["actual_ref"])
    cut = base - prop
    name = _fold(it["account_name"])
    term = next((t for t in ctx_terms if t in name), None)
    tags: list[str] = []
    reasons: list[str] = []
    if it["group"] == "PJ":
        tags.append("contract")
        reasons.append("Contratos PJ vigentes: o valor é calculado dos contratos.")
    if term:
        tags.append("legal")
        reasons.append(f"Obrigação legal ou contratual (conta de {term}).")
    if prop == 0:
        tags.append("zeroed")
        if it["absent"]:
            tags.append("absent")
            reasons.append("Zerada porque não veio no arquivo do Planejamento (pode estar em outra conta).")
        else:
            reasons.append("Zerada pelo Planejamento.")
    history = max(act_prev, act_ref)
    if history:
        gap = act_ref - prop
        text = f"Realizado {prev_year}: {brl(act_prev)}; {ref_year}: {brl(act_ref)}."
        if gap > 0:
            tags.append("below_actual")
            text += f" A proposta fica {brl(gap)} abaixo do realizado {ref_year}."
        else:
            text += f" A proposta ainda cobre o realizado {ref_year}."
        reasons.append(text)
    elif base:
        reasons.append(f"Sem realizado em {prev_year} e {ref_year} nesta conta.")
    if it["justifications"]:
        tags.append("justified")
        first = it["justifications"][0]
        reasons.append(f"Justificativa do gestor: “{first[:240]}{'…' if len(first) > 240 else ''}”")
    if it["questions_open"]:
        tags.append("question")
        reasons.append(f"{it['questions_open']} pergunta(s) em aberto sobre esta conta.")
    if term or it["group"] == "PJ" or (prop == 0 and history >= min_relevant):
        priority = "HIGH"
    elif ("below_actual" in tags and act_ref - prop >= min_relevant) or ("justified" in tags and cut >= min_relevant):
        priority = "MEDIUM"
    else:
        priority = "LOW"
    return priority, reasons, tags


# ------------------------------------------------------------------ montagem


def build(db: Session, ctx: Context, base: BudgetVersion, proposal: BudgetVersion) -> dict:
    rows_a, rows_b = cons.rows_for(db, ctx, base), cons.rows_for(db, ctx, proposal)
    ta, meta_a = _by_key(rows_a)
    tb, meta_b = _by_key(rows_b)
    keys = set(ta) | set(tb)
    cc_ids = {k[0] for k in keys}
    pj_code, _adj, _month = cons.pj_budget_params(ctx)
    prev_year, ref_year = ctx.prev_year, ctx.ref_year
    act_prev, closed_prev, f_prev = _year_actuals(db, prev_year, cc_ids)
    act_ref, closed_ref, f_ref = _year_actuals(db, ref_year, cc_ids)
    texts, absent = _texts(db, base, proposal, cc_ids)
    questions = _questions(db, base, proposal, cc_ids)
    codes = dict(db.execute(select(Account.id, Account.code)).all())
    q_by_key: dict[tuple, int] = defaultdict(int)
    for q in questions:
        if q.cost_center_id and q.account_id:
            q_by_key[(q.cost_center_id, codes.get(q.account_id))] += 1
    saved = {
        (d.cost_center_id, d.module, d.account_code): d
        for d in db.scalars(
            select(BudgetDefenseItem).where(
                BudgetDefenseItem.version_id == proposal.id, BudgetDefenseItem.base_version_id == base.id
            )
        )
    }
    terms = _terms(ctx)
    min_relevant = Decimal(str(ctx.param("alert.min_relevant_amount", 1000)))
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(cc_ids or {-1})))}
    accounts = {a.code: a for a in db.scalars(select(Account))}

    def group_of(module: str, code: str) -> str:
        return "PJ" if module == "OPEX" and code == pj_code else module

    items = []
    for k in keys:
        a, b = ta.get(k, ZERO), tb.get(k, ZERO)
        if a == b:
            continue
        cc_id, module, code = k
        row = meta_b.get(k) or meta_a[k]
        cc = ccs.get(cc_id)
        it = {
            "key": f"{cc_id}:{module}:{code}",
            "cost_center_id": cc_id,
            "cost_center": f"{cc.code} · {cc.name}" if cc else row.cost_center_code,
            "module": module,
            "group": group_of(module, code),
            "group_label": GROUP_LABELS[group_of(module, code)],
            "account": code,
            "account_name": row.account_name or (accounts[code].name if code in accounts else code),
            "package": row.package,
            "base": _m(a),
            "proposal": _m(b),
            "difference": _m(b - a),
            "difference_pct": _pct(b, a),
            "actual_prev": _m(act_prev.get((cc_id, code), ZERO)),
            "actual_ref": _m(act_ref.get((cc_id, code), ZERO)),
            "kind": "CUT" if b < a else "INCREASE",
            "absent": (cc_id, code) in absent,
            "justifications": texts.get((cc_id, code), []),
            "questions_open": q_by_key.get((cc_id, code), 0),
        }
        if it["kind"] == "CUT":
            priority, reasons, tags = _suggest(it, terms, min_relevant, (prev_year, ref_year))
            d = saved.get(k)
            cut = a - b
            request = min(d.request_amount, cut) if d is not None and d.request_amount is not None else cut
            it |= {
                "suggested_priority": priority,
                "priority": d.priority if d is not None else priority,
                "reasons": reasons,
                "suggested_argument": " ".join(reasons),
                "argument": (d.argument if d is not None else None) or None,
                "request_amount": _m(ZERO if (d is not None and d.priority == "ACCEPT") else request),
                "request_custom": d is not None and d.request_amount is not None,
                "edited": d is not None,
                "updated_at": d.updated_at.isoformat() if d is not None and d.updated_at else None,
            }
            it["tags"] = [{"key": t, "label": TAG_LABELS[t]} for t in tags]
            it["priority_label"] = PRIORITY_LABELS[it["priority"]]
        else:
            it |= {"tags": [], "reasons": [], "priority": None, "request_amount": "0.00", "edited": False}
        items.append(it)
    items.sort(key=lambda i: Decimal(i["difference"]))

    # resumo por grupo (Pessoal, PJ, OPEX, CAPEX): base, proposta, realizado e pedido
    natures = {a.code: a.nature for a in accounts.values()}
    summary: dict[str, dict[str, Decimal]] = {g: defaultdict(lambda: ZERO) for g in GROUPS}
    for k, v in ta.items():
        summary[group_of(k[1], k[2])]["base"] += v
    for k, v in tb.items():
        summary[group_of(k[1], k[2])]["proposal"] += v
    for name, source in (("actual_prev", act_prev), ("actual_ref", act_ref)):
        for (_cc, code), v in source.items():
            module = cons.module_of_nature(natures.get(code))
            if module:
                summary[group_of(module, code)][name] += v
    for it in items:
        if it["kind"] == "CUT":
            summary[it["group"]]["cut"] += -Decimal(it["difference"])
            summary[it["group"]]["ask"] += Decimal(it["request_amount"])
    groups = []
    for g in GROUPS:
        s = summary[g]
        if not any(s.values()):
            continue
        groups.append(
            {
                "group": g,
                "label": GROUP_LABELS[g],
                "base": _m(s["base"]),
                "proposal": _m(s["proposal"]),
                "difference": _m(s["proposal"] - s["base"]),
                "difference_pct": _pct(s["proposal"], s["base"]),
                "actual_prev": _m(s["actual_prev"]),
                "actual_ref": _m(s["actual_ref"]),
                "cut": _m(s["cut"]),
                "ask": _m(s["ask"]),
            }
        )

    total_a, total_b = sum(ta.values(), ZERO), sum(tb.values(), ZERO)
    cuts = [i for i in items if i["kind"] == "CUT"]
    ask = sorted(
        (i for i in cuts if i["priority"] != "ACCEPT"),
        key=lambda i: (PRIORITIES.index(i["priority"]), -Decimal(i["request_amount"])),
    )
    by_priority = []
    for p in PRIORITIES:
        chosen = [i for i in cuts if i["priority"] == p]
        if chosen:
            by_priority.append(
                {
                    "priority": p,
                    "label": PRIORITY_LABELS[p],
                    "count": len(chosen),
                    "cut": _m(sum((-Decimal(i["difference"]) for i in chosen), ZERO)),
                    "ask": _m(sum((Decimal(i["request_amount"]) for i in chosen), ZERO)),
                }
            )
    ask_total = sum((Decimal(i["request_amount"]) for i in ask), ZERO)
    q_cc = {
        q.id: ccs.get(q.cost_center_id) or (db.get(CostCenter, q.cost_center_id) if q.cost_center_id else None)
        for q in questions
    }
    return {
        "target_year": ctx.target_year,
        "years": {
            "prev": prev_year,
            "ref": ref_year,
            "prev_label": _year_label(db, prev_year, closed_prev, f_prev),
            "ref_label": _year_label(db, ref_year, closed_ref, f_ref),
        },
        "base": _version_out(base, total_a),
        "proposal": _version_out(proposal, total_b),
        "difference": _m(total_b - total_a),
        "difference_pct": _pct(total_b, total_a),
        "groups": groups,
        "totals": {
            "cuts": _m(sum((-Decimal(i["difference"]) for i in cuts), ZERO)),
            "cuts_count": len(cuts),
            "increases": _m(sum((Decimal(i["difference"]) for i in items if i["kind"] == "INCREASE"), ZERO)),
            "increases_count": len(items) - len(cuts),
            "actual_prev": _m(sum((Decimal(g["actual_prev"]) for g in groups), ZERO)),
            "actual_ref": _m(sum((Decimal(g["actual_ref"]) for g in groups), ZERO)),
        },
        "items": items,
        "ask": {
            "keys": [i["key"] for i in ask],
            "by_priority": by_priority,
            "total": _m(ask_total),
            "proposal_with_ask": _m(total_b + ask_total),
        },
        "open_points": {
            "planning": _planning_points(db, ctx, proposal, cc_ids),
            "questions": [
                {
                    "id": q.id,
                    "subject": q.subject,
                    "question": q.question,
                    "answer": q.answer,
                    "status": q.status,
                    "status_label": QUESTION_LABELS.get(q.status, q.status),
                    "cost_center": f"{q_cc[q.id].code} · {q_cc[q.id].name}" if q_cc.get(q.id) else None,
                    "asked_at": q.asked_at.isoformat() if q.asked_at else None,
                    "version": proposal.display_label if q.version_id == proposal.id else base.display_label,
                }
                for q in questions
            ],
        },
        "versions": [
            {"id": v.id, "label": v.display_label, "status": v.status, "is_base": v.is_base}
            for v in db.scalars(
                select(BudgetVersion)
                .where(BudgetVersion.cycle_id == ctx.cycle.id)
                .order_by(BudgetVersion.major, BudgetVersion.minor)
            )
        ],
        "priorities": [{"value": p, "label": PRIORITY_LABELS[p]} for p in PRIORITIES],
        "can_edit": proposal.status == "WORKING",
        "terms": terms,
    }


# ------------------------------------------------------------------ ajuste da Controladoria


def _cut_of(db: Session, ctx: Context, base, proposal, cc_id: int, module: str, code: str) -> Decimal:
    def total(version) -> Decimal:
        rows = cons.rows_for(db, ctx, version, {cc_id})
        return sum((r.total for r in rows if r.module == module and r.account_code == code), ZERO)

    return total(base) - total(proposal)


def _snap(d: BudgetDefenseItem | None) -> dict | None:
    if d is None:
        return None
    return {
        "priority": d.priority,
        "argument": d.argument,
        "request_amount": None if d.request_amount is None else _m(d.request_amount),
    }


def save(db: Session, ctx: Context, data: dict, user_id: int | None, ip: str | None = None) -> dict:
    """Grava (ou, com `reset`, apaga) o ajuste de um corte; auditoria `budget_defense` com antes × depois."""
    base, proposal = pick_versions(db, ctx, data.get("base_version_id"), data.get("version_id"))
    if proposal.status != "WORKING":
        raise DefenseError(f"A {proposal.display_label} está congelada: a defesa dela fica só para consulta")
    cc_id, module, code = int(data["cost_center_id"]), str(data["module"]), str(data["account"])
    cc = db.get(CostCenter, cc_id)
    if cc is None or module not in cons.MODULES:
        raise DefenseError("Centro de custo ou módulo inválido")
    cut = _cut_of(db, ctx, base, proposal, cc_id, module, code)
    if cut <= 0:
        raise DefenseError("Este item não é um corte entre as duas versões")
    item = db.scalar(
        select(BudgetDefenseItem).where(
            BudgetDefenseItem.version_id == proposal.id,
            BudgetDefenseItem.base_version_id == base.id,
            BudgetDefenseItem.cost_center_id == cc_id,
            BudgetDefenseItem.module == module,
            BudgetDefenseItem.account_code == code,
        )
    )
    before = _snap(item)
    subject = f"{cc.code} · {code} ({proposal.display_label} × {base.display_label})"
    if data.get("reset"):
        if item is None:
            return {"edited": False}
        entity_id = item.id
        db.delete(item)
        audit.record(
            db,
            user_id=user_id,
            action="DELETE",
            entity_type="budget_defense",
            entity_id=entity_id,
            before=before,
            reason=f"Defesa do orçamento: volta à sugestão do sistema — {subject}",
            ip=ip,
        )
        db.commit()
        return {"edited": False}
    priority = data.get("priority")
    if priority not in PRIORITIES:
        raise DefenseError("Escolha a prioridade: Alta, Média, Baixa ou Aceitar o corte")
    raw = data.get("request_amount")
    request = None
    if raw not in (None, ""):
        try:
            request = money(Decimal(str(raw)))
        except (InvalidOperation, ValueError) as exc:
            raise DefenseError("Valor a pedir de volta inválido") from exc
        if request < 0 or request > cut:
            raise DefenseError(f"O valor a pedir de volta vai de R$ 0,00 até o corte ({brl(cut)})")
    argument = (data.get("argument") or "").strip()[:4000] or None
    created = item is None
    if created:
        item = BudgetDefenseItem(
            version_id=proposal.id, base_version_id=base.id, cost_center_id=cc_id, module=module, account_code=code
        )
        db.add(item)
    item.priority, item.argument, item.request_amount = priority, argument, request
    item.updated_by, item.updated_at = user_id, datetime.utcnow()
    db.flush()
    audit.record(
        db,
        user_id=user_id,
        action="CREATE" if created else "UPDATE",
        entity_type="budget_defense",
        entity_id=item.id,
        before=before,
        after=_snap(item),
        reason=f"Defesa do orçamento: {PRIORITY_LABELS[priority]} — {subject}",
        ip=ip,
    )
    db.commit()
    return {"edited": True} | (_snap(item) or {})


# ------------------------------------------------------------------ exportação


def workbook(data: dict) -> bytes:
    """Excel da defesa: resumo (por módulo e por prioridade), pedir de volta, cortes e aumentos, pontos em aberto."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Resumo"
    years = data["years"]
    ws.cell(1, 1, f"Defesa do orçamento {data['target_year']}").font = TITLE_FONT
    ws.cell(2, 1, f"Base: {data['base']['label']} · Proposta: {data['proposal']['label']}")
    ws.cell(3, 1, f"{years['prev_label']} · {years['ref_label']}")
    row = 5
    for label, value in (
        (f"Base ({data['base']['label']})", data["base"]["total"]),
        (f"Proposta ({data['proposal']['label']})", data["proposal"]["total"]),
        ("Diferença", data["difference"]),
        ("Pedir de volta", data["ask"]["total"]),
        ("Proposta + pedido", data["ask"]["proposal_with_ask"]),
    ):
        ws.cell(row, 1, label)
        ws.cell(row, 2, _f(value)).number_format = "#,##0.00;[Red]-#,##0.00"
        row += 1
    row = _table(
        ws,
        row + 1,
        [
            "Módulo",
            years["prev_label"],
            years["ref_label"],
            data["base"]["label"],
            data["proposal"]["label"],
            "Diferença",
            "%",
            "Pedir de volta",
        ],
        [
            [
                g["label"],
                _f(g["actual_prev"]),
                _f(g["actual_ref"]),
                _f(g["base"]),
                _f(g["proposal"]),
                _f(g["difference"]),
                _f(g["difference_pct"]) if g["difference_pct"] is not None else None,
                _f(g["ask"]),
            ]
            for g in data["groups"]
        ],
        money_cols={2, 3, 4, 5, 6, 8},
        pct_cols={7},
        widths={1: 22},
        total=True,
    )
    _table(
        ws,
        row,
        ["Prioridade", "Itens", "Corte", "Pedir de volta"],
        [[p["label"], p["count"], _f(p["cut"]), _f(p["ask"])] for p in data["ask"]["by_priority"]],
        money_cols={3, 4},
        total=True,
    )
    by_key = {i["key"]: i for i in data["items"]}

    def cut_row(i: dict) -> list:
        return [
            i["priority_label"],
            i["cost_center"],
            i["group_label"],
            i["account"],
            i["account_name"],
            _f(i["actual_prev"]),
            _f(i["actual_ref"]),
            _f(i["base"]),
            _f(i["proposal"]),
            _f(i["difference"]),
            _f(i["request_amount"]),
            ", ".join(t["label"] for t in i["tags"]),
            i["argument"] or i["suggested_argument"],
        ]

    headers = [
        "Prioridade",
        "Centro de custo",
        "Módulo",
        "Conta",
        "Nome da conta",
        years["prev_label"],
        years["ref_label"],
        data["base"]["label"],
        data["proposal"]["label"],
        "Diferença",
        "Pedir de volta",
        "Marcadores",
        "Argumento",
    ]
    money_cols = {6, 7, 8, 9, 10, 11}
    widths = {2: 36, 5: 36, 12: 30, 13: 80}
    _sheet(
        wb,
        "Pedir de volta",
        headers,
        [cut_row(by_key[k]) for k in data["ask"]["keys"]],
        money_cols=money_cols,
        widths=widths,
        total=True,
    )
    _sheet(
        wb,
        "Cortes",
        headers,
        [cut_row(i) for i in data["items"] if i["kind"] == "CUT"],
        money_cols=money_cols,
        widths=widths,
        total=True,
    )
    _sheet(
        wb,
        "Aumentos",
        headers[1:10],
        [cut_row(i)[1:10] for i in data["items"] if i["kind"] == "INCREASE"],
        money_cols={m - 1 for m in money_cols if m < 11},
        widths={1: 36, 4: 36},
        total=True,
    )
    points = data["open_points"]
    _sheet(
        wb,
        "Em aberto",
        ["Tipo", "Centro de custo", "Assunto", "Detalhe", "Valor"],
        [
            [p["kind_label"], p["cost_center"], p["subject"], p["message"], _f(p["amount"]) if p["amount"] else None]
            for p in points["planning"]
        ]
        + [
            [f"Pergunta · {q['status_label']}", q["cost_center"], q["subject"], q["question"], None]
            for q in points["questions"]
        ],
        money_cols={5},
        widths={2: 36, 3: 40, 4: 90},
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
