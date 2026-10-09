"""Consolidação do orçamento: OPEX + CAPEX + Pessoal numa base única (chave empresa-filial-CC-conta × mês),
fotografia ao congelar a versão, revisão (nova versão) e variações contra o realizado.

Pessoal vira contas por CC (parâmetros do ciclo): salário com reajuste, encargos e benefícios (parte do
multiplicador do contrato, rateada entre contas por `personnel.charges_split`; sem rateio, tudo na conta de
encargos) e verbas rescisórias.
"""

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.domain.rules.personnel import normalize_split, split_amount
from app.models import (
    Account,
    AccountJustification,
    ActualEntry,
    Area,
    Branch,
    BudgetLine,
    BudgetLineValue,
    BudgetPackage,
    BudgetSnapshotLine,
    BudgetSubmission,
    BudgetVersion,
    CapexItem,
    CapexItemValue,
    CapexProject,
    Company,
    CostCenter,
    DatasetVersion,
    PackageReview,
    PersonnelMovement,
    PersonnelScenario,
    ReferenceBudgetEntry,
    WorkflowEvent,
)
from app.services import personnel as personnel_svc
from app.services.opex import Context, OpexError, closed_period

ZERO = Decimal("0")
MODULES = ("OPEX", "CAPEX", "PERSONNEL")
MODULE_LABELS = {"OPEX": "OPEX", "CAPEX": "CAPEX", "PERSONNEL": "Pessoal"}
MODULE_NATURES = {"OPEX": ("OPEX", "FINANCEIRO"), "CAPEX": ("CAPEX",), "PERSONNEL": ("PESSOAL",)}
PERSONNEL_ACCOUNTS = (
    ("salary", "personnel.salary_account", 6010101001, "Salários e ordenados"),
    ("charges", "personnel.charges_account", 6010102001, "Encargos e benefícios (multiplicador)"),
    ("severance", "personnel.severance_account", 6010101010, "Verbas rescisórias"),
    ("bonus", "personnel.annual_bonus_account", 6010101009, "Gratificações/Premiações (abono anual CLT)"),
    ("cc_bonus", "personnel.bonus_account", 6010101016, "Prov. Gratificações/Premiações (bônus CLT)"),
)
CHARGES_SPLIT_KEY = "personnel.charges_split"


class ConsolidationError(OpexError):
    """Erro de regra de negócio da consolidação."""


@dataclass
class Row:
    module: str
    company_code: str
    branch_code: str | None
    cost_center_id: int | None
    cost_center_code: str
    cost_center_name: str | None
    account_code: str
    account_name: str | None
    package: str | None
    values: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)
    # CAPEX com valor total mas sem distribuição mensal: é orçamento (conta no total do ano), só não tem mês
    unscheduled: Decimal = ZERO

    @property
    def total(self) -> Decimal:
        return money(sum(self.values, ZERO) + self.unscheduled)

    @property
    def key(self) -> str:
        """Chave orçamentária do template: empresa-filial-CC-conta."""
        return f"{self.company_code}-{self.branch_code or ''}-{self.cost_center_code}-{self.account_code}"


def version_context(ctx: Context, version: BudgetVersion) -> Context:
    return dataclasses.replace(ctx, version=version)


def personnel_accounts(db: Session, ctx: Context) -> dict[str, tuple[str, str]]:
    """Contas de pessoal configuradas no ciclo → (código, nome)."""
    out = {}
    for kind, key, default, label in PERSONNEL_ACCOUNTS:
        raw = ctx.params.get(key, default)
        code = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
        acc = db.scalar(select(Account).where(Account.code == code))
        out[kind] = (code, acc.name if acc else label)
    return out


def charges_split(db: Session, ctx: Context) -> list[tuple[str, str, Decimal]]:
    """Rateio da parte do multiplicador (encargos e benefícios) entre contas → [(código, nome, peso normalizado)].
    Parâmetro `personnel.charges_split` ausente, vazio ou sem peso válido: tudo em `personnel.charges_account`."""
    split = normalize_split(ctx.params.get(CHARGES_SPLIT_KEY))
    if not split:
        code, name = personnel_accounts(db, ctx)["charges"]
        return [(code, name, Decimal(1))]
    names = dict(db.execute(select(Account.code, Account.name).where(Account.code.in_([c for c, _ in split]))).all())
    return [(code, names.get(code, "Encargos e benefícios (rateio do multiplicador)"), w) for code, w in split]


def _dims(db: Session):
    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    companies = {c.id: c.code for c in db.scalars(select(Company))}
    branches = {b.id: b.code for b in db.scalars(select(Branch))}
    accounts = {a.id: a for a in db.scalars(select(Account))}
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}
    return ccs, companies, branches, accounts, packages


