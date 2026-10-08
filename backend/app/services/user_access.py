"""Acesso de usuários a centros de custo (tela Usuários → Acessos).

Usuário não global enxerga os CCs em que é gestor (`cost_centers.manager_user_id`, definido no cadastro do CC) mais
os escopos atribuídos em `user_scopes`: um CC ou uma empresa inteira. É a mesma regra de
`core.deps.visible_cost_center_ids`; aqui ela é calculada em lote (resumo da lista de usuários) e detalhada por
usuário (o que cada linha libera). Perfis globais (Administrador, Controladoria) veem tudo."""

from collections import defaultdict

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import is_global
from app.models import Company, CostCenter, User, UserScope


class AccessError(Exception):
    """Alteração de acesso recusada (mensagem pronta para o usuário)."""

    def __init__(self, message: str, status: int = 409) -> None:
        super().__init__(message)
        self.status = status


def _company_label(company: Company) -> str:
    return f"{company.code} · {company.short_name or company.name}"


def summaries(db: Session, users: list[User]) -> dict[int, dict]:
    """Por usuário: perfil global e quantos CCs distintos acessa (gestor + escopos), com poucas consultas."""
    ids = [u.id for u in users]
    managed: dict[int, set[int]] = defaultdict(set)
    rows = db.execute(select(CostCenter.manager_user_id, CostCenter.id).where(CostCenter.manager_user_id.in_(ids)))
    for user_id, cc_id in rows:
        managed[user_id].add(cc_id)
    scopes = list(db.scalars(select(UserScope).where(UserScope.user_id.in_(ids))))
    companies = {s.company_id for s in scopes if s.company_id and not s.cost_center_id}
    by_company: dict[int, set[int]] = defaultdict(set)
    if companies:
        rows = db.execute(select(CostCenter.company_id, CostCenter.id).where(CostCenter.company_id.in_(companies)))
        for company_id, cc_id in rows:
            by_company[company_id].add(cc_id)
    scoped: dict[int, set[int]] = defaultdict(set)
    count: dict[int, int] = defaultdict(int)
    for s in scopes:
        count[s.user_id] += 1
        if s.cost_center_id:
            scoped[s.user_id].add(s.cost_center_id)
        elif s.company_id:
            scoped[s.user_id] |= by_company[s.company_id]
    return {
        u.id: {
            "is_global": is_global(u),
            "cost_centers": len(managed[u.id] | scoped[u.id]),
            "managed": len(managed[u.id]),
            "scopes": count[u.id],
        }
        for u in users
    }


def _scope_out(db: Session, scope: UserScope) -> dict:
    base = {"id": scope.id, "cost_center_id": scope.cost_center_id, "company_id": scope.company_id}
    if scope.cost_center_id:  # mesma precedência de visible_cost_center_ids
        cc = db.get(CostCenter, scope.cost_center_id)
        return base | {
            "kind": "COST_CENTER",
            "company_id": cc.company_id,
            "code": cc.code,
            "name": cc.name,
            "company": _company_label(cc.company),
            "is_active": cc.is_active,
            "cost_centers": 1,
        }
    if scope.company_id:
        company = db.get(Company, scope.company_id)
        total = db.scalar(select(func.count(CostCenter.id)).where(CostCenter.company_id == company.id)) or 0
        return base | {
            "kind": "COMPANY",
            "code": company.code,
            "name": company.name,
            "company": _company_label(company),
            "is_active": company.is_active,
            "cost_centers": total,
        }
    # escopo vazio (gravado sem CC nem empresa pela substituição em lote): não libera nada, só pode ser removido
    return base | {
        "kind": "EMPTY",
        "code": None,
        "name": "Escopo vazio",
        "company": None,
        "is_active": False,
        "cost_centers": 0,
    }


def detail(db: Session, user: User) -> dict:
    managed = db.scalars(select(CostCenter).where(CostCenter.manager_user_id == user.id).order_by(CostCenter.code))
    scopes = sorted((_scope_out(db, s) for s in user.scopes), key=lambda s: (s["kind"] != "COMPANY", s["code"] or ""))
    return {
        "user_id": user.id,
        "is_global": is_global(user),
        "cost_centers": summaries(db, [user])[user.id]["cost_centers"],
        "managed": [
            {
                "id": cc.id,
                "code": cc.code,
                "name": cc.name,
                "company": _company_label(cc.company),
                "is_active": cc.is_active,
            }
            for cc in managed
        ],
        "scopes": scopes,
    }


