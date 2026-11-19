param([string]$DataDir = "")
$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$appPython = Join-Path $projectRoot ".venv/Scripts/python.exe"
Push-Location -LiteralPath $projectRoot
try {
    if (-not (Test-Path -LiteralPath $appPython)) {
        python -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw "Cannot create Python environment." }
        & $appPython -m pip install -e .
        if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }
    }
    $appArgs = @("-m", "matknow")
    if ($DataDir) { $appArgs += @("--data-dir", $DataDir) }
    & $appPython @appArgs
    if ($LASTEXITCODE -ne 0) { throw "MatKnow exited with code $LASTEXITCODE." }
} finally {
    Pop-Location
}
