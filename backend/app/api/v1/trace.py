"""Rastro (09/10/2026): do total do Painel ao lançamento — árvore por nível e lista dos registros de origem."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.core.deps import get_current_user
from app.db import get_db
from app.models import User
from app.services import trace as trace_svc

router = APIRouter(prefix="/trace", tags=["rastro"])

Level = Literal["department", "area", "cost_center", "package", "account"]


@router.get("/tree", summary="Um nível do rastro (área → setor → CC → pacote → conta) com os filtros do Painel")
def tree(
    level: Level = "department",
    q: trace_svc.TraceQuery = Depends(),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """`parent_*` fixam a trilha (os níveis já escolhidos); `years`/`months`/`modules` e os filtros do Painel valem
    como lá. `series=actual|budget` escolhe a série quando o ano tem realizado e orçado; sem ela, a série principal
    do Painel (o ano do ciclo traz o orçamento proposto)."""
    return trace_svc.tree(db, user, q, level)


@router.get("/entries", summary="Lançamentos de origem do recorte (maiores primeiro), com paginação e busca")
def entries(
    q: trace_svc.TraceQuery = Depends(),
    search: str | None = Query(None, max_length=100),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Orçamento do ciclo: linhas OPEX, itens de CAPEX, posições do quadro de pessoal (por componente) e contratos PJ
    (agregados para quem não tem acesso). Realizado e orçado de referência: cada partida com o lote de importação
    (arquivo e data). Só os CCs que o usuário enxerga."""
    return trace_svc.entries_out(db, user, q, search, offset, limit)


@router.get("/entries.xlsx", summary="Lançamentos do recorte em Excel (até 5.000 registros)")
def entries_xlsx(
    q: trace_svc.TraceQuery = Depends(),
    search: str | None = Query(None, max_length=100),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    content = trace_svc.export_xlsx(db, user, q, search)
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="rastro.xlsx"'},
    )
