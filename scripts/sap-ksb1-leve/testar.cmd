@echo off
REM Testa a conexao com o SAP HANA (so leitura): mostra colunas, mapeamento e uma amostra de 3 linhas. Nao envia nada.
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
%PY% sap_ksb1.py --test
pause
