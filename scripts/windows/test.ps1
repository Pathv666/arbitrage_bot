$ErrorActionPreference = "Stop"
$repo = Split-Path $PSScriptRoot -Parent | Split-Path -Parent
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Missing .venv; run setup.ps1." }
$previousEnvPath = $env:ENV_PATH
$emptyEnv = [System.IO.Path]::GetTempFileName()
try {
    $env:ENV_PATH = $emptyEnv
    Push-Location $repo
    try {
        & $python -m pytest
        if ($LASTEXITCODE -ne 0) { throw "Tests failed." }
        & $python -m compileall -q src tests scripts
        if ($LASTEXITCODE -ne 0) { throw "Compile failed." }
    } finally { Pop-Location }
} finally {
    if ($null -eq $previousEnvPath) { Remove-Item Env:\ENV_PATH -ErrorAction SilentlyContinue }
    else { $env:ENV_PATH = $previousEnvPath }
    Remove-Item $emptyEnv -ErrorAction SilentlyContinue
}
