"""Validação: a planilha como foi enviada (valores exatamente como estão no arquivo) lado a lado com o que o sistema
leu em cada linha — situação (válida, aviso, erro), campos interpretados e ocorrências da importação. Também aponta
células com fórmula sem valor calculado (arquivo salvo sem recalcular), causa comum de linhas "vazias".

O arquivo original fica no armazenamento (`ImportBatch.storage_path`); a leitura é cara (1–2 s para um template de
1 MB), então as últimas planilhas abertas ficam em cache na memória do processo."""

import io
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any

import openpyxl
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.imports.base import load_sheets
from app.models import ImportBatch, ImportError_, ImportRow, User
from app.services.storage import get_storage

MAX_ROWS = 3000  # linhas por aba devolvidas à tela
MAX_COLS = 80
TEMPLATE_TYPES = ("OPEX_TEMPLATE", "CAPEX_TEMPLATE", "EMPLOYEES")
_CACHE: "OrderedDict[int, list[SheetGrid]]" = OrderedDict()
_CACHE_SIZE = 4


@dataclass
class SheetGrid:
    name: str
    rows: list[tuple[Any, ...]]
    missing: set[tuple[int, int]] = field(default_factory=set)  # (linha, coluna) com fórmula sem valor


def _is_formula(value: Any) -> bool:
    if isinstance(value, str):
        return value.startswith("=")
    return hasattr(value, "text")  # ArrayFormula / DataTableFormula do openpyxl


def workbook(batch: ImportBatch) -> list[SheetGrid]:
    if batch.id in _CACHE:
        _CACHE.move_to_end(batch.id)
        return _CACHE[batch.id]
    content = get_storage().read(batch.storage_path)
    grids = [SheetGrid(s.name, s.rows) for s in load_sheets(content, batch.file_name)]
    if batch.file_name.lower().endswith((".xlsx", ".xlsm")):
        by_name = {g.name: g for g in grids}
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False, read_only=True)
        try:
            for ws in wb.worksheets:
                grid = by_name.get(ws.title)
                if grid is None:
                    continue
                for r, row in enumerate(ws.iter_rows(values_only=True), start=1):
                    if r > len(grid.rows):
                        break
                    values = grid.rows[r - 1]
                    for c, raw in enumerate(row, start=1):
                        if _is_formula(raw) and (c > len(values) or values[c - 1] is None):
                            grid.missing.add((r, c))
        finally:
            wb.close()
    _CACHE[batch.id] = grids
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return grids


def _json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value else None  # NaN
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    return str(value)


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def files(db: Session) -> list[dict]:
    """Arquivos de template importados (versão vigente de cada nome de arquivo), com contagens e CCs."""
    batches = list(
        db.scalars(
            select(ImportBatch)
            .where(ImportBatch.dataset_type.in_(TEMPLATE_TYPES), ImportBatch.status == "COMPLETED")
            .order_by(ImportBatch.id.desc())
        )
    )
    latest: dict[str, ImportBatch] = {}
    older: dict[str, int] = defaultdict(int)
    for b in batches:
        if b.file_name in latest:
            older[b.file_name] += 1
        else:
            latest[b.file_name] = b
    ids = [b.id for b in latest.values()]
    ccs: dict[int, set[str]] = defaultdict(set)
    for batch_id, cc in db.execute(
        select(ImportRow.batch_id, ImportRow.data["cost_center"].astext)
        .where(ImportRow.batch_id.in_(ids or [-1]), ImportRow.status.in_(("VALID", "WARNING")))
        .where(ImportRow.record_type.in_(("BUDGET_LINE", "TRAVEL", "CAPEX_ITEM", "EMPLOYEE", "VACANCY")))
        .distinct()
    ):
        if cc:
            ccs[batch_id].add(cc)
    users = {u.id: u.name for u in db.scalars(select(User))}
    return [
        {
            "id": b.id,
            "file_name": b.file_name,
            "dataset_type": b.dataset_type,
            "created_at": b.created_at.isoformat() if b.created_at else None,
            "uploaded_by": users.get(b.uploaded_by),
            "total_rows": b.total_rows,
            "valid_rows": b.valid_rows,
            "warning_rows": b.warning_rows,
            "error_rows": b.error_rows,
            "cost_centers": sorted(ccs[b.id]),
            "older_versions": older[b.file_name],
        }
        for b in latest.values()
    ]


