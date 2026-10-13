"""Central de dados (10/10/2026, "no Power BI eu tinha o Power Query para controlar"): o que monta cada número do
sistema — fontes carregadas, etapas do ETL por layout, parâmetros do ciclo e o histórico de quem mexeu em quê.

Só leitura e montagem de visões; a edição de parâmetro reusa `cycles.apply_parameter` (mesma validação e auditoria)."""

from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import seed_data as S
from app.domain.rules.common import money
from app.imports.parsers import capex_template, financial, master, opex_template, people
from app.models import (
    Account,
    ActualEntry,
    AuditLog,
    BudgetLine,
    BudgetSubmission,
    BudgetVersion,
    Company,
    CycleParameter,
    DatasetVersion,
    ImportBatch,
    ReferenceBudgetEntry,
    User,
)
from app.services import consolidation as cons
from app.services import opex as opex_svc
from app.services import personnel_premises as premises_svc

ZERO = Decimal("0")

# ------------------------------------------------------------------ etapas do ETL

# etapas comuns a toda importação (imports/pipeline.py), na ordem em que acontecem
PIPELINE = [
    ("Upload", "O arquivo é guardado com o hash SHA-256; o mesmo arquivo de novo só entra confirmando de propósito."),
    (
        "Detecção do layout",
        "Pelo tipo escolhido (ou pelo nome das abas) o sistema tenta os leitores do tipo, do mais específico para o "
        "mais genérico, e fica com o primeiro que reconhece os cabeçalhos.",
    ),
    ("Leitura", "Cada linha vira um registro; colunas são achadas pelos nomes aceitos (abaixo), em qualquer posição."),
    (
        "De-para com os cadastros",
        "Empresa (código ou nome), filial, centro de custo e conta são resolvidos contra os cadastros. O que não "
        "existe vira aviso na linha — o arquivo entra como veio e o erro aparece no sistema.",
    ),
    ("Comparação", "Prévia contra a base vigente: o que é novo, o que muda e o que sai, antes de confirmar."),
    ("Confirmação", "Só carrega depois da confirmação na tela de Importações (fica registrado quem confirmou)."),
    (
        "Carga",
        "Cria uma versão nova da base (empresa × ano); a anterior fica no histórico e deixa de valer. Substituir "
        "troca tudo do escopo; Mesclar troca só as combinações CC × conta presentes no arquivo.",
    ),
]

FIELD_LABELS = {
    "company": "Empresa",
    "branch": "Filial",
    "cost_center": "Centro de custo",
    "cost_center_name": "Nome do CC",
    "account": "Conta",
    "account_name": "Nome da conta",
    "year": "Ano",
    "period": "Mês",
    "posting_date": "Data de lançamento",
    "document": "Documento",
    "document_type": "Tipo de documento",
    "amount": "Valor",
    "vendor_code": "Fornecedor (código)",
    "vendor_name": "Fornecedor",
    "text": "Texto",
    "currency": "Moeda",
    "registration": "Matrícula",
    "name": "Nome",
    "position": "Cargo",
    "salary": "Salário",
    "contract": "Contrato",
    "package": "Pacote",
    "nature": "Natureza",
    "description": "Descrição",
}

