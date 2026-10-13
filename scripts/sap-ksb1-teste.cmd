@echo off
REM Testa a conexão com o SAP HANA (só leitura): mostra as colunas da view, o mapeamento e uma amostra.
cd /d "%~dp0..\backend"
.venv\Scripts\python.exe -m app.sap_push --test
pause
