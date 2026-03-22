@echo off
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"
call ".venv\Scriptsctivate.bat"
python -m tk_listing_workflow.cli serve-feishu-callback --tasks-root runtime/tasks --host 127.0.0.1 --port 8000 --callback-path /feishu/callback --health-path /healthz