# layout → descrição das etapas próprias (o que cada leitor faz além das etapas comuns)
LAYOUTS = [
    {
        "layout": "SAP_KSB1",
        "dataset": "ACTUAL",
        "title": "Realizado — exportação SAP KSB1",
        "read": "Partidas individuais de centro de custo: um lançamento por linha, com documento, fornecedor e texto. "
        "O mês vem da data de lançamento (ou de exercício/período). Pode vir de arquivo ou direto da view do SAP "
        "HANA (aba SAP da Central de dados, que monta a planilha e segue este mesmo caminho).",
        "load": "Uma versão por empresa × ano. O maior mês carregado vira o último mês fechado — a projeção só "
        "preenche os meses depois dele.",
        "aliases": "KSB1_ALIASES",
    },
    {
        "layout": "ACTUAL_LONG",
        "dataset": "ACTUAL",
        "title": "Realizado — base de controle (aba Realizado AAAA)",
        "read": "Uma linha por CC × conta × mês, só os meses até o corte. O CC pode vir escrito no texto.",
        "load": "Sempre Substituir, mas só OPEX e Pessoal: o CAPEX da versão anterior é mantido.",
        "aliases": "PROJECTION_ALIASES",
    },
    {
        "layout": "PROJECTION_BASE",
        "dataset": "PROJECTION",
        "title": "Projeção — meses sem realizado",
        "read": "Mesma base de controle; valem só os meses depois do último mês fechado do realizado.",
        "load": "Substitui a projeção anterior inteira; meses com realizado nunca são tocados. Entra marcada como "
        "projetada (no Painel aparece empilhada sobre o realizado).",
        "aliases": "PROJECTION_ALIASES",
    },
    {
        "layout": "WIDE_MONTHLY",
        "dataset": "ACTUAL / REFERENCE_BUDGET",
        "title": "Planilha mensal (colunas Jan…Dez)",
        "read": "Uma linha por CC × conta com os meses em colunas. Com blocos ORÇADO e REAL lado a lado, lê só o "
        "bloco do tipo importado.",
        "load": "Uma versão por empresa × ano (realizado) ou empresa × ano × cenário (orçado).",
        "aliases": "WIDE_ALIASES",
    },
    {
        "layout": "BUDGET_LONG",
        "dataset": "REFERENCE_BUDGET",
        "title": "Orçado de referência em linhas (aba Orçamento AAAA)",
        "read": "Uma linha por CC × conta × mês do orçado do ano anterior.",
        "load": "Uma versão por empresa × ano × cenário.",
        "aliases": "BUDGET_LONG_ALIASES",
    },
    {
        "layout": "CAPEX_ACUM",
        "dataset": "ACTUAL / PROJECTION",
        "title": "Capex Acum (realizado ou projeção de CAPEX)",
        "read": "Planilha acumulada de CAPEX por projeto, com os meses em colunas.",
        "load": "Entra na conta CAPEX do projeto; não mexe em OPEX/Pessoal.",
        "aliases": "CAPEX_ACUM_ALIASES",
    },
    {
        "layout": "TEMPLATE_OPEX",
        "dataset": "OPEX_TEMPLATE",
        "title": "Template OPEX do gestor",
        "read": "Todas as abas: cadastros (BD), realizado de referência, pacotes I a XII e viagens. Modelo sem a "
        "coluna CHAVE (REAM/NAVE) usa a empresa da base de despesas.",
        "load": "Por CC: substitui as linhas do orçamento do CC se ele ainda aceita alteração. Cadastros só entram "
        "se forem novos; realizado só se ainda não houver daquela empresa/ano.",
        "aliases": "BASE_ALIASES",
    },
    {
        "layout": "TEMPLATE_CAPEX",
        "dataset": "CAPEX_TEMPLATE",
        "title": "Template CAPEX do gestor",
        "read": "Cadastros, catálogo de ativos e solicitações (projetos e itens com cronograma).",
        "load": "Por CC: substitui os projetos CAPEX do CC se ele ainda aceita alteração.",
        "aliases": "ITEM_ALIASES",
    },
    {
        "layout": "TEMPLATE_QUADRO",
        "dataset": "EMPLOYEES",
        "title": "Quadro de funcionários",
        "read": "Aba Quadro Funcionários: pessoas, vagas e ações (promoção, desligamento, reajuste). Sem matrícula "
        "recebe uma provisória PROV-… com aviso.",
        "load": "Substitui o quadro dos CCs do arquivo; movimentações viram ações no mês informado.",
        "aliases": "EMPLOYEE_ALIASES",
    },
    {
        "layout": "TEMPLATE_BD",
        "dataset": "MASTER_DATA",
        "title": "Cadastros (aba BD)",
        "read": "Filiais, centros de custo e contas.",
        "load": "Cria o que é novo e atualiza o que mudou; campo vazio no arquivo não apaga o cadastro.",
        "aliases": "COST_CENTER_ALIASES",
    },
    {
        "layout": "TEMPLATE_PREMISSAS",
        "dataset": "MACRO_ASSUMPTIONS",
        "title": "Premissas macroeconômicas",
        "read": "Indicadores por ano (IPCA, câmbio…).",
        "load": "Substitui as premissas do arquivo.",
        "aliases": None,
    },
]

