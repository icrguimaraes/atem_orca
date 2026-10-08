"""Perguntas sobre o orçamento (defesa do orçamento, 08/10/2026).

Quem analisa o Painel (ex.: o VP) questiona um recorte — área, setor, pacote, conta ou CC — direto do "por quê?";
o gestor da área responde no sistema. Responde quem é gestor de algum CC do recorte (`scope.cost_center_ids`) ou a
Controladoria. Ciclo: OPEN → ANSWERED → CLOSED (quem perguntou, ou a Controladoria, encerra).
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import is_global, visible_cost_center_ids
from app.models import BudgetQuestion, CostCenter, User
from app.models.base import Role

STATUS_LABELS = {"OPEN": "Aguardando resposta", "ANSWERED": "Respondida", "CLOSED": "Encerrada"}


class QuestionError(ValueError):
    pass


def scope_ccs(q: BudgetQuestion) -> set[int]:
    ids = set((q.scope or {}).get("cost_center_ids") or [])
    if q.cost_center_id:
        ids.add(q.cost_center_id)
    return ids


def can_answer(db: Session, user: User, q: BudgetQuestion) -> bool:
    """Gestor de algum CC do recorte (o "gestor da área") ou a Controladoria."""
    if q.status == "CLOSED":
        return False
    if is_global(user):
        return True
    ccs = scope_ccs(q)
    if not ccs:
        return False
    managed = set(
        db.scalars(select(CostCenter.id).where(CostCenter.id.in_(ccs), CostCenter.manager_user_id == user.id))
    )
    if managed:
        return True
    if Role.MANAGER in user.role_codes:
        visible = visible_cost_center_ids(db, user)
        return visible is None or bool(ccs & set(visible))
    return False


def can_view(db: Session, user: User, q: BudgetQuestion, visible: set[int] | None) -> bool:
    if visible is None or q.asked_by == user.id:
        return True
    return bool(scope_ccs(q) & set(visible))


def serialize(db: Session, user: User, q: BudgetQuestion, names: dict[int, str]) -> dict:
    return {
        "id": q.id,
        "subject": q.subject,
        "scope": q.scope or {},
        "cost_center_id": q.cost_center_id,
        "account_id": q.account_id,
        "question": q.question,
        "status": q.status,
        "status_label": STATUS_LABELS.get(q.status, q.status),
        "asked_by": names.get(q.asked_by),
        "asked_at": q.asked_at.isoformat() if q.asked_at else None,
        "answer": q.answer,
        "answered_by": names.get(q.answered_by) if q.answered_by else None,
        "answered_at": q.answered_at.isoformat() if q.answered_at else None,
        "closed_at": q.closed_at.isoformat() if q.closed_at else None,
        "mine": q.asked_by == user.id,
        "can_answer": can_answer(db, user, q),
        "can_close": q.status != "CLOSED" and (q.asked_by == user.id or is_global(user)),
    }


def user_names(db: Session, ids: set[int]) -> dict[int, str]:
    ids = {i for i in ids if i}
    return {u.id: u.name for u in db.scalars(select(User).where(User.id.in_(ids or {-1})))}


def ask(db: Session, version_id: int, user: User, data: dict) -> BudgetQuestion:
    text = (data.get("question") or "").strip()
    subject = (data.get("subject") or "").strip()
    if not text:
        raise QuestionError("Escreva a pergunta")
    if not subject:
        raise QuestionError("Informe sobre o que é a pergunta")
    scope = dict(data.get("scope") or {})
    ccs = sorted({int(c) for c in scope.get("cost_center_ids") or []})
    visible = visible_cost_center_ids(db, user)
    if visible is not None:
        ccs = [c for c in ccs if c in visible]
    if not ccs and not data.get("cost_center_id"):
        raise QuestionError("A pergunta precisa de ao menos um centro de custo no recorte")
    scope["cost_center_ids"] = ccs
    q = BudgetQuestion(
        version_id=version_id,
        cost_center_id=data.get("cost_center_id") or (ccs[0] if len(ccs) == 1 else None),
        department_id=data.get("department_id"),
        account_id=data.get("account_id"),
        subject=subject[:300],
        scope=scope,
        question=text,
        status="OPEN",
        asked_by=user.id,
        asked_at=datetime.utcnow(),
    )
    db.add(q)
    db.flush()
    return q


def answer(db: Session, user: User, q: BudgetQuestion, text: str) -> None:
    text = (text or "").strip()
    if not text:
        raise QuestionError("Escreva a resposta")
    if not can_answer(db, user, q):
        raise PermissionError("Só o gestor da área (ou a Controladoria) responde esta pergunta")
    q.answer, q.answered_by, q.answered_at, q.status = text, user.id, datetime.utcnow(), "ANSWERED"
    db.flush()


def close(db: Session, user: User, q: BudgetQuestion) -> None:
    if q.status == "CLOSED":
        raise QuestionError("A pergunta já está encerrada")
    if not (q.asked_by == user.id or is_global(user)):
        raise PermissionError("Só quem perguntou (ou a Controladoria) encerra a pergunta")
    q.status, q.closed_by, q.closed_at = "CLOSED", user.id, datetime.utcnow()
    db.flush()
