import re
from datetime import date, datetime
from typing import Any

from app.domain.rules.common import MONTH_LABELS
from app.imports.base import Table, norm

_MONTH_NAMES = {
    "janeiro": 1,
    "fevereiro": 2,
    "marco": 3,
    "abril": 4,
    "maio": 5,
    "junho": 6,
    "julho": 7,
    "agosto": 8,
    "setembro": 9,
    "outubro": 10,
    "novembro": 11,
    "dezembro": 12,
}


def parse_month_header(cell: Any) -> tuple[int | None, int] | None:
    """Reconhece cabeçalhos de mês: datas (01/01/2026), 'JAN', 'jan/26', '2026-01', 'Janeiro'."""
    if isinstance(cell, (datetime, date)):
        return cell.year, cell.month
    text = norm(cell)
    if not text:
        return None
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)  # dd/mm/aaaa
    if m:
        return int(m.group(3)), int(m.group(2))
    m = re.fullmatch(r"(\d{4}) (\d{1,2})(?: (\d{1,2}))?", text)  # 2026-01[-01] (traços viram espaço)
    if m and 1 <= int(m.group(2)) <= 12:
        return int(m.group(1)), int(m.group(2))
    m = re.fullmatch(r"(\d{1,2})/(\d{4})", text)
    if m and 1 <= int(m.group(1)) <= 12:
        return int(m.group(2)), int(m.group(1))
    m = re.fullmatch(r"([a-z]{3})(?:[ /](\d{2,4}))?", text)
    if m and m.group(1).upper() in MONTH_LABELS:
        year = m.group(2)
        year_value = None if year is None else (2000 + int(year) if len(year) == 2 else int(year))
        return year_value, MONTH_LABELS.index(m.group(1).upper()) + 1
    if text in _MONTH_NAMES:
        return None, _MONTH_NAMES[text]
    return None


def attach_month_columns(table: Table) -> int | None:
    """Preenche table.month_columns; devolve o ano detectado nos cabeçalhos (se houver)."""
    header = table.sheet.rows[table.header_row - 1]
    years = set()
    used_cols = set(table.columns.values())
    for idx, cell in enumerate(header, start=1):
        if idx in used_cols:
            continue
        parsed = parse_month_header(cell)
        if parsed is None:
            continue
        year, month = parsed
        if month in table.month_columns:
            continue
        table.month_columns[month] = idx
        if year:
            years.add(year)
    table.unknown = [u for u in table.unknown if parse_month_header(u) is None]
    return years.pop() if len(years) == 1 else None
