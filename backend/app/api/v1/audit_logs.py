from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.deps import require_roles
from app.db import get_db
from app.models import AuditLog, User
from app.models.base import Role
from app.schemas.common import AuditOut, Page

router = APIRouter(prefix="/audit-logs", tags=["auditoria"])


@router.get("", response_model=Page)
def list_logs(
    entity_type: str | None = None,
    entity_id: str | None = None,
    user_id: int | None = None,
    action: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(100, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
    _: User = Depends(require_roles(Role.CONTROLLER)),
):
    stmt = select(AuditLog)
    for column, value in (
        (AuditLog.entity_type, entity_type),
        (AuditLog.entity_id, entity_id),
        (AuditLog.user_id, user_id),
        (AuditLog.action, action),
    ):
        if value is not None:
            stmt = stmt.where(column == value)
    if since:
        stmt = stmt.where(AuditLog.occurred_at >= since)
    if until:
        stmt = stmt.where(AuditLog.occurred_at <= until)
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    items = db.scalars(stmt.order_by(AuditLog.id.desc()).limit(limit).offset(offset))
    return Page(total=total, items=[AuditOut.model_validate(i) for i in items])
