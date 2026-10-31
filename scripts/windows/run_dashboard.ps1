$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
Set-Location $repo

$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    throw "Missing .venv. Run .\scripts\windows\setup.ps1 first."
}

Write-Host "Starting local dashboard. Default: http://127.0.0.1:8000" -ForegroundColor Green
& $python -m p2p_arb_bot.web
