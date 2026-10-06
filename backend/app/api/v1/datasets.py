"""Gestão das bases carregadas: inventário por escopo e exclusão (ex.: para reimportar do zero)."""

from collections import defaultdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.core.deps import client_ip, require_roles
from app.db import get_db
from app.models import (
    DatasetVersion,
    Employee,
    ImportBatch,
    ImportRow,
    PersonnelMovement,
    User,
)
from app.models.base import Role
from app.services import audit

router = APIRouter(prefix="/datasets", tags=["bases carregadas"])

DELETABLE = {"ACTUAL", "REFERENCE_BUDGET", "MACRO_ASSUMPTIONS", "EMPLOYEES"}
CONFIRM_WORD = "EXCLUIR"


class DatasetOut(BaseModel):
    dataset_type: str
    scope_key: str
    current_version: int | None
    versions: int
    rows: int
    last_loaded_at: datetime | None
    last_file_name: str | None
    deletable: bool


@router.get("", response_model=list[DatasetOut], summary="Bases carregadas por escopo (empresa × ano)")
def list_datasets(db: Session = Depends(get_db), _: User = Depends(require_roles(Role.CONTROLLER))):
    versions = db.scalars(select(DatasetVersion).order_by(DatasetVersion.version_number)).all()
    batches = {
        b.id: b
        for b in db.scalars(
            select(ImportBatch).where(ImportBatch.id.in_({v.import_batch_id for v in versions if v.import_batch_id}))
        )
    }
    grouped: dict[tuple[str, str], list[DatasetVersion]] = defaultdict(list)
    for v in versions:
        grouped[(v.dataset_type, v.scope_key)].append(v)
    out = []
    for (dtype, scope), items in sorted(grouped.items()):
        current = next((v for v in items if v.is_current), None)
        last = max(items, key=lambda v: v.created_at)
        out.append(
            DatasetOut(
                dataset_type=dtype,
                scope_key=scope,
                current_version=current.version_number if current else None,
                versions=len(items),
                rows=current.row_count if current else 0,
                last_loaded_at=last.created_at,
                last_file_name=batches[last.import_batch_id].file_name if last.import_batch_id in batches else None,
                deletable=dtype in DELETABLE,
            )
        )
    return out


