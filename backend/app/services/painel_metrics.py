"""Painel — métricas: execução (KPIs do fechado), evolução (cascata), séries por tipo/mês, rankings, mapa de calor,
orçamento em construção e critérios da versão. Saiu de `api/v1/dashboard.py` em 13/10/2026, sem mudar comportamento.
"""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import visible_cost_center_ids
from app.models import (
    Account,
    ActualEntry,
    BudgetCycle,
    BudgetPackage,
    Company,
    CostCenter,
    DatasetVersion,
    ReferenceBudgetEntry,
    User,
)
from app.services import consolidation as cons
from app.services import opex as opex_svc
from app.services.painel_facts import (
    MONTH_ABBR,
    Facts,
    Period,
    _actual_only,
    _base_sums,
    _closed_by_company,
    _ints,
    _last_closed,
    _main_model,
    _money,
    _pct,
    _share,
)

ZERO = Decimal("0")


@dataclass
class ExecutionScope:
    """Recorte da execução do orçamento: um único ano com realizado e orçado; `closed` = último mês contábil."""

    year: int
    closed: int

    @property
    def cap(self) -> int | None:
        return self.closed if self.closed < 12 else None

    @property
    def suffix(self) -> str:
        return f" até {MONTH_ABBR[self.closed - 1]}" if self.closed < 12 else ""


def _execution_scope(db: Session, P: Period) -> tuple[ExecutionScope | None, str | None]:
    """Execução só faz sentido com um ano de realizado (não o ano do ciclo) e o mês contábil conhecido."""
    if len(P.years) != 1:
        return None, "A execução do orçamento é calculada para um único ano: selecione só um ano."
    year = P.years[0]
    if year not in P.actual_years:
        return None, f"Sem realizado de {year} carregado."
    closed = max(_closed_by_company(db, "ACTUAL", year).values(), default=0)
    if not closed:
        return None, f"O realizado de {year} não informa o último mês fechado."
    return ExecutionScope(year, closed), None