def pj_budget_amounts(db: Session, ctx: Context, cc_ids, accounts, packages):
    """Contratos PJ ativos no orçamento do ano do ciclo (09/10/2026): valor mensal nos meses de vigência, com o
    reajuste `pj.adjustment_pct` a partir de `pj.adjustment_month`, mais a bonificação anual CHEIA diluída nos meses de
    vigência (o que se provisiona no ano é pago no seguinte). Tudo na conta `pj.budget_account`, somado por CC e mês —
    sem identificar o contrato (são confidenciais). Contrato sem CC ou sem valor fica de fora (pendência no PJ)."""
    from app.models.pj import PjContract

    year = ctx.target_year
    raw = ctx.params.get("pj.budget_account", 6010201016)
    code = str(int(raw)) if isinstance(raw, (int, float)) else str(raw).strip()
    account = next((a for a in accounts if a.code == code), None)
    name = account.name if account else "Provisão de Serviços"
    package = packages.get(account.package_id) if account else None
    try:
        adj = Decimal(str(ctx.params.get("pj.adjustment_pct", 0) or 0))
        adj_month = int(ctx.params.get("pj.adjustment_month", 1) or 1)
    except (ArithmeticError, ValueError):
        adj, adj_month = ZERO, 1
    stmt = select(PjContract).where(PjContract.archived_at.is_(None), PjContract.cost_center_id.is_not(None))
    if cc_ids is not None:
        stmt = stmt.where(PjContract.cost_center_id.in_(cc_ids or {-1}))
    totals: dict[tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    for c in db.scalars(stmt):
        if c.start_date is not None and c.start_date.year > year:
            continue
        if c.end_date is not None and c.end_date.year < year:
            continue
        first = 1 if c.start_date is None or c.start_date.year < year else c.start_date.month
        last = c.end_date.month if c.end_date is not None and c.end_date.year == year else 12
        months = [m for m in range(1, 13) if first <= m <= last]
        if not months:
            continue
        monthly = Decimal(c.monthly_value or 0)
        bonus = Decimal(c.annual_bonus or 0)
        paid = ZERO
        for i, m in enumerate(months, start=1):
            value = monthly * (1 + adj) if (adj and m >= adj_month) else monthly
            due = money(bonus * i / len(months))  # bonificação cheia, parcelas pelo acumulado (soma exata)
            totals[(c.cost_center_id, m)] += money(value) + (due - paid)
            paid = due
    for (cc_id, month), amount in totals.items():
        if amount:
            yield cc_id, code, name, package, month, amount


def live_rows(db: Session, ctx: Context, cc_ids: set[int] | None = None) -> list[Row]:
    """Orçamento da versão do contexto, calculado a partir das linhas, itens e quadro."""
    ccs, companies, branches, accounts, packages = _dims(db)
    grouped: dict[tuple, Row] = {}

    def add(module, company_id, branch_id, cc_id, account_code, account_name, package, month, amount):
        cc = ccs.get(cc_id)
        if cc is None:
            return
        k = (module, company_id, branch_id, cc_id, account_code)
        row = grouped.get(k)
        if row is None:
            row = grouped[k] = Row(
                module,
                companies.get(company_id or cc.company_id, ""),
                branches.get(branch_id) if branch_id else None,
                cc_id,
                cc.code,
                cc.name,
                account_code,
                account_name,
                package,
            )
        if month is None:
            row.unscheduled += amount or ZERO
        else:
            row.values[month - 1] += amount or ZERO

    sub_filter = [BudgetSubmission.version_id == ctx.version.id]
    if cc_ids is not None:
        sub_filter.append(BudgetSubmission.cost_center_id.in_(cc_ids or {-1}))

    opex = db.execute(
        select(
            BudgetLine.company_id,
            BudgetLine.branch_id,
            BudgetLine.cost_center_id,
            BudgetLine.account_id,
            BudgetLine.package_id,
            BudgetLineValue.month,
            func.sum(BudgetLineValue.amount),
        )
        .join(BudgetLineValue, BudgetLineValue.line_id == BudgetLine.id)
        .join(BudgetSubmission, BudgetSubmission.id == BudgetLine.submission_id)
        .where(BudgetSubmission.module == "OPEX", *sub_filter)
        .group_by(
            BudgetLine.company_id,
            BudgetLine.branch_id,
            BudgetLine.cost_center_id,
            BudgetLine.account_id,
            BudgetLine.package_id,
            BudgetLineValue.month,
        )
    )
    for company_id, branch_id, cc_id, acc_id, pkg_id, month, amount in opex:
        acc = accounts.get(acc_id)
        # linha importada antes de a conta ter pacote: vale o pacote atual da conta
        package = packages.get(pkg_id) or packages.get(acc.package_id)
        add("OPEX", company_id, branch_id, cc_id, acc.code, acc.name, package, month, amount)

    capex = db.execute(
        select(
            CapexProject.company_id,
            CapexProject.branch_id,
            CapexProject.cost_center_id,
            CapexItem.account_id,
            CapexItemValue.month,
            func.sum(CapexItemValue.amount),
        )
        .join(CapexItem, CapexItem.project_id == CapexProject.id)
        .join(CapexItemValue, CapexItemValue.item_id == CapexItem.id)
        .join(BudgetSubmission, BudgetSubmission.id == CapexProject.submission_id)
        .where(BudgetSubmission.module == "CAPEX", *sub_filter)
        .group_by(
            CapexProject.company_id,
            CapexProject.branch_id,
            CapexProject.cost_center_id,
            CapexItem.account_id,
            CapexItemValue.month,
        )
    )
    for company_id, branch_id, cc_id, acc_id, month, amount in capex:
        acc = accounts.get(acc_id)
        add("CAPEX", company_id, branch_id, cc_id, acc.code, acc.name, "Capex", month, amount)
    # item com valor total maior que o cronograma (ex.: template sem os meses): a diferença é orçamento sem mês
    items = db.execute(
        select(
            CapexProject.company_id,
            CapexProject.branch_id,
            CapexProject.cost_center_id,
            CapexItem.account_id,
            CapexItem.total_value,
            func.coalesce(func.sum(CapexItemValue.amount), 0),
        )
        .select_from(CapexItem)
        .join(CapexProject, CapexProject.id == CapexItem.project_id)
        .join(BudgetSubmission, BudgetSubmission.id == CapexProject.submission_id)
        .outerjoin(CapexItemValue, CapexItemValue.item_id == CapexItem.id)
        .where(BudgetSubmission.module == "CAPEX", *sub_filter)
        .group_by(
            CapexItem.id,
            CapexProject.company_id,
            CapexProject.branch_id,
            CapexProject.cost_center_id,
            CapexItem.account_id,
            CapexItem.total_value,
        )
    )
    for company_id, branch_id, cc_id, acc_id, total_value, scheduled in items:
        pending = (total_value or ZERO) - (scheduled or ZERO)
        if pending > 0:
            acc = accounts.get(acc_id)
            add("CAPEX", company_id, branch_id, cc_id, acc.code, acc.name, "Capex", None, pending)

    # contratos PJ (confidenciais): só o total por CC × mês na conta pj.budget_account
    for cc_id, code, name, package, month, amount in pj_budget_amounts(db, ctx, cc_ids, accounts.values(), packages):
        add("OPEX", None, None, cc_id, code, name, package, month, amount)

    for cc_id, _kind, code, name, m, amount in personnel_amounts(db, ctx, cc_ids):
        add("PERSONNEL", None, None, cc_id, code, name, "Pessoas", m, amount)
    rows = [r for r in grouped.values() if any(r.values) or r.unscheduled]
    for r in rows:
        r.values = [money(v) for v in r.values]
        r.unscheduled = money(r.unscheduled)
    return sorted(rows, key=lambda r: (r.company_code, r.cost_center_code, r.module, r.account_code))


def personnel_amounts(
    db: Session,
    ctx: Context,
    cc_ids: set[int] | None = None,
    *,
    scenario: personnel_svc.ScenarioInfo | None = None,
    positions: dict[int, list[personnel_svc.Position]] | None = None,
    include_severance: bool = True,
):
    """Valores de pessoal por CC, componente, conta e mês (a parte de pessoal de `live_rows`):
    gera (cc_id, componente, conta, nome da conta, mês, valor), componente em salary | severance | bonus |
    cc_bonus | charges (parte do multiplicador, rateada por `personnel.charges_split`).

    Abono, bônus por CC e rateio vêm de `ctx.params`; o what-if passa um contexto com parâmetros simulados, outro
    cenário (dissídio/multiplicadores), as posições já montadas (cache por request) e se entram as rescisórias."""
    pacc = personnel_accounts(db, ctx)
    split = charges_split(db, ctx)
    split_names = {code: name for code, name, _w in split}
    split_weights = [(code, w) for code, _name, w in split]
    scenario = scenario or personnel_svc.baseline(db, ctx)
    by_cc = dict(positions) if positions is not None else personnel_svc.build_positions(db, ctx, cc_ids)
    if not include_severance:
        by_cc = {
            cc: [dataclasses.replace(p, severance=ZERO) if p.severance else p for p in items]
            for cc, items in by_cc.items()
        }
    # bônus CLT por CC (parâmetro): entra mesmo em CC sem quadro carregado
    # (um código em duas empresas fica com um só CC: personnel.bonus_owners)
    owners = personnel_svc.bonus_owners(db, ctx)
    for cc_id in owners:
        if cc_ids is None or cc_id in cc_ids:
            by_cc.setdefault(cc_id, [])
    for cc_id, positions in by_cc.items():
        t = personnel_svc.add_cc_bonus(
            personnel_svc.totals_for(positions, scenario), personnel_svc.cc_bonus_for(db, ctx, cc_id, owners)
        )
        for kind, series in (
            ("salary", t.salary),
            ("severance", t.severance),
            ("bonus", t.bonus),
            ("cc_bonus", t.cc_bonus),
        ):
            code, name = pacc[kind]
            for m, amount in enumerate(series, start=1):
                if amount:
                    yield cc_id, kind, code, name, m, money(amount)
        # parte do multiplicador, mês a mês, rateada entre as contas (soma exata em centavos)
        for m in range(1, 13):
            charges = t.monthly[m - 1] - t.salary[m - 1] - t.severance[m - 1] - t.bonus[m - 1] - t.cc_bonus[m - 1]
            if not charges:
                continue
            for code, amount in split_amount(charges, split_weights).items():
                if amount:
                    yield cc_id, "charges", code, split_names[code], m, amount


# ------------------------------------------------------------------ premissas de pessoal (rateio × abono/bônus)

OVERLAP_LABELS = {"bonus": "abono anual do CLT", "cc_bonus": "bônus CLT por CC"}


def _positive(raw) -> Decimal:
    try:
        value = Decimal(str(raw or 0))
    except ArithmeticError:
        return ZERO
    return value if value.is_finite() and value > 0 else ZERO


def split_overlap(db: Session, ctx: Context) -> list[dict]:
    """Contas do rateio de encargos (`personnel.charges_split`, peso > 0) que já recebem o abono anual do CLT
    (`personnel.annual_bonus_account`, com `annual_bonus_clt` > 0) ou o bônus CLT por CC (`personnel.bonus_account`,
    com algum valor em `bonus_by_cc`): o rateio manda parte do multiplicador para elas e conta em dobro.
    → [{code, name, kind, label, share}] (sem cálculo do quadro; barato)."""
    split = dict(normalize_split(ctx.params.get(CHARGES_SPLIT_KEY)))
    if not split:
        return []
    pacc = personnel_accounts(db, ctx)
    bonus_by_cc = ctx.params.get("personnel.bonus_by_cc") or {}
    active = {
        "bonus": _positive(ctx.params.get("personnel.annual_bonus_clt", 0)) > 0,
        "cc_bonus": isinstance(bonus_by_cc, dict) and any(_positive(v) > 0 for v in bonus_by_cc.values()),
    }
    out = []
    for kind in ("bonus", "cc_bonus"):
        code, name = pacc[kind]
        if active[kind] and code in split:
            out.append({"code": code, "name": name, "kind": kind, "label": OVERLAP_LABELS[kind], "share": split[code]})
    return out


def overlap_amounts(db: Session, ctx: Context, overlap: list[dict]) -> dict[str, Decimal]:
    """R$ do ano que o rateio do multiplicador manda para as contas sobrepostas (por conta)."""
    codes = {o["code"] for o in overlap}
    out: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for _cc, kind, code, _name, _m, amount in personnel_amounts(db, ctx):
        if kind == "charges" and code in codes:
            out[code] += amount
    return {c: money(out[c]) for c in codes}


@dataclass
class PersonnelBreakdown:
    """Pessoal do ano agregado a partir de `personnel_amounts` (mesmo caminho da consolidação)."""

    by_kind: dict[str, Decimal] = field(default_factory=lambda: defaultdict(lambda: ZERO))
    # conta → {code, name, total, components: {componente: valor}}
    by_account: dict[str, dict] = field(default_factory=dict)
    # CC → {total, components}
    by_cc: dict[int, dict] = field(default_factory=dict)
    monthly: list[Decimal] = field(default_factory=lambda: [ZERO] * 12)

    @property
    def total(self) -> Decimal:
        return money(sum(self.monthly, ZERO))


def personnel_breakdown(
    db: Session,
    ctx: Context,
    *,
    scenario: personnel_svc.ScenarioInfo | None = None,
    positions: dict[int, list[personnel_svc.Position]] | None = None,
    include_severance: bool = True,
) -> PersonnelBreakdown:
    """Totais de pessoal por componente, conta, CC e mês. Sem argumentos é exatamente o que a consolidação mostra."""
    out = PersonnelBreakdown()
    for cc_id, kind, code, name, m, amount in personnel_amounts(
        db, ctx, scenario=scenario, positions=positions, include_severance=include_severance
    ):
        out.by_kind[kind] += amount
        acc = out.by_account.setdefault(
            code, {"code": code, "name": name, "total": ZERO, "components": defaultdict(lambda: ZERO)}
        )
        acc["total"] += amount
        acc["components"][kind] += amount
        cc = out.by_cc.setdefault(cc_id, {"total": ZERO, "components": defaultdict(lambda: ZERO)})
        cc["total"] += amount
        cc["components"][kind] += amount
        out.monthly[m - 1] += amount
    return out


def personnel_premises(db: Session, ctx: Context) -> dict:
    """Premissas do custo de pessoal do ciclo (página "Premissas de pessoal"): dissídio e multiplicadores do cenário
    base, abono anual do CLT, bônus por CC, rateio da parte do multiplicador com o R$ do ano por conta, total de
    pessoal por conta e as contas do rateio que já recebem abono/bônus explícito (contagem em dobro)."""
    scenario = personnel_svc.baseline(db, ctx)
    contracts = personnel_svc.contract_types(db)
    pacc = personnel_accounts(db, ctx)
    breakdown = personnel_breakdown(db, ctx, scenario=scenario)
    by_kind = breakdown.by_kind
    by_account = breakdown.by_account
    charges_by_account: dict[str, Decimal] = defaultdict(
        lambda: ZERO, {code: a["components"].get("charges", ZERO) for code, a in by_account.items()}
    )
    bonus_by_cc_id: dict[int, Decimal] = defaultdict(
        lambda: ZERO, {cc: c["components"].get("cc_bonus", ZERO) for cc, c in breakdown.by_cc.items()}
    )
    if ctx.version.status == "FROZEN":  # versão congelada: total por conta da fotografia
        frozen: dict[str, dict] = {}
        for r in snapshot_rows(db, ctx.version):
            if r.module != "PERSONNEL":
                continue
            acc = frozen.setdefault(
                r.account_code, {"code": r.account_code, "name": r.account_name, "total": ZERO, "components": {}}
            )
            acc["total"] += r.total
        by_account = frozen
    accounts = sorted(by_account.values(), key=lambda a: (-a["total"], a["code"]))
    total = money(sum((a["total"] for a in accounts), ZERO))

    overlap = split_overlap(db, ctx)
    overlap_codes = {o["code"] for o in overlap}
    raw_split = ctx.params.get(CHARGES_SPLIT_KEY)
    raw_weights = {str(k).strip(): v for k, v in raw_split.items()} if isinstance(raw_split, dict) else {}
    split_rows = [
        {
            "code": code,
            "name": name,
            "weight": str(raw_weights.get(code, "")),
            "share": str(share),
            "amount": str(money(charges_by_account[code])),
            "overlap": code in overlap_codes,
        }
        for code, name, share in charges_split(db, ctx)
    ]
    overlap_out = [
        {
            "code": o["code"],
            "name": o["name"],
            "kind": o["kind"],
            "label": o["label"],
            "share": str(o["share"]),
            "amount": str(money(charges_by_account[o["code"]])),
        }
        for o in overlap
    ]

    raw_cc = ctx.params.get("personnel.bonus_by_cc") or {}
    raw_cc = raw_cc if isinstance(raw_cc, dict) else {}
    owner_ids = personnel_svc.bonus_owners(db, ctx)  # o CC que de fato recebe cada código
    ccs = {c.code: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(set(owner_ids) or {-1})))}
    bonus_rows = []
    for cc_code, value in sorted(raw_cc.items()):
        cc = ccs.get(str(cc_code))
        bonus_rows.append(
            {
                "cost_center_id": cc.id if cc else None,
                "code": str(cc_code),
                "name": cc.name if cc else None,
                "annual": str(money(_positive(value))),
                "booked": str(money(bonus_by_cc_id[cc.id])) if cc else "0.00",
            }
        )

    def acc_out(kind: str) -> dict:
        return {"code": pacc[kind][0], "name": pacc[kind][1]}

    return {
        "cycle_id": ctx.cycle.id,
        "cycle_status": ctx.cycle.status,
        "target_year": ctx.target_year,
        "version": ctx.version.label,
        "frozen": ctx.frozen,
        "scenario": {
            "name": scenario.name,
            "salary_adjustment_pct": str(scenario.salary_adjustment_pct),
            "adjustment_month": scenario.adjustment_month,
        },
        "multipliers": [
            {
                "code": code,
                "name": c.name,
                "multiplier": str(scenario.multipliers.get(code, Decimal(c.default_multiplier))),
                "apply_multiplier": bool(c.apply_multiplier),
                "is_active": bool(c.is_active),
            }
            for code, c in sorted(contracts.items())
        ],
        "salary": {"account": acc_out("salary"), "total": str(money(by_kind["salary"]))},
        "severance": {"account": acc_out("severance"), "total": str(money(by_kind["severance"]))},
        "abono": {
            "value": str(money(scenario.annual_bonus)),
            "contracts": list(scenario.bonus_contracts),
            "account": acc_out("bonus"),
            "total": str(money(by_kind["bonus"])),
        },
        "bonus_by_cc": {
            "rows": bonus_rows,
            "total": str(money(sum((_positive(v) for v in raw_cc.values()), ZERO))),
            "booked": str(money(by_kind["cc_bonus"])),
            "account": acc_out("cc_bonus"),
        },
        "charges": {
            "default_account": acc_out("charges"),
            "has_split": bool(normalize_split(raw_split)),
            "total": str(money(by_kind["charges"])),
            "rows": split_rows,
        },
        "accounts": [
            {
                "code": a["code"],
                "name": a["name"],
                "total": str(money(a["total"])),
                "components": {k: str(money(v)) for k, v in a["components"].items()},
            }
            for a in accounts
        ],
        "total": str(total),
        "overlap": overlap_out,
        "overlap_amount": str(money(sum((charges_by_account[c] for c in overlap_codes), ZERO))),
    }


