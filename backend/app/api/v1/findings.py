from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.deps import get_current_user, visible_cost_center_ids
from app.db import get_db
from app.models import User
from app.services import findings as svc
from app.services import opex as opex_svc

router = APIRouter(prefix="/findings", tags=["apontamentos"])


@router.get("", summary="Apontamentos: divergências e erros de preenchimento dos orçamentos em aberto, item a item")
def findings(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    try:
        ctx = opex_svc.context(db)
    except opex_svc.OpexError as exc:
        raise HTTPException(409, str(exc)) from exc
    items = svc.collect(db, ctx, visible_cost_center_ids(db, user))
    return {
        "version": ctx.version.label,
        "items": items,
        "counts": {
            "critical": sum(1 for i in items if i["severity"] == "CRITICAL"),
            "warning": sum(1 for i in items if i["severity"] == "WARNING"),
            "cost_centers": len({i["cost_center_id"] for i in items}),
        },
    }
