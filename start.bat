@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem UTF-8 + unbuffered: the fixed log area flushes one line at a time
set "PYTHONIOENCODING=utf-8"
set "PYTHONUNBUFFERED=1"
echo Starting ledger server, listening on 0.0.0.0:8765 ...
echo On your phone open the URL printed below - same WiFi required.
echo Access log: time / client IP / device; fixed 10-line area, no scrolling.
echo.
where python >nul 2>nul
if %errorlevel%==0 (
  python ledger_server.py
) else (
  py -3 ledger_server.py
)
echo.
echo Server stopped.
pause
