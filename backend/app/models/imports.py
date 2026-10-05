from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_type: Mapped[str | None] = mapped_column(String(30))
    layout: Mapped[str | None] = mapped_column(String(40))
    file_name: Mapped[str] = mapped_column(String(300))
    file_sha256: Mapped[str] = mapped_column(String(64), index=True)
    storage_path: Mapped[str] = mapped_column(String(500))
    file_size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="UPLOADED", index=True)
    options: Mapped[dict | None] = mapped_column(JSONB)  # ano de referência, cenário, auto_create...
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    warning_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_rows: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict | None] = mapped_column(JSONB)
    error_message: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    confirmed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportRow(Base):
    """Staging normalizado das linhas lidas (base da prévia e da carga)."""

    __tablename__ = "import_rows"
    __table_args__ = (Index("ix_import_rows_batch", "batch_id", "status"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"))
    record_type: Mapped[str] = mapped_column(String(30))
    sheet: Mapped[str | None] = mapped_column(String(100))
    row_number: Mapped[int] = mapped_column(Integer)
    natural_key: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(20))  # VALID | WARNING | ERROR | DUPLICATE
    data: Mapped[dict[str, Any]] = mapped_column(JSONB)


class ImportError_(Base):
    __tablename__ = "import_errors"

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    sheet: Mapped[str | None] = mapped_column(String(100))
    row_number: Mapped[int | None] = mapped_column(Integer)
    column: Mapped[str | None] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # ERROR | WARNING
    message: Mapped[str] = mapped_column(Text)
    value: Mapped[str | None] = mapped_column(Text)