def split_without_overlap(db: Session, ctx: Context) -> tuple[dict, dict, list[dict]]:
    """Rateio sem as contas sobrepostas → (rateio atual, novo rateio, sobreposição). Os pesos restantes ficam como
    estão: `normalize_split` renormaliza no cálculo, então o total de encargos não muda."""
    overlap = split_overlap(db, ctx)
    codes = {o["code"] for o in overlap}
    raw = ctx.params.get(CHARGES_SPLIT_KEY)
    current = dict(raw) if isinstance(raw, dict) else {}
    return current, {k: v for k, v in current.items() if str(k).strip() not in codes}, overlap


# ------------------------------------------------------------------ what-if das premissas (nada é gravado)

WHAT_IF_TOP_CC = 15
WHAT_IF_SUMMARY = (
    ("total", "Total de pessoal"),
    ("salary", "Salários (com dissídio)"),
    ("charges", "Parte do multiplicador"),
    ("bonus", "Abono anual do CLT"),
    ("cc_bonus", "Bônus CLT por CC"),
    ("severance", "Verbas rescisórias"),
)


@dataclass
class PremisesWhatIf:
    """Premissas simuladas; None/False/0 = como está no ciclo."""

    salary_adjustment_pct: Decimal | None = None  # fração (0,05 = 5%)
    adjustment_month: int | None = None
    multipliers: dict[str, Decimal] = field(default_factory=dict)
    annual_bonus: Decimal | None = None  # abono anual do CLT, R$ por pessoa no ano
    cc_bonus_pct: Decimal = ZERO  # ajuste sobre o bônus por CC do parâmetro (fração: 0,05 = +5%)
    include_cc_bonus: bool = True
    remove_overlap: bool = False  # simula "Retirar do rateio"
    include_severance: bool = True