_ALIAS_MODULES = (financial, opex_template, capex_template, people, master)


def _aliases(name: str | None) -> list[dict]:
    if not name:
        return []
    for module in _ALIAS_MODULES:
        table = getattr(module, name, None)
        if isinstance(table, dict):
            return [
                {"field": FIELD_LABELS.get(k, k), "names": list(v) if isinstance(v, (list, tuple)) else [str(v)]}
                for k, v in table.items()
            ]
    return []


def steps(db: Session) -> dict:
    """Etapas comuns + cada layout com as colunas aceitas e a última importação que usou o layout."""
    last: dict[str, ImportBatch] = {}
    for b in db.scalars(select(ImportBatch).order_by(ImportBatch.id.desc()).limit(500)):
        if b.layout and b.layout not in last:
            last[b.layout] = b
    counts = dict(db.execute(select(ImportBatch.layout, func.count()).group_by(ImportBatch.layout)).all())
    layouts = []
    for spec in LAYOUTS:
        b = last.get(spec["layout"])
        layouts.append(
            {k: v for k, v in spec.items() if k != "aliases"}
            | {
                "columns": _aliases(spec["aliases"]),
                "imports": counts.get(spec["layout"], 0),
                "last_import": None
                if b is None
                else {
                    "id": b.id,
                    "file_name": b.file_name,
                    "status": b.status,
                    "created_at": b.created_at,
                    "total_rows": b.total_rows,
                    "valid_rows": b.valid_rows,
                    "warning_rows": b.warning_rows,
                    "error_rows": b.error_rows,
                    "duplicate_rows": b.duplicate_rows,
                },
            }
        )
    return {"pipeline": [{"title": t, "text": x} for t, x in PIPELINE], "layouts": layouts}


# ------------------------------------------------------------------ fontes e linhagem


def _user_names(db: Session) -> dict[int, str]:
    return dict(db.execute(select(User.id, User.name)).all())


def sources(db: Session) -> list[dict]:
    """Todas as versões de base (vigentes e substituídas) com o arquivo de origem, linhas e total."""
    names = _user_names(db)
    totals: dict[int, tuple] = {}
    for model in (ActualEntry, ReferenceBudgetEntry):
        cols = [model.dataset_version_id, func.sum(model.amount), func.min(model.period), func.max(model.period)]
        if model is ActualEntry:
            cols.append(func.sum(model.amount).filter(model.projected))
        for vid, total, first, last, *proj in db.execute(select(*cols).group_by(model.dataset_version_id)):
            totals[vid] = (total or ZERO, first, last, (proj[0] if proj else None) or ZERO)
    batches = {b.id: b for b in db.scalars(select(ImportBatch))}
    out = []
    for v in db.scalars(
        select(DatasetVersion).order_by(DatasetVersion.scope_key, DatasetVersion.version_number.desc())
    ):
        b = batches.get(v.import_batch_id) if v.import_batch_id else None
        total, first, last, projected = totals.get(v.id, (ZERO, None, None, ZERO))
        out.append(
            {
                "id": v.id,
                "dataset_type": v.dataset_type,
                "scope_key": v.scope_key,
                "version_number": v.version_number,
                "is_current": v.is_current,
                "source": v.source,
                "created_at": v.created_at,
                "created_by": names.get(v.created_by),
                "superseded_at": v.superseded_at,
                "row_count": v.row_count,
                "last_closed_period": v.last_closed_period,
                "first_period": first,
                "last_period": last,
                "total": str(money(total)),
                "projected": str(money(projected)),
                "batch": None
                if b is None
                else {"id": b.id, "file_name": b.file_name, "layout": b.layout, "mode": (b.options or {}).get("mode")},
            }
        )
    return out


