@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "PYTHONUTF8=1"

if exist ".venv\Scripts\python.exe" goto :check_repository

echo Starting first-time setup ...
set "ASSET_SETUP_NO_PAUSE=1"
call setup.bat
if errorlevel 1 exit /b 1
set "ASSET_SETUP_NO_PAUSE="

:check_repository
where git >nul 2>nul
if errorlevel 1 goto :git_missing

git rev-parse --is-inside-work-tree >nul 2>nul
if errorlevel 1 goto :repository_missing

for /f "delims=" %%B in ('git branch --show-current') do set "CURRENT_BRANCH=%%B"
if not defined CURRENT_BRANCH goto :branch_missing

rem Never replace a locally generated encrypted history that has not been committed yet.
git diff --quiet -- docs/data/portfolio.enc.json docs/data/watchlist.json
if errorlevel 1 goto :local_data_dirty
git diff --cached --quiet -- docs/data/portfolio.enc.json docs/data/watchlist.json
if errorlevel 1 goto :local_data_dirty

echo Syncing remote cumulative history before local update ...
git fetch origin "%CURRENT_BRANCH%"
if errorlevel 1 goto :sync_failed

git merge --ff-only "origin/%CURRENT_BRANCH%"
if errorlevel 1 goto :sync_failed

:run_update
".venv\Scripts\python.exe" update_watchlist.py
if errorlevel 1 goto :update_failed

".venv\Scripts\python.exe" update_portfolio.py %*
if errorlevel 1 goto :update_failed

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
echo Git is not installed, so remote cumulative history cannot be synchronized safely.
echo Download Git for Windows from https://git-scm.com/download/win
pause
exit /b 2

:repository_missing
echo.
echo This folder is not connected to GitHub, so cumulative history cannot be synchronized safely.
echo Run this file inside the existing stock_search Git repository.
pause
exit /b 2

:branch_missing
echo.
echo [ERROR] No current Git branch was found. Check out the repository branch before updating.
pause
exit /b 1

:local_data_dirty
echo.
echo [ERROR] portfolio.enc.json or watchlist.json has uncommitted local changes.
echo Commit/push or restore those generated files first. The update was stopped to prevent history loss.
pause
exit /b 1

:sync_failed
echo.
echo [ERROR] Could not fast-forward from origin/%CURRENT_BRANCH%.
echo Resolve local/remote Git differences first. The API update was not started, so history was not overwritten.
pause
exit /b 1

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
echo [ERROR] GitHub upload failed. The new local encrypted history was kept.
echo Re-run after the Git issue is resolved; the pre-update sync check prevents silent history loss.
pause
exit /b 1
