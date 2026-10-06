"""Estruturas comuns e utilitários de leitura de planilhas."""

import csv
import io
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import openpyxl


def norm(text: Any) -> str:
    """Normaliza cabeçalhos: sem acento, minúsculo, sem pontuação, espaços simples."""
    if text is None:
        return ""
    if isinstance(text, (datetime, date)):
        return text.strftime("%Y-%m-%d")
    value = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    value = re.sub(r"[^a-z0-9/]+", " ", value.lower())
    return re.sub(r"\s+", " ", value).strip()


def clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).replace("\xa0", " ").strip()
    if isinstance(value, float) and value.is_integer():
        text = str(int(value))
    return text or None


def clean_code(value: Any, width: int | None = None) -> str | None:
    """Códigos SAP: '1050101011', '0001'. Excel costuma converter em número e perder zeros."""
    text = clean_str(value)
    if text is None:
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        text = text[:-2]
    if width and text.isdigit() and len(text) < width:
        text = text.zfill(width)
    return text


def to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise ValueError("valor booleano")
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if not text or text in {"-", "—"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    if "," in text:  # formato brasileiro 1.234,56
        text = text.replace(".", "").replace(",", ".")
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"número inválido: {value!r}") from None
    return -number if negative else number


def to_date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d.%m.%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"data inválida: {value!r}")


@dataclass
class Issue:
    code: str
    message: str
    column: str | None = None
    value: Any = None
    severity: str = "ERROR"  # ERROR | WARNING


@dataclass
class Record:
    record_type: str
    sheet: str | None
    row_number: int
    data: dict[str, Any]
    natural_key: str | None = None
    issues: list[Issue] = field(default_factory=list)

    def error(self, code: str, message: str, column: str | None = None, value: Any = None) -> None:
        self.issues.append(Issue(code, message, column, value, "ERROR"))

    def warn(self, code: str, message: str, column: str | None = None, value: Any = None) -> None:
        self.issues.append(Issue(code, message, column, value, "WARNING"))

    @property
    def status(self) -> str:
        if any(i.severity == "ERROR" for i in self.issues):
            return "ERROR"
        return "WARNING" if self.issues else "VALID"


@dataclass
class ParseResult:
    dataset_type: str
    layout: str
    records: list[Record] = field(default_factory=list)
    structural: list[Issue] = field(default_factory=list)  # problemas de arquivo/aba/coluna
    meta: dict[str, Any] = field(default_factory=dict)


class StructureError(Exception):
    """Arquivo não reconhecido ou sem colunas obrigatórias."""


# ---------------------------------------------------------------- leitura de planilhas


class Sheet:
    """Abstração mínima (nome + linhas) para xlsx e csv."""

    def __init__(self, name: str, rows: list[tuple[Any, ...]]) -> None:
        self.name = name
        self.rows = rows

    def iter_rows(self, start: int = 1) -> Iterator[tuple[int, tuple[Any, ...]]]:
        yield from enumerate(self.rows[start - 1 :], start=start)

    def cell(self, row: int, col: int) -> Any:
        try:
            return self.rows[row - 1][col - 1]
        except IndexError:
            return None


EXPORT_MARKER = "ATEM_EXPORT"


def export_marker(module: str, cost_center: str, version: str) -> str:
    return f"{EXPORT_MARKER} module={module} cc={cost_center} version={version}"


def system_export(sheets: list[Sheet]) -> dict | None:
    """Planilha gerada pela exportação do sistema (marcador em A1 da primeira aba). Devolve
    {module, cost_center, version}: ao reimportar, os lançamentos **desse** CC são todos substituídos
    (não só os vindos de template); outros CCs no mesmo arquivo seguem a regra normal."""
    for s in sheets:
        text = str(s.rows[0][0] or "") if s.rows and s.rows[0] else ""
        if text.startswith(EXPORT_MARKER):
            fields = dict(re.findall(r"(\w+)=(\S+)", text))
            if fields.get("cc"):
                return {
                    "module": fields.get("module"),
                    "cost_center": fields["cc"],
                    "version": fields.get("version"),
                }
    return None


def load_sheets(content: bytes, file_name: str) -> list[Sheet]:
    name = file_name.lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        return [Sheet("csv", [tuple(r) for r in csv.reader(io.StringIO(text), dialect)])]
    if not name.endswith((".xlsx", ".xlsm")):
        raise StructureError("Formato não suportado. Envie .xlsx, .xlsm ou .csv")
    try:
        wb = openpyxl.load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    except Exception as exc:  # arquivo corrompido / não é Excel
        raise StructureError(f"Não foi possível abrir o arquivo Excel: {exc}") from exc
    sheets = []
    for ws in wb.worksheets:
        rows = [tuple(r) for r in ws.iter_rows(values_only=True)]
        while rows and not any(v not in (None, "") for v in rows[-1]):
            rows.pop()
        sheets.append(Sheet(ws.title, rows))
    wb.close()
    return sheets


@dataclass
class Table:
    sheet: Sheet
    header_row: int
    columns: dict[str, int]  # campo lógico -> índice de coluna (1-based)
    unknown: list[str]
    month_columns: dict[int, int] = field(default_factory=dict)  # mês -> índice de coluna

    def value(self, row: tuple[Any, ...], key: str) -> Any:
        col = self.columns.get(key)
        if col is None or col - 1 >= len(row):
            return None
        return row[col - 1]

    def rows(self) -> Iterator[tuple[int, tuple[Any, ...]]]:
        return self.sheet.iter_rows(self.header_row + 1)


def find_table(
    sheet: Sheet,
    aliases: dict[str, tuple[str, ...]],
    required: tuple[str, ...],
    *,
    max_scan_rows: int = 40,
) -> Table | None:
    """Procura a linha de cabeçalho que contenha todos os campos obrigatórios.

    `aliases` mapeia campo lógico → nomes aceitos. Colunas não mapeadas são devolvidas
    em `unknown` (geram aviso de coluna desconhecida quando aplicável).
    """
    lookup = {norm(a): key for key, names in aliases.items() for a in names}
    for idx, row in sheet.iter_rows(1):
        if idx > max_scan_rows:
            break
        columns: dict[str, int] = {}
        unknown: list[str] = []
        for i, cell in enumerate(row):
            header = norm(cell)
            if not header:
                continue
            key = lookup.get(header)
            if key and key not in columns:
                columns[key] = i + 1
            elif key is None:
                unknown.append(str(cell))
        if all(r in columns for r in required):
            return Table(sheet, idx, columns, unknown)
    return None


def is_blank(row: tuple[Any, ...]) -> bool:
    return all(v is None or (isinstance(v, str) and not v.strip()) for v in row)
