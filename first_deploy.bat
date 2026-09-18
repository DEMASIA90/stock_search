@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" goto :update_data

echo Starting first-time setup ...
set "ASSET_SETUP_NO_PAUSE=1"
call setup.bat
if errorlevel 1 exit /b 1
set "ASSET_SETUP_NO_PAUSE="

:update_data
".venv\Scripts\python.exe" update_watchlist.py
if errorlevel 1 goto :update_failed

".venv\Scripts\python.exe" update_portfolio.py
if errorlevel 1 goto :update_failed

where git >nul 2>nul
if errorlevel 1 goto :git_missing

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 goto :repository_missing

echo.
echo The following Market Watch and Yield Monitor files will be deployed:
git status --short
echo.
choice /C YN /N /M "Commit these changes and deploy to the existing GitHub and Firebase site? [Y/N] "
if errorlevel 2 goto :cancelled

git add -A
if errorlevel 1 goto :git_failed

git diff --cached --quiet
if not errorlevel 1 goto :no_changes

git commit -m "Add market watch and yield monitor"
if errorlevel 1 goto :commit_failed

git push origin HEAD
if errorlevel 1 goto :push_failed

echo.
echo Replacement uploaded. Check the GitHub Actions deployment status.
pause
exit /b 0

:cancelled
echo Deployment cancelled. Local files and encrypted data were kept.
pause
exit /b 0

:update_failed
echo.
echo [ERROR] Portfolio update failed. Nothing was committed or uploaded.
pause
exit /b 1

:git_missing
echo.
echo [ERROR] Git for Windows is not installed.
echo Download: https://git-scm.com/download/win
pause
exit /b 1

:repository_missing
echo.
echo [ERROR] The existing stock_search Git repository was not found.
pause
exit /b 1

:git_failed
echo.
echo [ERROR] Git could not stage the replacement files.
pause
exit /b 1

:no_changes
echo.
echo No Git changes were found.
pause
exit /b 0

:commit_failed
echo.
echo [ERROR] Git commit failed. Configure user.name and user.email, then retry.
pause
exit /b 1

:push_failed
echo.
echo [ERROR] GitHub push failed. Check your GitHub sign-in and network connection.
pause
exit /b 1
