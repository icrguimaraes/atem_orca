from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.models import AuditLog


def _jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def snapshot(obj: Any, exclude: set[str] | None = None) -> dict[str, Any]:
    exclude = (exclude or set()) | {"password_hash"}
    mapper = inspect(obj).mapper
    return {c.key: _jsonable(getattr(obj, c.key)) for c in mapper.column_attrs if c.key not in exclude}


def diff(before: dict | None, after: dict | None) -> tuple[dict | None, dict | None]:
    if not before or not after:
        return before, after
    keys = {k for k in set(before) | set(after) if before.get(k) != after.get(k)} - {"updated_at"}
    return {k: before.get(k) for k in keys}, {k: after.get(k) for k in keys}


def record(
    db: Session,
    *,
    user_id: int | None,
    action: str,
    entity_type: str,
    entity_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
    ip: str | None = None,
) -> AuditLog:
    if action == "UPDATE":
        before, after = diff(before, after)
    log = AuditLog(
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        before=_jsonable(before),
        after=_jsonable(after),
        reason=reason,
        ip=ip,
    )
    db.add(log)
    return log
