param(
    [Parameter(Mandatory=$true)][ValidateSet("Binance","OKX","Bybit","BingX")][string]$Exchange,
    [Parameter(Mandatory=$true)][string]$Network,
    [Parameter(Mandatory=$true)][decimal]$WithdrawFee,
    [Parameter(Mandatory=$true)][decimal]$MinWithdrawal,
    [bool]$DepositEnabled = $true,
    [bool]$WithdrawEnabled = $true,
    [string]$Asset = "USDT"
)

$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
Set-Location $repo
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing .venv. Run .\scripts\windows\setup.ps1 first." }

& $python -m p2p_arb_bot.network_snapshot set `
  --env ".env" `
  --exchange $Exchange `
  --asset $Asset `
  --network $Network `
  --deposit-enabled $DepositEnabled `
  --withdraw-enabled $WithdrawEnabled `
  --withdraw-fee $WithdrawFee `
  --min-withdrawal $MinWithdrawal
if ($LASTEXITCODE -ne 0) { throw "Snapshot update failed." }

Write-Host ""
Write-Host "Current snapshots:" -ForegroundColor Cyan
& $python -m p2p_arb_bot.network_snapshot --env ".env" show
Write-Host ""
Write-Host "Restart scanner after changing network snapshots." -ForegroundColor Yellow
