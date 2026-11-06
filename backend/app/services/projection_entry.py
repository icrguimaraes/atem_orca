"""Projeção digitada pelo gestor (05/11/2026): grade conta × mês por centro de custo, para OPEX, Pessoal e CAPEX.

- Meses fechados (até o último mês do realizado da empresa) mostram o realizado e não se editam.
- Meses abertos vêm preenchidos com a projeção vigente; conta sem projeção nos meses abertos recebe o orçado do ano
  como sugestão (origem "O"). A origem de cada valor vai na grade: "P" = projeção importada (planilha da área),
  "G" = projeção gravada na tela, "O" = orçado sugerido (ainda não é projeção).
- Gravar vale na hora: cria uma nova versão da projeção da empresa no ano (a anterior fica no histórico), trocando só
  as células alteradas deste CC. Antes de gravar, `compare` mostra a projeção antiga × a nova, célula a célula.
- Toda gravação vai para a auditoria (`entity_type="projection"`, `entity_id` = CC), com o antes × depois das células.
- Quando o realizado do mês entra (KSB1), a projeção daquele mês sai sozinha (`loaders._drop_closed_projection`).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from app.core.deps import visible_cost_center_ids
from app.imports.loaders import new_version
from app.models import (
    Account,
    ActualEntry,
    AuditLog,
    BudgetPackage,
    CostCenter,
    DatasetVersion,
    ReferenceBudgetEntry,
    User,
)
from app.services import audit
from app.services.opex import OpexError, context

ZERO = Decimal("0")
CENT = Decimal("0.01")
NATURES = ("OPEX", "PESSOAL", "CAPEX")
NATURE_ORDER = {n: i for i, n in enumerate(NATURES)}
SOURCE = "GESTOR"  # ActualEntry.source das células gravadas na tela
ENTITY = "projection"
MONTH_LABELS = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
MAX_ABS = Decimal("1000000000000")


class NotFound(Exception):
    pass


class Forbidden(Exception):
    pass


class Invalid(Exception):
    pass


class Conflict(Exception):
    pass


# ------------------------------------------------------------------ base


def _year(db: Session) -> int:
    try:
        return context(db).ref_year
    except OpexError:
        return date.today().year


def _scope(year: int, company_code: str) -> str:
    return f"PROJECTION:{year}:{company_code}"


def _closed(db: Session, year: int, company_code: str) -> int:
    """Último mês do realizado (KSB1) da empresa no ano; os seguintes são os meses abertos para projetar."""
    value = db.scalar(
        select(DatasetVersion.last_closed_period).where(
            DatasetVersion.dataset_type == "ACTUAL",
            DatasetVersion.is_current,
            DatasetVersion.scope_key == f"ACTUAL:{year}:{company_code}",
        )
    )
    return int(value or 0)


def _version(db: Session, year: int, company_code: str) -> DatasetVersion | None:
    return db.scalar(
        select(DatasetVersion).where(
            DatasetVersion.dataset_type == "PROJECTION",
            DatasetVersion.scope_key == _scope(year, company_code),
            DatasetVersion.is_current,
        )
    )


def _cost_center(db: Session, user: User, cc_id: int) -> CostCenter:
    cc = db.get(CostCenter, cc_id)
    if cc is None:
        raise NotFound("Centro de custo não encontrado")
    visible = visible_cost_center_ids(db, user)
    if visible is not None and cc.id not in visible:
        raise Forbidden("Sem acesso a este centro de custo")
    return cc


def can_edit(user: User) -> bool:
    """Grava quem vê o CC (gestor, escopo atribuído ou Controladoria), menos o perfil só de consulta."""
    return bool(user.role_codes - {"VIEWER"})


def _months() -> list[Decimal]:
    return [ZERO] * 12


def _money(value) -> Decimal:
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text:  # 1.234,56 (pt-BR)
        text = text.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(text)
    except (InvalidOperation, AttributeError):
        raise Invalid(f"Valor inválido: {value!r}") from None
    if not amount.is_finite() or abs(amount) >= MAX_ABS:
        raise Invalid(f"Valor inválido: {value!r}")
    return amount.quantize(CENT)


def _last_saves(db: Session, cc_ids: list[int] | None) -> dict[int, dict]:
    stmt = (
        select(AuditLog.entity_id, func.max(AuditLog.id))
        .where(AuditLog.entity_type == ENTITY)
        .group_by(AuditLog.entity_id)
    )
    if cc_ids is not None:
        stmt = stmt.where(AuditLog.entity_id.in_([str(i) for i in cc_ids] or ["-"]))
    ids = [log_id for _e, log_id in db.execute(stmt)]
    out: dict[int, dict] = {}
    for log in db.scalars(select(AuditLog).where(AuditLog.id.in_(ids or [-1]))):
        user = db.get(User, log.user_id) if log.user_id else None
        out[int(log.entity_id)] = {"at": log.occurred_at.isoformat(), "by": user.name if user else None}
    return out


# ------------------------------------------------------------------ lista de CCs


def list_cost_centers(db: Session, user: User) -> dict:
    """CCs visíveis com realizado até o mês fechado, projeção dos meses abertos, fechamento × orçado e a última
    gravação. Gestor: os CCs dele (mesmo sem números); Controladoria: os CCs com algum número no ano."""
    year = _year(db)
    visible = visible_cost_center_ids(db, user)
    nature_ok = Account.nature.in_(NATURES)

    def actual_sums():
        stmt = (
            select(ActualEntry.cost_center_id, ActualEntry.projected, func.sum(ActualEntry.amount))
            .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
            .join(Account, Account.id == ActualEntry.account_id)
            .where(DatasetVersion.is_current, ActualEntry.fiscal_year == year, nature_ok)
            .group_by(ActualEntry.cost_center_id, ActualEntry.projected)
        )
        if visible is not None:
            stmt = stmt.where(ActualEntry.cost_center_id.in_(visible or {-1}))
        return db.execute(stmt)

    actual: dict[int, Decimal] = defaultdict(lambda: ZERO)
    projection: dict[int, Decimal] = defaultdict(lambda: ZERO)
    for cc_id, projected, amount in actual_sums():
        (projection if projected else actual)[cc_id] += amount or ZERO
    stmt = (
        select(ReferenceBudgetEntry.cost_center_id, func.sum(ReferenceBudgetEntry.amount))
        .join(DatasetVersion, DatasetVersion.id == ReferenceBudgetEntry.dataset_version_id)
        .join(Account, Account.id == ReferenceBudgetEntry.account_id)
        .where(DatasetVersion.is_current, ReferenceBudgetEntry.fiscal_year == year, nature_ok)
        .group_by(ReferenceBudgetEntry.cost_center_id)
    )
    if visible is not None:
        stmt = stmt.where(ReferenceBudgetEntry.cost_center_id.in_(visible or {-1}))
    budget = {cc_id: amount or ZERO for cc_id, amount in db.execute(stmt)}

    ids = set(visible) if visible is not None else set(actual) | set(projection) | set(budget)
    ccs = list(db.scalars(select(CostCenter).where(CostCenter.id.in_(ids or {-1}))))
    saves = _last_saves(db, [c.id for c in ccs])
    closed_by_company: dict[str, int] = {}
    items = []
    for cc in sorted(ccs, key=lambda c: (c.company.code, c.code)):
        code = cc.company.code
        if code not in closed_by_company:
            closed_by_company[code] = _closed(db, year, code)
        a, p, b = actual[cc.id], projection[cc.id], budget.get(cc.id, ZERO)
        closing = a + p
        items.append(
            {
                "id": cc.id,
                "code": cc.code,
                "name": cc.name,
                "company": code,
                "closed": closed_by_company[code],
                "actual": str(a),
                "projection": str(p),
                "closing": str(closing),
                "budget": str(b),
                "deviation": str(closing - b),
                "deviation_pct": str(((closing - b) / b * 100).quantize(CENT)) if b else None,
                "last_saved": saves.get(cc.id),
            }
        )
    return {"year": year, "items": items, "closed_by_company": closed_by_company}


# ------------------------------------------------------------------ grade do CC


def _cells(db: Session, cc: CostCenter, year: int):
    """Realizado, projeção (com a origem de cada célula) e orçado do CC por conta × mês."""
    actual: dict[int, list[Decimal]] = defaultdict(_months)
    projection: dict[int, list[Decimal]] = defaultdict(_months)
    typed: dict[int, set[int]] = defaultdict(set)  # conta → meses com célula gravada na tela
    rows = db.execute(
        select(
            ActualEntry.account_id, ActualEntry.period, ActualEntry.projected, ActualEntry.source, ActualEntry.amount
        )
        .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
        .join(Account, Account.id == ActualEntry.account_id)
        .where(
            DatasetVersion.is_current,
            ActualEntry.cost_center_id == cc.id,
            ActualEntry.fiscal_year == year,
            Account.nature.in_(NATURES),
        )
    )
    has_projection: dict[int, set[int]] = defaultdict(set)
    for acc_id, period, projected, source, amount in rows:
        m = int(period) - 1
        if projected:
            projection[acc_id][m] += amount or ZERO
            has_projection[acc_id].add(m + 1)
            if source == SOURCE:
                typed[acc_id].add(m + 1)
        else:
            actual[acc_id][m] += amount or ZERO
    budget: dict[int, list[Decimal]] = defaultdict(_months)
    for acc_id, period, amount in db.execute(
        select(ReferenceBudgetEntry.account_id, ReferenceBudgetEntry.period, func.sum(ReferenceBudgetEntry.amount))
        .join(DatasetVersion, DatasetVersion.id == ReferenceBudgetEntry.dataset_version_id)
        .join(Account, Account.id == ReferenceBudgetEntry.account_id)
        .where(
            DatasetVersion.is_current,
            ReferenceBudgetEntry.cost_center_id == cc.id,
            ReferenceBudgetEntry.fiscal_year == year,
            Account.nature.in_(NATURES),
        )
        .group_by(ReferenceBudgetEntry.account_id, ReferenceBudgetEntry.period)
    ):
        budget[acc_id][int(period) - 1] += amount or ZERO
    return actual, projection, has_projection, typed, budget


def _account_rows(db: Session, ids: set[int]) -> list[Account]:
    accounts = list(db.scalars(select(Account).where(Account.id.in_(ids or {-1}))))

    def key(a: Account):
        pkg: BudgetPackage | None = a.package
        return (NATURE_ORDER.get(a.nature, 9), pkg.sort_order if pkg else 999, pkg.name if pkg else "", a.code)

    return sorted(accounts, key=key)


def grid(db: Session, user: User, cc_id: int) -> dict:
    cc = _cost_center(db, user, cc_id)
    year = _year(db)
    company = cc.company.code
    closed = _closed(db, year, company)
    version = _version(db, year, company)
    actual, projection, has_projection, typed, budget = _cells(db, cc, year)
    open_months = list(range(closed + 1, 13))
    accounts = _account_rows(db, set(actual) | set(projection) | set(budget))
    rows = []
    for acc in accounts:
        projected_open = {m for m in has_projection.get(acc.id, set()) if m > closed}
        suggest = not projected_open  # conta sem projeção nos meses abertos: sugere o orçado
        values, origin = [], []
        for m in range(1, 13):
            i = m - 1
            if m <= closed:
                values.append(str(actual[acc.id][i]))
                origin.append("R")
            elif suggest:
                values.append(str(budget[acc.id][i]))
                origin.append("O" if budget[acc.id][i] else "")
            else:
                values.append(str(projection[acc.id][i]))
                origin.append("G" if m in typed.get(acc.id, set()) else ("P" if m in projected_open else ""))
        rows.append(
            {
                "account_id": acc.id,
                "code": acc.code,
                "name": acc.name,
                "nature": acc.nature,
                "package": acc.package.name if acc.package else None,
                "actual": [str(v) for v in actual[acc.id]],
                "projection": [str(v) for v in projection[acc.id]],
                "budget": [str(v) for v in budget[acc.id]],
                "values": values,
                "origin": origin,
                "suggested": suggest and any(budget[acc.id][m - 1] for m in open_months),
            }
        )
    return {
        "year": year,
        "cost_center": {"id": cc.id, "code": cc.code, "name": cc.name, "company": company},
        "closed": closed,
        "open_months": open_months,
        "version_id": version.id if version else None,
        "version_number": version.version_number if version else None,
        "last_saved": _last_saves(db, [cc.id]).get(cc.id),
        "can_edit": can_edit(user),
        "rows": rows,
    }


def accounts(db: Session) -> list[dict]:
    """Contas que podem entrar na grade (OPEX, Pessoal e CAPEX ativas), para "Acrescentar conta"."""
    return [
        {
            "id": a.id,
            "code": a.code,
            "name": a.name,
            "nature": a.nature,
            "package": a.package.name if a.package else None,
        }
        for a in _account_rows(
            db, set(db.scalars(select(Account.id).where(Account.is_active, Account.nature.in_(NATURES))))
        )
    ]


# ------------------------------------------------------------------ comparar e gravar


def _parse(db: Session, values: list[dict], open_months: list[int]) -> dict[tuple[int, int], Decimal]:
    allowed = set(open_months)
    natures = dict(db.execute(select(Account.id, Account.nature)).all())
    out: dict[tuple[int, int], Decimal] = {}
    for item in values:
        acc_id, month = int(item["account_id"]), int(item["month"])
        if acc_id not in natures:
            raise Invalid(f"Conta {acc_id} não encontrada")
        if natures[acc_id] not in NATURES:
            raise Invalid("Só contas de OPEX, Pessoal e CAPEX entram na projeção")
        if month not in allowed:
            raise Invalid(f"{MONTH_LABELS[month - 1] if 1 <= month <= 12 else month} não está aberto para projeção")
        out[(acc_id, month)] = _money(item["amount"])
    return out


def compare(db: Session, user: User, cc_id: int, values: list[dict], version_id: int | None) -> dict:
    """Projeção antiga × nova do CC, sem gravar: células alteradas, totais por mês e por natureza, e o fechamento."""
    view = grid(db, user, cc_id)
    if version_id != view["version_id"]:
        raise Conflict("A projeção desta empresa mudou depois que você abriu a tela. Recarregue para ver a vigente.")
    new = _parse(db, values, view["open_months"])
    rows = {r["account_id"]: r for r in view["rows"]}
    missing = {a for a, _m in new} - set(rows)
    extra = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(missing or {-1})))}
    changes = []
    by_month: dict[int, list[Decimal]] = {m: [ZERO, ZERO] for m in view["open_months"]}
    by_nature: dict[str, list[Decimal]] = {n: [ZERO, ZERO] for n in NATURES}
    keys = {(a, m) for a in rows for m in view["open_months"]} | set(new)
    for acc_id, month in sorted(keys):
        row = rows.get(acc_id)
        before = Decimal(row["projection"][month - 1]) if row else ZERO
        after = new.get((acc_id, month), before)
        nature = row["nature"] if row else extra[acc_id].nature
        by_month[month][0] += before
        by_month[month][1] += after
        by_nature[nature][0] += before
        by_nature[nature][1] += after
        if after != before:
            acc = row or {"code": extra[acc_id].code, "name": extra[acc_id].name}
            changes.append(
                {
                    "account_id": acc_id,
                    "code": acc["code"],
                    "name": acc["name"],
                    "nature": nature,
                    "month": month,
                    "origin_before": row["origin"][month - 1] if row else "",
                    "before": str(before),
                    "after": str(after),
                    "difference": str(after - before),
                }
            )
    actual = sum((Decimal(v) for r in view["rows"] for v in r["actual"]), ZERO)
    budget = sum((Decimal(v) for r in view["rows"] for v in r["budget"]), ZERO)
    before_total = sum((b for b, _a in by_month.values()), ZERO)
    after_total = sum((a for _b, a in by_month.values()), ZERO)
    return {
        "cost_center": view["cost_center"],
        "year": view["year"],
        "closed": view["closed"],
        "version_id": view["version_id"],
        "changes": changes,
        "accepted_suggestions": sum(1 for c in changes if c["origin_before"] == "O"),
        "by_month": [{"month": m, "before": str(b), "after": str(a)} for m, (b, a) in by_month.items()],
        "by_nature": [{"nature": n, "before": str(b), "after": str(a)} for n, (b, a) in by_nature.items() if b or a],
        "totals": {
            "actual": str(actual),
            "budget": str(budget),
            "projection_before": str(before_total),
            "projection_after": str(after_total),
            "closing_before": str(actual + before_total),
            "closing_after": str(actual + after_total),
            "difference": str(after_total - before_total),
        },
    }


def save(
    db: Session, user: User, cc_id: int, values: list[dict], version_id: int | None, comment: str | None, ip=None
) -> dict:
    """Grava a projeção do CC: nova versão da projeção da empresa no ano com as células alteradas trocadas; o resto
    (outros CCs, outras contas, células sem alteração) segue igual. Auditoria com o antes × depois das células."""
    if not can_edit(user):
        raise Forbidden("Perfil de consulta não grava projeção")
    result = compare(db, user, cc_id, values, version_id)
    if not result["changes"]:
        raise Invalid("Nenhuma alteração para gravar")
    cc = db.get(CostCenter, cc_id)
    year = result["year"]
    company = cc.company.code
    previous = _version(db, year, company)
    changed = {(c["account_id"], c["month"]): Decimal(c["after"]) for c in result["changes"]}
    version = new_version(db, None, "PROJECTION", _scope(year, company), user.id, SOURCE)
    payload = []
    branches: dict[int, set] = defaultdict(set)
    if previous is not None:
        columns = [c for c in ActualEntry.__table__.columns if c.name not in ("id", "dataset_version_id")]
        for row in db.execute(select(*columns).where(ActualEntry.dataset_version_id == previous.id)).mappings():
            if row["cost_center_id"] == cc.id:
                branches[row["account_id"]].add(row["branch_id"])
                if (row["account_id"], int(row["period"])) in changed:
                    continue
            payload.append(dict(row) | {"dataset_version_id": version.id})
    who = user.name or user.email
    for (acc_id, month), amount in sorted(changed.items()):
        if amount == 0:
            continue
        options = branches.get(acc_id, set())
        payload.append(
            {
                "dataset_version_id": version.id,
                "company_id": cc.company_id,
                "branch_id": next(iter(options)) if len(options) == 1 else None,
                "cost_center_id": cc.id,
                "account_id": acc_id,
                "fiscal_year": year,
                "period": month,
                "amount": amount,
                "currency": "BRL",
                "source": SOURCE,
                "projected": True,
                "text": f"Projeção digitada por {who}"[:500],
            }
        )
    for start in range(0, len(payload), 5000):
        db.execute(insert(ActualEntry), payload[start : start + 5000])
    version.row_count = len(payload)
    version.last_closed_period = max((int(p["period"]) for p in payload), default=None)
    label = {c["account_id"]: c["code"] for c in result["changes"]}
    audit.record(
        db,
        user_id=user.id,
        action="UPDATE",
        entity_type=ENTITY,
        entity_id=cc.id,
        before={f"{label[a]} · {MONTH_LABELS[m - 1]}": str(c["before"]) for a, m, c in _cells_of(result)}
        | {"total da projeção": result["totals"]["projection_before"]},
        after={f"{label[a]} · {MONTH_LABELS[m - 1]}": str(c["after"]) for a, m, c in _cells_of(result)}
        | {"total da projeção": result["totals"]["projection_after"]},
        reason=(comment or "").strip()[:2000] or f"Projeção do CC {cc.code} gravada na tela",
        ip=ip,
    )
    db.commit()
    return result | {"version_number": version.version_number, "saved": len(result["changes"])}


def _cells_of(result: dict):
    for c in result["changes"]:
        yield c["account_id"], c["month"], c


def history(db: Session, user: User, cc_id: int) -> list[dict]:
    """Gravações da projeção do CC (da auditoria): quem, quando, comentário e as células com antes × depois."""
    cc = _cost_center(db, user, cc_id)
    logs = db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == ENTITY, AuditLog.entity_id == str(cc.id))
        .order_by(AuditLog.id.desc())
        .limit(50)
    )
    out = []
    for log in logs:
        who = db.get(User, log.user_id) if log.user_id else None
        before, after = log.before or {}, log.after or {}
        cells = [
            {"cell": k, "before": before.get(k), "after": after.get(k)}
            for k in sorted(set(before) | set(after))
            if k != "total da projeção"
        ]
        out.append(
            {
                "id": log.id,
                "at": log.occurred_at.isoformat(),
                "by": who.name if who else None,
                "comment": log.reason,
                "cells": cells,
                "total_before": before.get("total da projeção"),
                "total_after": after.get("total da projeção"),
            }
        )
    return out
