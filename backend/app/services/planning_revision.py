"""Revisão do Planejamento (10/11/2026): carrega na versão em elaboração o OPEX que o setor de Planejamento devolveu
(arquivo "Prévia Orçamento OPEX", aba CONSOLIDADO: empresa, nome do CC, nome da conta, pacote e os 12 meses do ano do
ciclo) e mostra antes × depois para comparar com a revisão anterior (congelada).

- O arquivo vem como veio ("não mexa no arquivo deles"): CC e conta casam pelo nome (sem códigos no arquivo); o que não
  casar vira erro na prévia e nada é gravado.
- Cada CC × conta × mês passa a valer o do arquivo. As linhas existentes são preservadas (descrição, viagem,
  justificativa) e têm o valor do mês ajustado na proporção; mês sem linha recebe uma linha "Ajuste da revisão do
  Planejamento". Lançamento negativo não existe no OPEX, então reduzir é sempre ajustar as linhas que já existem.
- O que está no sistema e não veio no arquivo: `absent="zero"` (padrão, igual ao arquivo) zera e marca a linha;
  `absent="keep"` mantém. Os dois casos viram Apontamentos para a Controladoria analisar ("pode ser que eles considerem
  em outra conta").
- Contratos PJ (conta `pj.budget_account`) são calculados dos contratos, não são linhas: ficam como estão e, se o
  arquivo não os trouxer, viram apontamento.
- Gravar exige a mesma prévia (`digest`): se o arquivo, a opção ou o orçamento mudaram desde a prévia, nada é gravado.
"""

from __future__ import annotations

import hashlib
import io
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

import openpyxl
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.rules.common import money
from app.models import Account, AuditLog, BudgetLine, BudgetLineValue, BudgetSubmission, Company, CostCenter
from app.services import audit
from app.services import opex as opex_svc
from app.services.opex import Context

ZERO = Decimal("0")
CENT = Decimal("0.01")
ACTION = "PLANNING_REVISION"
SOURCE = "PLANNING_REVISION"
MONTHS = ["JAN", "FEV", "MAR", "ABR", "MAI", "JUN", "JUL", "AGO", "SET", "OUT", "NOV", "DEZ"]
STOP = {"DE", "DA", "DO", "DAS", "DOS", "E", "A", "O"}
HEAD_COMPANY = "EMPRESA"
HEAD_CC = "DENOMINACAO DO CENTRO DE CUSTOS"
HEAD_ACCOUNT = "DENOMINACAO DA CONTA DO RAZAO"
HEAD_PACKAGE = "PACOTES ORCAMENTO"
HEAD_VP = "VICE PRESIDENTE"


class PlanningError(Exception):
    """Arquivo ou versão inválidos (mensagem pronta para o usuário)."""


def norm(text) -> str:
    t = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[.,;/()\-]+", " ", t).split())


def _tokens(text: str) -> list[str]:
    return [t for t in norm(text).split() if t not in STOP]


def _prefix_cover(a: list[str], b: list[str]) -> bool:
    """Cada palavra de `a` é início de alguma palavra de `b` (ex.: PLANEJ → PLANEJAMENTO)."""
    return bool(a) and all(any(y.startswith(x) or x.startswith(y) for y in b) for x in a)


def _year_of(value) -> int | None:
    if isinstance(value, (datetime, date)):
        return value.year
    m = re.fullmatch(r"\s*\d{1,2}/(\d{1,2})/(\d{4})\s*", str(value or ""))
    return int(m.group(2)) if m else None


def _month_of(value) -> int | None:
    if isinstance(value, (datetime, date)):
        return value.month
    m = re.fullmatch(r"\s*\d{1,2}/(\d{1,2})/(\d{4})\s*", str(value or ""))
    return int(m.group(1)) if m else None


def _amount(value) -> Decimal:
    if value is None or value == "":
        return ZERO
    try:
        return Decimal(str(value)).quantize(CENT)
    except InvalidOperation:
        raise PlanningError(f"Valor inválido no arquivo: {value!r}") from None


