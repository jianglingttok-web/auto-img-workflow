@echo off
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"
call ".venv\Scripts\activate.bat"
powershell -ExecutionPolicy Bypass -File "%ROOT%\scripts\check_env.ps1" -ProjectRoot "%ROOT%"
if errorlevel 1 (
  echo.
  echo Environment check failed. Fix the blocking items above first.
  exit /b 1
)
call "%ROOT%\scripts\start_local_feishu_loop.bat"
echo.
echo auto-img-workflow local loop started.
