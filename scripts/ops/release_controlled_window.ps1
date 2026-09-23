[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('verify', 'start', 'status', 'stop')]
    [string]$Action
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
Push-Location $repoRoot
try {
    # These values live only in this invocation and its supervised children.
    # NORMAL retains the ordinary explicit pipeline and Winner capture path.
    $env:RUNTIME_MODE = 'NORMAL'
    $env:WINNER_PROBABILITY_AUTO_MATURATION_ENABLED = 'false'
    $env:WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED = 'false'
    $env:MARKET_DATA_PREWARM_ENABLED = 'false'
    $env:SWINGLENS_LIFECYCLE_OVERRIDE_KEYS = (
        'DURABLE_WORKER_PROCESS_ENABLED,EMBEDDED_JOB_WORKER_ENABLED,JOB_WORKER_ENABLED,' +
        'WINNER_PROBABILITY_AUTO_MATURATION_ENABLED,' +
        'WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED,MARKET_DATA_PREWARM_ENABLED'
    )
    if ($Action -eq 'verify') {
        & uv run python scripts/ops/verify_release_controlled_window.py
    }
    else {
        & pwsh -NoProfile -File (Join-Path $repoRoot 'swinglens.ps1') $Action -RuntimeMode NORMAL -Json
    }
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
