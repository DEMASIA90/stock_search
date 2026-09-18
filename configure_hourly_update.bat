@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :configure
echo [ERROR] Run first_deploy.bat first.
pause
exit /b 1

:configure
".venv\Scripts\python.exe" configure_hourly_update.py
if errorlevel 1 goto :failed
pause
exit /b 0

:failed
echo.
echo [ERROR] Hourly GitHub update setup failed.
pause
exit /b 1
