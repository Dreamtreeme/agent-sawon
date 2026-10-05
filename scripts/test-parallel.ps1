param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonExe = Join-Path $RepoRoot '.venv/Scripts/python.exe'
if (-not (Test-Path $PythonExe)) { $PythonExe = 'python' }

Push-Location $RepoRoot
try {
    & $PythonExe -m pytest -n 4 --dist load --max-worker-restart=0 @PytestArgs
    if ($LASTEXITCODE -ne 0) { throw "Parallel tests failed" }
} finally {
    Pop-Location
}
