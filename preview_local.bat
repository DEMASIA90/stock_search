@echo off
setlocal
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto :start_preview
echo [ERROR] Run setup.bat first.
pause
exit /b 1

:start_preview
echo Local preview: http://127.0.0.1:8765
echo Press Ctrl+C in this window to stop the preview server.
start "" "http://127.0.0.1:8765"
".venv\Scripts\python.exe" -m http.server 8765 --bind 127.0.0.1 --directory docs
