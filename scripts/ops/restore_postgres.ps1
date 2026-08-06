param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,

    [string]$RestoreDatabaseUrl = $env:RESTORE_DATABASE_URL,
    [string]$ValidationReport = "backups/restore_validation_report.json",
    [string]$ExpectedEvidenceManifest,
    [string]$EvidenceComparisonReport = "backups/restore_evidence_comparison.json",
    [switch]$PlainSql
)

$ErrorActionPreference = "Stop"
Import-Module (Join-Path $PSScriptRoot "PostgresUrl.psm1") -Force

if ([string]::IsNullOrWhiteSpace($RestoreDatabaseUrl)) {
    throw "RESTORE_DATABASE_URL is required. Pass -RestoreDatabaseUrl or set `$env:RESTORE_DATABASE_URL."
}

if (-not (Test-Path $BackupPath)) {
    throw "Backup file was not found: $BackupPath"
}

$clientRestoreDatabaseUrl = ConvertTo-PostgresClientUrl -DatabaseUrl $RestoreDatabaseUrl

if ($PlainSql) {
    $psql = Get-Command psql -ErrorAction SilentlyContinue
    if (-not $psql) {
        throw "psql was not found on PATH. Install PostgreSQL client tools before restoring."
    }
    Write-Host "Restoring plain SQL backup into clean target database."
    & $psql.Source $clientRestoreDatabaseUrl -v ON_ERROR_STOP=1 -f $BackupPath
} else {
    $pgRestore = Get-Command pg_restore -ErrorAction SilentlyContinue
    if (-not $pgRestore) {
        throw "pg_restore was not found on PATH. Install PostgreSQL client tools before restoring."
    }
    Write-Host "Restoring custom-format backup into clean target database."
    & $pgRestore.Source --clean --if-exists --no-owner --dbname $clientRestoreDatabaseUrl $BackupPath
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

if ([string]::IsNullOrWhiteSpace($ExpectedEvidenceManifest)) {
    $backupBaseName = [System.IO.Path]::GetFileNameWithoutExtension($BackupPath)
    $candidateManifest = Join-Path (Split-Path $BackupPath -Parent) "$backupBaseName.evidence.json"
    if (Test-Path $candidateManifest) {
        $ExpectedEvidenceManifest = $candidateManifest
    }
}

if (-not [string]::IsNullOrWhiteSpace($ExpectedEvidenceManifest)) {
    if (-not (Test-Path $ExpectedEvidenceManifest)) {
        throw "Expected evidence manifest was not found: $ExpectedEvidenceManifest"
    }
    Write-Host "Comparing restored evidence to source manifest."
    uv run python scripts/ops/evidence_manifest.py verify `
        --database-url $RestoreDatabaseUrl `
        --expected $ExpectedEvidenceManifest `
        --report $EvidenceComparisonReport

    if ($LASTEXITCODE -ne 0) {
        throw "restored evidence comparison failed with exit code $LASTEXITCODE."
    }
    Write-Host "Evidence comparison report: $EvidenceComparisonReport"
}

Write-Host "Restore validation report: $ValidationReport"
