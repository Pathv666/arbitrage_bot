param(
    [string]$Python = "py"
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent | Split-Path -Parent)

function Invoke-Python {
    param([Parameter(ValueFromRemainingArguments = $true)][string[]]$PythonArgs)
    if ($Python -eq "py") {
        & py -3.11 @PythonArgs
        if ($LASTEXITCODE -ne 0) { throw "Python command failed." }
    } else {
        & $Python @PythonArgs
        if ($LASTEXITCODE -ne 0) { throw "Python command failed." }
    }
}

Write-Host "== P2P Arb V1 local setup (Windows) ==" -ForegroundColor Cyan

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv with Python 3.11..."
    Invoke-Python -m venv .venv
}

$venvPython = Resolve-Path ".venv\Scripts\python.exe"
Write-Host "Installing package and dev dependencies..."
& $venvPython -m pip install --upgrade pip
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed." }
& $venvPython -m pip install -e ".[dev]"
if ($LASTEXITCODE -ne 0) { throw "dependency install failed." }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example." -ForegroundColor Green
} else {
    Write-Host ".env already exists; left unchanged." -ForegroundColor Yellow
}

Write-Host "Running unit/regression tests..."
& $venvPython -m pytest
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

Write-Host ""
Write-Host "Setup complete." -ForegroundColor Green
Write-Host "Next:"
Write-Host "  1) Review .env (especially MAX_FIAT, filters and NETWORK_COSTS_JSON)."
Write-Host "  2) Run .\scripts\windows\smoke.ps1"
Write-Host "  3) Run .\scripts\windows\run_scanner.ps1"
Write-Host "  4) Optional dashboard: .\scripts\windows\run_dashboard.ps1"
Write-Host ""
Write-Host "NETWORK_COSTS_JSON stays empty by default: no route is marked feasible without verified network data." -ForegroundColor Yellow
