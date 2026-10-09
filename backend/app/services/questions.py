"""Perguntas sobre o orçamento (defesa do orçamento, 08/10/2026).

Quem analisa o Painel (ex.: o VP) questiona direto do "por quê?"; o gestor da área responde no sistema. Desde
09/10/2026 a pergunta é sobre o **lançamento** em si — linha do OPEX, movimentação de pessoal ou solicitação de CAPEX
(`item_type`), com o lançamento guardado como estava (`item_snapshot`); as perguntas antigas, sobre um recorte (área,
setor, pacote, conta ou CC), continuam listadas e respondidas. Responde quem é gestor de algum CC da pergunta
(`scope.cost_center_ids`, o CC do lançamento na hora da pergunta e o CC atual dele, se foi movido) ou a Controladoria.
Ciclo: OPEN → ANSWERED → CLOSED (quem perguntou, ou a Controladoria, encerra).
"""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.deps import is_global, visible_cost_center_ids
from app.domain.rules.common import money
from app.models import (
    Account,
    BudgetLine,
    BudgetPackage,
    BudgetQuestion,
    BudgetSubmission,
    CapexProject,
    CostCenter,
    JobPosition,
    PersonnelMovement,
    User,
)
from app.models.base import Role
from app.services import justifications as just_svc

STATUS_LABELS = {"OPEN": "Aguardando resposta", "ANSWERED": "Respondida", "CLOSED": "Encerrada"}
ZERO = Decimal("0")
# tipo de lançamento → (coluna da FK na pergunta, modelo, rótulo)
ITEM_TYPES = {
    "OPEX_LINE": ("budget_line_id", BudgetLine, "Lançamento OPEX"),
    "PERSONNEL_MOVEMENT": ("personnel_movement_id", PersonnelMovement, "Movimentação de pessoal"),
    "CAPEX_PROJECT": ("capex_project_id", CapexProject, "Solicitação de CAPEX"),
}
ITEM_PATHS = {"OPEX_LINE": "orcamento", "PERSONNEL_MOVEMENT": "pessoal", "CAPEX_PROJECT": "capex"}


class QuestionError(ValueError):
    pass


def _m(v) -> str:
    return str(money(Decimal(v or 0)))


def item_object(db: Session, q: BudgetQuestion):
    """O lançamento da pergunta como está hoje (None: pergunta de recorte ou lançamento excluído)."""
    if q.item_type not in ITEM_TYPES:
        return None
    column, model, _ = ITEM_TYPES[q.item_type]
    ident = getattr(q, column)
    return db.get(model, ident) if ident else None


def describe_item(db: Session, item_type: str, obj) -> dict:
    """Retrato do lançamento (CC, conta, descrição, valores): gravado na pergunta e comparado com o atual."""
    sub = db.get(BudgetSubmission, obj.submission_id)
    cc = db.get(CostCenter, sub.cost_center_id)
    out = {
        "type": item_type,
        "id": obj.id,
        "version_id": sub.version_id,
        "submission_id": sub.id,
        "cost_center_id": cc.id,
        "cost_center": f"{cc.code} · {cc.name}",
        "department_id": cc.department_id,
    }
    if item_type == "OPEX_LINE":
        acc = db.get(Account, obj.account_id)
        pkg_id = obj.package_id or (acc.package_id if acc else None)
        pkg = db.get(BudgetPackage, pkg_id) if pkg_id else None
        values = {v.month: v.amount for v in obj.values}
        account = f"{acc.code} {acc.name}" if acc else None
        return out | {
            "label": obj.description or obj.supplier or account or "Lançamento",
            "account_id": obj.account_id,
            "account": account,
            "package_id": pkg_id,
            "package": pkg.name if pkg else None,
            "line_type": obj.line_type,
            "description": obj.description,
            "supplier": obj.supplier,
            "justification": obj.justification,
            "total": _m(obj.total_amount),
            "values": [_m(values.get(m, ZERO)) for m in range(1, 13)],
        }
    if item_type == "PERSONNEL_MOVEMENT":
        noun = just_svc.MOVE_NOUNS.get(obj.movement_type, obj.movement_type.lower())
        pos = db.get(JobPosition, obj.position_id) if obj.position_id else None
        month = just_svc.MONTHS[obj.effective_month - 1] if obj.effective_month in range(1, 13) else None
        position = pos.name if pos else (obj.attributes or {}).get("position_name")
        return out | {
            "label": f"{noun[0].upper() + noun[1:]} de {just_svc._who(db, obj)}",
            "movement_type": obj.movement_type,
            "description": " · ".join(x for x in (month, position) if x) or None,
            "justification": obj.reason,
            "total": _m(obj.new_salary) if obj.new_salary is not None else None,
            "total_label": "Novo salário",
        }
    total = sum((i.total_value for i in obj.items), ZERO)
    return out | {
        "label": f"{obj.code} · {obj.title}",
        "description": obj.description,
        "justification": obj.justification,
        "total": _m(total),
    }


