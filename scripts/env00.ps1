param(
    [ValidateSet('Imports', 'Database', 'Sync', 'Reproduce')]
    [string]$Action = 'Imports'
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$uvExecutable = Join-Path $projectRoot '.tools\uv\uv.exe'
if (-not (Test-Path -LiteralPath $uvExecutable)) {
    throw 'Project-local uv is missing. Follow the ENV-00 setup documentation.'
}
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache\uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.tools\python'
$env:LANGGRAPH_STRICT_MSGPACK = 'true'
$env:EASYTRAVEL_MODEL_MODE = 'mock'

if ($Action -eq 'Sync') {
    & $uvExecutable sync --frozen
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $uvExecutable pip check
    exit $LASTEXITCODE
}

if ($Action -eq 'Reproduce') {
    # Create a second environment without deleting or changing the first one.
    $env:UV_PROJECT_ENVIRONMENT = Join-Path $projectRoot '.venv-repro'
    & $uvExecutable sync --frozen --offline
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & $uvExecutable pip check --python (Join-Path $env:UV_PROJECT_ENVIRONMENT 'Scripts\python.exe')
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

if ($Action -eq 'Database') {
    if (-not (Test-Path -LiteralPath '.env')) {
        throw 'Local .env is missing; the database check was not run.'
    }
    & $uvExecutable run --frozen --no-sync --env-file .env python scripts/verify_env.py --database
} else {
    & $uvExecutable run --frozen --no-sync python scripts/verify_env.py
}
exit $LASTEXITCODE
