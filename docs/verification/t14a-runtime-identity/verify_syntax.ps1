$repo = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
foreach ($relative in @('swinglens.ps1', 'scripts/ops/SwingLensLifecycle.psm1')) {
    $syntaxTokens = $null
    $syntaxErrors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile(
        (Join-Path $repo $relative), [ref]$syntaxTokens, [ref]$syntaxErrors)
    if ($syntaxErrors.Count -gt 0) { throw ($syntaxErrors | Out-String) }
}
Write-Output 'PowerShell syntax: PASS'
