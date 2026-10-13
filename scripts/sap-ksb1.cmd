@echo off
REM Envia o KSB1 do SAP HANA ao sistema (roda neste computador; ver backend/app/sap_push.py).
REM   sap-ksb1.cmd 2026         -> envia o ano (todas as empresas)
REM   sap-ksb1.cmd 2026 1001    -> envia o ano de uma empresa
REM Para só testar a conexão, use sap-ksb1-teste.cmd.
cd /d "%~dp0..\backend"
if "%1"=="" (
  echo Informe o ano. Exemplo: sap-ksb1.cmd 2026 1001
  pause
  exit /b 1
)
if /i "%1"=="teste" (
  .venv\Scripts\python.exe -m app.sap_push --test
) else if "%2"=="" (
  .venv\Scripts\python.exe -m app.sap_push --year %1
) else (
  .venv\Scripts\python.exe -m app.sap_push --year %1 --company %2
)
pause