@dataclass
class FileRow:
    row: int
    company: str
    cost_center: str
    account: str
    package: str | None
    vp: str | None
    values: list[Decimal]


@dataclass
class Parsed:
    rows: list[FileRow]
    sheet: str
    ignored_zero: int = 0
    vps: dict[str, Decimal] = field(default_factory=dict)


def parse(content: bytes, year: int) -> Parsed:
    """Aba com Empresa, Denominação do Centro de Custos, Denominação da Conta do Razão e os meses (01/01/2027…)."""
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 — qualquer arquivo que não seja Excel
        raise PlanningError("Não consegui ler o arquivo: envie o Excel (.xlsx) do Planejamento") from exc
    for ws in wb.worksheets:
        it = ws.iter_rows(values_only=True)
        for number, header in enumerate(it, start=1):
            if number > 15:
                break
            names = [norm(h) for h in (header or ())]
            if HEAD_CC in names and HEAD_ACCOUNT in names and HEAD_COMPANY in names:
                cols = {n: i for i, n in enumerate(names) if n}
                months = {i: _month_of(h) for i, h in enumerate(header) if _year_of(h) == year}
                if len(months) != 12 or sorted(months.values()) != list(range(1, 13)):
                    raise PlanningError(f"A aba {ws.title} não tem os 12 meses de {year} (01/01/{year} … 01/12/{year})")
                out = Parsed([], ws.title)
                for n, r in enumerate(it, start=number + 1):
                    if not r or not any(v is not None for v in r):
                        continue

                    def cell(key, r=r, cols=cols):
                        i = cols.get(key)
                        return r[i] if i is not None and i < len(r) else None

                    values = [ZERO] * 12
                    for i, m in months.items():
                        values[m - 1] = _amount(r[i] if i < len(r) else None)
                    if any(v < 0 for v in values):
                        raise PlanningError(f"Linha {n}: valor negativo (o OPEX não aceita lançamento negativo)")
                    vp = str(cell(HEAD_VP) or "").strip() or None
                    if not any(values):
                        out.ignored_zero += 1
                        continue
                    out.vps[vp or "—"] = out.vps.get(vp or "—", ZERO) + sum(values, ZERO)
                    out.rows.append(
                        FileRow(
                            row=n,
                            company=str(cell(HEAD_COMPANY) or "").strip(),
                            cost_center=str(cell(HEAD_CC) or "").strip(),
                            account=str(cell(HEAD_ACCOUNT) or "").strip(),
                            package=str(cell(HEAD_PACKAGE)).strip() if cell(HEAD_PACKAGE) else None,
                            vp=vp,
                            values=values,
                        )
                    )
                return out
    raise PlanningError(
        "Não encontrei a aba do Planejamento (colunas Empresa, Denominação do Centro de Custos, "
        "Denominação da Conta do Razão e os meses)"
    )


# ------------------------------------------------------------------ situação atual da versão


def _submissions(db: Session, ctx: Context) -> dict[int, BudgetSubmission]:
    return {
        s.cost_center_id: s
        for s in db.scalars(
            select(BudgetSubmission).where(
                BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.module == "OPEX"
            )
        )
    }


def _current(db: Session, subs: dict[int, BudgetSubmission]) -> dict[tuple[int, int], list[BudgetLine]]:
    out: dict[tuple[int, int], list[BudgetLine]] = defaultdict(list)
    ids = {s.id: cc for cc, s in subs.items()}
    for line in db.scalars(select(BudgetLine).where(BudgetLine.submission_id.in_(ids or {-1})).order_by(BudgetLine.id)):
        out[(line.cost_center_id, line.account_id)].append(line)
    return out


def _month_values(lines: list[BudgetLine]) -> list[Decimal]:
    out = [ZERO] * 12
    for line in lines:
        for v in line.values:
            if 1 <= v.month <= 12:
                out[v.month - 1] += v.amount or ZERO
    return out


