param(
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [string]$BackupDir = "backups",
    [string]$BackupId = $(Get-Date -Format "yyyyMMdd_HHmmss"),
    [string]$PostgresBin,
    [switch]$PlainSql
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "PostgresUrl.psm1") -Force

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    throw "DATABASE_URL is required. Pass -DatabaseUrl or set `$env:DATABASE_URL."
}

$toolArgs = @('run', 'python', 'scripts/ops/postgres_tooling.py', '--tool', 'pg_dump', '--database-url', $DatabaseUrl)
if (-not [string]::IsNullOrWhiteSpace($PostgresBin)) {
    $toolArgs += @('--postgres-bin', $PostgresBin)
}
$toolingJson = & uv @toolArgs
if ($LASTEXITCODE -ne 0) { throw 'Compatible PostgreSQL dump client discovery failed.' }
$tooling = $toolingJson | ConvertFrom-Json
$pgDump = [string]$tooling.path
Write-Host "Using pg_dump major $($tooling.client_major) for PostgreSQL server major $($tooling.server_major): $pgDump"

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$extension = if ($PlainSql) { "sql" } else { "dump" }
$backupPath = Join-Path $BackupDir "swinglens_$BackupId.$extension"
$metadataPath = Join-Path $BackupDir "swinglens_$BackupId.metadata.json"
$evidenceManifestPath = Join-Path $BackupDir "swinglens_$BackupId.evidence.json"
$format = if ($PlainSql) { "plain" } else { "custom" }
$clientDatabaseUrl = ConvertTo-PostgresClientUrl -DatabaseUrl $DatabaseUrl

Write-Host "Creating SwingLens PostgreSQL backup: $backupPath"
& $pgDump --format=$format --file=$backupPath $clientDatabaseUrl

if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE."
}

Write-Host "Capturing deterministic evidence manifest: $evidenceManifestPath"
uv run python scripts/ops/evidence_manifest.py capture `
    --database-url $DatabaseUrl `
    --report $evidenceManifestPath

if ($LASTEXITCODE -ne 0) {
    throw "evidence manifest capture failed with exit code $LASTEXITCODE."
}

$metadata = [ordered]@{
    backup_id = $BackupId
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    format = $format
    backup_path = (Resolve-Path $backupPath).Path
    evidence_manifest_path = (Resolve-Path $evidenceManifestPath).Path
    database_url_fingerprint = ($DatabaseUrl -replace "://.*@", "://<redacted>@")
    commit = (git rev-parse HEAD 2>$null)
    server_major = $tooling.server_major
    pg_dump_major = $tooling.client_major
    pg_dump_path = $pgDump
}

$metadata | ConvertTo-Json -Depth 4 | Set-Content -Path $metadataPath -Encoding UTF8
Write-Host "Backup metadata: $metadataPath"
