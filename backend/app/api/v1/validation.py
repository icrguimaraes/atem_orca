from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db import get_db
from app.imports.base import StructureError
from app.models import ImportBatch, User
from app.models.base import Role
from app.services import validation as svc

router = APIRouter(prefix="/validation", tags=["validação"])
controller = require_roles(Role.CONTROLLER)


def _batch(db: Session, batch_id: int) -> ImportBatch:
    batch = db.get(ImportBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "Importação não encontrada")
    return batch


@router.get("/files", summary="Arquivos de template importados (versão vigente de cada um)")
def files(db: Session = Depends(get_db), _: User = Depends(controller)):
    return svc.files(db)


@router.get("/{batch_id}", summary="Abas do arquivo e o que o sistema leu em cada uma")
def overview(batch_id: int, db: Session = Depends(get_db), _: User = Depends(controller)):
    batch = _batch(db, batch_id)
    try:
        return svc.overview(db, batch)
    except (StructureError, FileNotFoundError, OSError) as exc:
        raise HTTPException(409, f"Arquivo original indisponível: {exc}") from exc


@router.get("/{batch_id}/sheet", summary="Uma aba como está no arquivo, com o que o sistema leu em cada linha")
def sheet(
    batch_id: int,
    name: str = Query(..., description="Nome da aba"),
    db: Session = Depends(get_db),
    _: User = Depends(controller),
):
    batch = _batch(db, batch_id)
    try:
        out = svc.sheet(db, batch, name)
    except (StructureError, FileNotFoundError, OSError) as exc:
        raise HTTPException(409, f"Arquivo original indisponível: {exc}") from exc
    if out is None:
        raise HTTPException(404, "Aba não encontrada")
    return out