def _pj_account_id(db: Session, ctx: Context) -> int | None:
    raw = ctx.params.get("pj.budget_account", 6010201016)
    return db.scalar(select(Account.id).where(Account.code == str(raw).strip()))


# ------------------------------------------------------------------ casar nomes


def _companies(db: Session) -> dict[str, Company]:
    out = {}
    for c in db.scalars(select(Company)):
        for key in (c.code, c.name, c.short_name):
            if key:
                out[norm(key)] = c
    return out


def _match_cc(name: str, candidates: list[CostCenter], in_scope: set[int]) -> tuple[CostCenter | None, str | None]:
    exact = [c for c in candidates if norm(c.name) == norm(name)]
    if len(exact) > 1:
        exact = [c for c in exact if c.id in in_scope] or exact
    if len(exact) == 1:
        return exact[0], None
    if len(exact) > 1:
        return None, f"mais de um CC com o nome {name!r}: {', '.join(c.code for c in exact)}"
    t = _tokens(name)
    fuzzy = [c for c in candidates if _prefix_cover(_tokens(c.name), t) or _prefix_cover(t, _tokens(c.name))]
    if len(fuzzy) > 1:
        fuzzy = [c for c in fuzzy if c.id in in_scope] or fuzzy
    if len(fuzzy) == 1:
        return fuzzy[0], None
    if fuzzy:
        return None, f"nome {name!r} parecido com mais de um CC: {', '.join(f'{c.code} {c.name}' for c in fuzzy)}"
    return None, f"nenhum CC com o nome {name!r}"


@dataclass
class Plan:
    targets: dict[tuple[int, int], list[Decimal]]
    sources: dict[tuple[int, int], list[int]]
    errors: list[dict]
    matched: dict[tuple[str, str], dict]
    parsed: Parsed


def build(db: Session, ctx: Context, parsed: Parsed) -> Plan:
    subs = _submissions(db, ctx)
    current = _current(db, subs)
    companies = _companies(db)
    ccs_by_company: dict[int, list[CostCenter]] = defaultdict(list)
    for cc in db.scalars(select(CostCenter)):
        ccs_by_company[cc.company_id].append(cc)
    accounts_by_name: dict[str, list[Account]] = defaultdict(list)
    for acc in db.scalars(select(Account)):
        accounts_by_name[norm(acc.name)].append(acc)
    used_accounts = {acc for _cc, acc in current}
    targets: dict[tuple[int, int], list[Decimal]] = defaultdict(lambda: [ZERO] * 12)
    sources: dict[tuple[int, int], list[int]] = defaultdict(list)
    errors: list[dict] = []
    matched: dict[tuple[str, str], dict] = {}
    cc_cache: dict[tuple[str, str], tuple] = {}
    for r in parsed.rows:
        company = companies.get(norm(r.company))
        if company is None:
            errors.append({"row": r.row, "message": f"Empresa {r.company!r} não cadastrada"})
            continue
        ck = (company.code, norm(r.cost_center))
        if ck not in cc_cache:
            cc_cache[ck] = _match_cc(r.cost_center, ccs_by_company[company.id], set(subs))
        cc, problem = cc_cache[ck]
        if cc is None:
            errors.append({"row": r.row, "message": f"{company.short_name or company.code}: {problem}"})
            continue
        matched[ck] = {"file": r.cost_center, "code": cc.code, "name": cc.name, "company": company.code}
        options = accounts_by_name.get(norm(r.account), [])
        if len(options) > 1:
            options = (
                [a for a in options if (cc.id, a.id) in current]
                or [a for a in options if a.id in used_accounts]
                or options
            )
        if len(options) != 1:
            text = "nenhuma conta" if not options else "mais de uma conta (" + ", ".join(a.code for a in options) + ")"
            errors.append({"row": r.row, "message": f"Conta {r.account!r}: {text} com esse nome"})
            continue
        key = (cc.id, options[0].id)
        for m in range(12):
            targets[key][m] += r.values[m]
        sources[key].append(r.row)
    return Plan(dict(targets), dict(sources), errors, matched, parsed)