def what_if_context(db: Session, ctx: Context, w: PremisesWhatIf) -> tuple[Context, list[str]]:
    """Contexto com os parâmetros simulados (abono, bônus por CC, rateio) → (contexto, contas retiradas do rateio).
    Cópia de `ctx.params`: nada é gravado."""
    params = dict(ctx.params)
    if w.annual_bonus is not None:
        params["personnel.annual_bonus_clt"] = str(money(w.annual_bonus))
    raw_cc = params.get("personnel.bonus_by_cc") or {}
    raw_cc = raw_cc if isinstance(raw_cc, dict) else {}
    if not w.include_cc_bonus:
        params["personnel.bonus_by_cc"] = {}
    elif w.cc_bonus_pct:
        params["personnel.bonus_by_cc"] = {
            k: str(money(_positive(v) * (1 + w.cc_bonus_pct))) for k, v in raw_cc.items()
        }
    sim = dataclasses.replace(ctx, params=params)
    removed: list[str] = []
    if w.remove_overlap:
        # as contas sobrepostas hoje e as que a simulação sobrepõe (ex.: abono que passa a existir)
        _current, new_split, overlap = split_without_overlap(db, ctx)
        codes = {o["code"] for o in overlap} | {o["code"] for o in split_overlap(db, sim)}
        new_split = {k: v for k, v in new_split.items() if str(k).strip() not in codes}
        removed = sorted(codes & {str(k).strip() for k in (_current or {})})
        params[CHARGES_SPLIT_KEY] = new_split
    return sim, removed