def _execution(db: Session, f: Facts, P: Period) -> dict:
    """Execução do orçamento do ano (Painel, 10/10/2026): realizado contábil até o último mês fechado (sem a projeção)
    × orçado no mesmo período, % executado do orçado do ano e projeção de fechamento = realizado + projeção do gestor.
    A projeção só é mostrada quando cobre todos os meses que faltam em todas as empresas com realizado no recorte;
    caso contrário, vem indisponível com o motivo — nada é estimado."""
    scope, reason = _execution_scope(db, P)
    if scope is None:
        return {"available": False, "reason": reason}
    year, cap = scope.year, scope.cap
    budget_year = f.total(ReferenceBudgetEntry, [year])
    if not budget_year:
        return {"available": False, "year": year, "reason": f"Sem orçado {year} carregado para este recorte."}
    budget_ytd = f.total(ReferenceBudgetEntry, [year], cap)
    split = _actual_only(f, [year], (), cap)
    actual_ytd = split.get((), ZERO)

    # projeção de fechamento: meses do filtro (ou o ano) ainda sem realizado contábil
    wanted = sorted(P.months) if P.months else list(range(1, 13))
    remaining = [m for m in wanted if m > scope.closed]
    forecast: dict = {"available": False, "value": None, "projected": None, "var": None, "var_pct": None}
    if not remaining:
        forecast |= {"available": True, "kind": "closed", "value": _money(actual_ytd), "projected": _money(ZERO)}
        forecast_value = actual_ytd
    else:
        actual_by_company = _closed_by_company(db, "ACTUAL", year)
        projection = _closed_by_company(db, "PROJECTION", year)
        companies = {c.id: c.code for c in db.scalars(select(Company))}
        in_scope = {companies[i] for i in f.company_ids if i in companies} or set(actual_by_company)
        missing = sorted(c for c in in_scope & set(actual_by_company) if projection.get(c, 0) < max(remaining))
        forecast_value = None
        if not projection:
            first = MONTH_ABBR[remaining[0] - 1]
            last = MONTH_ABBR[remaining[-1] - 1]
            span = first if first == last else f"{first}–{last}"
            forecast["reason"] = f"Sem projeção do gestor carregada para {span} de {year}."
        elif missing:
            names = {c.code: c.short_name or c.name for c in db.scalars(select(Company))}
            forecast["reason"] = (
                f"A projeção do gestor não cobre até {MONTH_ABBR[max(remaining) - 1]} em "
                f"{', '.join(names.get(c, c) for c in missing)}."
            )
        else:
            projected = sum(
                (
                    amount
                    for flag, amount in f.sums(ActualEntry, [year], "projected")
                    if flag  # só as linhas da projeção (meses sem realizado contábil)
                ),
                ZERO,
            )
            forecast_value = actual_ytd + projected
            forecast |= {
                "available": True,
                "kind": "projection",
                "value": _money(forecast_value),
                "projected": _money(projected),
                "from_month": MONTH_ABBR[remaining[0] - 1],
                "to_month": MONTH_ABBR[remaining[-1] - 1],
            }
    if forecast_value is not None:
        forecast["var"] = _money(forecast_value - budget_year)
        forecast["var_pct"] = _pct(forecast_value, budget_year)
    return {
        "available": True,
        "reason": None,
        "year": year,
        "closed": scope.closed,
        "closed_month": MONTH_ABBR[scope.closed - 1],
        "actual_label": f"Realizado {year}{scope.suffix}",
        "budget_ytd_label": f"Orçado {year}{scope.suffix}",
        "budget_label": f"Orçado {year}",
        "budget_year": _money(budget_year),
        "budget_ytd": _money(budget_ytd),
        "actual_ytd": _money(actual_ytd),
        "var": _money(actual_ytd - budget_ytd),
        "var_pct": _pct(actual_ytd, budget_ytd),
        "execution_pct": _share(actual_ytd, budget_year),
        "budget_phase_pct": _share(budget_ytd, budget_year),  # quanto do orçado do ano cabe até o mês fechado
        "monthly_avg": _money(actual_ytd / len([m for m in wanted if m <= scope.closed]))
        if any(m <= scope.closed for m in wanted)
        else None,
        "forecast": forecast,
    }


def _thresholds(db: Session) -> tuple[float, float]:
    """Limiares do semáforo do ciclo (alert.growth_pct / alert.reduction_pct)."""
    try:
        ctx = opex_svc.context(db)
        return float(ctx.param("alert.growth_pct", 0.2)), float(ctx.param("alert.reduction_pct", 0.3))
    except opex_svc.OpexError:
        return 0.2, 0.3


