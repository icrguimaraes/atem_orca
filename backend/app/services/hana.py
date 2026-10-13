"""Integração com o SAP HANA Cloud (13/10/2026): lê a view do KSB1 (`VWG_FD_KSB1`) e entrega as partidas ao fluxo normal
de importação, no lugar da planilha exportada.

Segurança: só leitura (um `SELECT` fixo sobre a view configurada, com filtros por parâmetro); usuário e senha vêm de
variáveis de ambiente (HANA_USER / HANA_PASSWORD) e nunca são gravados, devolvidos pela API ou escritos em log. O
resultado vira uma planilha que passa pelo mesmo caminho dos arquivos (detecção, de-para, prévia, comparação e
confirmação), então nada entra na base sem a confirmação em Importações."""

import io
import re
from collections.abc import Iterator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from openpyxl import Workbook

from app.config import get_settings
from app.imports.base import norm
from app.imports.parsers.financial import KSB1_ALIASES

# variáveis de ambiente (Railway); host, usuário e senha são obrigatórios
ENV_REQUIRED = ("HANA_HOST", "HANA_USER", "HANA_PASSWORD")
SAMPLE_ROWS = 3
FETCH_SIZE = 5000
MAX_ROWS = 1_000_000  # trava de segurança (a planilha tem limite de linhas e o upload, de tamanho)
_IDENT = re.compile(r"^[A-Za-z0-9_]{1,128}$")

# campo do sistema → nomes de coluna prováveis na view (além dos nomes do KSB1 em `KSB1_ALIASES`): técnicos do SAP
# e em inglês. Comparados sem acento, caixa ou pontuação; o parâmetro `sap.ksb1_columns` sobrepõe tudo.
TECHNICAL = {
    "company": ("BUKRS", "COMPANY_CODE", "COMPANYCODE"),
    "branch": ("BUPLA", "GSBER", "BUSINESS_PLACE", "BUSINESS_AREA"),
    "cost_center": ("KOSTL", "COST_CENTER", "COSTCENTER"),
    "cost_center_name": ("KTEXT", "COST_CENTER_NAME", "COSTCENTER_NAME"),
    "account": ("KSTAR", "HKONT", "SAKNR", "GL_ACCOUNT", "COST_ELEMENT", "COSTELEMENT"),
    "account_name": ("KSTAR_TXT", "COST_ELEMENT_NAME", "COSTELEMENT_NAME", "GL_ACCOUNT_NAME", "TXT50", "TXT20"),
    "year": ("GJAHR", "FISCAL_YEAR", "FISCALYEAR"),
    "period": ("MONAT", "POPER", "PERIOD", "FISCAL_PERIOD", "FISCALPERIOD"),
    "posting_date": ("BUDAT", "POSTING_DATE", "POSTINGDATE"),
    "document": ("BELNR", "REFBN", "DOCUMENT_NUMBER", "DOCUMENTNUMBER", "DOC_NUMBER"),
    "document_type": ("BLART", "DOCUMENT_TYPE", "DOCUMENTTYPE"),
    "amount": ("WKGBTR", "WOGBTR", "WTGBTR", "AMOUNT", "VALOR", "VALUE"),
    "currency": ("WAERS", "KWAER", "OWAER", "CURRENCY"),
    "text": ("SGTXT", "BKTXT", "TEXT", "ITEM_TEXT"),
    "vendor_code": ("LIFNR", "VENDOR", "VENDOR_CODE", "SUPPLIER"),
    "vendor_name": ("NAME1", "VENDOR_NAME", "SUPPLIER_NAME"),
}
# o mínimo para o KSB1 ser lido: CC, conta, valor e (ano + período) ou data de lançamento
REQUIRED = ("cost_center", "account", "amount")
FIELD_LABELS = {
    "company": "Empresa",
    "branch": "Filial",
    "cost_center": "Centro de custo",
    "cost_center_name": "Nome do CC",
    "account": "Conta",
    "account_name": "Nome da conta",
    "year": "Ano",
    "period": "Mês",
    "posting_date": "Data de lançamento",
    "document": "Documento",
    "document_type": "Tipo de documento",
    "amount": "Valor",
    "currency": "Moeda",
    "text": "Texto",
    "vendor_code": "Fornecedor (código)",
    "vendor_name": "Fornecedor",
}


class HanaError(Exception):
    """Falha de configuração ou de conexão com o HANA (mensagem segura para a tela)."""


def _key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", norm(name).upper())


def missing_env() -> list[str]:
    s = get_settings()
    values = {"HANA_HOST": s.hana_host, "HANA_USER": s.hana_user, "HANA_PASSWORD": s.hana_password}
    return [k for k in ENV_REQUIRED if not values[k]]


def status() -> dict:
    """Situação da integração para a tela (sem segredos)."""
    s = get_settings()
    missing = missing_env()
    return {
        "configured": not missing,
        "missing": missing,
        "host_set": bool(s.hana_host),
        "port": s.hana_port,
        "schema": s.hana_schema,
        "view": s.hana_view,
    }


def _ident(value: str, what: str) -> str:
    if not _IDENT.match(value or ""):
        raise HanaError(f"{what} inválido: use só letras, números e _")
    return f'"{value}"'


def _source() -> str:
    s = get_settings()
    return f"{_ident(s.hana_schema, 'Schema')}.{_ident(s.hana_view, 'View')}"


