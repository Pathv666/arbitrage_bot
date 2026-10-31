param([switch]$PublicOnly, [switch]$Watch, [switch]$Notify, [switch]$TestNotification)
$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
Set-Location $repo
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing .venv; run setup.ps1." }
$arguments = @("-m", "p2p_arb_bot.uni_review")
if ($PublicOnly) { $arguments += "--public-only" }
else { $arguments += "--load-local-env" }
if ($Watch) { $arguments += "--watch" }
if ($Notify -or $TestNotification) { $arguments += "--notify" }
if ($TestNotification) { $arguments += "--test-notification" }
Write-Host "UNI checkpoint: SPOT GET-only. No orders, transfers, withdrawals or permission changes."
& $python @arguments
if ($LASTEXITCODE -eq 2) { Write-Host "BLOCKED: review the private uni_readonly_report.json; no sale is authorized." -ForegroundColor Yellow }
exit $LASTEXITCODE