# ------------------------------------------------------------------ prévia


def _digest(content: bytes, absent: str, version_id: int, current: dict) -> str:
    h = hashlib.sha256(content)
    h.update(f"|{absent}|{version_id}|".encode())
    for key in sorted(current):
        for line in current[key]:
            h.update(f"{line.id}:{','.join(str(v.amount) for v in line.values)};".encode())
    return h.hexdigest()


def preview(db: Session, ctx: Context, content: bytes, absent: str = "zero") -> dict:
    if ctx.frozen:
        raise PlanningError(f"A {ctx.version.label} está congelada: abra uma revisão antes de carregar o Planejamento")
    if absent not in ("zero", "keep"):
        raise PlanningError("Opção inválida para o que não veio no arquivo")
    parsed = parse(content, ctx.target_year)
    plan = build(db, ctx, parsed)
    subs = _submissions(db, ctx)
    current = _current(db, subs)
    pj_account = _pj_account_id(db, ctx)
    ccs = {c.id: c for c in db.scalars(select(CostCenter))}
    accounts = {a.id: a for a in db.scalars(select(Account))}

    keys = set(plan.targets) | {k for k, lines in current.items() if any(_month_values(lines))}
    cells, zeroed, kept_absent, changes = [], [], [], {}
    for key in sorted(keys):
        before = _month_values(current.get(key, []))
        in_file = key in plan.targets
        after = plan.targets[key] if in_file else ([ZERO] * 12 if absent == "zero" else before)
        if not in_file and any(before):
            item = {
                "cost_center_id": key[0],
                "cost_center": f"{ccs[key[0]].code} · {ccs[key[0]].name}",
                "account": accounts[key[1]].code,
                "account_name": accounts[key[1]].name,
                "total": str(money(sum(before, ZERO))),
            }
            (zeroed if absent == "zero" else kept_absent).append(item)
        if after == before:
            continue
        changes[key] = (before, after)
        for m in range(12):
            if before[m] != after[m]:
                cells.append(
                    {
                        "cost_center": f"{ccs[key[0]].code} · {ccs[key[0]].name}",
                        "account": accounts[key[1]].code,
                        "account_name": accounts[key[1]].name,
                        "month": m + 1,
                        "before": str(before[m]),
                        "after": str(after[m]),
                        "difference": str(after[m] - before[m]),
                    }
                )

    # contratos PJ: calculados dos contratos (não são linhas); o arquivo não muda isso
    pj_absent = []
    if pj_account:
        for cc_id in sorted(subs):
            pj = opex_svc.pj_monthly_by_account(db, ctx, cc_id).get(pj_account)
            total = sum(pj or [], ZERO)
            if total and not any(plan.targets.get((cc_id, pj_account), [])):
                pj_absent.append(
                    {
                        "cost_center_id": cc_id,
                        "cost_center": f"{ccs[cc_id].code} · {ccs[cc_id].name}",
                        "total": str(money(total)),
                    }
                )

    def grouped(fn):
        out: dict = defaultdict(lambda: [ZERO, ZERO])
        for key in keys:
            before = _month_values(current.get(key, []))
            b, a = changes.get(key, (before, before))
            g = fn(key)
            out[g][0] += sum(b, ZERO)
            out[g][1] += sum(a, ZERO)
        return out

    by_cc = grouped(lambda k: k[0])
    by_account = grouped(lambda k: k[1])
    total_before = sum((v[0] for v in by_cc.values()), ZERO)
    total_after = sum((v[1] for v in by_cc.values()), ZERO)
    monthly_before = [ZERO] * 12
    monthly_after = [ZERO] * 12
    for key in keys:
        before = _month_values(current.get(key, []))
        b, a = changes.get(key, (before, before))
        for m in range(12):
            monthly_before[m] += b[m]
            monthly_after[m] += a[m]

    def rows_of(groups, label):
        return sorted(
            (
                label(k) | {"before": str(money(b)), "after": str(money(a)), "difference": str(money(a - b))}
                for k, (b, a) in groups.items()
                if b or a
            ),
            key=lambda r: Decimal(r["difference"]),
        )

    file_total = sum((sum(r.values, ZERO) for r in parsed.rows), ZERO)
    return {
        "version": {"id": ctx.version.id, "label": ctx.version.label},
        "year": ctx.target_year,
        "sheet": parsed.sheet,
        "file_rows": len(parsed.rows),
        "ignored_zero_rows": parsed.ignored_zero,
        "file_total": str(money(file_total)),
        "vice_presidencies": {k: str(money(v)) for k, v in parsed.vps.items()},
        "absent": absent,
        "errors": plan.errors,
        "matched_cost_centers": sorted(plan.matched.values(), key=lambda m: (m["company"], m["code"])),
        "totals": {
            "before": str(money(total_before)),
            "after": str(money(total_after)),
            "difference": str(money(total_after - total_before)),
        },
        "monthly": [
            {"month": m + 1, "before": str(money(monthly_before[m])), "after": str(money(monthly_after[m]))}
            for m in range(12)
        ],
        "by_cost_center": rows_of(by_cc, lambda k: {"cost_center": f"{ccs[k].code} · {ccs[k].name}"}),
        "by_account": rows_of(by_account, lambda k: {"account": accounts[k].code, "name": accounts[k].name}),
        "cells": cells,
        "changed_keys": len(changes),
        "zeroed": zeroed,
        "kept_absent": kept_absent,
        "pj_absent": pj_absent,
        "pj_note": "Contratos PJ entram calculados dos contratos (conta Provisão de Serviços): a carga não os altera",
        "digest": _digest(content, absent, ctx.version.id, current),
    }


