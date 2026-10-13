@echo off
REM Envia o KSB1 do SAP HANA ao sistema (roda neste computador; ver backend/app/sap_push.py).
REM   sap-ksb1.cmd teste            -> conecta e mostra colunas e amostra
REM   sap-ksb1.cmd 2026             -> envia o ano (todas as empresas)
REM   sap-ksb1.cmd 2026 1001        -> envia o ano de uma empresa
cd /d "%~dp0..\backend"
if /i "%1"=="teste" (
  .venv\Scripts\python.exe -m app.sap_push --test
) else if "%2"=="" (
  .venv\Scripts\python.exe -m app.sap_push --year %1
) else (
  .venv\Scripts\python.exe -m app.sap_push --year %1 --company %2
)
pause