def _cmp(current: Decimal, simulated: Decimal) -> dict:
    current, simulated = money(current), money(simulated)
    diff = simulated - current
    return {
        "current": str(current),
        "simulated": str(simulated),
        "difference": str(diff),
        "difference_pct": str((diff / abs(current)).quantize(Decimal("0.0001"))) if current else None,
    }


def _ranked(rows: list[dict]) -> list[dict]:
    """Maior diferença primeiro (em módulo); empate (ex.: sem simulação) pelo maior valor atual."""
    return sorted(rows, key=lambda r: (-abs(Decimal(r["difference"])), -Decimal(r["current"]), str(r.get("label"))))


def _top(rows: list[dict], n: int, cur: Decimal, sim: Decimal) -> dict:
    top, rest = rows[:n], rows[n:]
    others = None
    if rest:
        others = {
            "label": f"Demais ({len(rest)})",
            **_cmp(
                sum((Decimal(r["current"]) for r in rest), ZERO), sum((Decimal(r["simulated"]) for r in rest), ZERO)
            ),
        }
    return {"rows": top, "others": others, "count": len(rows), "total": {"label": "Total", **_cmp(cur, sim)}}


def premises_what_if(db: Session, ctx: Context, w: PremisesWhatIf) -> dict:
    """Atual × simulado do custo de pessoal do ano (página Premissas de pessoal, Controladoria: todos os CCs). Os dois
    lados passam por `personnel_amounts` — o atual é exatamente o da consolidação; o quadro é montado uma vez só
    (cache do request). Só leitura: parâmetros e cenário simulados vivem em memória."""
    base = personnel_svc.baseline(db, ctx)
    row = db.get(PersonnelScenario, base.id) if base.id else None
    sim_ctx, removed = what_if_context(db, ctx, w)
    sim = personnel_svc.with_cycle_bonus(
        personnel_svc.scenario_info(
            db,
            row,
            multipliers=w.multipliers,
            salary_adjustment_pct=w.salary_adjustment_pct,
            adjustment_month=w.adjustment_month,
            name="Simulação",
        ),
        sim_ctx,
    )
    positions = personnel_svc.build_positions(db, ctx)
    cur = personnel_breakdown(db, ctx, scenario=base, positions=positions)
    simb = personnel_breakdown(db, sim_ctx, scenario=sim, positions=positions, include_severance=w.include_severance)

    summary = []
    for key, label in WHAT_IF_SUMMARY:
        a = cur.total if key == "total" else cur.by_kind.get(key, ZERO)
        b = simb.total if key == "total" else simb.by_kind.get(key, ZERO)
        summary.append({"key": key, "label": f"{label} {ctx.target_year}" if key == "total" else label, **_cmp(a, b)})

    accounts = []
    for code in set(cur.by_account) | set(simb.by_account):
        a, b = cur.by_account.get(code), simb.by_account.get(code)
        accounts.append(
            {
                "code": code,
                "label": (a or b)["name"],
                **_cmp(a["total"] if a else ZERO, b["total"] if b else ZERO),
            }
        )
    accounts.sort(key=lambda r: (-max(Decimal(r["current"]), Decimal(r["simulated"])), r["code"]))

    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(set(cur.by_cc) | set(simb.by_cc))))}
    areas = {a.id: a.name for a in db.scalars(select(Area))}
    cc_rows = []
    by_area: dict[str, list[Decimal]] = defaultdict(lambda: [ZERO, ZERO])
    for cc_id in set(cur.by_cc) | set(simb.by_cc):
        a = cur.by_cc.get(cc_id, {}).get("total", ZERO)
        b = simb.by_cc.get(cc_id, {}).get("total", ZERO)
        if not a and not b:
            continue
        cc = ccs.get(cc_id)
        cc_rows.append(
            {"cost_center_id": cc_id, "code": cc.code if cc else None, "label": cc.name if cc else "—", **_cmp(a, b)}
        )
        area = areas.get(cc.area_id) if cc and cc.area_id else None
        by_area[area or "Sem área"][0] += a
        by_area[area or "Sem área"][1] += b
    area_rows = [{"label": name, **_cmp(v[0], v[1])} for name, v in by_area.items()]

    monthly = [{"month": m, **_cmp(cur.monthly[m - 1], simb.monthly[m - 1])} for m in range(1, 13)]
    return {
        "target_year": ctx.target_year,
        "version": ctx.version.label,
        "frozen": ctx.frozen,
        "current_premises": {
            "salary_adjustment_pct": str(base.salary_adjustment_pct),
            "adjustment_month": base.adjustment_month,
            "multipliers": {k: str(v) for k, v in base.multipliers.items()},
            "annual_bonus": str(money(base.annual_bonus)),
        },
        "simulated_premises": {
            "salary_adjustment_pct": str(sim.salary_adjustment_pct),
            "adjustment_month": sim.adjustment_month,
            "multipliers": {k: str(v) for k, v in sim.multipliers.items()},
            "annual_bonus": str(money(sim.annual_bonus)),
            "cc_bonus_pct": str(w.cc_bonus_pct),
            "include_cc_bonus": w.include_cc_bonus,
            "remove_overlap": w.remove_overlap,
            "include_severance": w.include_severance,
        },
        "removed_from_split": removed,
        "summary": summary,
        "accounts": accounts,
        "cost_centers": _top(_ranked(cc_rows), WHAT_IF_TOP_CC, cur.total, simb.total),
        "areas": _top(_ranked(area_rows), 50, cur.total, simb.total),
        "monthly": monthly,
    }


