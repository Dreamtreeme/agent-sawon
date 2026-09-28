param([switch]$Gui)
$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BenchRoot = Join-Path (Split-Path -Parent $RepoRoot) 'agent-sawon-bench'
if (!(Test-Path -LiteralPath (Join-Path $BenchRoot 'interfaces/pyproject.toml'))) {
    throw 'Place agent-sawon-bench next to this repository before setup.'
}
Push-Location $RepoRoot
try {
    if (!(Test-Path -LiteralPath '.venv/Scripts/python.exe')) {
        py -3.13 -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 environment creation failed.' }
    }
    $PythonExe = Join-Path $RepoRoot '.venv/Scripts/python.exe'
    & $PythonExe -m pip install -e "$BenchRoot/interfaces" -e "$BenchRoot[dev]" -e '.[dev]'
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
    if ($Gui) {
        & $PythonExe -m pip install -e "$BenchRoot[gui]"
        if ($LASTEXITCODE -ne 0) { throw 'GUI dependency installation failed.' }
    }
} finally { Pop-Location }
