[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action,

    [ValidateSet('NORMAL', 'CERTIFICATION')]
    [string]$RuntimeMode
)

if ($PSVersionTable.PSEdition -ne 'Core' -or $PSVersionTable.PSVersion -lt [Version]'7.4') {
    [Console]::Error.WriteLine('SwingLens lifecycle requires PowerShell 7.4 or newer.')
    [Console]::Error.WriteLine(('Current shell: {0} {1}' -f $PSVersionTable.PSEdition, $PSVersionTable.PSVersion))
    [Console]::Error.WriteLine('Run:')
    [Console]::Error.WriteLine('pwsh .\swinglens.ps1 start')
    exit 1
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# This mutation is confined to the short-lived pwsh launcher process. Each
# Python child receives a fresh role-specific environment derived from Settings.
$env:PROCESS_ROLE = 'CLI_OR_MAINTENANCE'
if ($PSBoundParameters.ContainsKey('RuntimeMode')) {
    $env:RUNTIME_MODE = $RuntimeMode
    if ($RuntimeMode -eq 'CERTIFICATION') {
        $env:USE_DURABLE_PIPELINE = 'true'
        $env:DURABLE_WORKER_PROCESS_ENABLED = 'true'
        $env:EMBEDDED_JOB_WORKER_ENABLED = 'false'
        $env:JOB_WORKER_ENABLED = 'false'
        $env:WINNER_PROBABILITY_AUTO_MATURATION_ENABLED = 'false'
        $env:WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED = 'false'
        $env:MARKET_DATA_PREWARM_ENABLED = 'false'
    }
}

$modulePath = Join-Path $PSScriptRoot 'scripts\ops\SwingLensLifecycle.psm1'
Import-Module $modulePath -Force

try {
    $exitCode = Invoke-SwingLensLifecycle -Action $Action
    exit [int]$exitCode
}
catch {
    $message = [string]$_.Exception.Message
    $message = $message -replace '(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@', '$1<redacted>@'
    Write-Error $message
    exit 1
}
