@echo off
REM Instala os dois pacotes de que o script precisa (uma vez por computador).
set PY=py
where py >nul 2>nul || set PY=python
%PY% -m pip install hdbcli openpyxl
echo.
echo Se apareceu "Successfully installed" (ou "already satisfied"), esta pronto. Proximo passo: testar.cmd
pause