def connect(timeout_ms: int = 30000) -> Any:
    """Conexão hdbcli (importada só aqui: o resto do sistema roda sem o driver)."""
    s = get_settings()
    if missing_env():
        raise HanaError("Integração SAP não configurada: faltam " + ", ".join(missing_env()))
    try:
        from hdbcli import dbapi
    except ImportError as exc:  # pragma: no cover - o driver está em requirements.txt
        raise HanaError("Driver do SAP HANA (hdbcli) não instalado") from exc
    try:
        return dbapi.connect(
            address=s.hana_host,
            port=s.hana_port,
            user=s.hana_user,
            password=s.hana_password,
            encrypt=True,
            sslValidateCertificate=True,
            connectTimeout=15000,
            communicationTimeout=timeout_ms,
        )
    except Exception as exc:  # hdbcli.dbapi.Error: a mensagem não traz a senha
        raise HanaError(f"Não foi possível conectar ao SAP HANA: {exc}") from exc


def resolve_columns(columns: list[str], override: dict | None = None) -> tuple[dict[str, str], list[str]]:
    """Campo do sistema → coluna da view. `override` (parâmetro `sap.ksb1_columns`) vale primeiro; depois os nomes do
    KSB1 e os técnicos do SAP. Devolve (mapa, campos obrigatórios que ficaram sem coluna)."""
    by_key = {_key(c): c for c in columns}
    mapping: dict[str, str] = {}
    for field in TECHNICAL:
        chosen = (override or {}).get(field)
        if chosen and chosen in columns:
            mapping[field] = chosen
            continue
        candidates = [*(KSB1_ALIASES.get(field, ())), *TECHNICAL[field]]
        for cand in candidates:
            if _key(cand) in by_key:
                mapping[field] = by_key[_key(cand)]
                break
    missing = [f for f in REQUIRED if f not in mapping]
    if not ({"year", "period"} <= mapping.keys() or "posting_date" in mapping):
        missing.append("year+period ou posting_date")
    return mapping, missing


def _plain(value: Any) -> Any:
    """Valor da célula/amostra: números e datas como estão, o resto como texto."""
    if value is None or isinstance(value, (int, float, Decimal, date, datetime)):
        return value
    return str(value)


def inspect(override: dict | None = None) -> dict:
    """Testa a conexão e devolve as colunas da view, o mapeamento para os campos do KSB1 e uma amostra curta."""
    source = _source()
    conn = connect()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {source} LIMIT {SAMPLE_ROWS}")
        names = [d[0] for d in cur.description]
        types = [str(d[1]) for d in cur.description]
        sample = [[_plain(v) for v in row] for row in cur.fetchall()]
    except HanaError:
        raise
    except Exception as exc:
        raise HanaError(f"Falha ao ler a view: {exc}") from exc
    finally:
        conn.close()
    mapping, missing = resolve_columns(names, override)
    inverse = {col: field for field, col in mapping.items()}
    return {
        "columns": [{"name": n, "type": t, "field": inverse.get(n)} for n, t in zip(names, types, strict=True)],
        "mapping": {f: {"label": FIELD_LABELS[f], "column": c} for f, c in mapping.items()},
        "missing": missing,
        "sample": {"columns": names, "rows": sample},
    }


def _where(mapping: dict[str, str], year: int, company: str | None) -> tuple[str, list]:
    if "year" in mapping:
        clauses = [f"CAST({_ident(mapping['year'], 'Coluna')} AS NVARCHAR(10)) = ?"]
    else:  # sem coluna de ano: o ano da data de lançamento
        clauses = [f"YEAR({_ident(mapping['posting_date'], 'Coluna')}) = ?"]
    params: list = [str(year) if "year" in mapping else year]
    if company and "company" in mapping:
        clauses.append(f"CAST({_ident(mapping['company'], 'Coluna')} AS NVARCHAR(20)) = ?")
        params.append(company)
    return " AND ".join(clauses), params


def fetch_ksb1(
    year: int, company: str | None, override: dict | None = None
) -> tuple[Iterator[list[Any]], list[str], dict[str, str]]:
    """Partidas do ano (e da empresa, se informada). Devolve (linhas, colunas, mapeamento); a conexão fecha ao
    esgotar o gerador."""
    source = _source()
    conn = connect(timeout_ms=300000)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {source} LIMIT 0")
        names = [d[0] for d in cur.description]
        mapping, missing = resolve_columns(names, override)
        if missing:
            raise HanaError(
                "A view não tem coluna para: " + ", ".join(missing) + " — ajuste o parâmetro sap.ksb1_columns"
            )
        where, params = _where(mapping, year, company)
        cur.execute(f"SELECT * FROM {source} WHERE {where}", params)
    except Exception:
        conn.close()
        raise

    def rows() -> Iterator[list[Any]]:
        try:
            total = 0
            while True:
                chunk = cur.fetchmany(FETCH_SIZE)
                if not chunk:
                    return
                total += len(chunk)
                if total > MAX_ROWS:
                    raise HanaError(f"Mais de {MAX_ROWS:,} linhas: filtre por empresa".replace(",", "."))
                yield from (list(r) for r in chunk)
        finally:
            conn.close()

    return rows(), names, mapping


def to_workbook(rows: Iterator[list[Any]], names: list[str], mapping: dict[str, str]) -> tuple[bytes, int]:
    """Planilha no layout do KSB1 (cabeçalhos = o primeiro nome aceito de cada campo), que a importação já lê."""
    index = {n: i for i, n in enumerate(names)}
    fields = [f for f in KSB1_ALIASES if f in mapping]
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("KSB1")
    ws.append([KSB1_ALIASES[f][0] for f in fields])
    count = 0
    for row in rows:
        out = []
        for f in fields:
            v = row[index[mapping[f]]]
            if isinstance(v, datetime):
                v = v.date()
            elif isinstance(v, bytes):
                v = v.decode(errors="replace")
            out.append(v)
        ws.append(out)
        count += 1
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), count