def overview(db: Session, batch: ImportBatch) -> dict:
    """Abas do arquivo com o que o sistema leu em cada uma."""
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for sheet, status, n in db.execute(
        select(ImportRow.sheet, ImportRow.status, func.count())
        .where(ImportRow.batch_id == batch.id)
        .group_by(ImportRow.sheet, ImportRow.status)
    ):
        counts[sheet or ""][status] += n
    sheets = []
    for g in workbook(batch):
        c = counts.get(g.name, {})
        sheets.append(
            {
                "name": g.name,
                "rows": len(g.rows),
                "cols": min(max((len(r) for r in g.rows), default=0), MAX_COLS),
                "records": sum(c.values()),
                "warnings": c.get("WARNING", 0),
                "errors": c.get("ERROR", 0) + c.get("DUPLICATE", 0),
                "missing_formulas": len(g.missing),
            }
        )
    return {
        "id": batch.id,
        "file_name": batch.file_name,
        "dataset_type": batch.dataset_type,
        "status": batch.status,
        "created_at": batch.created_at.isoformat() if batch.created_at else None,
        "total_rows": batch.total_rows,
        "valid_rows": batch.valid_rows,
        "warning_rows": batch.warning_rows,
        "error_rows": batch.error_rows,
        "sheets": sheets,
    }


def sheet(db: Session, batch: ImportBatch, name: str) -> dict | None:
    """Uma aba: células como estão no arquivo (só linhas com conteúdo, até MAX_ROWS) e, por linha lida, o registro
    interpretado e as ocorrências."""
    grid = next((g for g in workbook(batch) if g.name == name), None)
    if grid is None:
        return None
    # última coluna com conteúdo (ou fórmula sem valor), limitada
    width = 0
    for r, row in enumerate(grid.rows, start=1):
        for c in range(len(row), 0, -1):
            if not _blank(row[c - 1]) or (r, c) in grid.missing:
                width = max(width, c)
                break
    width = max(width, max((c for _, c in grid.missing), default=0))
    width = min(width, MAX_COLS)

    marks: dict[int, dict] = {}
    for row in db.scalars(
        select(ImportRow).where(ImportRow.batch_id == batch.id, ImportRow.sheet == name).order_by(ImportRow.id)
    ):
        mark = marks.setdefault(row.row_number, {"records": [], "issues": []})
        mark["records"].append(
            {
                "record_type": row.record_type,
                "status": row.status,
                "data": {k: v for k, v in (row.data or {}).items() if not k.startswith("_")},
            }
        )
    for err in db.scalars(
        select(ImportError_)
        .where(ImportError_.batch_id == batch.id, ImportError_.sheet == name)
        .order_by(ImportError_.id)
    ):
        if err.row_number is None:
            continue
        marks.setdefault(err.row_number, {"records": [], "issues": []})["issues"].append(
            {"severity": err.severity, "code": err.code, "message": err.message, "column": err.column}
        )

    rows = []
    for r, row in enumerate(grid.rows, start=1):
        cells = [_json(row[c]) if c < len(row) else None for c in range(width)]
        has_missing = any((r, c) in grid.missing for c in range(1, width + 1))
        if all(_blank(v) for v in cells) and r not in marks and not has_missing:
            continue
        rows.append({"n": r, "cells": cells})
        if len(rows) >= MAX_ROWS:
            break
    return {
        "name": name,
        "cols": width,
        "rows": rows,
        "truncated": len(rows) >= MAX_ROWS,
        "missing": sorted([r, c] for r, c in grid.missing if c <= width),
        "marks": {str(k): v for k, v in sorted(marks.items())},
    }
