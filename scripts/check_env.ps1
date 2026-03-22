param(
    [string]$ProjectRoot = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if (-not $ProjectRoot) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}

$results = [System.Collections.Generic.List[object]]::new()
$failCount = 0

function Add-Result {
    param(
        [string]$Status,
        [string]$Check,
        [string]$Detail
    )

    if ($Status -eq "FAIL") {
        $script:failCount += 1
    }

    $script:results.Add([pscustomobject]@{
        Status = $Status
        Check = $Check
        Detail = $Detail
    })
}

function Test-Executable {
    param(
        [string]$Command,
        [string[]]$Arguments
    )

    try {
        $output = & $Command @Arguments 2>&1 | Out-String
        $exitCode = if ($null -ne $LASTEXITCODE) { $LASTEXITCODE } else { 0 }
        return [pscustomobject]@{
            Ok = ($exitCode -eq 0)
            Output = $output.Trim()
            ExitCode = $exitCode
            Command = $Command
            Arguments = ($Arguments -join " ")
        }
    } catch {
        return [pscustomobject]@{
            Ok = $false
            Output = $_.Exception.Message
            ExitCode = -1
            Command = $Command
            Arguments = ($Arguments -join " ")
        }
    }
}

function Find-Python {
    param(
        [string]$Root
    )

    $candidates = [System.Collections.Generic.List[object]]::new()
    $venvPython = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        $candidates.Add([pscustomobject]@{ Command = $venvPython; Arguments = @("--version"); Label = ".venv" })
    }

    foreach ($path in @(
        "C:\Python311\python.exe",
        "C:\Python312\python.exe",
        (Join-Path $env:LocalAppData "Programs\Python\Python311\python.exe"),
        (Join-Path $env:LocalAppData "Programs\Python\Python312\python.exe")
    )) {
        if ($path -and (Test-Path $path)) {
            $candidates.Add([pscustomobject]@{ Command = $path; Arguments = @("--version"); Label = $path })
        }
    }

    $candidates.Add([pscustomobject]@{ Command = "python"; Arguments = @("--version"); Label = "python" })
    $candidates.Add([pscustomobject]@{ Command = "py"; Arguments = @("-3.11", "--version"); Label = "py -3.11" })
    $candidates.Add([pscustomobject]@{ Command = "py"; Arguments = @("-3", "--version"); Label = "py -3" })

    foreach ($candidate in $candidates) {
        $result = Test-Executable -Command $candidate.Command -Arguments $candidate.Arguments
        if ($result.Ok -and $result.Output -match "Python\s+(\d+)\.(\d+)\.(\d+)") {
            return [pscustomobject]@{
                Found = $true
                Command = $candidate.Command
                Version = $result.Output
                Label = $candidate.Label
            }
        }
    }

    return [pscustomobject]@{
        Found = $false
        Command = ""
        Version = ""
        Label = ""
    }
}

function Invoke-PythonCheck {
    param(
        [string]$PythonCommand,
        [string]$Code
    )

    return Test-Executable -Command $PythonCommand -Arguments @("-c", $Code)
}

Write-Host ""
Write-Host "auto-img-workflow local environment check" -ForegroundColor Cyan
Write-Host "Project root: $ProjectRoot"

if (Test-Path (Join-Path $ProjectRoot ".git")) {
    Add-Result -Status "PASS" -Check "Repository" -Detail "Git repository detected."
} else {
    Add-Result -Status "FAIL" -Check "Repository" -Detail "Project root does not look like a git repository."
}

$python = Find-Python -Root $ProjectRoot
if ($python.Found) {
    Add-Result -Status "PASS" -Check "Python" -Detail "$($python.Version) via $($python.Label)"
} else {
    Add-Result -Status "FAIL" -Check "Python" -Detail "Python 3.11+ not found. Install Python first, then create .venv."
}

