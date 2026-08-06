function ConvertTo-PostgresClientUrl {
    param(
        [Parameter(Mandatory = $true)]
        [string]$DatabaseUrl
    )

    return $DatabaseUrl -replace '^postgresql\+[^:]+://', 'postgresql://'
}

Export-ModuleMember -Function ConvertTo-PostgresClientUrl
