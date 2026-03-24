param(
    [string]$ProjectRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

Write-Host ""
Write-Host "auto-img-workflow runtime status" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot"
Write-Host ""

$procs = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -like '*run-feishu-image-worker*' -or $_.CommandLine -like '*run-feishu-long-connection*'
} | Select-Object ProcessId, ParentProcessId, Name, CommandLine

if (-not $procs) {
    Write-Host "[FAIL] No worker or long-connection process is running." -ForegroundColor Red
    exit 1
}

foreach ($proc in $procs) {
    Write-Host ("[RUNNING] PID {0} {1}" -f $proc.ProcessId, $proc.CommandLine) -ForegroundColor Green
}

$tasksRoot = Join-Path $ProjectRoot 'runtime\tasks'
$eventsDir = Join-Path $tasksRoot '_callback_events'
if (Test-Path $eventsDir) {
    $latestEvent = Get-ChildItem -Path $eventsDir -Filter 'event_*.json' | Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($latestEvent) {
        Write-Host ""
        Write-Host ("Latest callback event: {0} ({1})" -f $latestEvent.Name, $latestEvent.LastWriteTime) -ForegroundColor Yellow
    }
}

exit 0