def _current(db: Session, q: BudgetQuestion) -> dict | None:
    obj = item_object(db, q)
    if obj is None:
        return None
    current = describe_item(db, q.item_type, obj)
    return current if current["version_id"] == q.version_id else None


def item_out(db: Session, q: BudgetQuestion) -> dict | None:
    """Contexto do lançamento para a tela: como estava na pergunta × como está agora, e o link para abri-lo."""
    if not q.item_type:
        return None
    snap = q.item_snapshot or {}
    current = _current(db, q)
    link = None
    if current is not None:
        link = f"/{ITEM_PATHS[q.item_type]}/{current['cost_center_id']}"
        if q.item_type == "OPEX_LINE" and current.get("package_id"):
            link += f"?pacote={current['package_id']}&linha={current['id']}"
    return {
        "type": q.item_type,
        "type_label": ITEM_TYPES[q.item_type][2] if q.item_type in ITEM_TYPES else q.item_type,
        "snapshot": snap,
        "current": current,
        "deleted": current is None,
        "moved": bool(current and snap and current["cost_center_id"] != snap.get("cost_center_id")),
        "changed": bool(current and snap and current.get("total") != snap.get("total")),
        "link": link,
    }


def scope_ccs(q: BudgetQuestion, db: Session | None = None) -> set[int]:
    """CCs da pergunta: o recorte gravado e, se o lançamento mudou de CC depois, também o CC atual dele."""
    ids = set((q.scope or {}).get("cost_center_ids") or [])
    if q.cost_center_id:
        ids.add(q.cost_center_id)
    if db is not None and q.item_type:
        current = _current(db, q)
        if current is not None:
            ids.add(current["cost_center_id"])
    return ids


def can_answer(db: Session, user: User, q: BudgetQuestion) -> bool:
    """Gestor de algum CC da pergunta (o "gestor da área", ou o do CC do lançamento) ou a Controladoria."""
    if q.status == "CLOSED":
        return False
    if is_global(user):
        return True
    ccs = scope_ccs(q, db)
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
    return bool(scope_ccs(q, db) & set(visible))


def serialize(db: Session, user: User, q: BudgetQuestion, names: dict[int, str]) -> dict:
    return {
        "id": q.id,
        "subject": q.subject,
        "scope": q.scope or {},
        "cost_center_id": q.cost_center_id,
        "account_id": q.account_id,
        "item_type": q.item_type,
        "item": item_out(db, q),
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


def ask_item(db: Session, version_id: int, user: User, data: dict) -> BudgetQuestion:
    """Pergunta sobre um lançamento: vai para o gestor do CC do lançamento (e a Controladoria)."""
    text = (data.get("question") or "").strip()
    if not text:
        raise QuestionError("Escreva a pergunta")
    item_type = data.get("item_type")
    if item_type not in ITEM_TYPES:
        raise QuestionError("Tipo de lançamento inválido")
    column, model, type_label = ITEM_TYPES[item_type]
    obj = db.get(model, int(data.get("item_id") or 0))
    snap = describe_item(db, item_type, obj) if obj is not None else None
    if snap is None or snap["version_id"] != version_id:
        raise QuestionError("Lançamento não encontrado na versão atual do orçamento")
    visible = visible_cost_center_ids(db, user)
    if visible is not None and snap["cost_center_id"] not in visible:
        raise PermissionError("Sem acesso ao centro de custo deste lançamento")
    scope = dict(data.get("scope") or {})
    scope["cost_center_ids"] = [snap["cost_center_id"]]
    subject = (data.get("subject") or "").strip() or snap["label"] or type_label
    q = BudgetQuestion(
        version_id=version_id,
        cost_center_id=snap["cost_center_id"],
        department_id=snap["department_id"],
        account_id=snap.get("account_id"),
        item_type=item_type,
        item_snapshot=snap,
        subject=subject[:300],
        scope=scope,
        question=text,
        status="OPEN",
        asked_by=user.id,
        asked_at=datetime.utcnow(),
    )
    setattr(q, column, obj.id)
    db.add(q)
    db.flush()
    return q


def ask(db: Session, version_id: int, user: User, data: dict) -> BudgetQuestion:
    if data.get("item_type"):
        return ask_item(db, version_id, user, data)
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
