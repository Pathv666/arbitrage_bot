$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
Set-Location $repo

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Missing .venv. Run .\scripts\windows\setup.ps1 first."
}

Write-Host "Running tests..." -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "test.ps1")
if ($LASTEXITCODE -ne 0) { throw "Tests failed." }

Write-Host "Running read-only live endpoint smoke..." -ForegroundColor Cyan
& $python scripts\smoke_endpoints.py
if ($LASTEXITCODE -ne 0) { throw "Live smoke failed." }

Write-Host "Local smoke completed successfully." -ForegroundColor Green
