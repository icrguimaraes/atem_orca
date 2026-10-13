"""Painel: realizado × orçamento de referência e checagens de qualidade dos dados.

Toda agregação considera apenas as versões vigentes (`dataset_versions.is_current`): versões
anteriores ficam no banco para auditoria, mas nunca somam no painel.
"""

from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import client_ip, get_current_user, require_roles, visible_cost_center_ids
from app.db import get_db
from app.models import (
    Account,
    ActualEntry,
    Area,
    BudgetCycle,
    BudgetPackage,
    Company,
    ContractType,
    CostCenter,
    CycleParameter,
    DatasetVersion,
    Department,
    Employee,
    ImportBatch,
    MacroAssumption,
    ReferenceBudgetEntry,
    User,
)
from app.models.base import Role
from app.services import painel_figures
from app.services.painel_facts import (
    Facts,
    _actual_only,
    _base_sums,
    _base_total,
    _main_model,
    _money,
    _pct,
    _period,
    _share,
)
from app.services.painel_metrics import (
    MAX_CRITERIA,
    _by_module,
    _criteria_items,
    _evolution,
    _execution,
    _execution_scope,
    _heatmap,
    _monthly,
    _progress_by_package,
    _ranking,
    _thresholds,
    budget_progress,
)

router = APIRouter(prefix="/dashboard", tags=["painel"])
ZERO = Decimal("0")