def snapshot_rows(db: Session, version: BudgetVersion, cc_ids: set[int] | None = None) -> list[Row]:
    stmt = select(BudgetSnapshotLine).where(BudgetSnapshotLine.version_id == version.id)
    if cc_ids is not None:
        stmt = stmt.where(BudgetSnapshotLine.cost_center_id.in_(cc_ids or {-1}))
    return [
        Row(
            s.module,
            s.company_code,
            s.branch_code,
            s.cost_center_id,
            s.cost_center_code,
            s.cost_center_name,
            s.account_code,
            s.account_name,
            s.package,
            [Decimal(v) for v in s.values[:12]],
            Decimal(s.values[12]) if len(s.values) > 12 else ZERO,  # 13º valor: parcela sem mês
        )
        for s in db.scalars(stmt.order_by(BudgetSnapshotLine.id))
    ]


def rows_for(db: Session, ctx: Context, version: BudgetVersion, cc_ids: set[int] | None = None) -> list[Row]:
    """Versão congelada lê a fotografia; versão em elaboração é calculada na hora."""
    if version.status == "FROZEN":
        return snapshot_rows(db, version, cc_ids)
    return live_rows(db, version_context(ctx, version), cc_ids)


# ------------------------------------------------------------------ versões


def freeze(db: Session, ctx: Context, user_id: int | None, reason: str | None) -> dict:
    version = ctx.version
    if version.status != "WORKING":
        raise ConsolidationError(f"A versão {version.label} já está congelada")
    rows = live_rows(db, ctx)
    for r in rows:
        db.add(
            BudgetSnapshotLine(
                version_id=version.id,
                module=r.module,
                company_code=r.company_code,
                branch_code=r.branch_code,
                cost_center_id=r.cost_center_id,
                cost_center_code=r.cost_center_code,
                cost_center_name=r.cost_center_name,
                account_code=r.account_code,
                account_name=r.account_name,
                package=r.package,
                values=[str(v) for v in r.values] + [str(r.unscheduled)],
                total=r.total,
            )
        )
    version.status = "FROZEN"
    version.frozen_at = datetime.utcnow()
    if reason:
        version.reason = reason
    db.flush()
    return {"version": version.label, "lines": len(rows), "total": str(money(sum((r.total for r in rows), ZERO)))}


