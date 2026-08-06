param(
    [string]$DatabaseUrl = $env:DATABASE_URL,
    [string]$BackupDir = "backups",
    [string]$BackupId = $(Get-Date -Format "yyyyMMdd_HHmmss"),
    [switch]$PlainSql
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "PostgresUrl.psm1") -Force

if ([string]::IsNullOrWhiteSpace($DatabaseUrl)) {
    throw "DATABASE_URL is required. Pass -DatabaseUrl or set `$env:DATABASE_URL."
}

$pgDump = Get-Command pg_dump -ErrorAction SilentlyContinue
if (-not $pgDump) {
    throw "pg_dump was not found on PATH. Install PostgreSQL client tools before running backup."
}

New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
$extension = if ($PlainSql) { "sql" } else { "dump" }
$backupPath = Join-Path $BackupDir "swinglens_$BackupId.$extension"
$metadataPath = Join-Path $BackupDir "swinglens_$BackupId.metadata.json"
$format = if ($PlainSql) { "plain" } else { "custom" }
$clientDatabaseUrl = ConvertTo-PostgresClientUrl -DatabaseUrl $DatabaseUrl

Write-Host "Creating SwingLens PostgreSQL backup: $backupPath"
& $pgDump.Source --format=$format --file=$backupPath $clientDatabaseUrl

if ($LASTEXITCODE -ne 0) {
    throw "pg_dump failed with exit code $LASTEXITCODE."
}

$metadata = [ordered]@{
    backup_id = $BackupId
    created_at = (Get-Date).ToUniversalTime().ToString("o")
    format = $format
    backup_path = (Resolve-Path $backupPath).Path
    database_url_fingerprint = ($DatabaseUrl -replace "://.*@", "://<redacted>@")
    commit = (git rev-parse HEAD 2>$null)
}

$metadata | ConvertTo-Json -Depth 4 | Set-Content -Path $metadataPath -Encoding UTF8
Write-Host "Backup metadata: $metadataPath"
