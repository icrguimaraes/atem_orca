"""Contratos PJ: rotas finas sobre `services.pj`. Todas exigem "Vê contratos PJ" (`users.can_view_pj`), seja qual
for o perfil; o escopo "Da área" / "Todos" é aplicado pelo serviço."""

from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.deps import client_ip, require_pj
from app.db import get_db
from app.domain.rules.pj import PjError
from app.models import User
from app.services import pj as S

router = APIRouter(prefix="/pj", tags=["contratos PJ"])


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except S.NotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except S.Forbidden as exc:
        raise HTTPException(403, str(exc)) from exc
    except (S.Invalid, PjError) as exc:
        raise HTTPException(422, str(exc)) from exc


class ContractIn(BaseModel):
    """Datas "AAAA-MM-DD"; valores em string decimal ("12345.67"). Na edição, só os campos enviados mudam.
    Nada é obrigatório: campos importantes vazios voltam em `missing` ("Falta preencher")."""

    name: str | None = Field(default=None, max_length=300)
    company_name: str | None = Field(default=None, max_length=300)
    cnpj: str | None = Field(default=None, max_length=30)
    role: str | None = Field(default=None, max_length=300)
    cost_center_id: int | None = None
    email: str | None = Field(default=None, max_length=300)
    phone: str | None = Field(default=None, max_length=60)
    monthly_value: str | None = Field(default=None, max_length=30)
    annual_bonus: str | None = Field(default=None, max_length=30)
    start_date: str | None = Field(default=None, max_length=30)
    end_date: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=4000)
    photo_blurred: bool | None = None


@router.get("", summary="Contratos (não arquivados) com a bonificação do ano; pending=true só os com pendência")
def list_contracts(
    year: int | None = None,
    status: Literal["ACTIVE", "ENDED"] | None = None,
    cost_center_id: int | None = None,
    q: str | None = None,
    pending: bool = False,
    db: Session = Depends(get_db),
    user: User = Depends(require_pj),
):
    return _call(
        S.list_contracts, db, user, year=year, status=status, cost_center_id=cost_center_id, q=q, pending=pending
    )


@router.get("/summary", summary="Indicadores: ativos, total mensal, bonificação anual e devida no ano")
def summary(
    year: int | None = None,
    cost_center_id: int | None = None,
    q: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_pj),
):
    return _call(S.summary, db, user, year=year, cost_center_id=cost_center_id, q=q)


@router.get("/options", summary="Centros de custo (do escopo, com área e setor) para filtros e formulário")
def options(db: Session = Depends(get_db), user: User = Depends(require_pj)):
    return S.options(db, user)


@router.get("/photos", summary="Fotos dos contratos (data URLs)")
def photos(db: Session = Depends(get_db), user: User = Depends(require_pj)):
    return S.photos(db, user)


@router.get("/export.xlsx", summary="Exportação dos contratos (Excel)")
def export(request: Request, year: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_pj)):
    content = _call(S.export_xlsx, db, user, year, client_ip(request))
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="contratos_pj.xlsx"'},
    )


@router.get("/{contract_id}", summary="Um contrato")
def get_contract(
    contract_id: int, year: int | None = None, db: Session = Depends(get_db), user: User = Depends(require_pj)
):
    return _call(S.get_contract, db, user, contract_id, year)


@router.post("", status_code=201, summary="Novo contrato")
def create_contract(
    payload: ContractIn, request: Request, db: Session = Depends(get_db), user: User = Depends(require_pj)
):
    return _call(S.create_contract, db, user, payload.model_dump(exclude_unset=True), client_ip(request))


@router.patch("/{contract_id}", summary="Editar contrato (encerrar = informar a data de término)")
def update_contract(
    contract_id: int,
    payload: ContractIn,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_pj),
):
    return _call(S.update_contract, db, user, contract_id, payload.model_dump(exclude_unset=True), client_ip(request))


@router.delete("/{contract_id}", summary="Arquivar contrato (exclusão lógica)")
def archive_contract(
    contract_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_pj)
):
    return _call(S.archive_contract, db, user, contract_id, client_ip(request))


@router.put("/{contract_id}/photo", summary="Trocar a foto (JPEG/PNG/WebP até 400 KB)")
async def set_photo(
    contract_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_pj),
):
    content = await file.read()
    return _call(S.set_photo, db, user, contract_id, content, file.content_type or "", client_ip(request))


@router.delete("/{contract_id}/photo", summary="Remover a foto")
def delete_photo(contract_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_pj)):
    return _call(S.delete_photo, db, user, contract_id, client_ip(request))
