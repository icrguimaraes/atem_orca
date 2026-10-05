from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.deps import require_roles
from app.db import get_db
from app.imports import pipeline
from app.imports.base import StructureError
from app.models import DatasetVersion, ImportBatch, ImportError_, ImportRow, User
from app.models.base import DatasetType, Role
from app.schemas.common import (
    DatasetVersionOut,
    ImportBatchOut,
    ImportErrorOut,
    ImportPreview,
    ImportRowOut,
    Page,
)
from app.services.storage import LocalStorage, get_storage

router = APIRouter(tags=["importação"])
importer = require_roles(Role.CONTROLLER)


def _batch(db: Session, batch_id: int) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "Importação não encontrada")
    return batch


@router.post(
    "/imports",
    response_model=ImportBatchOut,
    status_code=202,
    summary="Enviar arquivo (validação assíncrona; acompanhe pelo status)",
)
async def upload(
    request: Request,
    file: Annotated[UploadFile, File(description=".xlsx, .xlsm ou .csv")],
    dataset_type: Annotated[DatasetType | None, Form(description="Vazio = detecção automática")] = None,
    reference_year: Annotated[int | None, Form()] = None,
    company_code: Annotated[str | None, Form(description="Empresa padrão quando o arquivo não tem a coluna")] = "1001",
    scenario: Annotated[str | None, Form(description="Cenário do orçamento de referência")] = "ORC",
    create_missing_dimensions: Annotated[bool, Form()] = False,
    mode: Annotated[
        Literal["MERGE", "REPLACE"],
        Form(
            description="Realizado/orçamento: MERGE atualiza só as combinações CC×conta×filial do arquivo e mantém as "
            "demais; REPLACE substitui toda a base da empresa no ano"
        ),
    ] = "MERGE",
    deactivate_missing: Annotated[bool, Form(description="Colaboradores ausentes ficam inativos")] = False,
    db: Session = Depends(get_db),
    storage: LocalStorage = Depends(get_storage),
    actor: User = Depends(importer),
):
    content = await file.read()
    limit = get_settings().max_upload_mb * 1024 * 1024
    if len(content) > limit:
        raise HTTPException(413, f"Arquivo excede {get_settings().max_upload_mb} MB")
    if not content:
        raise HTTPException(422, "Arquivo vazio")
    options = {
        "reference_year": reference_year,
        "company_code": company_code,
        "scenario": scenario,
        "create_missing_dimensions": create_missing_dimensions,
        "mode": mode,
        "deactivate_missing": deactivate_missing,
    }
    try:
        batch = pipeline.create_batch(
            db,
            storage,
            content=content,
            file_name=file.filename or "arquivo",
            user_id=actor.id,
            dataset_type=dataset_type.value if dataset_type else None,
            options={k: v for k, v in options.items() if v is not None},
        )
    except StructureError as exc:
        raise HTTPException(422, str(exc)) from exc
    return batch


@router.get("/imports", response_model=Page)
def list_batches(
    status: str | None = None,
    dataset_type: str | None = None,
    limit: int = Query(50, le=200),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(importer),
):
    stmt = select(ImportBatch)
    if status:
        stmt = stmt.where(ImportBatch.status == status)
    if dataset_type:
        stmt = stmt.where(ImportBatch.dataset_type == dataset_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(ImportBatch.id.desc()).limit(limit).offset(offset))
    return Page(total=total, items=[ImportBatchOut.model_validate(b) for b in items])


@router.get("/imports/{batch_id}", response_model=ImportBatchOut)
def get_batch(batch_id: int, db: Session = Depends(get_db), _: User = Depends(importer)):
    return _batch(db, batch_id)


@router.get(
    "/imports/{batch_id}/preview",
    response_model=ImportPreview,
    summary="Prévia: contagens, erros agrupados e amostra de linhas",
)
def preview(
    batch_id: int,
    status: str | None = Query(None, description="VALID | WARNING | ERROR | DUPLICATE"),
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(importer),
):
    batch = _batch(db, batch_id)
    stmt = select(ImportRow).where(ImportRow.batch_id == batch_id)
    if status:
        stmt = stmt.where(ImportRow.status == status)
    rows = db.scalars(stmt.order_by(ImportRow.row_number).limit(limit).offset(offset))
    return ImportPreview(
        batch=ImportBatchOut.model_validate(batch),
        errors_by_code=pipeline.errors_by_code(db, batch_id),
        rows=[ImportRowOut.model_validate(r) for r in rows],
    )


@router.get("/imports/{batch_id}/errors", response_model=list[ImportErrorOut])
def list_errors(
    batch_id: int,
    code: str | None = None,
    limit: int = Query(500, le=5000),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(importer),
):
    _batch(db, batch_id)
    stmt = select(ImportError_).where(ImportError_.batch_id == batch_id)
    if code:
        stmt = stmt.where(ImportError_.code == code)
    return list(db.scalars(stmt.order_by(ImportError_.row_number).limit(limit).offset(offset)))


@router.get("/imports/{batch_id}/errors.xlsx", summary="Relatório de inconsistências (Excel)")
def errors_xlsx(batch_id: int, db: Session = Depends(get_db), _: User = Depends(importer)):
    batch = _batch(db, batch_id)
    return Response(
        pipeline.error_report(db, batch),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="inconsistencias_importacao_{batch_id}.xlsx"'},
    )


@router.post(
    "/imports/{batch_id}/confirm",
    response_model=ImportBatchOut,
    summary="Confirmar carga dos registros válidos (gera nova versão)",
)
def confirm(
    batch_id: int,
    force: bool = Query(False, description="Confirmar mesmo se o arquivo já foi importado ou não traz alterações"),
    db: Session = Depends(get_db),
    actor: User = Depends(importer),
):
    batch = _batch(db, batch_id)
    try:
        return pipeline.confirm_batch(db, batch, actor.id, force=force)
    except pipeline.ImportStateError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/imports/{batch_id}/reject", response_model=ImportBatchOut)
def reject(batch_id: int, reason: str | None = None, db: Session = Depends(get_db), actor: User = Depends(importer)):
    batch = _batch(db, batch_id)
    try:
        return pipeline.reject_batch(db, batch, actor.id, reason)
    except pipeline.ImportStateError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get(
    "/dataset-versions",
    response_model=list[DatasetVersionOut],
    summary="Histórico de versões (Importação → Versão → Data → Usuário → Fonte)",
)
def dataset_versions(
    dataset_type: str | None = None,
    current_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(importer),
):
    stmt = select(DatasetVersion)
    if dataset_type:
        stmt = stmt.where(DatasetVersion.dataset_type == dataset_type)
    if current_only:
        stmt = stmt.where(DatasetVersion.is_current)
    return list(db.scalars(stmt.order_by(DatasetVersion.scope_key, DatasetVersion.version_number.desc())))
