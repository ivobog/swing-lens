[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet('start', 'stop', 'restart', 'status')]
    [string]$Action
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