def _fact_lineage(db: Session, model, label: str) -> list[dict]:
    """Por ano: total vigente, por tipo (OPEX/CAPEX/Pessoal) e por fonte (versão vigente de cada escopo)."""
    companies = dict(db.execute(select(Company.id, Company.code)).all())
    stmt = (
        select(
            model.fiscal_year,
            DatasetVersion.id,
            DatasetVersion.dataset_type,
            DatasetVersion.scope_key,
            model.company_id,
            Account.nature,
            func.sum(model.amount),
        )
        .join(DatasetVersion, DatasetVersion.id == model.dataset_version_id)
        .join(Account, Account.id == model.account_id)
        .where(DatasetVersion.is_current)
        .group_by(model.fiscal_year, DatasetVersion.id, model.company_id, Account.nature)
    )
    years: dict[int, dict] = {}
    for year, vid, dtype, scope, company_id, nature, amount in db.execute(stmt):
        y = years.setdefault(year, {"total": ZERO, "modules": defaultdict(lambda: ZERO), "sources": {}})
        amount = amount or ZERO
        module = cons.module_of_nature(nature) or "OUTROS"
        y["total"] += amount
        y["modules"][module] += amount
        src = y["sources"].setdefault(
            vid,
            {
                "version_id": vid,
                "dataset_type": dtype,
                "scope_key": scope,
                "company": companies.get(company_id),
                "total": ZERO,
                "modules": defaultdict(lambda: ZERO),
            },
        )
        src["total"] += amount
        src["modules"][module] += amount
    out = []
    for year in sorted(years, reverse=True):
        y = years[year]
        out.append(
            {
                "key": f"{label}:{year}",
                "title": f"{'Realizado' if model is ActualEntry else 'Orçado'} {year}",
                "year": year,
                "total": str(money(y["total"])),
                "modules": {k: str(money(v)) for k, v in y["modules"].items()},
                "sources": [
                    s
                    | {"total": str(money(s["total"])), "modules": {k: str(money(v)) for k, v in s["modules"].items()}}
                    for s in sorted(y["sources"].values(), key=lambda s: (s["company"] or "", s["dataset_type"]))
                ],
            }
        )
    return out


PERSONNEL_KIND_LABELS = {
    "salary": "Salários (com dissídio)",
    "charges": "Encargos e benefícios (multiplicador, rateado)",
    "severance": "Verbas rescisórias",
    "bonus": "Abono anual do CLT",
    "cc_bonus": "Bônus CLT por CC",
}


def _budget_lineage(db: Session) -> dict | None:
    """Orçamento do ciclo: componentes da versão aberta (o que o gestor lançou × o que o sistema calcula) e o total
    de cada versão (congeladas vêm da foto)."""
    try:
        ctx = opex_svc.context(db)
    except opex_svc.OpexError:
        return None
    opex_lines = db.scalar(
        select(func.coalesce(func.sum(BudgetLine.total_amount), 0))
        .join(BudgetSubmission, BudgetSubmission.id == BudgetLine.submission_id)
        .where(BudgetSubmission.version_id == ctx.version.id, BudgetSubmission.module == "OPEX")
    )
    accounts = list(db.scalars(select(Account)))
    pj = sum((a for *_x, a in cons.pj_budget_amounts(db, ctx, None, accounts, {})), ZERO)
    rows = cons.rows_for(db, ctx, ctx.version)
    capex_sched = sum((sum(r.values, ZERO) for r in rows if r.module == "CAPEX"), ZERO)
    capex_unsched = sum((r.unscheduled for r in rows if r.module == "CAPEX"), ZERO)
    pers = premises_svc.personnel_breakdown(db, ctx)
    components = [
        {
            "module": "OPEX",
            "label": "Linhas lançadas pelos gestores (templates e telas)",
            "total": opex_lines,
            "origin": "gestor",
        },
        {
            "module": "OPEX",
            "label": f"Contratos PJ (conta {ctx.params.get('pj.budget_account', '')})",
            "total": pj,
            "origin": "calculado",
        },
        {"module": "CAPEX", "label": "Itens com cronograma mensal", "total": capex_sched, "origin": "gestor"},
        {
            "module": "CAPEX",
            "label": "Itens sem cronograma (só no total do ano)",
            "total": capex_unsched,
            "origin": "gestor",
        },
    ] + [
        {"module": "PERSONNEL", "label": PERSONNEL_KIND_LABELS.get(k, k), "total": v, "origin": "calculado"}
        for k, v in sorted(
            pers.by_kind.items(),
            key=lambda kv: list(PERSONNEL_KIND_LABELS).index(kv[0]) if kv[0] in PERSONNEL_KIND_LABELS else 99,
        )
    ]
    versions = []
    for v in db.scalars(
        select(BudgetVersion)
        .where(BudgetVersion.cycle_id == ctx.cycle.id)
        .order_by(BudgetVersion.major, BudgetVersion.minor)
    ):
        vrows = rows if v.id == ctx.version.id else cons.rows_for(db, cons.version_context(ctx, v), v)
        by_module: dict[str, Decimal] = defaultdict(lambda: ZERO)
        for r in vrows:
            by_module[r.module] += r.total
        versions.append(
            {
                "id": v.id,
                "label": v.label,
                "status": v.status,
                "frozen_at": v.frozen_at,
                "total": str(money(sum(by_module.values(), ZERO))),
                "modules": {k: str(money(x)) for k, x in by_module.items()},
            }
        )
    return {
        "key": f"BUDGET:{ctx.target_year}",
        "title": f"Orçamento {ctx.target_year} — {ctx.version.label}",
        "year": ctx.target_year,
        "frozen": ctx.frozen,
        "total": str(money(sum((r.total for r in rows), ZERO))),
        "components": [c | {"total": str(money(Decimal(c["total"] or 0)))} for c in components],
        "versions": versions,
    }


