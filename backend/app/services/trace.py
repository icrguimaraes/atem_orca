"""Rastro (09/10/2026): do total do Painel até o lançamento que o originou.

Para qualquer recorte do Painel (empresa, área, CC, tipo, ano, mês, pacote) a tela desce
área → setor → centro de custo → pacote → conta → **lançamento**: a linha do OPEX, o item do CAPEX, a posição do
quadro de pessoal, o contrato PJ (agregado para quem não tem acesso) ou a partida do realizado/orçado de referência
com o lote de importação de onde veio. Os níveis da árvore somam exatamente como o Painel (`dashboard.Facts`).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.api.v1.dashboard import Facts, Period, _ints, _main_model, _period
from app.core.deps import is_global, visible_cost_center_ids
from app.domain.rules.personnel import split_amount
from app.models import (
    Account,
    ActualEntry,
    Area,
    BudgetLine,
    BudgetPackage,
    BudgetSubmission,
    CapexProject,
    CostCenter,
    DatasetVersion,
    Department,
    ImportBatch,
    ReferenceBudgetEntry,
    User,
)
from app.services import consolidation as cons
from app.services import opex as opex_svc
from app.services import personnel as personnel_svc
from app.services import pj as pj_svc
from app.services.exports import _sheet

ZERO = Decimal("0")
LEVELS = ("department", "area", "cost_center", "package", "account")
LEVEL_LABELS = {
    "department": "Área",
    "area": "Setor",
    "cost_center": "Centro de custo",
    "package": "Pacote",
    "account": "Conta",
}
KEYS = {
    "department": "department_id",
    "area": "area_id",
    "cost_center": "cost_center_id",
    "package": "package_id",
    "account": "account_id",
}
KIND_LABELS = {
    "OPEX_LINE": "Lançamento OPEX",
    "CAPEX_ITEM": "Item de CAPEX",
    "PERSONNEL": "Pessoal",
    "PJ": "Contrato PJ",
    "PJ_GROUP": "Contratos PJ",
    "ACTUAL": "Realizado",
    "PROJECTION": "Projeção",
    "REFERENCE": "Orçado de referência",
}
MOVEMENT_LABELS = {
    "KEEP": "Manter",
    "PROMOTION": "Promoção",
    "SALARY_ADJUSTMENT": "Reajuste",
    "HIRE": "Admissão",
    "TERMINATION": "Desligamento",
    "TRANSFER": "Transferência",
}
COMPONENT_LABELS = {
    "salary": "Salário",
    "severance": "Verba rescisória",
    "bonus": "Abono anual",
    "cc_bonus": "Bônus CLT do CC (parâmetro)",
    "charges": "Encargos e benefícios (rateio do multiplicador)",
}


def _m(v: Decimal | None) -> str:
    return str((v or ZERO).quantize(Decimal("0.01")))


def _share(part: Decimal, total: Decimal) -> str | None:
    return None if not total else str((part / total).quantize(Decimal("0.0001")))


@dataclass
class TraceQuery:
    """Filtros do Painel + trilha do rastro (parent_*)."""

    company_id: str | None = None
    cost_center_id: int | None = None
    package_id: int | None = None
    account_id: int | None = None
    department_id: str | None = None
    years: str | None = None
    months: str | None = None
    modules: str | None = None
    series: str | None = None  # actual | budget (padrão: a série principal do período, como no Painel)
    parent_department_id: int | None = None
    parent_no_department: bool = False
    parent_area_id: int | None = None
    parent_no_area: bool = False
    parent_cost_center_id: int | None = None
    parent_package_id: int | None = None
    parent_no_package: bool = False
    parent_account_id: int | None = None

    @property
    def parent(self) -> dict:
        return {
            "package_id": self.parent_package_id,
            "no_package": self.parent_no_package,
            "account_id": self.parent_account_id,
            "department_id": self.parent_department_id,
            "no_department_id": self.parent_no_department,
            "area_id": self.parent_area_id,
            "no_area_id": self.parent_no_area,
        }

    @property
    def effective_cost_center_id(self) -> int | None:
        return self.parent_cost_center_id or self.cost_center_id


@dataclass
class Series:
    """O que se rastreia: realizado (ActualEntry), orçado de referência (ReferenceBudgetEntry) ou o orçamento
    proposto do ano do ciclo (consolidação) — anos escolhidos somados, como no Painel."""

    period: Period
    model: type
    years: list[int]
    label: str

    @property
    def from_target(self) -> bool:
        return self.model is ReferenceBudgetEntry and self.period.target_year in self.years

    @property
    def sql_years(self) -> list[int]:
        return [y for y in self.years if not (self.from_target and y == self.period.target_year)]


def series_for(db: Session, q: TraceQuery) -> Series:
    P = _period(db, q.years, q.months, q.modules, False, True)
    if q.series == "actual" and P.actual_selected:
        return Series(P, ActualEntry, P.actual_selected, P.actual_label or "Realizado")
    if q.series == "budget" and P.budget_selected:
        return Series(P, ReferenceBudgetEntry, P.budget_selected, P.budget_label or "Orçado")
    return Series(P, _main_model(P), P.main_years, P.main_label)


def facts_for(db: Session, user: User, q: TraceQuery, s: Series) -> Facts:
    return Facts(
        db,
        user,
        q.company_id,
        q.effective_cost_center_id,
        q.package_id,
        s.period,
        q.parent,
        account_id=q.account_id,
        department_id=q.department_id,
    )


def scope_ccs(db: Session, user: User, q: TraceQuery) -> set[int]:
    """CCs do recorte (filtros do Painel + trilha), dentro do que o usuário enxerga."""
    stmt = select(CostCenter.id)
    visible = visible_cost_center_ids(db, user)
    if visible is not None:
        stmt = stmt.where(CostCenter.id.in_(visible or {-1}))
    if _ints(q.company_id):
        stmt = stmt.where(CostCenter.company_id.in_(_ints(q.company_id)))
    if q.effective_cost_center_id:
        stmt = stmt.where(CostCenter.id == q.effective_cost_center_id)
    if _ints(q.department_id):
        stmt = stmt.where(CostCenter.department_id.in_(_ints(q.department_id)))
    for key in ("department_id", "area_id"):
        column = getattr(CostCenter, key)
        if q.parent[key] is not None:
            stmt = stmt.where(column == q.parent[key])
        elif q.parent[f"no_{key}"]:
            stmt = stmt.where(column.is_(None))
    return set(db.scalars(stmt))


# ------------------------------------------------------------------ árvore


def _names(db: Session, level: str, ids: set) -> tuple[dict, tuple[str | None, str]]:
    ids = {i for i in ids if i is not None} or {-1}
    if level == "department":
        return {d.id: (None, d.name) for d in db.scalars(select(Department).where(Department.id.in_(ids)))}, (
            None,
            "Sem área",
        )
    if level == "area":
        return {a.id: (None, a.name) for a in db.scalars(select(Area).where(Area.id.in_(ids)))}, (None, "Sem setor")
    if level == "package":
        return {p.id: (None, p.name) for p in db.scalars(select(BudgetPackage).where(BudgetPackage.id.in_(ids)))}, (
            None,
            "Sem pacote",
        )
    if level == "account":
        return {a.id: (a.code, a.name) for a in db.scalars(select(Account).where(Account.id.in_(ids)))}, (
            None,
            "Conta não cadastrada",
        )
    return {c.id: (c.code, c.name) for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(ids)))}, (
        None,
        "Sem centro de custo",
    )


def trail(db: Session, q: TraceQuery) -> list[dict]:
    """Migalhas da trilha (nível → nome), na ordem dos níveis."""
    out = []
    for level in LEVELS:
        key = KEYS[level]
        ident = getattr(q, f"parent_{key}", None)
        none = getattr(q, f"parent_no_{level}", False) if level in ("department", "area", "package") else False
        if ident is None and not none:
            continue
        names, missing = _names(db, level, {ident})
        code, name = names.get(ident, missing)
        out.append({"level": level, "label": LEVEL_LABELS[level], "id": ident, "code": code, "name": name})
    return out


def tree(db: Session, user: User, q: TraceQuery, level: str) -> dict:
    s = series_for(db, q)
    f = facts_for(db, user, q, s)
    key = KEYS[level]
    cur = {k: v for k, v in f.sums(s.model, s.years, key) if v}
    idx = LEVELS.index(level)
    next_level = LEVELS[idx + 1] if idx + 1 < len(LEVELS) else None
    children: dict = defaultdict(set)
    if next_level:
        for k, child, v in f.sums(s.model, s.years, key, KEYS[next_level]):
            if v:
                children[k].add(child)
    else:  # conta: quantos lançamentos compõem o valor
        counts = _record_counts(db, user, q, s, f)
        children = {k: set(range(n)) for k, n in counts.items()}
    names, missing = _names(db, level, set(cur))
    total = sum(cur.values(), ZERO)
    rows = []
    for k, v in cur.items():
        code, name = names.get(k, missing)
        rows.append(
            {
                "id": k,
                "code": code,
                "name": name,
                "value": _m(v),
                "share": _share(v, total),
                "children": len(children.get(k, ())),
            }
        )
    rows.sort(key=lambda r: Decimal(r["value"]), reverse=True)
    return {
        "level": level,
        "level_label": LEVEL_LABELS[level],
        "next_level": next_level,
        "next_label": LEVEL_LABELS[next_level] if next_level else "Lançamento",
        "series": "actual" if s.model is ActualEntry else "budget",
        "series_label": s.label,
        "period": s.period.summary(),
        "available_years": s.period.available,
        "trail": trail(db, q),
        "rows": rows,
        "total": _m(total),
        "count": len(rows),
    }


# ------------------------------------------------------------------ lançamentos


@dataclass
class Record:
    kind: str
    id: str
    module: str
    cost_center_id: int | None
    account_code: str | None
    account_id: int | None
    package_id: int | None
    title: str
    detail: str | None = None
    values: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)
    unscheduled: Decimal = ZERO
    month: int | None = None  # partida do realizado: um só mês
    justification: str | None = None
    fields: list[tuple[str, str | None]] = field(default_factory=list)
    source: dict | None = None
    link: str | None = None
    search: str = ""

    @property
    def total(self) -> Decimal:
        return sum(self.values, ZERO) + self.unscheduled


def _month_filter(values: list[Decimal], months: set[int] | None) -> list[Decimal]:
    return [v if (not months or m in months) else ZERO for m, v in enumerate(values, start=1)]


def _accounts(db: Session) -> tuple[dict[int, Account], dict[str, Account]]:
    accounts = {a.id: a for a in db.scalars(select(Account))}
    return accounts, {a.code: a for a in accounts.values()}


def _opex_records(db: Session, ctx, scope: set[int], months, accounts: dict[int, Account]) -> list[Record]:
    stmt = (
        select(BudgetLine)
        .join(BudgetSubmission, BudgetSubmission.id == BudgetLine.submission_id)
        .where(
            BudgetSubmission.version_id == ctx.version.id,
            BudgetSubmission.module == "OPEX",
            BudgetSubmission.cost_center_id.in_(scope or {-1}),
        )
        .order_by(BudgetLine.id)
    )
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}
    out = []
    for ln in db.scalars(stmt):
        acc = accounts.get(ln.account_id)
        pkg_id = ln.package_id or (acc.package_id if acc else None)
        raw = {v.month: v.amount for v in ln.values}
        values = _month_filter([raw.get(m, ZERO) for m in range(1, 13)], months)
        if not any(values):
            continue
        link = f"/orcamento/{ln.cost_center_id}"
        if pkg_id:
            link += f"?pacote={pkg_id}&linha={ln.id}"
        out.append(
            Record(
                "OPEX_LINE",
                f"opex:{ln.id}",
                "OPEX",
                ln.cost_center_id,
                acc.code if acc else None,
                ln.account_id,
                pkg_id,
                ln.description or (acc.name if acc else "Lançamento OPEX"),
                ln.supplier,
                values,
                justification=ln.justification,
                fields=[
                    ("Tipo de linha", ln.line_type),
                    ("Fornecedor", ln.supplier),
                    ("Gestor do contrato", ln.contract_manager),
                    ("Premissa", ln.assumption),
                    ("Pacote", packages.get(pkg_id)),
                    ("Criado em", ln.created_at.isoformat() if getattr(ln, "created_at", None) else None),
                    ("Atualizado em", ln.updated_at.isoformat() if getattr(ln, "updated_at", None) else None),
                ],
                source={"label": "Orçamento OPEX (linha)", "line_id": ln.id, "submission_id": ln.submission_id},
                link=link,
                search=" ".join(filter(None, [ln.description, ln.supplier, ln.justification])),
            )
        )
    return out


def _capex_records(db: Session, ctx, scope: set[int], months, accounts: dict[int, Account]) -> list[Record]:
    stmt = (
        select(CapexProject)
        .join(BudgetSubmission, BudgetSubmission.id == CapexProject.submission_id)
        .where(
            BudgetSubmission.version_id == ctx.version.id,
            BudgetSubmission.module == "CAPEX",
            BudgetSubmission.cost_center_id.in_(scope or {-1}),
        )
        .order_by(CapexProject.id)
    )
    out = []
    for proj in db.scalars(stmt):
        for item in proj.items:
            acc = accounts.get(item.account_id)
            raw = {v.month: v.amount for v in item.values}
            scheduled = [raw.get(m, ZERO) for m in range(1, 13)]
            values = _month_filter(scheduled, months)
            pending = (item.total_value or ZERO) - sum(scheduled, ZERO)
            unscheduled = pending if (pending > 0 and not months) else ZERO
            if not any(values) and not unscheduled:
                continue
            out.append(
                Record(
                    "CAPEX_ITEM",
                    f"capex:{item.id}",
                    "CAPEX",
                    proj.cost_center_id,
                    acc.code if acc else None,
                    item.account_id,
                    acc.package_id if acc else None,
                    f"{proj.code} · {proj.title}",
                    item.item_name,
                    values,
                    unscheduled,
                    justification=proj.justification,
                    fields=[
                        ("Item", item.item_name),
                        ("Descrição do item", item.description),
                        ("Quantidade", _m(item.quantity)),
                        ("Valor unitário", _m(item.unit_value)),
                        ("Valor total do item", _m(item.total_value)),
                        ("Sem cronograma", _m(unscheduled) if unscheduled else None),
                        ("Tipo", proj.project_type_code),
                        ("Prioridade", proj.priority),
                        ("Descrição do projeto", proj.description),
                    ],
                    source={"label": "Orçamento CAPEX (item)", "project_id": proj.id, "item_id": item.id},
                    link=f"/capex/{proj.cost_center_id}",
                    search=" ".join(filter(None, [proj.code, proj.title, item.item_name, item.description])),
                )
            )
    return out


def _personnel_records(db: Session, ctx, scope: set[int], months, by_code: dict[str, Account]) -> list[Record]:
    """Um registro por posição e componente (salário, verba rescisória, abono, encargos rateados) mais o bônus
    CLT do CC: a soma por conta bate com a consolidação (`consolidation.personnel_amounts`) a menos de centavos
    de arredondamento do rateio, que ali é feito sobre o total do CC."""
    pacc = cons.personnel_accounts(db, ctx)
    split = cons.charges_split(db, ctx)
    weights = [(code, w) for code, _name, w in split]
    scenario = personnel_svc.baseline(db, ctx)
    by_cc = personnel_svc.build_positions(db, ctx, scope)
    owners = personnel_svc.bonus_owners(db, ctx)
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(scope or {-1})))}
    out: list[Record] = []

    def rec(kind: str, ident: str, cc_id: int, code: str, title: str, detail, values, fields, search) -> None:
        values = _month_filter([Decimal(v) for v in values], months)
        if not any(values):
            return
        acc = by_code.get(code)
        out.append(
            Record(
                "PERSONNEL",
                ident,
                "PERSONNEL",
                cc_id,
                code,
                acc.id if acc else None,
                acc.package_id if acc else None,
                title,
                detail,
                values,
                fields=[("Componente", COMPONENT_LABELS[kind]), *fields],
                source={"label": "Quadro de pessoal + movimentações (cálculo do sistema)"},
                link=f"/pessoal/{cc_id}",
                search=search,
            )
        )

    for cc_id, positions in by_cc.items():
        if cc_id not in scope:
            continue
        for pos in positions:
            cost, salary, _hc = personnel_svc.position_cost(pos, scenario)
            bonus = personnel_svc.position_bonus(pos, scenario)
            severance = [ZERO] * 12
            if pos.severance and pos.plan.effective_month:
                severance[pos.plan.effective_month - 1] = pos.severance
            emp, mv = pos.employee, pos.movement
            if pos.kind == "HIRE":
                title = f"Vaga: {pos.extra.get('position') or 'cargo a definir'}"
                if pos.plan.quantity > 1:
                    title += f" × {pos.plan.quantity}"
                ident = f"hire:{mv.id if mv else cc_id}"
            elif pos.kind == "TRANSFER_IN":
                origin = ccs.get(pos.extra.get("from_cost_center_id"))
                title = f"{emp.registration} · {emp.name}" if emp else "Transferência recebida"
                title += f" (transferido de {origin.code if origin else 'outro CC'})"
                ident = f"transfer:{emp.id if emp else cc_id}"
            else:
                title = f"{emp.registration} · {emp.name}" if emp else "Colaborador"
                ident = f"emp:{emp.id if emp else cc_id}"
            movement = pos.plan.movement
            move_label = MOVEMENT_LABELS.get(movement, movement)
            if pos.plan.effective_month and movement != "KEEP":
                move_label += f" em {pos.plan.effective_month:02d}"
            fields = [
                ("Cargo", pos.extra.get("new_position") or pos.extra.get("position")),
                ("Contrato", pos.plan.contract_type),
                ("Salário base", _m(pos.plan.base_salary)),
                ("Novo salário", _m(pos.plan.new_salary) if pos.plan.new_salary is not None else None),
                ("Movimentação", move_label),
                ("Motivo", mv.reason if mv else None),
                ("Custo mensal total (com multiplicador)", _m(sum(cost, ZERO))),
            ]
            search = " ".join(filter(None, [title, pos.extra.get("position"), move_label]))
            for kind, series in (("salary", salary), ("severance", severance), ("bonus", bonus)):
                rec(kind, f"{ident}:{kind}", cc_id, pacc[kind][0], title, move_label, series, fields, search)
            charges = [cost[i] - salary[i] - severance[i] - bonus[i] for i in range(12)]
            per_account: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO] * 12)
            for i, amount in enumerate(charges):
                if amount:
                    for code, part in split_amount(amount, weights).items():
                        per_account[code][i] += part
            for code, series in per_account.items():
                rec("charges", f"{ident}:charges:{code}", cc_id, code, title, move_label, series, fields, search)
        cc_bonus = personnel_svc.cc_bonus_for(db, ctx, cc_id, owners)
        if any(cc_bonus):
            cc = ccs.get(cc_id)
            rec(
                "cc_bonus",
                f"ccbonus:{cc_id}",
                cc_id,
                pacc["cc_bonus"][0],
                COMPONENT_LABELS["cc_bonus"],
                f"CC {cc.code}" if cc else None,
                cc_bonus,
                [("Parâmetro", "personnel.bonus_by_cc")],
                "bônus clt cc parâmetro",
            )
    return out


def _pj_records(db: Session, ctx, user: User, scope: set[int], months, by_code: dict[str, Account]) -> list[Record]:
    """Contratos PJ: quem tem acesso (flag "Vê contratos PJ", no escopo dele) vê um registro por contrato; para os
    demais, só "Contratos PJ · n contratos" por CC, sem nomes."""
    code, _adj, _adj_month = cons.pj_budget_params(ctx)
    acc = by_code.get(code)
    pj_scope = pj_svc.scope_for(db, user) if user.can_view_pj else None
    grouped: dict[int, list] = defaultdict(list)
    for contract, amounts in cons.pj_contract_amounts(db, ctx, scope):
        values = _month_filter([amounts.get(m, ZERO) for m in range(1, 13)], months)
        if any(values):
            grouped[contract.cost_center_id].append((contract, values))
    out: list[Record] = []
    base = {
        "module": "OPEX",
        "account_code": code,
        "account_id": acc.id if acc else None,
        "package_id": acc.package_id if acc else None,
    }
    for cc_id, items in grouped.items():
        if pj_scope is not None and pj_scope.allows(cc_id):
            for c, values in items:
                out.append(
                    Record(
                        "PJ",
                        f"pj:{c.id}",
                        cost_center_id=cc_id,
                        title=c.name or c.company_name or f"Contrato PJ #{c.id}",
                        detail=c.role,
                        values=values,
                        fields=[
                            ("Razão social", c.company_name),
                            ("Função", c.role),
                            ("Valor mensal", _m(c.monthly_value)),
                            ("Bonificação anual", _m(c.annual_bonus)),
                            ("Início", c.start_date.isoformat() if c.start_date else None),
                            ("Término", c.end_date.isoformat() if c.end_date else None),
                        ],
                        source={"label": "Contratos PJ (cálculo com reajuste e bonificação)", "contract_id": c.id},
                        link="/pj",
                        search=" ".join(filter(None, [c.name, c.company_name, c.role])),
                        **base,
                    )
                )
        else:
            total = [sum(v[i] for _c, v in items) for i in range(12)]
            out.append(
                Record(
                    "PJ_GROUP",
                    f"pjgroup:{cc_id}",
                    cost_center_id=cc_id,
                    title=f"Contratos PJ · {len(items)} contrato{'s' if len(items) != 1 else ''}",
                    detail="Confidencial: detalhe só para quem tem acesso aos contratos PJ",
                    values=total,
                    fields=[("Contratos", str(len(items)))],
                    source={"label": "Contratos PJ (agregado por CC)"},
                    link=None,
                    search="contratos pj",
                    **base,
                )
            )
    return out


def _target_records(db: Session, user: User, q: TraceQuery, s: Series, scope: set[int]) -> list[Record]:
    """Lançamentos do orçamento proposto do ano do ciclo, já no recorte (módulo, pacote, conta, meses)."""
    try:
        ctx = opex_svc.context(db)
    except opex_svc.OpexError:
        return []
    if not scope:
        return []
    months = s.period.months
    modules = s.period.modules or set(cons.MODULES)
    accounts, by_code = _accounts(db)
    out: list[Record] = []
    if "OPEX" in modules:
        out += _opex_records(db, ctx, scope, months, accounts)
        out += _pj_records(db, ctx, user, scope, months, by_code)
    if "CAPEX" in modules:
        out += _capex_records(db, ctx, scope, months, accounts)
    if "PERSONNEL" in modules:
        out += _personnel_records(db, ctx, scope, months, by_code)
    pkg = q.package_id or q.parent_package_id
    acc_ids = {a for a in (q.account_id, q.parent_account_id) if a}
    keep = []
    for r in out:
        if pkg and r.package_id != pkg:
            continue
        if q.parent_no_package and r.package_id is not None:
            continue
        if acc_ids and r.account_id not in acc_ids:
            continue
        if r.total == 0:
            continue
        keep.append(r)
    return keep


def _sql_stmt(db: Session, user: User, q: TraceQuery, s: Series, f: Facts, search: str | None):
    model = s.model
    stmt = select(model).where(model.fiscal_year.in_(s.sql_years))
    stmt = f._filtered(model, stmt)
    if search and search.strip():
        like = f"%{search.strip()}%"
        conds = [
            model.cost_center_id.in_(
                select(CostCenter.id).where(or_(CostCenter.code.ilike(like), CostCenter.name.ilike(like)))
            ),
            model.account_id.in_(select(Account.id).where(or_(Account.code.ilike(like), Account.name.ilike(like)))),
        ]
        if model is ActualEntry:
            conds += [
                ActualEntry.text.ilike(like),
                ActualEntry.document_number.ilike(like),
                ActualEntry.vendor_name.ilike(like),
                ActualEntry.vendor_code.ilike(like),
            ]
        stmt = stmt.where(or_(*conds))
    return stmt


def _sql_records(db: Session, user: User, rows: list, model) -> list[Record]:
    version_ids = {r.dataset_version_id for r in rows}
    versions = {v.id: v for v in db.scalars(select(DatasetVersion).where(DatasetVersion.id.in_(version_ids or {-1})))}
    batch_ids = {v.import_batch_id for v in versions.values() if v.import_batch_id}
    batches = {b.id: b for b in db.scalars(select(ImportBatch).where(ImportBatch.id.in_(batch_ids or {-1})))}
    accounts, _ = _accounts(db)
    controller = is_global(user)
    out = []
    for r in rows:
        acc = accounts.get(r.account_id)
        v = versions.get(r.dataset_version_id)
        b = batches.get(v.import_batch_id) if v and v.import_batch_id else None
        values = [ZERO] * 12
        values[int(r.period) - 1] = r.amount or ZERO
        loaded = (b.completed_at or b.created_at) if b else (v.created_at if v else None)
        source = {
            "label": "Importação",
            "version_id": v.id if v else None,
            "version": v.version_number if v else None,
            "scope": v.scope_key if v else None,
            "batch_id": b.id if b else None,
            "file_name": b.file_name if b else None,
            "layout": b.layout if b else None,
            "loaded_at": loaded.isoformat() if loaded else None,
        }
        if model is ActualEntry:
            kind = "PROJECTION" if r.projected else "ACTUAL"
            title = r.text or r.document_number or (acc.name if acc else "Partida")
            detail = r.vendor_name
            fields = [
                ("Documento", r.document_number),
                ("Tipo de documento", r.document_type),
                ("Data de lançamento", r.posting_date.isoformat() if r.posting_date else None),
                ("Fornecedor", f"{r.vendor_code} · {r.vendor_name}" if r.vendor_code else r.vendor_name),
                ("Texto", r.text),
                ("Moeda", r.currency),
                ("Origem", r.source),
                ("Projeção do gestor", "Sim" if r.projected else None),
            ]
            search = " ".join(filter(None, [r.text, r.document_number, r.vendor_name]))
        else:
            kind = "REFERENCE"
            title = acc.name if acc else "Orçado de referência"
            detail = f"Cenário {r.scenario}"
            fields = [("Cenário", r.scenario)]
            search = title
        out.append(
            Record(
                kind,
                f"{kind.lower()}:{r.id}",
                cons.module_of_nature(acc.nature) if acc else "OPEX",
                r.cost_center_id,
                acc.code if acc else None,
                r.account_id,
                acc.package_id if acc else None,
                title,
                detail,
                values,
                month=int(r.period),
                fields=fields,
                source=source,
                link=f"/importacoes/{b.id}" if (b and controller) else None,
                search=search,
            )
        )
    return out


def _record_counts(db: Session, user: User, q: TraceQuery, s: Series, f: Facts) -> dict[int | None, int]:
    """Quantos lançamentos compõem cada conta do nível (para a coluna "lançamentos")."""
    counts: dict[int | None, int] = defaultdict(int)
    if s.sql_years:
        model = s.model
        stmt = select(model.account_id, func.count()).where(model.fiscal_year.in_(s.sql_years))
        stmt = f._filtered(model, stmt).group_by(model.account_id)
        for acc_id, n in db.execute(stmt):
            counts[acc_id] += n
    if s.from_target:
        for r in _target_records(db, user, q, s, scope_ccs(db, user, q)):
            counts[r.account_id] += 1
    return counts


def _matches(r: Record, needle: str) -> bool:
    return needle in r.search.lower() or needle in r.title.lower() or needle in (r.account_code or "")


def _serialize(db: Session, records: list[Record]) -> list[dict]:
    cc_ids = {r.cost_center_id for r in records if r.cost_center_id}
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(cc_ids or {-1})))}
    acc_ids = {r.account_id for r in records if r.account_id}
    accounts = {a.id: a for a in db.scalars(select(Account).where(Account.id.in_(acc_ids or {-1})))}
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}
    out = []
    for r in records:
        cc, acc = ccs.get(r.cost_center_id), accounts.get(r.account_id)
        out.append(
            {
                "id": r.id,
                "kind": r.kind,
                "kind_label": KIND_LABELS[r.kind],
                "module": r.module,
                "cost_center": {"id": cc.id, "code": cc.code, "name": cc.name} if cc else None,
                "account": {"id": acc.id, "code": acc.code, "name": acc.name}
                if acc
                else ({"id": None, "code": r.account_code, "name": "Conta não cadastrada"} if r.account_code else None),
                "package": packages.get(r.package_id),
                "title": r.title,
                "detail": r.detail,
                "month": r.month,
                "values": [_m(v) for v in r.values],
                "unscheduled": _m(r.unscheduled),
                "total": _m(r.total),
                "justification": r.justification,
                "fields": [[k, v] for k, v in r.fields if v],
                "source": r.source,
                "link": r.link,
            }
        )
    return out


def entries(
    db: Session, user: User, q: TraceQuery, search: str | None, offset: int, limit: int | None
) -> tuple[list[Record], int, Decimal, Series]:
    """Lançamentos do recorte ordenados pelo valor (maiores primeiro): (página, total de registros, soma, série)."""
    s = series_for(db, q)
    f = facts_for(db, user, q, s)
    records: list[Record] = []
    count, total = 0, ZERO
    if s.sql_years:
        stmt = _sql_stmt(db, user, q, s, f, search)
        sub = stmt.subquery()
        n, amount = db.execute(select(func.count(), func.coalesce(func.sum(sub.c.amount), 0)).select_from(sub)).one()
        count, total = int(n), Decimal(amount)
        if not s.from_target:
            page = stmt.order_by(s.model.amount.desc(), s.model.id).offset(offset)
            if limit:
                page = page.limit(limit)
            return _sql_records(db, user, list(db.scalars(page)), s.model), count, total, s
        records += _sql_records(db, user, list(db.scalars(stmt.order_by(s.model.id))), s.model)
    if s.from_target:
        target = _target_records(db, user, q, s, scope_ccs(db, user, q))
        if search and search.strip():
            needle = search.strip().lower()
            target = [r for r in target if _matches(r, needle)]
        records += target
        count += len(target)
        total += sum((r.total for r in target), ZERO)
    records.sort(key=lambda r: (-r.total, r.id))
    page = records[offset:] if limit is None else records[offset : offset + limit]
    return page, count, total, s


def entries_out(db: Session, user: User, q: TraceQuery, search: str | None, offset: int, limit: int) -> dict:
    page, count, total, s = entries(db, user, q, search, offset, limit)
    f = facts_for(db, user, q, s)
    level_total = f.total(s.model, s.years)
    return {
        "items": _serialize(db, page),
        "count": count,
        "total": _m(total),
        "level_total": _m(level_total),
        "difference": _m(level_total - total),  # arredondamento do rateio de pessoal (centavos) ou busca ativa
        "offset": offset,
        "limit": limit,
        "series": "actual" if s.model is ActualEntry else "budget",
        "series_label": s.label,
        "period": s.period.summary(),
        "available_years": s.period.available,
        "trail": trail(db, q),
    }


MONTH_HEADERS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def export_xlsx(db: Session, user: User, q: TraceQuery, search: str | None, cap: int = 5000) -> bytes:
    page, _count, _total, s = entries(db, user, q, search, 0, cap)
    items = _serialize(db, page)
    headers = [
        "Tipo",
        "Centro de custo",
        "Conta",
        "Pacote",
        "Lançamento",
        "Detalhe",
        "Mês",
        *MONTH_HEADERS,
        "Sem cronograma",
        "Total",
        "Justificativa",
        "Origem",
        "Arquivo",
        "Carregado em",
        "Detalhes",
    ]
    rows = []
    for i in items:
        src = i["source"] or {}
        rows.append(
            [
                i["kind_label"],
                f"{i['cost_center']['code']} · {i['cost_center']['name']}" if i["cost_center"] else "",
                f"{i['account']['code']} {i['account']['name']}" if i["account"] else "",
                i["package"] or "",
                i["title"],
                i["detail"] or "",
                i["month"] or "",
                *[float(v) for v in i["values"]],
                float(i["unscheduled"]),
                float(i["total"]),
                i["justification"] or "",
                src.get("label") or "",
                src.get("file_name") or "",
                src.get("loaded_at") or "",
                "; ".join(f"{k}: {v}" for k, v in i["fields"]),
            ]
        )
    wb = Workbook()
    wb.remove(wb.active)
    money_cols = set(range(8, 22))
    _sheet(wb, "Rastro", headers, rows, money_cols=money_cols, total=True)
    info = wb.create_sheet("Recorte")
    info.append(["Série", s.label])
    for crumb in trail(db, q):
        info.append([crumb["label"], f"{crumb['code'] or ''} {crumb['name']}".strip()])
    info.append(["Registros", len(rows)])
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()