def snapshot(db: Session, user: User) -> list[dict]:
    """Escopos do usuário para a auditoria (ids e rótulo legível)."""
    out = []
    for s in sorted(user.scopes, key=lambda s: s.id or 0):
        row = _scope_out(db, s)
        label = {"COST_CENTER": "CC", "COMPANY": "Empresa"}.get(row["kind"])
        out.append(
            {
                "id": s.id,
                "cost_center_id": s.cost_center_id,
                "company_id": s.company_id,
                "label": f"{label} {row['code']} · {row['name']}" if label else row["name"],
            }
        )
    return out


def add_scope(db: Session, user: User, *, cost_center_id: int | None, company_id: int | None) -> UserScope:
    """Libera um CC ou uma empresa inteira; recusa o que o usuário já acessa pelo mesmo caminho."""
    if bool(cost_center_id) == bool(company_id):
        raise AccessError("Informe um centro de custo ou uma empresa inteira (apenas um dos dois).", 422)
    company_scopes = {s.company_id for s in user.scopes if s.company_id and not s.cost_center_id}
    if cost_center_id:
        cc = db.get(CostCenter, cost_center_id)
        if cc is None:
            raise AccessError("Centro de custo não encontrado.", 404)
        if cc.manager_user_id == user.id:
            raise AccessError(f"{user.name} já acessa o CC {cc.code} como gestor (cadastro do CC).")
        if any(s.cost_center_id == cc.id for s in user.scopes):
            raise AccessError(f"O CC {cc.code} já está atribuído a {user.name}.")
        if cc.company_id in company_scopes:
            raise AccessError(f"{user.name} já acessa a empresa inteira do CC {cc.code}.")
        scope = UserScope(user_id=user.id, cost_center_id=cc.id)
    else:
        company = db.get(Company, company_id)
        if company is None:
            raise AccessError("Empresa não encontrada.", 404)
        if company.id in company_scopes:
            raise AccessError(f"A empresa {company.code} já está liberada para {user.name}.")
        scope = UserScope(user_id=user.id, company_id=company.id)
    user.scopes.append(scope)
    db.flush()
    return scope


def set_cost_centers(db: Session, user: User, cost_center_ids: list[int], manager_of: list[int]) -> dict:
    """Define em lote os CCs do usuário: escopos por CC (substitui; empresas inteiras ficam) e de quais CCs ele é o
    gestor. CC em que ele é gestor não precisa de escopo. Devolve o que mudou de gestor para a auditoria."""
    wanted_mgr = set(manager_of)
    wanted = set(cost_center_ids) - wanted_mgr
    known = set(db.scalars(select(CostCenter.id).where(CostCenter.id.in_(wanted | wanted_mgr or {-1}))))
    missing = (wanted | wanted_mgr) - known
    if missing:
        raise AccessError(f"Centro(s) de custo não encontrado(s): {sorted(missing)}", 404)
    user.scopes = [s for s in user.scopes if not s.cost_center_id] + [
        UserScope(user_id=user.id, cost_center_id=cc_id) for cc_id in sorted(wanted)
    ]
    changed = []
    for cc in db.scalars(
        select(CostCenter).where((CostCenter.manager_user_id == user.id) | CostCenter.id.in_(wanted_mgr or {-1}))
    ):
        if cc.id in wanted_mgr and cc.manager_user_id != user.id:
            changed.append({"cost_center": cc.code, "from": cc.manager_user_id, "to": user.id})
            cc.manager_user_id, cc.manager_name = user.id, user.name
        elif cc.id not in wanted_mgr and cc.manager_user_id == user.id:
            changed.append({"cost_center": cc.code, "from": user.id, "to": None})
            cc.manager_user_id = None
    db.flush()
    return {"managers": changed}


def remove_scope(db: Session, user: User, scope_id: int) -> None:
    scope = next((s for s in user.scopes if s.id == scope_id), None)
    if scope is None:
        raise AccessError("Acesso não encontrado para este usuário.", 404)
    user.scopes.remove(scope)  # delete-orphan apaga a linha
    db.flush()