def _evolution_options(db: Session, f: Facts, P: Period) -> list[dict]:
    """Todos os marcos possíveis da "Evolução do orçamento", em ordem cronológica (no mesmo ano, o orçado antes do
    realizado, como no slide da diretoria): Realizado de cada ano carregado (ano cheio ou "até MÊS"; o ano em curso
    também anualizado), Orçado de cada ano com orçamento de referência e o Orçamento do ciclo (proposto). Os filtros de
    ano e mês não se aplicam (`f` vem sem meses); empresa, área, CC, pacote, conta e tipo valem. Marco zerado fica de
    fora. Chave: "actual:2025", "actual_ann:2026", "budget:2026", "target:2027"; os orçados também "até MÊS"
    ("budget_ytd:2026", "target_ytd:2027"), somados até o último mês fechado do realizado — comparação com o mesmo
    período do realizado em curso (sem o CAPEX sem cronograma, que não tem mês)."""
    target = P.target_year or (max(P.actual_years) + 1 if P.actual_years else None)
    out: list[dict] = []
    # último mês fechado do realizado mais recente: corte do "mesmo período" para os orçados
    cut = _last_closed(db, max(P.actual_years)) if P.actual_years else None
    cut = cut if cut and cut < 12 else None

    def add(key: str, kind: str, word: str, year: int, value: Decimal, note: str | None = None, **extra) -> None:
        if value:
            label = f"{word} {year}" + (f" ({note})" if note else "")
            milestone = {"key": key, "kind": kind, "word": word, "year": year, "note": note, "label": label}
            out.append(milestone | {"value": _money(value)} | extra)

    budget_years = {y for y in P.budget_years if y != target}
    for year in sorted(set(P.actual_years) | budget_years):
        if year in budget_years:
            if cut:
                note = f"até {MONTH_ABBR[cut - 1]}"
                add(f"budget_ytd:{year}", "budget", "Orçado", year, f.total(ReferenceBudgetEntry, [year], cut), note)
            add(f"budget:{year}", "budget", "Orçado", year, f.total(ReferenceBudgetEntry, [year]))
        if year in P.actual_years:
            closed = _last_closed(db, year)
            ytd = f.total(ActualEntry, [year])
            if closed and closed < 12:
                add(f"actual:{year}", "actual", "Realizado", year, ytd, f"até {MONTH_ABBR[closed - 1]}")
                add(
                    f"actual_ann:{year}",
                    "actual",
                    "Realizado",
                    year,
                    ytd * 12 / closed,
                    "anualizado",
                    ytd=_money(ytd),
                    closed=closed,
                    closed_month=MONTH_ABBR[closed - 1],
                )
            else:
                add(f"actual:{year}", "actual", "Realizado", year, ytd)
    if target is not None and P.target_year == target:
        if cut:
            add(
                f"target_ytd:{target}",
                "budget",
                "Orçamento",
                target,
                f.total(ReferenceBudgetEntry, [target], cut),
                f"proposto até {MONTH_ABBR[cut - 1]}",
            )
        add(
            f"target:{target}",
            "budget",
            "Orçamento",
            target,
            f.total(ReferenceBudgetEntry, [target]),
            "proposto",
            unscheduled=_money(f.unscheduled_total()),
        )
    return out


def _evolution_default(options: list[dict], target: int | None) -> list[str]:
    """Marcos padrão: Realizado do ano retrasado → Orçado do ano anterior → Realizado do ano anterior (anualizado,
    se o ano não fechou) → Orçamento do ciclo."""
    if target is None:
        return []
    keys = {o["key"] for o in options}
    before, ref = target - 2, target - 1
    wanted = [f"actual:{before}", f"budget:{ref}"]
    wanted.append(f"actual_ann:{ref}" if f"actual_ann:{ref}" in keys else f"actual:{ref}")
    wanted.append(f"target:{target}")
    return [k for k in wanted if k in keys]


def _evolution(db: Session, f: Facts, P: Period, marks: list[str] | None = None) -> tuple[list[dict], list[dict]]:
    """(marcos escolhidos, todas as opções). Sem escolha (ou escolha inválida), os marcos padrão."""
    options = _evolution_options(db, f, P)
    target = P.target_year or (max(P.actual_years) + 1 if P.actual_years else None)
    valid = [k for k in (marks or []) if any(o["key"] == k for o in options)]
    chosen = set(valid if len(valid) >= 2 else _evolution_default(options, target))
    return [o for o in options if o["key"] in chosen], options


def _by_module(f: Facts, P: Period) -> list[dict]:
    """Cards por tipo de orçamento: série principal e base de comparação por módulo. Com o filtro de tipo
    ativo, o card do total já é o do tipo: a lista vem vazia."""
    if P.modules:
        return []
    main = f.by_module(_main_model(P), P.main_years)
    if P.base_kind == "prev":
        base = {k: v * P.base_scale for k, v in f.by_module(ActualEntry, P.prev_years, P.base_cap).items()}
    elif P.base_kind == "budget":
        base = f.by_module(ReferenceBudgetEntry, P.years, P.base_cap)
    elif P.base_kind == "actual":
        base = {k: v * P.base_scale for k, v in f.by_module(ActualEntry, P.actual_selected).items()}
    else:
        base = {}
    unscheduled = f.unscheduled_by_module() if (P.main == "budget" and P.target_year in P.years) else {}
    return [
        {
            "module": m,
            "label": cons.MODULE_LABELS[m],
            "main": _money(main.get(m, ZERO)),
            "base": _money(base.get(m, ZERO)),
            "var_pct": _pct(main.get(m, ZERO), base.get(m, ZERO)) if P.base_kind else None,
            "unscheduled": _money(unscheduled.get(m, ZERO)),
        }
        for m in cons.MODULES
    ]


