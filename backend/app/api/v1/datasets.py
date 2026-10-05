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
        if db.scalar(
            select(func.count()).select_from(PersonnelMovement).where(PersonnelMovement.employee_id.in_(emp_ids))
        ):
            raise HTTPException(409, "Há movimentações de pessoal usando estes colaboradores; exclua-as antes")
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
        ip=client_ip(request),
    )
    db.commit()
    return result
