$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
$callbackStdout = Join-Path $root 'runtime\callback_server_stdout.log'
$callbackStderr = Join-Path $root 'runtime\callback_server_stderr.log'
$tunnelStdout = Join-Path $root 'runtime\cloudflared_stdout.log'
$tunnelStderr = Join-Path $root 'runtime\cloudflared_stderr.log'
$urlFile = Join-Path $root 'runtime\public_callback_url.txt'

if (-not (Test-Path $python)) {
    throw "Python venv not found: $python"
}

$cloudflared = "$env:ProgramFiles(x86)\cloudflared\cloudflared.exe"
if (-not (Test-Path $cloudflared)) {
    $cloudflared = "$env:ProgramFiles\cloudflared\cloudflared.exe"
}
if (-not (Test-Path $cloudflared)) {
    $cmd = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($cmd) {
        $cloudflared = $cmd.Source
    }
}
if (-not (Test-Path $cloudflared)) {
    throw "cloudflared.exe not found"
}

$listener = Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $listener) {
    $callbackCommand = "$env:PYTHONPATH='$root\src'; Set-Location '$root'; & '$python' -m tk_listing_workflow.cli serve-feishu-callback --tasks-root runtime/tasks --host 127.0.0.1 --port 8000 --callback-path /feishu/callback --health-path /healthz"
    Start-Process -FilePath 'C:\WINDOWS\System32\WindowsPowerShell\v1.0\powershell.exe' -ArgumentList '-Command', $callbackCommand -WorkingDirectory $root -RedirectStandardOutput $callbackStdout -RedirectStandardError $callbackStderr | Out-Null
    Start-Sleep -Seconds 3
}

$health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/healthz'
if (-not $health.ok) {
    throw "Local callback health check failed"
}

Start-Process -FilePath $cloudflared -ArgumentList 'tunnel','--url','http://127.0.0.1:8000' -WorkingDirectory $root -RedirectStandardOutput $tunnelStdout -RedirectStandardError $tunnelStderr | Out-Null

$url = ''
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    $combined = ''
    if (Test-Path $tunnelStdout) { $combined += Get-Content $tunnelStdout -Raw -Encoding UTF8 }
    if (Test-Path $tunnelStderr) { $combined += "`n" + (Get-Content $tunnelStderr -Raw -Encoding UTF8) }
    $match = [regex]::Match($combined, 'https://[a-z0-9\-]+\.trycloudflare\.com')
    if ($match.Success) {
        $url = $match.Value
        break
    }
}

if (-not $url) {
    throw "Failed to capture public tunnel URL. Check $tunnelStderr"
}

Set-Content -Path $urlFile -Value $url -Encoding UTF8
[PSCustomObject]@{
    health = 'http://127.0.0.1:8000/healthz'
    callback = "$url/feishu/callback"
    health_public = "$url/healthz"
    url_file = $urlFile
    cloudflared = $cloudflared
} | ConvertTo-Json -Depth 4