def lineage(db: Session) -> dict:
    return {
        "budget": _budget_lineage(db),
        "actual": _fact_lineage(db, ActualEntry, "ACTUAL"),
        "reference": _fact_lineage(db, ReferenceBudgetEntry, "REFERENCE"),
    }


# ------------------------------------------------------------------ parâmetros

GROUPS = [
    ("personnel.", "Pessoal"),
    ("pj.", "Contratos PJ"),
    ("alert.", "Alertas da análise"),
    ("review.", "Envio e validação"),
    ("travel.", "Viagens"),
    ("capex.", "CAPEX"),
    ("budget.", "Painel"),
    ("sap.", "SAP"),
]

USED_IN = {
    "personnel.salary_adjustment_pct": "Pessoal: salário com dissídio a partir do mês da data-base",
    "personnel.adjustment_month": "Pessoal: mês do dissídio",
    "personnel.salary_account": "Consolidação e carga SAP: conta do salário",
    "personnel.charges_account": "Consolidação: conta dos encargos quando o rateio está vazio",
    "personnel.severance_account": "Consolidação: conta das verbas rescisórias",
    "personnel.annual_bonus_clt": "Pessoal: abono por CLT, diluído em 12 parcelas",
    "personnel.annual_bonus_account": "Consolidação: conta do abono",
    "personnel.bonus_by_cc": "Pessoal: bônus por CC, diluído jan–dez",
    "personnel.bonus_account": "Consolidação: conta do bônus por CC",
    "personnel.charges_split": "Consolidação: rateio dos encargos entre contas (Premissas de pessoal)",
    "pj.budget_account": "Orçamento: conta dos contratos PJ (total por CC)",
    "pj.adjustment_pct": "Orçamento: reajuste dos contratos PJ",
    "pj.adjustment_month": "Orçamento: mês do reajuste PJ",
    "alert.growth_pct": "Análise e apontamentos: alerta de crescimento",
    "alert.reduction_pct": "Análise e apontamentos: alerta de redução",
    "alert.history_band_pct": "Análise: banda em torno da média histórica",
    "alert.min_relevant_amount": "Apontamentos: valor mínimo para conta sem orçamento",
    "review.justification_blocks": "Envio do orçamento: bloqueia sem justificativa",
    "travel.one_way_factor": "Viagens: passagem só de ida",
    "travel.route_estimates": "Viagens: passagem estimada por rota",
    "travel.flat_estimate": "Viagens: passagem sem rota cadastrada",
    "capex.min_unit_value": "CAPEX: enquadramento por valor unitário",
    "capex.min_useful_life_months": "CAPEX: enquadramento por vida útil",
    "budget.criteria": "Painel: card Critérios do orçamento",
    "sap.ksb1_columns": "SAP HANA: colunas da view do KSB1 quando a detecção automática erra (aba SAP)",
}