def _monthly(f: Facts, P: Period) -> list[dict]:
    """Série mensal (anos escolhidos somados mês a mês): ano anterior (base), realizado e orçado."""
    monthly = {m: {"month": m, "prev": ZERO, "ref": ZERO, "budget": ZERO, "proj": ZERO} for m in range(1, 13)}
    if P.compare:
        for period, amount in f.sums(ActualEntry, P.prev_years, "period", max_period=P.base_cap):
            monthly[int(period)]["prev"] = amount
    for period, amount in f.sums(ActualEntry, P.years, "period"):
        monthly[int(period)]["ref"] = amount
    for period, projected, amount in f.sums(ActualEntry, P.years, "period", "projected"):
        if projected:
            monthly[int(period)]["proj"] = amount
    for period, amount in f.sums(ReferenceBudgetEntry, P.main_years, "period"):
        monthly[int(period)]["budget"] = amount
    return [{k: (_money(v) if k != "month" else v) for k, v in row.items()} for row in monthly.values()]


def _ranking(db: Session, f: Facts, P: Period, key: str, label_model, limit: int = 10) -> list[dict]:
    """Maiores itens (CC ou conta) na série principal, com a base de comparação quando houver."""
    cur = dict(f.sums(_main_model(P), P.main_years, key))
    base = _base_sums(f, P, key)
    ids = sorted((k for k in cur if k is not None), key=lambda k: cur[k], reverse=True)[:limit]
    objs = {o.id: o for o in db.scalars(select(label_model).where(label_model.id.in_(ids)))} if ids else {}
    return [
        {
            "id": i,
            "code": getattr(objs.get(i), "code", None),
            "name": getattr(objs.get(i), "name", None),
            "ref_ytd": _money(cur[i]),
            "prev_ytd": _money(base.get(i)),
            "ytd_var_pct": _pct(cur[i], base.get(i, ZERO)) if P.base_kind else None,
        }
        for i in ids
    ]


def _heatmap(db: Session, f: Facts, model, years: list[int], limit: int = 12) -> dict:
    """Centro de custo × mês (maiores CCs do período, anos somados) na série principal, para o mapa de calor."""
    rows: dict[int, dict[int, Decimal]] = defaultdict(lambda: defaultdict(lambda: ZERO))
    for cc_id, period, amount in f.sums(model, years, "cost_center_id", "period"):
        if cc_id is not None:
            rows[cc_id][int(period)] += amount
    top = sorted(rows, key=lambda k: sum(rows[k].values()), reverse=True)[:limit]
    ccs = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(top)))} if top else {}
    return {
        "year": max(years),
        "years": years,
        "rows": [
            {
                "id": cc_id,
                "code": ccs[cc_id].code if cc_id in ccs else None,
                "name": ccs[cc_id].name if cc_id in ccs else "—",
                "values": [_money(rows[cc_id].get(m)) for m in range(1, 13)],
                "total": _money(sum(rows[cc_id].values(), ZERO)),
            }
            for cc_id in top
        ],
    }


