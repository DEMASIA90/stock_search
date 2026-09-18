@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :run_tests
echo [ERROR] Run setup.bat first.
pause
exit /b 1

:run_tests
".venv\Scripts\python.exe" -m unittest discover -s tests -v
if errorlevel 1 goto :failed

where node >nul 2>nul
if errorlevel 1 goto :success

node --check docs\assets\app.mjs
if errorlevel 1 goto :failed
node --check docs\assets\crypto.mjs
if errorlevel 1 goto :failed

:success
echo All tests passed.
pause
exit /b 0

:failed
echo [ERROR] One or more tests failed.
pause
exit /b 1
