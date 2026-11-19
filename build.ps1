$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$appPython = Join-Path $projectRoot ".venv/Scripts/python.exe"
$examplesDir = Join-Path $projectRoot "examples"
$releaseRoot = Join-Path $projectRoot "build/release-bundle"
$releaseBundle = Join-Path $releaseRoot "MatKnow"
if (-not (Test-Path -LiteralPath $appPython)) { throw "Run start.ps1 or create .venv first." }
$buildOriginalPath = $env:PATH
Push-Location -LiteralPath $projectRoot
try {
    $pythonBase = & $appPython -c "import sys; print(sys.base_prefix)"
    if ($LASTEXITCODE -ne 0) { throw "Cannot determine Python runtime directory." }
    # Keep unrelated PDF/toolkit DLLs out of PyInstaller's dependency search.
    $env:PATH = @((Join-Path $env:WINDIR "System32"), $env:WINDIR, (Join-Path $projectRoot ".venv/Scripts"), $pythonBase) -join ";"
    & $appPython -m PyInstaller --noconfirm --clean --windowed --onedir --name MatKnow --distpath $releaseRoot --paths $projectRoot --add-data "$examplesDir;examples" --exclude-module PySide6.QtNetwork --specpath build matknow/__main__.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }
    Copy-Item -LiteralPath agent.md,library_agent.md,experiment_agent.md -Destination $releaseBundle
    & $appPython scripts/collect_licenses.py (Join-Path $releaseBundle "THIRD_PARTY_NOTICES.txt")
    if ($LASTEXITCODE -ne 0) { throw "License collection failed." }
    & $appPython scripts/release_bundle.py
    if ($LASTEXITCODE -ne 0) { throw "Release audit or publishing failed." }
} finally {
    $env:PATH = $buildOriginalPath
    Pop-Location
}