@router.delete("", summary="Excluir uma base (todas as versões do escopo) para reimportar do zero")
def delete_dataset(
    request: Request,
    dataset_type: str,
    scope_key: str | None = Query(None, description="Vazio = todos os escopos do tipo"),
    confirm: str = Query(..., description=f"Digite {CONFIRM_WORD} para confirmar"),
    reason: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    if confirm.strip().upper() != CONFIRM_WORD:
        raise HTTPException(422, f"Para excluir, digite {CONFIRM_WORD}")
    result = _delete_dataset(db, dataset_type, scope_key, actor, reason, client_ip(request))
    db.commit()
    return result


def _delete_dataset(
    db: Session, dataset_type: str, scope_key: str | None, actor: User, reason: str | None, ip: str | None
) -> dict:
    if dataset_type not in DELETABLE:
        raise HTTPException(
            409,
            "Cadastros não são excluídos por base: edite ou inative os registros em Cadastros."
            if dataset_type == "MASTER_DATA"
            else f"Tipo {dataset_type} não pode ser excluído",
        )
    stmt = select(DatasetVersion).where(DatasetVersion.dataset_type == dataset_type)
    if scope_key:
        stmt = stmt.where(DatasetVersion.scope_key == scope_key)
    versions = db.scalars(stmt).all()
    if not versions:
        raise HTTPException(404, "Nenhuma base encontrada para os filtros informados")
    version_ids = [v.id for v in versions]
    batch_ids = {v.import_batch_id for v in versions if v.import_batch_id}

    removed_employees = 0
    if dataset_type == "EMPLOYEES":
        emp_ids = select(Employee.id).where(Employee.dataset_version_id.in_(version_ids))
        # ações do ano (promoção, desligamento...) desses colaboradores saem junto com o quadro
        db.execute(delete(PersonnelMovement).where(PersonnelMovement.employee_id.in_(emp_ids)))
        removed_employees = db.execute(delete(Employee).where(Employee.dataset_version_id.in_(version_ids))).rowcount

    rows = sum(v.row_count for v in versions if v.is_current)
    scopes = sorted({v.scope_key for v in versions})
    db.execute(delete(DatasetVersion).where(DatasetVersion.id.in_(version_ids)))  # fatos saem em cascata
    if batch_ids:
        # lotes revertidos deixam de bloquear a reimportação do mesmo arquivo
        db.execute(update(ImportBatch).where(ImportBatch.id.in_(batch_ids)).values(status="REVERTED"))
        db.execute(delete(ImportRow).where(ImportRow.batch_id.in_(batch_ids)))
    result = {
        "dataset_type": dataset_type,
        "scopes": scopes,
        "versions_deleted": len(version_ids),
        "current_rows_deleted": rows,
        "employees_deleted": removed_employees,
        "imports_reverted": len(batch_ids),
    }
    audit.record(
        db,
        user_id=actor.id,
        action="DELETE_DATASET",
        entity_type="dataset",
        entity_id=scope_key or dataset_type,
        before=result,
        reason=reason,
        ip=ip,
    )
    return result


# ------------------------------------------------------------------ orçamento lançado (OPEX/CAPEX)

BUDGET_MODULES = ("OPEX", "CAPEX", "PERSONNEL")
TEMPLATE_TYPES = {"OPEX": "OPEX_TEMPLATE", "CAPEX": "CAPEX_TEMPLATE", "PERSONNEL": None}


def _working_version(db: Session):
    from app.models import BudgetCycle, BudgetVersion

    cycle = db.scalar(select(BudgetCycle).order_by(BudgetCycle.fiscal_year.desc()))
    if cycle is None:
        return None, None
    version = db.scalar(
        select(BudgetVersion)
        .where(BudgetVersion.cycle_id == cycle.id, BudgetVersion.status == "WORKING")
        .order_by(BudgetVersion.major.desc(), BudgetVersion.minor.desc())
    )
    return cycle, version


@router.get("/budget", summary="Orçamento lançado no ciclo, por módulo (OPEX/CAPEX)")
def budget_inventory(db: Session = Depends(get_db), _: User = Depends(require_roles(Role.CONTROLLER))):
    from app.models import BudgetLine, BudgetSubmission, CapexItem, CapexProject

    cycle, version = _working_version(db)
    if version is None:
        return []
    out = []
    for module in BUDGET_MODULES:
        subs = select(BudgetSubmission.id).where(
            BudgetSubmission.version_id == version.id, BudgetSubmission.module == module
        )
        started = db.scalar(select(func.count()).select_from(subs.subquery()))
        if module == "OPEX":
            rows, total = db.execute(
                select(func.count(BudgetLine.id), func.coalesce(func.sum(BudgetLine.total_amount), 0)).where(
                    BudgetLine.submission_id.in_(subs)
                )
            ).one()
        elif module == "PERSONNEL":
            # movimentações (promoções, desligamentos, vagas...); o custo depende do cenário, não é somado aqui
            rows = db.scalar(select(func.count(PersonnelMovement.id)).where(PersonnelMovement.submission_id.in_(subs)))
            total = None
        else:
            rows, total = db.execute(
                select(func.count(CapexItem.id), func.coalesce(func.sum(CapexItem.total_value), 0))
                .join(CapexProject, CapexProject.id == CapexItem.project_id)
                .where(CapexProject.submission_id.in_(subs))
            ).one()
        out.append(
            {
                "module": module,
                "fiscal_year": cycle.fiscal_year,
                "version": version.label,
                "cost_centers": started,
                "rows": rows,
                "total": None if total is None else str(total),
            }
        )
    return out


def _delete_budget(db: Session, module: str) -> dict:
    from app.models import BudgetSubmission

    _, version = _working_version(db)
    if version is None:
        raise HTTPException(409, "Não há versão em elaboração: a versão atual está congelada. Abra uma revisão antes.")
    ids = list(
        db.scalars(
            select(BudgetSubmission.id).where(
                BudgetSubmission.version_id == version.id, BudgetSubmission.module == module
            )
        )
    )
    # linhas, itens, justificativas, validações GMD e histórico do fluxo saem em cascata
    if ids:
        db.execute(delete(BudgetSubmission).where(BudgetSubmission.id.in_(ids)))
    reverted = 0
    if TEMPLATE_TYPES[module]:
        reverted = db.execute(
            update(ImportBatch)
            .where(ImportBatch.dataset_type == TEMPLATE_TYPES[module], ImportBatch.status == "COMPLETED")
            .values(status="REVERTED")
        ).rowcount
    return {"module": module, "cost_centers": len(ids), "imports_reverted": reverted}


def _reset_versions(db: Session) -> dict:
    """Base do zero: apaga fotografias e revisões; o ciclo volta à versão 1.0 em elaboração."""
    from app.models import BudgetSnapshotLine, BudgetSubmission, BudgetVersion

    cycle, _ = _working_version(db)
    if cycle is None:
        return {"versions_removed": 0}
    versions = list(
        db.scalars(
            select(BudgetVersion)
            .where(BudgetVersion.cycle_id == cycle.id)
            .order_by(BudgetVersion.major, BudgetVersion.minor)
        )
    )
    if not versions:
        return {"versions_removed": 0}
    first, others = versions[0], versions[1:]
    ids = [v.id for v in versions]
    db.execute(delete(BudgetSnapshotLine).where(BudgetSnapshotLine.version_id.in_(ids)))
    db.execute(delete(BudgetSubmission).where(BudgetSubmission.version_id.in_(ids)))
    for v in reversed(others):
        db.delete(v)
    db.flush()
    first.status, first.frozen_at = "WORKING", None
    return {"versions_removed": len(others), "version": first.label}


@router.delete("/budget", summary="Excluir o orçamento lançado de um módulo (volta todos os CCs para 'Não iniciado')")
def delete_budget(
    request: Request,
    module: str = Query(..., description="OPEX ou CAPEX"),
    confirm: str = Query(..., description=f"Digite {CONFIRM_WORD} para confirmar"),
    reason: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    if confirm.strip().upper() != CONFIRM_WORD:
        raise HTTPException(422, f"Para excluir, digite {CONFIRM_WORD}")
    if module not in BUDGET_MODULES:
        raise HTTPException(422, "Módulo inválido (OPEX, CAPEX ou PERSONNEL)")
    result = _delete_budget(db, module)
    audit.record(
        db,
        user_id=actor.id,
        action="DELETE_BUDGET",
        entity_type="budget",
        entity_id=module,
        before=result,
        reason=reason,
        ip=client_ip(request),
    )
    db.commit()
    return result


@router.delete("/all", summary="Excluir todas as bases importadas e o orçamento lançado (cadastros são mantidos)")
def delete_all(
    request: Request,
    confirm: str = Query(..., description=f"Digite {CONFIRM_WORD} para confirmar"),
    reason: str | None = None,
    db: Session = Depends(get_db),
    actor: User = Depends(require_roles(Role.ADMIN)),
):
    if confirm.strip().upper() != CONFIRM_WORD:
        raise HTTPException(422, f"Para excluir, digite {CONFIRM_WORD}")
    budgets = [_reset_versions(db)]
    types = sorted(
        set(db.scalars(select(DatasetVersion.dataset_type).where(DatasetVersion.dataset_type.in_(DELETABLE))))
    )
    datasets = []
    for dtype in types:
        if dtype == "EMPLOYEES":
            # movimentações dependem dos colaboradores
            emp_ids = select(Employee.id).join(DatasetVersion, DatasetVersion.id == Employee.dataset_version_id)
            db.execute(delete(PersonnelMovement).where(PersonnelMovement.employee_id.in_(emp_ids)))
        datasets.append(_delete_dataset(db, dtype, None, actor, reason, client_ip(request)))
    # demais lotes concluídos (cadastros e templates) deixam de bloquear o reenvio do mesmo arquivo
    db.execute(update(ImportBatch).where(ImportBatch.status == "COMPLETED").values(status="REVERTED"))
    result = {"budgets": budgets, "datasets": datasets}
    audit.record(
        db,
        user_id=actor.id,
        action="DELETE_ALL_DATA",
        entity_type="dataset",
        entity_id="ALL",
        before=result,
        reason=reason,
        ip=client_ip(request),
    )
    db.commit()
    return result