def _group(key: str) -> str:
    return next((label for prefix, label in GROUPS if key.startswith(prefix)), "Outros")


def _kind(key: str, value) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (dict, list)):
        return "json"
    if key.endswith("_account"):
        return "account"
    if key.endswith("_pct") or key.endswith("_factor"):
        return "percent" if key.endswith("_pct") else "number"
    if key.endswith("_month"):
        return "month"
    if isinstance(value, (int, float)):
        return "number"
    return "text"


def parameters(db: Session) -> dict:
    ctx = opex_svc.context(db)
    names = _user_names(db)
    accounts = dict(db.execute(select(Account.code, Account.name)).all())
    params = {p.key: p for p in db.scalars(select(CycleParameter).where(CycleParameter.cycle_id == ctx.cycle.id))}
    logs: dict[str, list[AuditLog]] = defaultdict(list)
    prefix = f"{ctx.cycle.id}:"
    for log in db.scalars(
        select(AuditLog)
        .where(AuditLog.entity_type == "cycle_parameter", AuditLog.entity_id.like(f"{prefix}%"))
        .order_by(AuditLog.id.desc())
    ):
        logs[log.entity_id[len(prefix) :]].append(log)
    keys = list(dict.fromkeys([*S.CYCLE_PARAMETERS, *params]))
    out = []
    for key in keys:
        p = params.get(key)
        default, seed_desc = S.CYCLE_PARAMETERS.get(key, (None, None))
        value = p.value if p is not None else default
        history = logs.get(key, [])
        out.append(
            {
                "key": key,
                "group": _group(key),
                "kind": _kind(key, value if value is not None else default),
                "value": value,
                "default": default,
                "is_default": value == default,
                "description": (p.description if p is not None and p.description else seed_desc) or "",
                "used_in": USED_IN.get(key),
                "account_name": accounts.get(str(value)) if key.endswith("_account") and value else None,
                "changes": len(history),
                "last_change": None
                if not history
                else {
                    "at": history[0].occurred_at,
                    "user": names.get(history[0].user_id),
                    "reason": history[0].reason,
                    "before": (history[0].before or {}).get("value"),
                },
            }
        )
    return {"cycle_id": ctx.cycle.id, "cycle": ctx.cycle.name, "items": out, "groups": [g for _p, g in GROUPS]}


# ------------------------------------------------------------------ histórico

HISTORY_ACTIONS = {
    "SET_PARAMETER": "Parâmetro alterado",
    "UPLOAD": "Arquivo enviado",
    "CONFIRM": "Importação confirmada",
    "LOAD": "Base carregada",
    "DELETE_DATASET": "Base excluída",
    "DELETE_BUDGET": "Orçamento excluído",
    "DELETE_ALL_DATA": "Todas as bases excluídas",
    "VERSION_FREEZE": "Versão congelada",
    "VERSION_REVISE": "Revisão aberta",
    "SET_BASELINE": "Cenário base definido",
    "CORRECTED": "Correção feita pelo sistema",
    "IMPORT_BUDGET": "Orçamento importado",
    "IMPORT_CAPEX": "CAPEX importado",
    "REJECT": "Importação descartada",
}


def history(db: Session, limit: int = 200) -> list[dict]:
    names = _user_names(db)
    return [
        {
            "id": log.id,
            "at": log.occurred_at,
            "user": names.get(log.user_id),
            "action": log.action,
            "action_label": HISTORY_ACTIONS.get(log.action, log.action),
            "entity_type": log.entity_type,
            "entity_id": log.entity_id,
            "reason": log.reason,
            "before": log.before,
            "after": log.after,
        }
        for log in db.scalars(
            select(AuditLog).where(AuditLog.action.in_(HISTORY_ACTIONS)).order_by(AuditLog.id.desc()).limit(limit)
        )
    ]
