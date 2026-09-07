[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$modulePath = Join-Path $PSScriptRoot 'scripts\ops\SwingLensLifecycle.psm1'
Import-Module $modulePath -Force

try {
    Invoke-SwingLensLifecycle -Action $Action
}
catch {
    $message = [string]$_.Exception.Message
    $message = $message -replace '(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@', '$1<redacted>@'
    Write-Error $message
    exit 1
}
