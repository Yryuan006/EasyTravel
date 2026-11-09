# Prepare only the dedicated ENV-00 instance. No existing data is deleted.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

& docker info --format '{{.ServerVersion}}'
if ($LASTEXITCODE -ne 0) { throw 'Docker engine is unavailable; no database preparation was performed.' }

$containerNames = @(& docker ps -a --format '{{.Names}}')
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Docker containers.' }
if ($containerNames -contains 'easytravel-env00-postgres') {
    $scope = & docker inspect easytravel-env00-postgres --format '{{index .Config.Labels "com.easytravel.scope"}}'
    if ($LASTEXITCODE -ne 0 -or $scope -ne 'env00') { throw 'Container name belongs to an unrecognized resource.' }
}
$volumeNames = @(& docker volume ls --format '{{.Name}}')
if ($LASTEXITCODE -ne 0) { throw 'Could not inspect Docker volumes.' }
if ($volumeNames -contains 'easytravel_env00_pgdata') {
    $scope = & docker volume inspect easytravel_env00_pgdata --format '{{index .Labels "com.easytravel.scope"}}'
    if ($LASTEXITCODE -ne 0 -or $scope -ne 'env00') { throw 'Volume name belongs to an unrecognized resource.' }
}

$privateFiles = @('.env', '.local/postgres-admin-password', '.local/postgres-init/01-test-database.sql')
$existingFiles = @($privateFiles | Where-Object { Test-Path -LiteralPath $_ })
if ($existingFiles.Count -eq 0) {
    # Never silently attach fresh credentials to an existing database volume.
    if ($volumeNames -contains 'easytravel_env00_pgdata') {
        throw 'Existing ENV-00 volume has no matching local credentials; do not reset or overwrite it.'
    }
    New-Item -ItemType Directory -Path '.local/postgres-init' -Force | Out-Null
    $adminPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    $testPassword = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
    [IO.File]::WriteAllText((Join-Path $projectRoot '.local/postgres-admin-password'), $adminPassword)
    $initializationSql = @"
CREATE ROLE easytravel_env00 LOGIN PASSWORD '$testPassword' NOSUPERUSER NOCREATEDB NOCREATEROLE;
CREATE DATABASE easytravel_env00_test OWNER easytravel_env00;
ALTER DATABASE easytravel_env00_test SET timezone TO 'Asia/Shanghai';
"@
    [IO.File]::WriteAllText((Join-Path $projectRoot '.local/postgres-init/01-test-database.sql'), $initializationSql)
    $environmentFile = @"
EASYTRAVEL_TEST_DATABASE_URL=postgresql://easytravel_env00:${testPassword}@127.0.0.1:55432/easytravel_env00_test
EASYTRAVEL_MODEL_MODE=mock
LANGGRAPH_STRICT_MSGPACK=true
"@
    [IO.File]::WriteAllText((Join-Path $projectRoot '.env'), $environmentFile)
    Remove-Variable adminPassword,testPassword,initializationSql,environmentFile
    Write-Output 'Created ignored local credentials for the dedicated ENV-00 instance; values are not printed.'
} elseif ($existingFiles.Count -ne $privateFiles.Count) {
    throw 'Partial local credential state found; refusing to overwrite files.'
}

& docker compose -f compose.env00.yaml config --quiet
if ($LASTEXITCODE -ne 0) { throw 'Compose validation failed.' }
& docker compose -f compose.env00.yaml up -d --wait --wait-timeout 60
if ($LASTEXITCODE -ne 0) { throw 'ENV-00 instance did not become healthy. Existing data has not been deleted.' }
Write-Output 'Dedicated test database is ready at 127.0.0.1:55432; credentials remain local and ignored.'
