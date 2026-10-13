@echo off
REM Busca o KSB1 no SAP HANA e envia ao sistema de orcamento (cria uma importacao para revisar e confirmar).
REM   enviar.cmd 2026         -> o ano, todas as empresas
REM   enviar.cmd 2026 1001    -> o ano de uma empresa (1001 ATEM, 1012 NAVE, 2001 REAM)
REM Sem argumentos, o script pergunta.
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
set ANO=%1
set EMPRESA=%2
if "%ANO%"=="" set /p ANO=Ano (ex.: 2026): 
if "%EMPRESA%"=="" if "%1"=="" set /p EMPRESA=Empresa (Enter = todas; 1001, 1012 ou 2001): 
if "%EMPRESA%"=="" (
  %PY% sap_ksb1.py --year %ANO%
) else (
  %PY% sap_ksb1.py --year %ANO% --company %EMPRESA%
)
pause
