@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" goto :find_python
".venv\Scripts\python.exe" -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13),(3,14)) else 1)" >nul 2>&1
if not errorlevel 1 goto :install_packages

echo [ERROR] The existing .venv is invalid or uses an unsupported Python version.
echo Rename or remove the .venv folder, then run setup.bat again.
goto :failed

:find_python
set "PY_CMD="

py -3.14 -c "import sys" >nul 2>&1
if not errorlevel 1 set "PY_CMD=py -3.14"

if not defined PY_CMD py -3.13 -c "import sys" >nul 2>&1
if not defined PY_CMD if not errorlevel 1 set "PY_CMD=py -3.13"

if not defined PY_CMD py -3.12 -c "import sys" >nul 2>&1
if not defined PY_CMD if not errorlevel 1 set "PY_CMD=py -3.12"

if not defined PY_CMD py -3.11 -c "import sys" >nul 2>&1
if not defined PY_CMD if not errorlevel 1 set "PY_CMD=py -3.11"

if not defined PY_CMD python -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13),(3,14)) else 1)" >nul 2>&1
if not defined PY_CMD if not errorlevel 1 set "PY_CMD=python"

if not defined PY_CMD python3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] in ((3,11),(3,12),(3,13),(3,14)) else 1)" >nul 2>&1
if not defined PY_CMD if not errorlevel 1 set "PY_CMD=python3"

if not defined PY_CMD goto :python_missing

echo [1/3] Creating a private Python environment with %PY_CMD% ...
%PY_CMD% -m venv ".venv"
if errorlevel 1 goto :failed

:install_packages
echo [2/3] Updating the package installer ...
".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 goto :failed

echo [3/3] Installing account inquiry and encryption packages ...
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo Setup completed. Run publish_update.bat next.
if defined ASSET_SETUP_NO_PAUSE exit /b 0
pause
exit /b 0

:python_missing
echo.
echo [ERROR] Python 3.11, 3.12, 3.13, or 3.14 is not installed.
echo The Python Launcher exists, but no actual Python runtime was found.
echo Download the 64-bit Windows installer from:
echo https://www.python.org/downloads/windows/
echo During installation, enable "Add python.exe to PATH".
goto :failed

:failed
echo.
echo Setup stopped. No portfolio data was changed.
if defined ASSET_SETUP_NO_PAUSE exit /b 1
pause
exit /b 1
