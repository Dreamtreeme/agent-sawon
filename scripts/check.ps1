$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BenchRoot = Join-Path (Split-Path -Parent $RepoRoot) 'agent-sawon-bench'
$PythonExe = Join-Path $RepoRoot '.venv/Scripts/python.exe'
foreach ($Repository in @($RepoRoot, $BenchRoot)) {
    Push-Location $Repository
    try {
        & $PythonExe -m ruff check .
        if ($LASTEXITCODE -ne 0) { throw "Lint failed: $Repository" }
        & $PythonExe -m ruff format --check .
        if ($LASTEXITCODE -ne 0) { throw "Formatting failed: $Repository" }
        & $PythonExe -m pytest
        if ($LASTEXITCODE -ne 0) { throw "Tests failed: $Repository" }
    } finally { Pop-Location }
}
