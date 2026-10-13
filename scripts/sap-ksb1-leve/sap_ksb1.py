#!/usr/bin/env python3
"""Envio do KSB1 do SAP HANA para o sistema de orçamento (versão leve, arquivo único).

Para rodar num computador da rede da ATEM (o HANA só aceita IPs liberados). Precisa só de Python 3.9+ e de dois pacotes:
`py -m pip install hdbcli openpyxl` (ou o atalho instalar.cmd). Não depende do projeto: leia o LEIA-ME.txt.

Configuração: arquivo `sap_ksb1.env` ao lado deste script (criado vazio na primeira execução; preencha HANA_HOST,
HANA_USER, HANA_PASSWORD, SAP_PUSH_URL e SAP_PUSH_TOKEN). Variáveis de ambiente com o mesmo nome valem mais que o arquivo.

    python sap_ksb1.py --test                       # conecta e mostra colunas, mapeamento e amostra (não envia)
    python sap_ksb1.py --year 2026                  # busca o ano (todas as empresas) e envia ao sistema
    python sap_ksb1.py --year 2026 --company 1001   # só uma empresa
    python sap_ksb1.py --year 2026 --save ksb1.xlsx # grava a planilha neste computador em vez de enviar

Somente leitura no HANA. O envio cria um lote em Importações (prévia e confirmação na tela): nada entra na base sem
a confirmação. A senha do HANA fica só neste computador; ao sistema vai apenas a planilha, autenticada pelo token."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import unicodedata
import urllib.error
import urllib.request
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV_FILE = HERE / "sap_ksb1.env"
ENV_TEMPLATE = """# Configuração do envio do KSB1 (este arquivo fica só neste computador; não envie a ninguém).
# Preencha depois do "=", sem aspas e sem espaços.
HANA_HOST=
HANA_USER=
HANA_PASSWORD=
HANA_PORT=443
HANA_SCHEMA=FD_S4HANA_PRD
HANA_VIEW=VWG_FD_KSB1
SAP_PUSH_URL=https://atemorca-production.up.railway.app
SAP_PUSH_TOKEN=
"""

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SAMPLE_ROWS = 3
FETCH_SIZE = 5000
MAX_ROWS = 1_000_000
_IDENT = re.compile(r"^[A-Za-z0-9_]{1,128}$")

# --- cópia de backend/app/imports/parsers/financial.py (KSB1_ALIASES) e de app/services/hana.py (TECHNICAL, REQUIRED);
# um teste do projeto garante que continuam iguais ---------------------------------------------------------------
KSB1_ALIASES = {
    "company": ("Empresa", "Empresa CO", "Código da empresa", "Area contab custos"),
    "branch": ("Local de negócios", "Divisão", "Filial", "Centro"),
    "cost_center": ("Centro de custo", "Centro custo", "Centro cst", "Centro de custos", "Objeto"),
    "cost_center_name": ("Denominação do objeto", "Denominação objeto", "Denominação centro de custo"),
    "account": ("Classe de custo", "Classe custo", "Conta do Razão", "Conta Razão"),
    "account_name": ("Denominação da classe de custo", "Denom classe custo", "Denom.classe custo", "Txt classe custo"),
    "year": ("Exercício", "Ano"),
    "period": ("Período", "Mês", "Período contábil"),
    "posting_date": ("Data de lançamento", "Data lançamento", "Dt lançamento", "Data do documento"),
    "document": (
        "Nº documento",
        "No documento",
        "Número documento",
        "Documento",
        "Nº doc.de referência",
        "Nº doc de referência",
        "Nº doc ref",
        "Doc compras",
    ),
    "document_type": ("Tipo de documento", "Tipo doc", "Ctg doc"),
    "amount": (
        "Valor/moeda ACC",
        "Valor/moeda obj",
        "Valor/MACC",
        "Valor/moeda objeto",
        "Valor em moeda da área de contabilidade de custos",
        "Valor",
        "Montante",
    ),
    "currency": ("Moeda", "Moeda ACC", "Moeda da transação"),
    "text": ("Texto", "Denominação", "Texto cabeçalho documento", "Texto do cabeçalho do documento"),
    "vendor_code": ("Fornecedor",),
    "vendor_name": ("Nome do fornecedor", "Nome fornecedor", "Nome 1"),
}
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
REQUIRED = ("cost_center", "account", "amount")
# ------------------------------------------------------------------------------------------------------------


class Falha(Exception):
    """Erro com mensagem segura para mostrar (sem senha, host nem IP)."""


# ---------------------------------------------------------------- configuração


def carregar_env() -> None:
    """Lê sap_ksb1.env (cria o modelo vazio na primeira vez). Variáveis de ambiente têm prioridade."""
    if not ENV_FILE.exists():
        ENV_FILE.write_text(ENV_TEMPLATE, encoding="utf-8")
        raise SystemExit(f"Criei o arquivo de configuração: {ENV_FILE}\nPreencha-o (HANA_HOST, HANA_USER, HANA_PASSWORD, SAP_PUSH_TOKEN) e rode de novo.")
    for linha in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        if not os.environ.get(chave.strip()):  # variável de ambiente preenchida vale mais; vazia, vale o arquivo
            os.environ[chave.strip()] = valor.strip().strip('"').strip("'")


def cfg(chave: str, padrao: str = "") -> str:
    return (os.environ.get(chave) or padrao).strip()


def faltando() -> list[str]:
    return [k for k in ("HANA_HOST", "HANA_USER", "HANA_PASSWORD") if not cfg(k)]


def redigir(msg: str) -> str:
    host = cfg("HANA_HOST")
    if host:
        msg = msg.replace(host, "<host>")
    return re.sub(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b", "<ip>", msg)


# ---------------------------------------------------------------- HANA


def _ident(valor: str, o_que: str) -> str:
    if not _IDENT.match(valor or ""):
        raise Falha(f"{o_que} inválido: use só letras, números e _")
    return f'"{valor}"'


def _origem() -> str:
    return f"{_ident(cfg('HANA_SCHEMA', 'FD_S4HANA_PRD'), 'Schema')}.{_ident(cfg('HANA_VIEW', 'VWG_FD_KSB1'), 'View')}"


def conectar(timeout_ms: int = 30000):
    if faltando():
        raise Falha("Faltam no sap_ksb1.env: " + ", ".join(faltando()))
    try:
        from hdbcli import dbapi
    except ImportError:
        raise Falha("Driver do HANA não instalado: rode instalar.cmd (py -m pip install hdbcli openpyxl)") from None
    try:
        return dbapi.connect(
            address=cfg("HANA_HOST"),
            port=int(cfg("HANA_PORT", "443")),
            user=cfg("HANA_USER"),
            password=cfg("HANA_PASSWORD"),
            encrypt=True,
            sslValidateCertificate=True,
            connectTimeout=30000,
            communicationTimeout=timeout_ms,
        )
    except Exception as exc:
        raise Falha(f"Não foi possível conectar ao SAP HANA: {redigir(str(exc))}") from None


def _chave(nome: str) -> str:
    texto = unicodedata.normalize("NFKD", str(nome)).encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]", "", texto)


def resolver_colunas(colunas: list[str], override: dict | None = None):
    por_chave = {_chave(c): c for c in colunas}
    mapa: dict[str, str] = {}
    for campo in TECHNICAL:
        escolhida = (override or {}).get(campo)
        if escolhida and escolhida in colunas:
            mapa[campo] = escolhida
            continue
        for cand in (*KSB1_ALIASES.get(campo, ()), *TECHNICAL[campo]):
            if _chave(cand) in por_chave:
                mapa[campo] = por_chave[_chave(cand)]
                break
    faltam = [c for c in REQUIRED if c not in mapa]
    if not ({"year", "period"} <= mapa.keys() or "posting_date" in mapa):
        faltam.append("year+period ou posting_date")
    return mapa, faltam


def _simples(valor):
    if valor is None or isinstance(valor, (int, float, Decimal, date, datetime)):
        return valor
    return str(valor)


def testar(override: dict | None = None) -> dict:
    origem = _origem()
    conn = conectar()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {origem} LIMIT {SAMPLE_ROWS}")
        nomes = [d[0] for d in cur.description]
        tipos = [str(d[1]) for d in cur.description]
        amostra = [[_simples(v) for v in linha] for linha in cur.fetchall()]
    except Exception as exc:
        raise Falha(f"Falha ao ler a view: {redigir(str(exc))}") from None
    finally:
        conn.close()
    mapa, faltam = resolver_colunas(nomes, override)
    inverso = {col: campo for campo, col in mapa.items()}
    return {"colunas": list(zip(nomes, tipos, [inverso.get(n) for n in nomes])), "mapa": mapa, "faltam": faltam,
            "amostra": (nomes, amostra)}


def _filtro(mapa: dict, ano: int, empresa: str | None):
    if "year" in mapa:
        clausulas = [f"CAST({_ident(mapa['year'], 'Coluna')} AS NVARCHAR(10)) = ?"]
        params: list = [str(ano)]
    else:
        clausulas = [f"YEAR({_ident(mapa['posting_date'], 'Coluna')}) = ?"]
        params = [ano]
    if empresa and "company" in mapa:
        clausulas.append(f"CAST({_ident(mapa['company'], 'Coluna')} AS NVARCHAR(20)) = ?")
        params.append(empresa)
    return " AND ".join(clausulas), params


def montar_planilha(ano: int, empresa: str | None, override: dict | None = None):
    """Busca o KSB1 do ano (e da empresa) e devolve (bytes do .xlsx, nº de linhas)."""
    try:
        from openpyxl import Workbook
    except ImportError:
        raise Falha("Pacote openpyxl não instalado: rode instalar.cmd (py -m pip install hdbcli openpyxl)") from None
    origem = _origem()
    conn = conectar(timeout_ms=300000)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {origem} LIMIT 0")
        nomes = [d[0] for d in cur.description]
        mapa, faltam = resolver_colunas(nomes, override)
        if faltam:
            raise Falha("A view não tem coluna para: " + ", ".join(faltam) + " — informe no parâmetro sap.ksb1_columns")
        onde, params = _filtro(mapa, ano, empresa)
        cur.execute(f"SELECT * FROM {origem} WHERE {onde}", params)
        indice = {n: i for i, n in enumerate(nomes)}
        campos = [f for f in KSB1_ALIASES if f in mapa]
        wb = Workbook(write_only=True)
        ws = wb.create_sheet("KSB1")
        ws.append([KSB1_ALIASES[f][0] for f in campos])
        total = 0
        while True:
            lote = cur.fetchmany(FETCH_SIZE)
            if not lote:
                break
            total += len(lote)
            if total > MAX_ROWS:
                raise Falha("Mais de 1.000.000 de linhas: filtre por empresa")
            for linha in lote:
                saida = []
                for f in campos:
                    v = linha[indice[mapa[f]]]
                    if isinstance(v, datetime):
                        v = v.date()
                    elif isinstance(v, bytes):
                        v = v.decode(errors="replace")
                    saida.append(v)
                ws.append(saida)
    except Falha:
        raise
    except Exception as exc:
        raise Falha(f"Falha ao ler a view: {redigir(str(exc))}") from None
    finally:
        conn.close()
    import io

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue(), total


# ---------------------------------------------------------------- sistema


def _api(caminho: str) -> str:
    base = cfg("SAP_PUSH_URL").rstrip("/")
    if not base:
        raise SystemExit("Falta SAP_PUSH_URL no sap_ksb1.env")
    return f"{base}/api/v1/sap{caminho}"


def _token() -> str:
    token = cfg("SAP_PUSH_TOKEN")
    if not token:
        raise SystemExit("Falta SAP_PUSH_TOKEN no sap_ksb1.env (o mesmo cadastrado no Railway)")
    return token


def _chamar(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detalhe = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detalhe = ""
        raise SystemExit(f"O sistema recusou o envio (HTTP {exc.code}): {detalhe}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Não foi possível falar com o sistema: {exc.reason}") from None


def mapeamento_do_sistema() -> dict:
    req = urllib.request.Request(_api("/ksb1/config"), headers={"X-Push-Token": _token()})
    return _chamar(req).get("columns") or {}


def _multipart(campos: dict, nome_arquivo: str, conteudo: bytes):
    limite = uuid.uuid4().hex
    partes = []
    for chave, valor in campos.items():
        partes.append(f'--{limite}\r\nContent-Disposition: form-data; name="{chave}"\r\n\r\n{valor}\r\n'.encode())
    partes.append(
        f'--{limite}\r\nContent-Disposition: form-data; name="file"; filename="{nome_arquivo}"\r\n'
        f"Content-Type: {XLSX}\r\n\r\n".encode()
        + conteudo
        + b"\r\n"
    )
    partes.append(f"--{limite}--\r\n".encode())
    return b"".join(partes), f"multipart/form-data; boundary={limite}"


def _mostrar_teste(r: dict) -> None:
    print("\nColunas da view:")
    for nome, tipo, campo in r["colunas"]:
        print(f"  {nome:<32} {tipo:<10} {campo or '— não usada'}")
    if r["faltam"]:
        print("\nATENÇÃO: faltou coluna para: " + ", ".join(r["faltam"]))
        print("Mande os NOMES das colunas acima (sem os dados) para ajustar o mapeamento.")
    else:
        print("\nTodos os campos necessários foram encontrados.")
    nomes, linhas = r["amostra"]
    print("\nAmostra:\n  " + " | ".join(nomes))
    for linha in linhas:
        print("  " + " | ".join("—" if v is None else str(v) for v in linha))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Envia o KSB1 do SAP HANA para o sistema de orçamento")
    ap.add_argument("--test", action="store_true", help="só conecta e mostra colunas, mapeamento e amostra")
    ap.add_argument("--year", type=int, help="ano do realizado (ex.: 2026)")
    ap.add_argument("--company", help="código da empresa (ex.: 1001); vazio = todas")
    ap.add_argument("--mode", choices=("MERGE", "REPLACE"), default="MERGE", help="MERGE atualiza; REPLACE substitui")
    ap.add_argument("--save", metavar="ARQUIVO", help="grava a planilha neste arquivo em vez de enviar")
    ap.add_argument("--local-only", action="store_true", help="não consulta o mapeamento de colunas no sistema")
    args = ap.parse_args(argv)

    carregar_env()
    if faltando():
        raise SystemExit("Faltam no sap_ksb1.env: " + ", ".join(faltando()))
    override: dict = {}
    if not args.local_only and cfg("SAP_PUSH_TOKEN") and cfg("SAP_PUSH_URL"):
        override = mapeamento_do_sistema()
    try:
        if args.test:
            _mostrar_teste(testar(override))
            return 0
        if not args.year:
            ap.error("informe --year (ou use --test)")
        empresa = (args.company or "").strip() or None
        print(f"Buscando o KSB1 de {args.year}" + (f" (empresa {empresa})" if empresa else "") + " no SAP HANA…")
        conteudo, total = montar_planilha(args.year, empresa, override)
    except Falha as exc:
        raise SystemExit(f"Erro: {exc}") from None
    if not total:
        raise SystemExit("A view não trouxe lançamentos para esse filtro: nada foi enviado.")
    print(f"{total:,} lançamentos lidos.".replace(",", "."))
    if args.save:
        Path(args.save).write_bytes(conteudo)
        print(f"Planilha gravada em {args.save} (não enviada).")
        return 0
    corpo, tipo = _multipart(
        {"year": str(args.year), "company_code": empresa or "", "mode": args.mode}, f"ksb1_{args.year}.xlsx", conteudo
    )
    req = urllib.request.Request(
        _api("/ksb1/push"), data=corpo, method="POST", headers={"Content-Type": tipo, "X-Push-Token": _token()}
    )
    lote = _chamar(req)
    print(f"Importação #{lote['id']} criada. Revise e confirme em: {cfg('SAP_PUSH_URL').rstrip('/')}/importacoes/{lote['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
