@echo off
setlocal
set "ROOT=%~dp0.."
start "tk-feishu-long-connection" cmd /k call "%ROOT%\.venv\Scripts\activate.bat" ^&^& cd /d "%ROOT%" ^&^& python -m tk_listing_workflow.cli run-feishu-long-connection --tasks-root runtime/tasks --log-level INFO