def _copy(obj, **overrides):
    """Cópia rasa de uma linha ORM (sem id), com campos substituídos."""
    mapper = obj.__mapper__
    data = {
        c.key: getattr(obj, c.key)
        for c in mapper.column_attrs
        if c.key not in ("id", "created_at", "updated_at") and c.key not in overrides
    }
    return type(obj)(**data, **overrides)


def revise(db: Session, ctx: Context, user_id: int | None, reason: str, *, major: bool = False) -> BudgetVersion:
    """Nova versão em elaboração a partir da congelada: copia orçamentos, linhas, itens e movimentações.
    Orçamentos consolidados voltam para 'Aprovado' (a Controladoria reabre só os CCs que vão mudar)."""
    old = ctx.version
    if old.status != "FROZEN":
        raise ConsolidationError("Congele a versão atual antes de abrir uma revisão")
    if not (reason or "").strip():
        raise ConsolidationError("Informe o motivo da revisão")
    new = BudgetVersion(
        cycle_id=old.cycle_id,
        major=old.major + 1 if major else old.major,
        minor=0 if major else old.minor + 1,
        status="WORKING",
        parent_version_id=old.id,
        reason=reason.strip(),
        created_by=user_id,
    )
    if db.scalar(
        select(BudgetVersion).where(
            BudgetVersion.cycle_id == new.cycle_id, BudgetVersion.major == new.major, BudgetVersion.minor == new.minor
        )
    ):
        raise ConsolidationError(f"A versão {new.major}.{new.minor} já existe")
    db.add(new)
    db.flush()
    for sub in db.scalars(select(BudgetSubmission).where(BudgetSubmission.version_id == old.id)):
        status = "APPROVED" if sub.status == "CONSOLIDATED" else sub.status
        copy = _copy(sub, version_id=new.id, status=status)
        db.add(copy)
        db.flush()
        for line in db.scalars(select(BudgetLine).where(BudgetLine.submission_id == sub.id)):
            nl = _copy(line, submission_id=copy.id)
            nl.values = [BudgetLineValue(month=v.month, amount=v.amount) for v in line.values]
            db.add(nl)
        for j in db.scalars(select(AccountJustification).where(AccountJustification.submission_id == sub.id)):
            db.add(_copy(j, submission_id=copy.id))
        for r in db.scalars(select(PackageReview).where(PackageReview.submission_id == sub.id)):
            db.add(_copy(r, submission_id=copy.id))
        for p in db.scalars(select(CapexProject).where(CapexProject.submission_id == sub.id)):
            np_ = _copy(p, submission_id=copy.id)
            np_.items = []
            for item in p.items:
                ni = _copy(item, project_id=None)
                ni.values = [CapexItemValue(month=v.month, amount=v.amount) for v in item.values]
                np_.items.append(ni)
            db.add(np_)
        for mv in db.scalars(select(PersonnelMovement).where(PersonnelMovement.submission_id == sub.id)):
            db.add(_copy(mv, submission_id=copy.id))
        db.add(
            WorkflowEvent(
                submission_id=copy.id,
                from_status=sub.status,
                to_status=status,
                action="revision",
                comment=f"Revisão {new.label} a partir da {old.label}: {reason.strip()}",
                version_label=new.label,
                user_id=user_id,
            )
        )
    db.flush()
    return new


# ------------------------------------------------------------------ realizado e variações


@dataclass
class Reference:
    prev: dict[str, Decimal]
    ref_ytd: dict[str, Decimal]
    ref_annualized: dict[str, Decimal]
    ref_budget: dict[str, Decimal]
    natures: dict[str, str]
    names: dict[str, str]


def reference(db: Session, ctx: Context, cc_ids: set[int] | None = None) -> Reference:
    """Realizado (ano anterior e ano de referência anualizado por empresa) e orçado de referência por conta."""
    accounts = {a.id: a for a in db.scalars(select(Account))}
    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    companies = {c.id: c.code for c in db.scalars(select(Company))}
    closed_cache: dict[str, int | None] = {}

    def sums(model, year):
        stmt = (
            select(model.cost_center_id, model.account_id, func.sum(model.amount))
            .join(DatasetVersion, DatasetVersion.id == model.dataset_version_id)
            .where(DatasetVersion.is_current, model.fiscal_year == year)
            .group_by(model.cost_center_id, model.account_id)
        )
        if cc_ids is not None:
            stmt = stmt.where(model.cost_center_id.in_(cc_ids or {-1}))
        return db.execute(stmt).all()

    prev: dict[str, Decimal] = defaultdict(lambda: ZERO)
    ytd: dict[str, Decimal] = defaultdict(lambda: ZERO)
    ann: dict[str, Decimal] = defaultdict(lambda: ZERO)
    budget: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for _cc, acc_id, amount in sums(ActualEntry, ctx.prev_year):
        prev[accounts[acc_id].code] += amount or ZERO
    for cc_id, acc_id, amount in sums(ActualEntry, ctx.ref_year):
        code = accounts[acc_id].code
        ytd[code] += amount or ZERO
        company = companies.get(ccs[cc_id].company_id) if cc_id in ccs else None
        if company not in closed_cache:
            closed_cache[company] = closed_period(db, ctx.ref_year, company) if company else None
        closed = closed_cache[company]
        if closed:
            ann[code] += (amount or ZERO) * 12 / closed
    for _cc, acc_id, amount in sums(ReferenceBudgetEntry, ctx.ref_year):
        budget[accounts[acc_id].code] += amount or ZERO
    natures = {a.code: a.nature for a in accounts.values()}
    names = {a.code: a.name for a in accounts.values()}
    return Reference(prev, ytd, {k: money(v) for k, v in ann.items()}, budget, natures, names)