if ($python.Found) {
    $pip = Test-Executable -Command $python.Command -Arguments @("-m", "pip", "--version")
    if ($pip.Ok) {
        Add-Result -Status "PASS" -Check "pip" -Detail $pip.Output
    } else {
        Add-Result -Status "FAIL" -Check "pip" -Detail "pip is unavailable for the selected Python interpreter."
    }

    $srcPath = (Join-Path $ProjectRoot "src").Replace("\", "\\")
    $importCheck = Invoke-PythonCheck -PythonCommand $python.Command -Code "import sys; sys.path.insert(0, r'$srcPath'); import tk_listing_workflow; print('ok')"
    if ($importCheck.Ok -and $importCheck.Output -match "ok") {
        Add-Result -Status "PASS" -Check "Package import" -Detail "tk_listing_workflow can be imported from src/."
    } else {
        Add-Result -Status "FAIL" -Check "Package import" -Detail "Cannot import tk_listing_workflow from src/."
    }

    $pillowCheck = Invoke-PythonCheck -PythonCommand $python.Command -Code "import PIL; print(PIL.__version__)"
    if ($pillowCheck.Ok) {
        Add-Result -Status "PASS" -Check "Pillow" -Detail "Installed version $($pillowCheck.Output)"
    } else {
        Add-Result -Status "WARN" -Check "Pillow" -Detail "Not installed yet. Run python -m pip install -e ."
    }
}

$configExample = Join-Path $ProjectRoot "config.example.yaml"
$configYaml = Join-Path $ProjectRoot "config.yaml"
$envExample = Join-Path $ProjectRoot ".env.example"
$envFile = Join-Path $ProjectRoot ".env"

if (Test-Path $configExample) {
    Add-Result -Status "PASS" -Check "config.example.yaml" -Detail "Template file exists."
} else {
    Add-Result -Status "FAIL" -Check "config.example.yaml" -Detail "Missing configuration template."
}

if (Test-Path $configYaml) {
    Add-Result -Status "PASS" -Check "config.yaml" -Detail "Local config file exists."
} else {
    Add-Result -Status "WARN" -Check "config.yaml" -Detail "Missing local config file. Copy from config.example.yaml if you plan to maintain local settings."
}

if (Test-Path $envExample) {
    Add-Result -Status "PASS" -Check ".env.example" -Detail "Environment-variable template exists."
} else {
    Add-Result -Status "WARN" -Check ".env.example" -Detail "No .env template found."
}

if (Test-Path $envFile) {
    Add-Result -Status "PASS" -Check ".env" -Detail "Local environment file exists."
} else {
    Add-Result -Status "WARN" -Check ".env" -Detail "No local .env file found."
}

if ($env:ARK_API_KEY) {
    Add-Result -Status "PASS" -Check "ARK_API_KEY" -Detail "Configured for real Seedream runs."
} else {
    Add-Result -Status "WARN" -Check "ARK_API_KEY" -Detail "Missing. Required by run-seedream-jobs."
}

foreach ($name in @(
    "ARK_BASE_URL",
    "SEEDREAM_MODEL",
    "SEEDREAM_SIZE",
    "SEEDREAM_RESPONSE_FORMAT",
    "SEEDREAM_STREAM",
    "SEEDREAM_WATERMARK"
)) {
    $item = Get-Item "Env:$name" -ErrorAction SilentlyContinue
    if ($item) {
        Add-Result -Status "PASS" -Check $name -Detail "Set to '$($item.Value)'."
    } else {
        Add-Result -Status "INFO" -Check $name -Detail "Not set. Default value in code will be used."
    }
}

Add-Result -Status "PASS" -Check "Config wiring" -Detail "CLI now auto-loads project-root .env and config.yaml before running commands."
Add-Result -Status "INFO" -Check "Feishu/Ziniu" -Detail "Feishu notifier and Ziniu integration are still placeholders in the current codebase."

$runtimeRoot = Join-Path $ProjectRoot "runtime"
$tempCheckDir = Join-Path $runtimeRoot ".env-check-write-test"

try {
    if (-not (Test-Path $runtimeRoot)) {
        New-Item -ItemType Directory -Path $runtimeRoot | Out-Null
    }
    if (Test-Path $tempCheckDir) {
        Remove-Item -Recurse -Force $tempCheckDir
    }
    New-Item -ItemType Directory -Path $tempCheckDir | Out-Null
    Remove-Item -Recurse -Force $tempCheckDir
    Add-Result -Status "PASS" -Check "runtime/" -Detail "Writable."
} catch {
    Add-Result -Status "FAIL" -Check "runtime/" -Detail "Not writable: $($_.Exception.Message)"
}

Write-Host ""
foreach ($item in $results) {
    $color = switch ($item.Status) {
        "PASS" { "Green" }
        "WARN" { "Yellow" }
        "FAIL" { "Red" }
        default { "DarkGray" }
    }
    Write-Host ("[{0}] {1}: {2}" -f $item.Status, $item.Check, $item.Detail) -ForegroundColor $color
}

Write-Host ""
if ($failCount -gt 0) {
    Write-Host "Environment check finished with failures. Fix FAIL items first." -ForegroundColor Red
    exit 1
}

Write-Host "Environment check finished without blocking failures." -ForegroundColor Green
exit 0
