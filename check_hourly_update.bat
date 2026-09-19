@echo off
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" goto :run
call setup.bat
if errorlevel 1 exit /b 1

:run
".venv\Scripts\python.exe" check_hourly_update.py
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
