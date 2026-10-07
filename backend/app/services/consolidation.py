"""Consolidação do orçamento: OPEX + CAPEX + Pessoal numa base única (chave empresa-filial-CC-conta × mês),
fotografia ao congelar a versão, revisão (nova versão) e variações contra o realizado.

Pessoal vira três contas por CC (parâmetros do ciclo): salário com reajuste, encargos e benefícios (parte do
multiplicador do contrato) e verbas rescisórias.
"""

import dataclasses
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.models import (
    Account,
    AccountJustification,
    ActualEntry,
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
)


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


def _dims(db: Session):
    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    companies = {c.id: c.code for c in db.scalars(select(Company))}
    branches = {b.id: b.code for b in db.scalars(select(Branch))}
    accounts = {a.id: a for a in db.scalars(select(Account))}
    packages = {p.id: p.name for p in db.scalars(select(BudgetPackage))}
    return ccs, companies, branches, accounts, packages


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
        add("OPEX", company_id, branch_id, cc_id, acc.code, acc.name, packages.get(pkg_id), month, amount)

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

    pacc = personnel_accounts(db, ctx)
    scenario = personnel_svc.baseline(db, ctx)
    for cc_id, positions in personnel_svc.build_positions(db, ctx, cc_ids).items():
        t = personnel_svc.totals_for(positions, scenario)
        parts = {
            "salary": t.salary,
            "charges": [t.monthly[i] - t.salary[i] - t.severance[i] for i in range(12)],
            "severance": t.severance,
        }
        for kind, series in parts.items():
            code, name = pacc[kind]
            for m, amount in enumerate(series, start=1):
                if amount:
                    add("PERSONNEL", None, None, cc_id, code, name, "Pessoas", m, money(amount))
    rows = [r for r in grouped.values() if any(r.values) or r.unscheduled]
    for r in rows:
        r.values = [money(v) for v in r.values]
        r.unscheduled = money(r.unscheduled)
    return sorted(rows, key=lambda r: (r.company_code, r.cost_center_code, r.module, r.account_code))


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
