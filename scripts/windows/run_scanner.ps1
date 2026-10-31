$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
Set-Location $repo

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Missing .venv. Run .\scripts\windows\setup.ps1 first."
}

$env:SCANNER_V1 = "true"
$env:NO_INPUT = "true"

Write-Host "Starting V1 scanner in READ-ONLY mode. Ctrl+C to stop." -ForegroundColor Green
& $python -m p2p_arb_bot.main