def budget_progress(
    db: Session,
    user: User,
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
) -> dict | None:
    """Andamento do orçamento OPEX do ciclo: CCs por status e proposto × anualizado por pacote."""
    from app.models import BudgetLine, BudgetSubmission, BudgetVersion

    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    if cycle is None:
        return None
    version = db.scalar(
        select(BudgetVersion)
        .where(BudgetVersion.cycle_id == cycle.id, BudgetVersion.status == "WORKING")
        .order_by(BudgetVersion.major.desc(), BudgetVersion.minor.desc())
    )
    if version is None:
        return None
    visible = visible_cost_center_ids(db, user)
    cc_stmt = select(CostCenter.id).where(CostCenter.is_active)
    if visible is not None:
        cc_stmt = cc_stmt.where(CostCenter.id.in_(visible or {-1}))
    if _ints(company_id):
        cc_stmt = cc_stmt.where(CostCenter.company_id.in_(_ints(company_id)))
    if cost_center_id:
        cc_stmt = cc_stmt.where(CostCenter.id == cost_center_id)
    cc_ids = set(db.scalars(cc_stmt))
    statuses = dict(
        db.execute(
            select(BudgetSubmission.cost_center_id, BudgetSubmission.status).where(
                BudgetSubmission.version_id == version.id,
                BudgetSubmission.module == "OPEX",
                BudgetSubmission.cost_center_id.in_(cc_ids or {-1}),
            )
        ).all()
    )
    # pacote da linha; linha importada antes de a conta ter pacote usa o pacote atual da conta
    line_pkg = func.coalesce(BudgetLine.package_id, Account.package_id)
    line_stmt = (
        select(line_pkg, BudgetLine.cost_center_id, func.sum(BudgetLine.total_amount))
        .join(BudgetSubmission, BudgetSubmission.id == BudgetLine.submission_id)
        .join(Account, Account.id == BudgetLine.account_id)
        .where(
            BudgetSubmission.version_id == version.id,
            BudgetSubmission.module == "OPEX",
            BudgetLine.cost_center_id.in_(cc_ids or {-1}),
        )
        .group_by(line_pkg, BudgetLine.cost_center_id)
    )
    if package_id:
        line_stmt = line_stmt.where(line_pkg == package_id)
    proposed: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    started: set[int] = set()
    for pkg_id, cc_id, amount in db.execute(line_stmt):
        proposed[pkg_id] += amount or ZERO
        if amount:
            started.add(cc_id)
    # só os CCs que têm OPEX (lançamento ou orçamento já enviado): CC sem despesa (ex.: só CAPEX) ou zerado depois
    # de uma correção não aparece como "não iniciado" (decisão de 07/10/2026)
    sent = {"SUBMITTED", "UNDER_REVIEW", "ADJUSTMENT_REQUESTED", "APPROVED", "CONSOLIDATED"}
    counted = {cc for cc in cc_ids if cc in started or statuses.get(cc) in sent}
    counts: dict[str, int] = defaultdict(int)
    for cc in counted:
        counts[statuses.get(cc, "DRAFT")] += 1

    # 2026 anualizado só dos CCs que já lançaram 2027 (comparação justa durante o preenchimento)
    ref = cycle.actual_reference_year
    annualized: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    closed_by_company: dict[str | None, int | None] = {}
    if started:
        stmt = (
            select(Account.package_id, CostCenter.company_id, func.sum(ActualEntry.amount))
            .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
            .join(Account, Account.id == ActualEntry.account_id)
            .join(CostCenter, CostCenter.id == ActualEntry.cost_center_id)
            .where(
                DatasetVersion.is_current,
                ActualEntry.fiscal_year == ref,
                ActualEntry.cost_center_id.in_(started),
                Account.nature.in_(("OPEX", "FINANCEIRO")),
            )
            .group_by(Account.package_id, CostCenter.company_id)
        )
        if package_id:
            stmt = stmt.where(Account.package_id == package_id)
        companies = {c.id: c.code for c in db.scalars(select(Company))}
        for pkg_id, comp_id, amount in db.execute(stmt):
            code = companies.get(comp_id)
            if code not in closed_by_company:
                closed_by_company[code] = _last_closed_company(db, ref, code)
            closed = closed_by_company[code]
            if closed:
                annualized[pkg_id] += amount * 12 / closed
    packages = {p.id: p for p in db.scalars(select(BudgetPackage))}
    by_package = sorted(
        (
            {
                "package_id": pid,
                "package": packages[pid].name if pid in packages else "Sem pacote",
                "proposed": _money(proposed.get(pid)),
                "ref_annualized": _money(annualized.get(pid)),
            }
            for pid in set(proposed) | set(annualized)
        ),
        key=lambda r: -max(Decimal(r["proposed"]), Decimal(r["ref_annualized"])),
    )
    return {
        "target_year": cycle.fiscal_year,
        "ref_year": ref,
        "cycle_status": cycle.status,
        "deadline": cycle.opex_deadline.isoformat() if cycle.opex_deadline else None,
        "total_cost_centers": len(counted),
        "started_cost_centers": len(started),
        "status_counts": dict(counts),
        "proposed_total": _money(sum(proposed.values(), ZERO)),
        "annualized_started_total": _money(sum(annualized.values(), ZERO)),
        # com a projeção do gestor o ano está completo: "Realizado 2026" em vez de "2026 anualizado"
        "ref_label": f"Realizado {ref}"
        if closed_by_company and all(c == 12 for c in closed_by_company.values())
        else f"{ref} anualizado",
        "by_package": by_package,
    }


