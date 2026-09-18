@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" goto :run_update

echo Starting first-time setup ...
set "ASSET_SETUP_NO_PAUSE=1"
call setup.bat
if errorlevel 1 exit /b 1
set "ASSET_SETUP_NO_PAUSE="

:run_update
".venv\Scripts\python.exe" update_watchlist.py
if errorlevel 1 goto :update_failed

".venv\Scripts\python.exe" update_portfolio.py %*
if errorlevel 1 goto :update_failed

where git >nul 2>nul
if errorlevel 1 goto :git_missing

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 goto :repository_missing

git add -- docs/data/portfolio.enc.json docs/data/watchlist.json
if errorlevel 1 goto :git_failed

git diff --cached --quiet -- docs/data/portfolio.enc.json docs/data/watchlist.json
if not errorlevel 1 goto :no_changes

git commit -m "Update watchlist and encrypted yield monitor"
if errorlevel 1 goto :commit_failed

git push origin HEAD
if errorlevel 1 goto :push_failed

echo.
echo Upload completed. Firebase Hosting and GitHub Pages will update after Actions finishes.
pause
exit /b 0

:update_failed
echo.
echo [ERROR] Update stopped. Nothing was uploaded to GitHub.
pause
exit /b 1

:git_missing
echo.
echo The encrypted data was updated locally, but Git is not installed.
echo Download Git for Windows from https://git-scm.com/download/win
pause
exit /b 2

:repository_missing
echo.
echo The encrypted data was updated locally, but this folder is not connected to GitHub.
echo Complete the one-time steps in GITHUB_SETUP.md, then run this file again.
pause
exit /b 2

:no_changes
echo.
echo No portfolio data changed, so no new Git commit was created.
pause
exit /b 0

:git_failed
echo.
echo [ERROR] Git could not stage the encrypted data file.
pause
exit /b 1

:commit_failed
echo.
echo [ERROR] Git commit failed. Check the user.name and user.email instructions in GITHUB_SETUP.md.
pause
exit /b 1

:push_failed
echo.
echo [ERROR] GitHub upload failed. Check your internet connection and GitHub sign-in.
pause
exit /b 1
