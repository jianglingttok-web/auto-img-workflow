@echo off
setlocal
powershell -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run-feishu-image-worker*' -or $_.CommandLine -like '*run-feishu-long-connection*' } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }"
echo Stopped local Feishu worker and long-connection receiver.