def _progress_by_package(db: Session, f: Facts, ref: int) -> dict:
    """Orçamento do ciclo em construção, todos os tipos: proposto (consolidação) × realizado do ano de referência por
    pacote, só dos CCs que já têm valor proposto. Realizado anualizado se o ano ainda não fechou (sem projeção)."""
    target = f.target_year
    if target is None:
        return {}
    proposed_rows = f.sums(ReferenceBudgetEntry, [target], "package_id", "cost_center_id")
    started = {cc for _pkg, cc, amount in proposed_rows if cc is not None and amount}
    proposed: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    for pkg, cc, amount in proposed_rows:
        if cc in started:
            proposed[pkg] += amount
    closed = _last_closed(db, ref)
    scale = Decimal(12) / Decimal(closed) if closed and closed < 12 else Decimal(1)
    base: dict[int | None, Decimal] = defaultdict(lambda: ZERO)
    for pkg, cc, amount in f.sums(ActualEntry, [ref], "package_id", "cost_center_id"):
        if cc in started:
            base[pkg] += amount * scale
    packages = {pk.id: pk for pk in db.scalars(select(BudgetPackage))}
    by_package = sorted(
        (
            {
                "package_id": pid,
                "package": packages[pid].name if pid in packages else "Sem pacote",
                "proposed": _money(proposed.get(pid)),
                "ref_annualized": _money(base.get(pid)),
            }
            for pid in set(proposed) | set(base)
            if proposed.get(pid) or base.get(pid)
        ),
        key=lambda r: -max(Decimal(r["proposed"]), Decimal(r["ref_annualized"])),
    )
    return {
        "started_cost_centers": len(started),
        "proposed_total": _money(sum(proposed.values(), ZERO)),
        "annualized_started_total": _money(sum(base.values(), ZERO)),
        "ref_label": f"Realizado {ref}" if scale == 1 else f"{ref} anualizado",
        "by_package": by_package,
        "all_modules": True,
    }


def _last_closed_company(db: Session, year: int, company_code: str | None) -> int | None:
    return opex_svc.closed_period(db, year, company_code) if company_code else None


# ------------------------------------------------------------------ critérios da versão (informativo)

MAX_CRITERIA = 30


def _criteria_items(raw) -> list[dict]:
    items = []
    for it in raw if isinstance(raw, list) else []:
        if isinstance(it, dict) and (str(it.get("item") or "").strip() or str(it.get("texto") or "").strip()):
            items.append({"item": str(it.get("item") or "").strip(), "texto": str(it.get("texto") or "").strip()})
    return items
