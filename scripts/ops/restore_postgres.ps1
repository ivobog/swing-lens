param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,

    [string]$RestoreDatabaseUrl = $env:RESTORE_DATABASE_URL,
    [string]$ValidationReport = "backups/restore_validation_report.json",
    [switch]$PlainSql
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RestoreDatabaseUrl)) {
    throw "RESTORE_DATABASE_URL is required. Pass -RestoreDatabaseUrl or set `$env:RESTORE_DATABASE_URL."
}

if (-not (Test-Path $BackupPath)) {
    throw "Backup file was not found: $BackupPath"
}

if ($PlainSql) {
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if (-not $psql) {
        throw "psql was not found on PATH. Install PostgreSQL client tools before restoring."
    }
    Write-Host "Restoring plain SQL backup into clean target database."
    & $psql.Source $RestoreDatabaseUrl -v ON_ERROR_STOP=1 -f $BackupPath
} else {
    $pgRestore = Get-Command pg_restore -ErrorAction SilentlyContinue
    if (-not $pgRestore) {
        throw "pg_restore was not found on PATH. Install PostgreSQL client tools before restoring."
    }
    Write-Host "Restoring custom-format backup into clean target database."
    & $pgRestore.Source --clean --if-exists --no-owner --dbname $RestoreDatabaseUrl $BackupPath
}

if ($LASTEXITCODE -ne 0) {
    throw "restore command failed with exit code $LASTEXITCODE."
}

Write-Host "Validating restored database."
uv run python scripts/ops/validate_restore.py `
    --database-url $RestoreDatabaseUrl `
    --report $ValidationReport

if ($LASTEXITCODE -ne 0) {
    throw "restore validation failed with exit code $LASTEXITCODE."
}

Write-Host "Restore validation report: $ValidationReport"
