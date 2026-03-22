@echo off
setlocal
set "ROOT=%~dp0.."
cd /d "%ROOT%"
call ".venv\Scripts\activate.bat"
python -m tk_listing_workflow.cli run-feishu-image-worker --tasks-root runtime/tasks --poll-interval 30