# Project-local tools only: no system PATH, registry, service, or global Git changes.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$uvVersion = '0.13.7'
$uvDigest = '2835e2ab397d0db86323b9975dce75ecd76aa9cfa0d6a2aea84734aae7f8c9b3'
$uvExecutable = Join-Path $projectRoot '.tools/uv/uv.exe'

if (-not (Test-Path -LiteralPath $uvExecutable)) {
    New-Item -ItemType Directory -Path '.tools/uv' -Force | Out-Null
    $metadata = Invoke-RestMethod -Uri "https://pypi.org/pypi/uv/$uvVersion/json"
    $wheel = $metadata.urls | Where-Object { $_.filename -match 'win_amd64\.whl$' } | Select-Object -First 1
    if (-not $wheel -or $wheel.digests.sha256 -ne $uvDigest) {
        throw 'Expected official uv Windows wheel metadata was not found.'
    }
    $archivePath = Join-Path $projectRoot '.tools/uv/bootstrap.whl'
    Invoke-WebRequest -Uri $wheel.url -OutFile $archivePath
    if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $uvDigest) {
        throw 'uv SHA256 verification failed; executable was not installed.'
    }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
    try {
        $entry = $archive.Entries | Where-Object { $_.FullName.EndsWith('/uv.exe') } | Select-Object -First 1
        if (-not $entry) { throw 'uv.exe was not found in the verified wheel.' }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $uvExecutable, $false)
    } finally {
        $archive.Dispose()
    }
}

$observedVersion = & $uvExecutable --version
if ($LASTEXITCODE -ne 0 -or $observedVersion -notmatch '^uv 0\.13\.7\s') {
    throw 'Unexpected uv version; refusing to replace an existing executable.'
}
Write-Output $observedVersion
$env:UV_CACHE_DIR = Join-Path $projectRoot '.cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $projectRoot '.tools/python'
$pythonVersion = (Get-Content -LiteralPath '.python-version' -Raw).Trim()
& $uvExecutable python install $pythonVersion --no-bin --no-registry
if ($LASTEXITCODE -ne 0) { throw 'Project-local Python installation failed.' }
& $uvExecutable sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Frozen environment synchronization failed.' }
& $uvExecutable pip check
exit $LASTEXITCODE