@router.get("/overview", summary="KPIs, evolução mensal e rankings do período (anos somados × meses × tipos)")
def overview(
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    year: int | None = None,
    years: str | None = None,
    months: str | None = None,
    modules: str | None = None,
    compare: bool = True,
    same_period: bool = True,
    base_version_id: int | None = None,
    figures: bool = False,
    full_labels: bool = True,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """`figures=true` acrescenta as figuras Plotly do Painel 2 (mesmos números, desenhados em Plotly).
    `years` (ex.: "2025,2026") são somados; `months` ("1,2,3") e `modules` ("OPEX,CAPEX,PERSONNEL") filtram
    tudo. `compare` liga a comparação com o ano anterior (só com um ano escolhido); `same_period` limita a base
    ao último mês fechado do realizado em foco. O ano do ciclo traz o orçamento proposto como orçado."""
    painel_figures.FULL_LABELS.set(full_labels)  # rótulos dos gráficos: numérico completo (padrão) ou R$ mil/mi
    P = _period(db, years, months, modules, compare, same_period, year, base_version_id)
    f = Facts(db, user, company_id, cost_center_id, package_id, P, account_id=account_id, department_id=department_id)
    main_model = _main_model(P)
    actual_total = f.total(ActualEntry, P.years)
    budget_total = f.total(ReferenceBudgetEntry, P.main_years)
    main_total = actual_total if P.main == "actual" else budget_total
    base_total = _base_total(f, P)
    # com uma revisão congelada como base, a série "anterior" (roxa) é a foto da revisão
    prev_full = f.total(ActualEntry, P.prev_years) if P.compare else (base_total if P.base_kind == "version" else ZERO)
    ytd_cap = P.closed if (P.main == "actual" and P.closed and P.closed < 12 and not P.months) else None
    budget_cmp = f.total(ReferenceBudgetEntry, P.years, ytd_cap) if P.main == "actual" else budget_total
    annualizable = P.main == "actual" and len(P.years) == 1 and bool(ytd_cap)
    annualized = (actual_total * 12 / P.closed) if annualizable else None

    data = {
        "reference_year": max(P.years),
        "previous_year": P.prev_years[0] if P.compare else None,
        "selected_years": P.years,
        "selected_months": sorted(P.months or []),
        "last_closed_period": P.closed,
        "years_loaded": P.actual_years,
        "budget_years": P.budget_years,
        "available_years": P.available,
        "target_year": P.target_year,
        "period": P.summary(),
        "has_actual": bool(P.actual_selected),
        "has_prev": P.compare or P.base_kind == "version",
        "has_budget": budget_total != ZERO,
        "kpis": {
            "prev_total": _money(prev_full),
            "prev_ytd": _money(base_total),  # base de comparação (ano anterior ou orçado)
            "ref_ytd": _money(main_total),  # série principal (realizado ou orçado)
            "actual_total": _money(actual_total),
            "ytd_var_pct": _pct(main_total, base_total) if P.base_kind else None,
            "ref_annualized": _money(annualized),
            "annualized_vs_prev_pct": _pct(annualized, prev_full) if (annualized is not None and P.compare) else None,
            "budget_total": _money(budget_total),
            "budget_ytd": _money(budget_cmp),
            "budget_consumption_pct": _pct(actual_total, budget_cmp) if (P.main == "actual" and budget_cmp) else None,
            # parcela do orçamento do ano do ciclo sem mês (CAPEX sem cronograma): está no total, não no mensal
            "budget_unscheduled": _money(f.unscheduled_total() if P.target_year in P.years else ZERO),
            # idem na revisão congelada usada como base (a coluna TOTAL do mensal soma ao roxo)
            "prev_unscheduled": _money(f.unscheduled_total(P.base_version) if P.base_kind == "version" else ZERO),
        },
        "by_module": _by_module(f, P),
        # execução do orçamento (realizado contábil × orçado no mesmo período, % executado, projeção de fechamento)
        "execution": _execution(db, f, P),
        "monthly": _monthly(f, P),
        "top_cost_centers": _ranking(db, f, P, "cost_center_id", CostCenter),
        "top_accounts": _ranking(db, f, P, "account_id", Account),
        "heatmap": _heatmap(db, f, main_model, P.main_years),
        "budget_progress": budget_progress(db, user, company_id, cost_center_id, package_id),
    }
    if data["budget_progress"]:
        # proposto × base por pacote com todos os tipos (OPEX, Pessoal, CAPEX) e os filtros do Painel (09/10/2026:
        # "em Todos não aparecem as contas de pessoal"); o andamento por status continua o do fluxo OPEX
        f_all = Facts(
            db,
            user,
            company_id,
            cost_center_id,
            package_id,
            replace(P, months=None),
            account_id=account_id,
            department_id=department_id,
        )
        data["budget_progress"] |= _progress_by_package(db, f_all, data["budget_progress"]["ref_year"])
    if figures:
        # Painel 2, como o Power BI: o visual em que se clicou não é filtrado pela própria seleção — mostra todos
        # os itens, com o escolhido em destaque (e clicável de novo para desmarcar); os outros visuais filtram.
        all_months = replace(P, months=None)
        f_months = Facts(
            db,
            user,
            company_id,
            cost_center_id,
            package_id,
            all_months,
            account_id=account_id,
            department_id=department_id,
        )
        f_ccs = Facts(db, user, company_id, None, package_id, P, account_id=account_id, department_id=department_id)
        f_accounts = Facts(db, user, company_id, cost_center_id, package_id, P, department_id=department_id)
        f_heat = Facts(
            db, user, company_id, None, package_id, all_months, account_id=account_id, department_id=department_id
        )
        data["heatmap_all"] = _heatmap(db, f_heat, main_model, P.main_years)
        # total de todos os CCs (o ranking mostra só os maiores), sem o filtro de CC, como o próprio visual
        top_main, top_base = f_ccs.total(main_model, P.main_years), _base_total(f_ccs, P)
        data["top_cost_centers_total"] = {
            "main": _money(top_main),
            "base": _money(top_base) if P.base_kind else None,
            "var_pct": _pct(top_main, top_base) if P.base_kind else None,
        }
        # evolução do orçamento: sem o filtro de meses (f_months), com todos os demais filtros
        data["evolution"], options = _evolution(db, f_months, P)
        data["evolution_options"] = [
            {"key": x["key"], "label": x["label"], "kind": x["kind"], "value": x["value"]} for x in options
        ]
        data["figures"] = painel_figures.build(
            data,
            monthly=_monthly(f_months, all_months),
            top_cost_centers=_ranking(db, f_ccs, P, "cost_center_id", CostCenter),
            top_accounts=_ranking(db, f_accounts, P, "account_id", Account),
            selected={"months": sorted(P.months or []), "cost_center_id": cost_center_id, "account_id": account_id},
            evolution=data["evolution"],
        )
    return data


@router.get("/evolution", summary="Evolução do orçamento (cascata) com os marcos escolhidos no próprio visual")
def evolution(
    marks: str | None = Query(None, description='Marcos, ex.: "actual:2024,actual:2025,target:2027"'),
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    modules: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    P = _period(db, None, None, modules, True, True)
    f = Facts(db, user, company_id, cost_center_id, package_id, P, account_id=account_id, department_id=department_id)
    selected, options = _evolution(db, f, P, [k.strip() for k in (marks or "").split(",") if k.strip()])
    return {
        "options": [{"key": o["key"], "label": o["label"], "kind": o["kind"], "value": o["value"]} for o in options],
        "selected": [o["key"] for o in selected],
        "milestones": selected,
        "figure": painel_figures.fig_evolution(selected),
    }


@router.get("/breakdown", summary="Tabela do painel com drill-down: pacote GMD → conta → centro de custo")
def breakdown(
    group_by: Literal["department", "area", "package", "account", "cost_center"] = "package",
    parent_department_id: int | None = None,
    parent_no_department: bool = False,
    parent_area_id: int | None = None,
    parent_no_area: bool = False,
    parent_package_id: int | None = None,
    parent_no_package: bool = False,
    parent_account_id: int | None = None,
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    years: str | None = None,
    months: str | None = None,
    modules: str | None = None,
    compare: bool = True,
    same_period: bool = True,
    base_version_id: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Uma linha por grupo com a série principal do período, a base de comparação (ano anterior ou orçado),
    participação (AV %), variação e variação %. `parent_*` restringem aos filhos de uma linha expandida
    (drill-down). `thresholds` traz os limiares do ciclo usados no semáforo (alert.growth_pct / reduction_pct)."""
    P = _period(db, years, months, modules, compare, same_period, base_version_id=base_version_id)
    parent = {
        "package_id": parent_package_id,
        "no_package": parent_no_package,
        "account_id": parent_account_id,
        "department_id": parent_department_id,
        "no_department_id": parent_no_department,
        "area_id": parent_area_id,
        "no_area_id": parent_no_area,
    }
    f = Facts(
        db, user, company_id, cost_center_id, package_id, P, parent, account_id=account_id, department_id=department_id
    )
    key = {
        "department": "department_id",
        "area": "area_id",
        "package": "package_id",
        "account": "account_id",
        "cost_center": "cost_center_id",
    }[group_by]
    cur = dict(f.sums(_main_model(P), P.main_years, key))
    base = _base_sums(f, P, key)

    ids = [k for k in set(cur) | set(base) if k is not None]
    if group_by == "department":  # Área (Controladoria, Tributos…)
        objs = {d.id: (None, d.name) for d in db.scalars(select(Department).where(Department.id.in_(ids or [-1])))}
        missing = (None, "Sem área")
    elif group_by == "area":  # Setor (Fiscal, Contabilidade…)
        objs = {a.id: (None, a.name) for a in db.scalars(select(Area).where(Area.id.in_(ids or [-1])))}
        missing = (None, "Sem setor")
    elif group_by == "package":
        objs = {
            p.id: (None, p.name) for p in db.scalars(select(BudgetPackage).where(BudgetPackage.id.in_(ids or [-1])))
        }
        missing = (None, "Sem pacote")
    elif group_by == "account":
        objs = {a.id: (a.code, a.name) for a in db.scalars(select(Account).where(Account.id.in_(ids or [-1])))}
        missing = (None, "Conta não cadastrada")
    else:
        objs = {c.id: (c.code, c.name) for c in db.scalars(select(CostCenter).where(CostCenter.id.in_(ids or [-1])))}
        missing = (None, "—")
    total_cur = sum(cur.values(), ZERO)
    total_base = sum(base.values(), ZERO)
    rows = []
    for k in set(cur) | set(base):
        c, b = cur.get(k, ZERO), base.get(k, ZERO)
        if c == 0 and b == 0:
            continue
        code, name = objs.get(k, missing)
        rows.append(
            {
                "id": k,
                "code": code,
                "name": name,
                "ref": _money(c),
                "base": _money(b),
                # AV %: participação no total da coluna (não é variação)
                "share_ref": _share(c, total_cur),
                "share_base": _share(b, total_base),
                "var": _money(c - b),
                "var_pct": _pct(c, b) if P.base_kind else None,
                # "Sem pacote" (id nulo) também detalha: os filhos vêm com parent_no_package; já uma conta
                # sem cadastro (só no orçamento proposto) não tem como ser filtrada
                "has_children": group_by in ("department", "area", "package")
                or (group_by == "account" and k is not None),
            }
        )
    rows.sort(key=lambda r: Decimal(r["ref"]), reverse=True)
    growth, reduction = _thresholds(db)
    return {
        "group_by": group_by,
        "reference_year": max(P.years),
        "previous_year": P.prev_years[0] if P.compare else None,
        "last_closed_period": P.closed,
        "period": P.summary(),
        "base": P.base_kind,
        "base_label": P.base_label,
        "main_label": P.main_label,
        "thresholds": {"growth": growth, "reduction": reduction},
        "rows": rows,
        "total": {
            "ref": _money(total_cur),
            "base": _money(total_base),
            "var": _money(total_cur - total_base),
            "var_pct": _pct(total_cur, total_base) if P.base_kind else None,
        },
    }


DEVIATION_GROUPS = {
    "pair": ("cost_center_id", "account_id"),
    "cost_center": ("cost_center_id",),
    "account": ("account_id",),
}


@router.get("/deviations", summary="Maiores desvios do recorte: conta × centro de custo, por CC e por conta")
def deviations(
    company_id: str | None = None,
    cost_center_id: int | None = None,
    package_id: int | None = None,
    account_id: int | None = None,
    department_id: str | None = None,
    years: str | None = None,
    months: str | None = None,
    modules: str | None = None,
    compare: bool = True,
    same_period: bool = True,
    base_version_id: int | None = None,
    limit: int = Query(8, ge=1, le=30),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Maiores diferenças (em valor absoluto) com os mesmos filtros do Painel. Com um ano de realizado e orçado
    (`basis = "execution"`): realizado contábil até o último mês fechado (sem a projeção do gestor) × orçado no mesmo
    período. Sem isso, a série principal do Painel × a base de comparação (`basis = "period"`). `out_of_range` usa os
    limiares do ciclo (os mesmos do semáforo da tabela)."""
    P = _period(db, years, months, modules, compare, same_period, base_version_id=base_version_id)
    f = Facts(db, user, company_id, cost_center_id, package_id, P, account_id=account_id, department_id=department_id)
    scope, _ = _execution_scope(db, P)
    use_execution = scope is not None and f.total(ReferenceBudgetEntry, [scope.year]) != ZERO

    def grouped(rows) -> dict[tuple, Decimal]:
        out: dict[tuple, Decimal] = defaultdict(lambda: ZERO)
        for *key, amount in rows:
            out[tuple(key)] += amount or ZERO
        return out

    def series(keys: tuple[str, ...]) -> tuple[dict, dict]:
        if use_execution:
            main = _actual_only(f, [scope.year], keys, scope.cap)
            base = grouped(f.sums(ReferenceBudgetEntry, [scope.year], *keys, max_period=scope.cap))
            return main, base
        main = grouped(f.sums(_main_model(P), P.main_years, *keys))
        if P.base_kind == "version":
            base = grouped(f.version_sums(P.base_version, *keys))
        elif P.base_kind == "prev":
            base = grouped(f.sums(ActualEntry, P.prev_years, *keys, max_period=P.base_cap))
        elif P.base_kind == "budget":
            base = grouped(f.sums(ReferenceBudgetEntry, P.years, *keys, max_period=P.base_cap))
        elif P.base_kind == "actual":
            base = grouped(f.sums(ActualEntry, P.actual_selected, *keys))
        else:
            base = {}
        if P.base_kind in ("prev", "actual") and P.base_scale != 1:
            base = {k: v * P.base_scale for k, v in base.items()}
        return main, base

    if use_execution:
        main_label, base_label = f"Realizado {scope.year}{scope.suffix}", f"Orçado {scope.year}{scope.suffix}"
    else:
        main_label, base_label = P.main_label, P.base_label
    growth, reduction = _thresholds(db)
    has_base = use_execution or bool(P.base_kind)

    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    accounts = {a.id: a for a in db.scalars(select(Account))}
    companies = {c.id: c for c in db.scalars(select(Company))}

    def cc_out(i):
        c = ccs.get(i)
        if not c:
            return None
        company = companies.get(c.company_id)
        return {
            "id": c.id,
            "code": c.code,
            "name": c.name,
            "company": (company.short_name or company.name) if company else None,
        }

    def account_out(i):
        a = accounts.get(i)
        return {"id": a.id, "code": a.code, "name": a.name} if a else None

    groups = {}
    for name, keys in DEVIATION_GROUPS.items():
        main, base = series(keys)
        items = []
        for key in set(main) | set(base):
            if any(k is None for k in key):  # sem CC ou conta cadastrada: não há como filtrar nem rastrear
                continue
            m, b = main.get(key, ZERO), base.get(key, ZERO)
            var = m - b
            if not var or not has_base:
                continue
            ratio = (var / abs(b)) if b else None
            items.append((abs(var), key, m, b, var, ratio))
        items.sort(key=lambda x: (-x[0], x[1]))
        rows = []
        for _, key, m, b, var, ratio in items[:limit]:
            attrs = dict(zip(keys, key, strict=True))
            rows.append(
                {
                    "cost_center": cc_out(attrs["cost_center_id"]) if "cost_center_id" in attrs else None,
                    "account": account_out(attrs["account_id"]) if "account_id" in attrs else None,
                    "main": _money(m),
                    "base": _money(b),
                    "var": _money(var),
                    "var_pct": _pct(m, b),
                    # sem base (item novo) também pede atenção; fora da faixa = acima do crescimento ou queda forte
                    # contra uma revisão congelada, "fora da faixa" = subiu em relação a ela (13/10/2026)
                    "out_of_range": (ratio is None or ratio > 0)
                    if P.base_kind == "version"
                    else (ratio is None or ratio > Decimal(str(growth)) or ratio < -Decimal(str(reduction))),
                }
            )
        groups[name] = rows
    return {
        "basis": "execution" if use_execution else "period",
        "main_label": main_label,
        "base_label": base_label,
        "main_series": "actual" if use_execution else P.main,
        "base_series": "budget" if use_execution else P.base_kind,
        "thresholds": {"growth": growth, "reduction": reduction},
        "groups": groups,
    }


@router.get("/data-quality", summary="Checagens de consistência da base após as importações")
def data_quality(db: Session = Depends(get_db), _: User = Depends(require_roles(Role.CONTROLLER))):
    checks = []

    def add(code, title, severity, count, detail=None, samples=None):
        checks.append(
            {
                "code": code,
                "title": title,
                "severity": severity if count else "OK",
                "count": count,
                "detail": detail,
                "samples": samples or [],
            }
        )

    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    ref = cycle.actual_reference_year if cycle else datetime.utcnow().year
    loaded = {
        s
        for s in db.scalars(
            select(DatasetVersion.scope_key).where(DatasetVersion.dataset_type == "ACTUAL", DatasetVersion.is_current)
        )
    }
    for year in (ref - 1, ref):
        has = any(s.startswith(f"ACTUAL:{year}:") for s in loaded)
        add(
            f"NO_ACTUAL_{year}",
            f"Realizado {year} carregado" if has else f"Realizado {year} ainda não carregado",
            "WARNING",
            0 if has else 1,
            None if has else f"Importe o realizado {year} para habilitar a comparação histórica.",
        )

    multi = list(
        db.execute(
            select(DatasetVersion.scope_key, func.count())
            .where(DatasetVersion.is_current)
            .group_by(DatasetVersion.dataset_type, DatasetVersion.scope_key)
            .having(func.count() > 1)
        )
    )
    add(
        "MULTIPLE_CURRENT",
        "Bases com mais de uma versão vigente (risco de soma duplicada)",
        "ERROR",
        len(multi),
        samples=[s for s, _ in multi],
    )

    no_manager = db.scalars(
        select(CostCenter.code)
        .where(CostCenter.is_active, CostCenter.manager_user_id.is_(None))
        .order_by(CostCenter.code)
    ).all()
    add(
        "CC_NO_USER",
        "Centros de custo sem usuário gestor vinculado",
        "WARNING",
        len(no_manager),
        "Sem usuário vinculado, nenhum gestor enxerga o CC. Cadastre o usuário com o mesmo nome do gestor do CC "
        "ou ajuste o cadastro.",
        no_manager[:15],
    )

    no_pkg = db.scalars(select(Account.code).where(Account.is_active, Account.package_id.is_(None))).all()
    add("ACCOUNT_NO_PACKAGE", "Contas sem pacote GMD", "WARNING", len(no_pkg), samples=no_pkg[:15])

    orphan_amount = db.scalar(
        select(func.sum(ActualEntry.amount))
        .join(DatasetVersion, DatasetVersion.id == ActualEntry.dataset_version_id)
        .join(Account, Account.id == ActualEntry.account_id)
        .where(DatasetVersion.is_current, Account.package_id.is_(None))
    )
    add(
        "ACTUAL_NO_PACKAGE",
        "Realizado vigente em contas sem pacote (fica fora dos totais por pacote)",
        "WARNING",
        1 if orphan_amount else 0,
        f"R$ {_money(orphan_amount)}" if orphan_amount else None,
    )

    pending = db.scalars(
        select(ImportBatch.id).where(
            ImportBatch.status == "VALIDATED", ImportBatch.validated_at < datetime.utcnow() - timedelta(hours=24)
        )
    ).all()
    add(
        "PENDING_IMPORTS",
        "Importações validadas aguardando confirmação há mais de 24h",
        "INFO",
        len(pending),
        samples=[f"#{i}" for i in pending],
    )

    repeated = db.execute(
        select(ImportBatch.file_name, func.count())
        .where(ImportBatch.status == "COMPLETED")
        .group_by(ImportBatch.file_sha256, ImportBatch.file_name)
        .having(func.count() > 1)
    ).all()
    add(
        "REPEATED_FILES",
        "Arquivos idênticos carregados mais de uma vez",
        "INFO",
        len(repeated),
        "Não duplica valores (só a versão vigente soma), mas gera versões redundantes.",
        [f"{n} ({c}×)" for n, c in repeated],
    )

    emp_no_cc = db.scalar(
        select(func.count()).select_from(Employee).where(Employee.is_active, Employee.cost_center_id.is_(None))
    )
    add("EMPLOYEE_NO_CC", "Colaboradores ativos sem centro de custo", "WARNING", emp_no_cc or 0)

    order = {"ERROR": 0, "WARNING": 1, "INFO": 2, "OK": 3}
    checks.sort(key=lambda c: order[c["severity"]])
    return {"checks": checks, "issues": sum(1 for c in checks if c["severity"] in ("ERROR", "WARNING"))}


@router.get("/inventory", summary="O que existe na base: cadastros, quadro de pessoal e premissas")
def inventory(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    visible = visible_cost_center_ids(db, user)

    # ---- cadastros
    cc_stmt = select(CostCenter).where(CostCenter.is_active)
    if visible is not None:
        cc_stmt = cc_stmt.where(CostCenter.id.in_(visible or {-1}))
    ccs = db.scalars(cc_stmt).all()
    accounts_by_nature = dict(
        db.execute(select(Account.nature, func.count()).where(Account.is_active).group_by(Account.nature)).all()
    )
    package_counts = [
        {"package": name, "package_type": ptype, "accounts": n}
        for name, ptype, n in db.execute(
            select(BudgetPackage.name, BudgetPackage.package_type, func.count(Account.id))
            .join(Account, Account.package_id == BudgetPackage.id, isouter=True)
            .group_by(BudgetPackage.id)
            .order_by(BudgetPackage.sort_order)
        )
    ]

    # ---- pessoal (quadro vigente; custo estimado = salário × multiplicador do tipo de contrato)
    emp_stmt = select(Employee).where(Employee.is_active)
    if visible is not None:
        emp_stmt = emp_stmt.where(Employee.cost_center_id.in_(visible or {-1}))
    employees = db.scalars(emp_stmt).all()
    contracts = {c.code: c for c in db.scalars(select(ContractType))}
    by_contract: dict[str, dict] = {}
    by_cc: dict[int | None, dict] = {}
    payroll = cost = ZERO
    for e in employees:
        ct = contracts.get(e.contract_type_code)
        mult = Decimal(ct.default_multiplier) if ct and ct.apply_multiplier else Decimal(1)
        salary = Decimal(e.base_salary or 0)
        payroll += salary
        cost += salary * mult
        c = by_contract.setdefault(
            e.contract_type_code,
            {"contract": e.contract_type_code, "headcount": 0, "payroll": ZERO, "multiplier": str(mult)},
        )
        c["headcount"] += 1
        c["payroll"] += salary
        k = by_cc.setdefault(e.cost_center_id, {"cost_center_id": e.cost_center_id, "headcount": 0, "payroll": ZERO})
        k["headcount"] += 1
        k["payroll"] += salary
    cc_names = {c.id: c for c in db.scalars(select(CostCenter).where(CostCenter.id.in_([k for k in by_cc if k])))}
    personnel = {
        "headcount": len(employees),
        "monthly_payroll": _money(payroll),
        "monthly_estimated_cost": _money(cost),
        "annual_estimated_cost": _money(cost * 12),
        "by_contract": [c | {"payroll": _money(c["payroll"])} for c in by_contract.values()],
        "by_cost_center": sorted(
            (
                {
                    "code": cc_names[k["cost_center_id"]].code if k["cost_center_id"] in cc_names else None,
                    "name": cc_names[k["cost_center_id"]].name if k["cost_center_id"] in cc_names else "Sem CC",
                    "headcount": k["headcount"],
                    "payroll": _money(k["payroll"]),
                }
                for k in by_cc.values()
            ),
            key=lambda r: -r["headcount"],
        )[:10],
    }

    # ---- premissas macroeconômicas (versão vigente), uma linha por indicador × fonte
    macro_version = db.scalar(
        select(DatasetVersion.id).where(DatasetVersion.dataset_type == "MACRO_ASSUMPTIONS", DatasetVersion.is_current)
    )
    macro_rows: dict[tuple, dict] = {}
    years: set[int] = set()
    if macro_version:
        for m in db.scalars(
            select(MacroAssumption)
            .where(MacroAssumption.dataset_version_id == macro_version)
            .order_by(MacroAssumption.id)
        ):
            key = (m.category, m.indicator, m.segment, m.source)
            row = macro_rows.setdefault(
                key,
                {
                    "category": m.category,
                    "indicator": m.indicator,
                    "segment": m.segment,
                    "source": m.source,
                    "reference_date": m.reference_date.isoformat() if m.reference_date else None,
                    "values": {},
                },
            )
            row["values"][m.year] = str(m.value)
            years.add(m.year)

    return {
        "master": {
            "cost_centers": len(ccs),
            "cost_centers_without_user": sum(1 for c in ccs if c.manager_user_id is None),
            "accounts_by_nature": accounts_by_nature,
            "packages": package_counts,
        },
        "personnel": personnel,
        "macro": {"years": sorted(years), "rows": list(macro_rows.values())},
        "figures": {"people_by_cost_center": painel_figures.fig_people(personnel["by_cost_center"])},
    }


@router.get("/criteria", summary="Critérios do orçamento da versão (informativo, parâmetro budget.criteria)")
def criteria(db: Session = Depends(get_db), _: User = Depends(get_current_user)):
    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    if cycle is None:
        return {"target_year": None, "items": []}
    param = db.get(CycleParameter, (cycle.id, "budget.criteria"))
    return {"target_year": cycle.fiscal_year, "items": _criteria_items(param.value if param else [])}


@router.put("/criteria", summary="Edita os critérios do orçamento (Controladoria; auditado)")
def set_criteria(
    request: Request,
    items: list[dict] = Body(..., embed=True),
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(Role.CONTROLLER)),
):
    from app.api.v1.cycles import apply_parameter

    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    if cycle is None:
        raise HTTPException(404, "Nenhum ciclo cadastrado")
    clean = _criteria_items(items)
    if len(clean) > MAX_CRITERIA or any(len(i["item"]) > 80 or len(i["texto"]) > 600 for i in clean):
        raise HTTPException(422, "Critérios: até 30 itens, título com até 80 e texto com até 600 caracteres")
    apply_parameter(db, cycle.id, "budget.criteria", clean, user_id=actor.id, ip=client_ip(request))
    db.commit()
    return {"target_year": cycle.fiscal_year, "items": clean}
