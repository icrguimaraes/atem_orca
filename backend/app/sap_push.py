"""Envio do KSB1 do SAP HANA para o sistema, rodando no computador de quem tem acesso ao HANA (13/10/2026).

Uso (na pasta `backend`, com a configuração em `backend/.env` — ver `.env.example`):

    python -m app.sap_push --test                       # conecta e mostra colunas, mapeamento e amostra (não envia)
    python -m app.sap_push --year 2026                  # busca o ano (todas as empresas) e envia ao sistema
    python -m app.sap_push --year 2026 --company 1001   # só uma empresa
    python -m app.sap_push --year 2026 --save ksb1.xlsx # grava a planilha local em vez de enviar (para conferir)

O envio cria um lote em Importações (mesma validação, prévia e confirmação dos arquivos): nada entra na base sem a
confirmação na tela. A senha do HANA fica só neste computador; ao sistema vai apenas a planilha, autenticada pelo token
`SAP_PUSH_TOKEN` (o mesmo cadastrado nas variáveis do Railway). Somente leitura no HANA."""

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from app.config import get_settings
from app.services import hana

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _api(path: str) -> str:
    base = (get_settings().sap_push_url or "").rstrip("/")
    if not base:
        raise SystemExit("Falta SAP_PUSH_URL no backend/.env (ex.: https://atemorca-production.up.railway.app)")
    return f"{base}/api/v1/sap{path}"


def _token() -> str:
    token = get_settings().sap_push_token
    if not token:
        raise SystemExit("Falta SAP_PUSH_TOKEN no backend/.env (o mesmo cadastrado no Railway)")
    return token


def _request(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        raise SystemExit(f"O sistema recusou o envio (HTTP {exc.code}): {detail}") from None
    except urllib.error.URLError as exc:
        raise SystemExit(f"Não foi possível falar com o sistema: {exc.reason}") from None


def _remote_override() -> dict:
    """Mapeamento de colunas cadastrado no sistema (parâmetro sap.ksb1_columns), se houver."""
    req = urllib.request.Request(_api("/ksb1/config"), headers={"X-Push-Token": _token()})
    return _request(req).get("columns") or {}


def _multipart(fields: dict[str, str], file_name: str, content: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for key, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        f"Content-Type: {XLSX}\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _print_test(result: dict) -> None:
    print("\nColunas da view:")
    for c in result["columns"]:
        label = result["mapping"][c["field"]]["label"] if c["field"] else "— não usada"
        print(f"  {c['name']:<32} {c['type']:<10} {label}")
    if result["missing"]:
        print("\nATENÇÃO: faltou coluna para: " + ", ".join(result["missing"]))
        print("Cadastre o nome da coluna no parâmetro sap.ksb1_columns (Central de dados → Parâmetros).")
    else:
        print("\nTodos os campos necessários foram encontrados.")
    print("\nAmostra:")
    print("  " + " | ".join(result["sample"]["columns"]))
    for row in result["sample"]["rows"]:
        print("  " + " | ".join("—" if v is None else str(v) for v in row))


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

    if hana.missing_env():
        raise SystemExit("Faltam no backend/.env: " + ", ".join(hana.missing_env()))
    override: dict = {}
    if not args.local_only and get_settings().sap_push_token and get_settings().sap_push_url:
        override = _remote_override()
    try:
        if args.test:
            _print_test(hana.inspect(override))
            return 0
        if not args.year:
            ap.error("informe --year (ou use --test)")
        company = (args.company or "").strip() or None
        print(f"Buscando o KSB1 de {args.year}" + (f" (empresa {company})" if company else "") + " no SAP HANA…")
        content, count = hana.ksb1_workbook(args.year, company, override)
    except hana.HanaError as exc:
        raise SystemExit(f"Erro: {exc}") from None
    if not count:
        raise SystemExit("A view não trouxe lançamentos para esse filtro: nada foi enviado.")
    print(f"{count:,} lançamentos lidos.".replace(",", "."))
    if args.save:
        Path(args.save).write_bytes(content)
        print(f"Planilha gravada em {args.save} (não enviada).")
        return 0
    body, ctype = _multipart(
        {"year": str(args.year), "company_code": company or "", "mode": args.mode}, f"ksb1_{args.year}.xlsx", content
    )
    req = urllib.request.Request(
        _api("/ksb1/push"), data=body, method="POST", headers={"Content-Type": ctype, "X-Push-Token": _token()}
    )
    batch = _request(req)
    base = (get_settings().sap_push_url or "").rstrip("/")
    print(f"Importação #{batch['id']} criada. Revise e confirme em: {base}/importacoes/{batch['id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
