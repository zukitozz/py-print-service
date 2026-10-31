@echo off
cd /d "%~dp0"
git fetch origin main >nul 2>&1
for /f %%i in ('git rev-parse HEAD') do set LOCAL=%%i
for /f %%i in ('git rev-parse origin/main') do set REMOTE=%%i
if "%LOCAL%"=="%REMOTE%" exit /b 0
git pull origin main
pip install -r requirements.txt
pm2 restart print-service