def module_of_nature(nature: str | None) -> str | None:
    for module, natures in MODULE_NATURES.items():
        if nature in natures:
            return module
    return None


def account_variations(rows: list[Row], ref: Reference, ctx: Context) -> list[dict]:
    """Por conta: realizado × 2027, com os mesmos alertas do OPEX (crescimento/redução/conta nova/sem orçamento)."""
    growth = ctx.param("alert.growth_pct", 0.2)
    reduction = ctx.param("alert.reduction_pct", 0.3)
    min_relevant = ctx.param("alert.min_relevant_amount", 1000)
    proposed: dict[str, Decimal] = defaultdict(lambda: ZERO)
    names: dict[str, str | None] = {}
    modules: dict[str, str] = {}
    packages: dict[str, str | None] = {}
    for r in rows:
        proposed[r.account_code] += r.total
        names[r.account_code] = r.account_name
        modules[r.account_code] = r.module
        packages[r.account_code] = r.package
    codes = set(proposed) | {c for c, v in ref.ref_annualized.items() if v} | {c for c, v in ref.prev.items() if v}
    out = []
    for code in codes:
        module = modules.get(code) or module_of_nature(ref.natures.get(code))
        if module is None:
            continue  # custo/receita fora do orçamento de despesas
        p = proposed.get(code, ZERO)
        base = ref.ref_annualized.get(code, ZERO) or ref.ref_budget.get(code, ZERO) or ref.prev.get(code, ZERO)
        flags = []
        if max(abs(base), abs(p)) >= min_relevant:
            if base == 0:
                flags.append("NEW_ACCOUNT")
            elif p == 0:
                flags.append("NO_BUDGET")
            elif p > base * (1 + growth):
                flags.append("GROWTH_ABOVE")
            elif p < base * (1 - reduction):
                flags.append("REDUCTION_ABOVE")
        out.append(
            {
                "account": code,
                "name": names.get(code) or ref.names.get(code),
                "module": module,
                "package": packages.get(code),
                "prev_actual": str(money(ref.prev.get(code, ZERO))),
                "ref_actual_ytd": str(money(ref.ref_ytd.get(code, ZERO))),
                "ref_annualized": str(money(ref.ref_annualized.get(code, ZERO))),
                "ref_budget": str(money(ref.ref_budget.get(code, ZERO))),
                "proposed": str(money(p)),
                "variation": str(money(p - base)),
                "variation_pct": str(((p - base) / base).quantize(Decimal("0.0001"))) if base else None,
                "flags": flags,
            }
        )
    return sorted(out, key=lambda r: -abs(Decimal(r["variation"])))


# ------------------------------------------------------------------ comparação entre versões


def compare_versions(
    db: Session, ctx: Context, from_version: BudgetVersion, to_version: BudgetVersion, cc_ids: set[int] | None
) -> dict:
    """O que mudou de uma versão para outra: por módulo, por CC e por conta (chave CC × conta)."""
    a = rows_for(db, ctx, from_version, cc_ids)
    b = rows_for(db, ctx, to_version, cc_ids)

    def by(rows: list[Row], key) -> dict:
        out: dict = defaultdict(lambda: ZERO)
        for r in rows:
            out[key(r)] += r.total
        return out

    def diff_table(ka: dict, kb: dict, label) -> list[dict]:
        items = []
        for k in set(ka) | set(kb):
            va, vb = ka.get(k, ZERO), kb.get(k, ZERO)
            if va == vb:
                continue
            items.append(
                {
                    **label(k),
                    "from": str(money(va)),
                    "to": str(money(vb)),
                    "difference": str(money(vb - va)),
                    "difference_pct": str(((vb - va) / abs(va)).quantize(Decimal("0.0001"))) if va else None,
                }
            )
        return sorted(items, key=lambda i: -abs(Decimal(i["difference"])))

    names = {}
    for r in a + b:
        names[("cc", r.cost_center_id)] = (r.cost_center_code, r.cost_center_name)
        names[("acc", r.account_code)] = r.account_name
    modules = diff_table(
        by(a, lambda r: r.module), by(b, lambda r: r.module), lambda k: {"module": k, "label": MODULE_LABELS[k]}
    )
    ccs = diff_table(
        by(a, lambda r: r.cost_center_id),
        by(b, lambda r: r.cost_center_id),
        lambda k: {"cost_center_id": k, "code": names[("cc", k)][0], "label": names[("cc", k)][1]},
    )
    accounts = diff_table(
        by(a, lambda r: (r.cost_center_id, r.module, r.account_code)),
        by(b, lambda r: (r.cost_center_id, r.module, r.account_code)),
        lambda k: {
            "cost_center_id": k[0],
            "cost_center": f"{names[('cc', k[0])][0]} · {names[('cc', k[0])][1]}",
            "module": MODULE_LABELS[k[1]],
            "account": k[2],
            "label": names[("acc", k[2])],
        },
    )
    total_a, total_b = sum((r.total for r in a), ZERO), sum((r.total for r in b), ZERO)
    monthly = [str(money(sum((r.values[i] for r in b), ZERO) - sum((r.values[i] for r in a), ZERO))) for i in range(12)]
    return {
        "from": {
            "id": from_version.id,
            "label": from_version.label,
            "status": from_version.status,
            "total": str(money(total_a)),
        },
        "to": {
            "id": to_version.id,
            "label": to_version.label,
            "status": to_version.status,
            "total": str(money(total_b)),
        },
        "difference": str(money(total_b - total_a)),
        "difference_pct": str(((total_b - total_a) / abs(total_a)).quantize(Decimal("0.0001"))) if total_a else None,
        "monthly_difference": monthly,
        "modules": modules,
        "cost_centers": ccs,
        "accounts": accounts[:300],
        "changed_accounts": len(accounts),
    }
