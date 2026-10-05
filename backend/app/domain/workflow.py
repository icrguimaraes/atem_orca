"""Máquina de estados do workflow de aprovação (docs/02, seção 6).

DRAFT → IN_PROGRESS → SUBMITTED → UNDER_REVIEW → APPROVED → CONSOLIDATED
                ▲                       │
                └── ADJUSTMENT_REQUESTED ◀┘
"""

from dataclasses import dataclass

EDITABLE = frozenset({"DRAFT", "IN_PROGRESS", "ADJUSTMENT_REQUESTED"})

STATUS_LABELS = {
    "DRAFT": "Rascunho",
    "IN_PROGRESS": "Em preenchimento",
    "SUBMITTED": "Enviado para validação",
    "UNDER_REVIEW": "Em análise",
    "ADJUSTMENT_REQUESTED": "Ajuste solicitado",
    "APPROVED": "Aprovado",
    "CONSOLIDATED": "Consolidado",
}


@dataclass(frozen=True)
class Transition:
    action: str
    label: str
    sources: frozenset[str]
    target: str
    roles: frozenset[str]  # perfis que podem executar (ADMIN sempre pode)
    requires_comment: bool = False
    owner_allowed: bool = False  # gestor do CC pode executar


TRANSITIONS: dict[str, Transition] = {
    t.action: t
    for t in (
        Transition(
            "submit",
            "Enviar para validação",
            frozenset({"IN_PROGRESS", "ADJUSTMENT_REQUESTED"}),
            "SUBMITTED",
            frozenset({"CONTROLLER"}),
            owner_allowed=True,
        ),
        Transition(
            "recall",
            "Retirar envio",
            frozenset({"SUBMITTED"}),
            "IN_PROGRESS",
            frozenset({"CONTROLLER"}),
            owner_allowed=True,
        ),
        Transition(
            "start_review", "Iniciar análise", frozenset({"SUBMITTED"}), "UNDER_REVIEW", frozenset({"CONTROLLER"})
        ),
        Transition(
            "request_adjustment",
            "Solicitar ajuste",
            frozenset({"SUBMITTED", "UNDER_REVIEW"}),
            "ADJUSTMENT_REQUESTED",
            frozenset({"CONTROLLER"}),
            requires_comment=True,
        ),
        Transition("approve", "Aprovar", frozenset({"UNDER_REVIEW"}), "APPROVED", frozenset({"CONTROLLER"})),
        Transition(
            "reopen",
            "Reabrir para ajuste",
            frozenset({"APPROVED"}),
            "ADJUSTMENT_REQUESTED",
            frozenset({"CONTROLLER"}),
            requires_comment=True,
        ),
        Transition("consolidate", "Consolidar", frozenset({"APPROVED"}), "CONSOLIDATED", frozenset({"CONTROLLER"})),
    )
}


class WorkflowError(Exception):
    pass


def check_transition(
    action: str,
    status: str,
    *,
    roles: set[str],
    is_owner: bool,
    comment: str | None,
    blockers: list[str] | None = None,
) -> Transition:
    """Valida a ação; levanta WorkflowError com a mensagem para o usuário."""
    t = TRANSITIONS.get(action)
    if t is None:
        raise WorkflowError(f"Ação desconhecida: {action}")
    if status not in t.sources:
        raise WorkflowError(f"'{t.label}' não é possível com o orçamento em '{STATUS_LABELS.get(status, status)}'")
    allowed = "ADMIN" in roles or bool(roles & t.roles) or (t.owner_allowed and is_owner)
    if not allowed:
        raise WorkflowError(f"Seu perfil não pode executar '{t.label}'")
    if t.requires_comment and not (comment or "").strip():
        raise WorkflowError(f"'{t.label}' exige um comentário explicando o motivo")
    if blockers:
        raise WorkflowError("Pendências: " + "; ".join(blockers))
    return t


def available_actions(status: str, *, roles: set[str], is_owner: bool) -> list[dict]:
    out = []
    for t in TRANSITIONS.values():
        if status in t.sources and ("ADMIN" in roles or roles & t.roles or (t.owner_allowed and is_owner)):
            out.append({"action": t.action, "label": t.label, "requires_comment": t.requires_comment})
    return out