# ------------------------------------------------------------------ gravar


def _scale(lines: list[BudgetLine], month: int, target: Decimal) -> list[BudgetLine]:
    """Ajusta o mês das linhas para somar `target`, na proporção de cada uma; o centavo que sobra vai na maior.
    Devolve as linhas alteradas."""
    vals = {line.id: next((v for v in line.values if v.month == month), None) for line in lines}
    current = sum((v.amount for v in vals.values() if v is not None), ZERO)
    if current == target or current == 0:
        return []
    changed = []
    acc = ZERO
    biggest = max(lines, key=lambda line: vals[line.id].amount if vals[line.id] else ZERO)
    for line in lines:
        v = vals[line.id]
        if v is None or not v.amount:
            continue
        new = (v.amount * target / current).quantize(CENT)
        acc += new
        v.amount = new
        changed.append(line)
    residual = target - acc
    if residual and vals[biggest.id] is not None:
        vals[biggest.id].amount += residual
    return changed


def apply(
    db: Session,
    ctx: Context,
    content: bytes,
    *,
    absent: str,
    digest: str,
    reason: str,
    file_name: str | None,
    user_id: int | None,
    ip: str | None = None,
) -> dict:
    if not (reason or "").strip():
        raise PlanningError("Informe o motivo (vai para a auditoria)")
    result = preview(db, ctx, content, absent)
    if result["errors"]:
        raise PlanningError("O arquivo tem nomes que não casaram com o cadastro: corrija antes de gravar")
    if result["digest"] != digest:
        raise PlanningError("O arquivo, a opção ou o orçamento mudaram desde a prévia: gere a prévia de novo")
    plan = build(db, ctx, parse(content, ctx.target_year))
    subs = _submissions(db, ctx)
    current = _current(db, subs)
    label = ctx.version.label
    touched: set[int] = set()
    stats: dict = defaultdict(int)
    keys = set(plan.targets) | {k for k, lines in current.items() if any(_month_values(lines))}
    for key in sorted(keys):
        lines = current.get(key, [])
        before = _month_values(lines)
        in_file = key in plan.targets
        if not in_file and absent == "keep":
            continue
        target = plan.targets.get(key, [ZERO] * 12)
        if target == before:
            continue
        cc_id, account_id = key
        sub = subs.get(cc_id) or opex_svc.get_submission(db, ctx, cc_id)
        subs[cc_id] = sub
        snapshots = {line.id: opex_svc.line_out(line) for line in lines}
        changed: dict[int, BudgetLine] = {}
        adjust: BudgetLine | None = None
        for m in range(12):
            if target[m] == before[m]:
                continue
            if before[m]:
                for line in _scale(lines, m + 1, target[m]):
                    changed[line.id] = line
            else:
                if adjust is None:
                    cc = db.get(CostCenter, cc_id)
                    acc = db.get(Account, account_id)
                    adjust = BudgetLine(
                        submission_id=sub.id,
                        company_id=cc.company_id,
                        cost_center_id=cc_id,
                        account_id=account_id,
                        package_id=acc.package_id,
                        line_type="GENERIC",
                        description="Ajuste da revisão do Planejamento",
                        justification=f"Valor do arquivo do Planejamento ({label})",
                        attributes={"source": SOURCE, "version": label, "rows": plan.sources.get(key, [])},
                        created_by=user_id,
                        updated_by=user_id,
                    )
                    db.add(adjust)
                adjust.values.append(BudgetLineValue(month=m + 1, amount=target[m]))
        for line in changed.values():
            line.total_amount = money(sum((v.amount for v in line.values), ZERO))
            attrs = dict(line.attributes or {})
            info = {"version": label, "before_total": snapshots[line.id]["total"]}
            if not any(target):
                info["zeroed"] = True
                info["absent"] = not in_file
            attrs["planning_revision"] = info
            line.attributes = attrs
            line.updated_by = user_id
            audit.record(
                db,
                user_id=user_id,
                action="UPDATE",
                entity_type="budget_line",
                entity_id=line.id,
                before=snapshots[line.id],
                after=opex_svc.line_out(line),
                reason=f"Revisão do Planejamento ({label}): {reason.strip()}",
                ip=ip,
            )
            stats["lines_adjusted"] += 1
        if adjust is not None:
            adjust.total_amount = money(sum((v.amount for v in adjust.values), ZERO))
            db.flush()
            audit.record(
                db,
                user_id=user_id,
                action="CREATE",
                entity_type="budget_line",
                entity_id=adjust.id,
                after=opex_svc.line_out(adjust),
                reason=f"Revisão do Planejamento ({label}): {reason.strip()}",
                ip=ip,
            )
            stats["lines_created"] += 1
        touched.add(cc_id)
    for cc_id in touched | {p["cost_center_id"] for p in result["pj_absent"]}:
        sub = subs[cc_id]
        if sub.status not in ("IN_PROGRESS", "ADJUSTMENT_REQUESTED"):  # reabre para os apontamentos aparecerem
            opex_svc.log_event(
                db,
                sub,
                action="planning_revision",
                to_status="IN_PROGRESS",
                user_id=user_id,
                comment=f"Revisão do Planejamento: {reason.strip()}",
                version_label=label,
            )
    db.flush()
    summary = {
        "file": file_name,
        "absent": absent,
        "totals": result["totals"],
        "file_total": result["file_total"],
        "cost_centers": len(touched),
        "zeroed": result["zeroed"],
        "kept_absent": result["kept_absent"],
        "pj_absent": result["pj_absent"],
    } | dict(stats)
    audit.record(
        db,
        user_id=user_id,
        action=ACTION,
        entity_type="budget_version",
        entity_id=ctx.version.id,
        after=summary,
        reason=reason.strip(),
        ip=ip,
    )
    db.commit()
    return summary


def last_revision(db: Session, version_id: int) -> dict | None:
    """Resumo da última carga do Planejamento nesta versão (para os apontamentos)."""
    log = db.scalar(
        select(AuditLog)
        .where(
            AuditLog.action == ACTION, AuditLog.entity_type == "budget_version", AuditLog.entity_id == str(version_id)
        )
        .order_by(AuditLog.id.desc())
        .limit(1)
    )
    return log.after if log else None
