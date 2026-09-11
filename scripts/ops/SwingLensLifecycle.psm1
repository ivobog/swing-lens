Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$script:RuntimeStatePath = Join-Path $script:RepoRoot 'data\cache\swinglens-lifecycle.json'

function Protect-SwingLensText {
    param([AllowNull()][string]$Text)
    if ($null -eq $Text) { return '' }
    $safe = $Text -replace '(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@', '$1<redacted>@'
    return $safe -replace '(?i)(password|passwd|pwd|secret|token)\s*[=:]\s*[^\s,;]+', '$1=<redacted>'
}

function Get-SwingLensPython {
    $python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'SwingLens virtual environment is missing. Run uv sync --frozen --extra dev.'
    }
    return $python
}

function Invoke-LifecycleProbe {
    param([Parameter(Mandatory = $true)][string]$Command, [string[]]$Arguments = @())
    $probe = Join-Path $script:RepoRoot 'scripts\ops\lifecycle_probe.py'
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & (Get-SwingLensPython) $probe $Command @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally { $ErrorActionPreference = $previousPreference }
    $text = ($output | ForEach-Object { [string]$_ }) -join "`n"
    if ($exitCode -ne 0) { throw ('Lifecycle probe failed: ' + (Protect-SwingLensText $text)) }
    try { return $text | ConvertFrom-Json }
    catch { throw ('Lifecycle probe returned invalid output: ' + (Protect-SwingLensText $text)) }
}

function Get-LifecycleConfig { return Invoke-LifecycleProbe -Command 'config' }

function Get-PortOwners {
    param([int]$Port)
    $connections = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
    $owners = @()
    foreach ($processId in @($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$processId" -ErrorAction SilentlyContinue
        if ($null -ne $process) { $owners += $process }
    }
    return @($owners)
}

function Invoke-HttpProbe {
    param([Parameter(Mandatory = $true)][string]$Uri, [int]$TimeoutSeconds = 3)
    try {
        $response = Invoke-WebRequest -SkipHttpErrorCheck -Uri $Uri -TimeoutSec $TimeoutSeconds
        $payload = $null
        try { $payload = $response.Content | ConvertFrom-Json } catch { $payload = $null }
        return [pscustomobject]@{ Reachable = $true; StatusCode = [int]$response.StatusCode; Payload = $payload }
    }
    catch { return [pscustomobject]@{ Reachable = $false; StatusCode = 0; Payload = $null } }
}

function Resolve-ReadinessPayloadState {
    param($Payload)
    if ($null -eq $Payload) { return 'failed' }
    $state = ([string]$Payload.status).ToLowerInvariant()
    if ($state -in @('ok', 'degraded', 'failed', 'optional_unavailable')) { return $state }
    return 'failed'
}

function Get-ReadinessState {
    param([int]$WebPort)
    $probe = Invoke-HttpProbe -Uri ("http://127.0.0.1:{0}/ready" -f $WebPort) -TimeoutSeconds 5
    if (-not $probe.Reachable) { return 'failed' }
    return Resolve-ReadinessPayloadState -Payload $probe.Payload
}

function Get-GitCommit {
    $value = & git -C $script:RepoRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot determine the current repository commit.' }
    return ([string]$value).Trim()
}

function Get-WebOwner {
    param([int]$Port)
    $owners = @(Get-PortOwners -Port $Port)
    if ($owners.Count -gt 1) { throw ("CONFLICT: port {0} has multiple listeners." -f $Port) }
    return $(if ($owners.Count -eq 1) { $owners[0] } else { $null })
}

function Get-ValidatedRuntime {
    param([int]$WebPort)
    $owner = Get-WebOwner -Port $WebPort
    if ($null -eq $owner) {
        if (Test-Path -LiteralPath $script:RuntimeStatePath -PathType Leaf) {
            $stale = Invoke-LifecycleProbe -Command 'runtime-state'
            if ($stale.stale) { Remove-Item -LiteralPath $script:RuntimeStatePath -Force }
            elseif ($stale.conflict) { throw ('CONFLICT: ' + $stale.error) }
        }
        return $null
    }
    $runtime = Invoke-LifecycleProbe -Command 'runtime-state' -Arguments @('--listener-pid', [string]$owner.ProcessId)
    if (-not $runtime.valid) {
        throw ('CONFLICT: port {0} is occupied but strong SwingLens runtime identity failed: {1}' -f $WebPort, $runtime.error)
    }
    return $runtime
}

function New-LifecycleMutex {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes($script:RepoRoot.ToLowerInvariant())
        $hash = [Convert]::ToHexString($sha.ComputeHash($bytes)).Substring(0, 24)
    }
    finally { $sha.Dispose() }
    return [Threading.Mutex]::new($false, "Local\SwingLensLifecycle-$hash")
}

function Invoke-WithLifecycleLock {
    param([string]$Action, [int]$TimeoutSeconds, [scriptblock]$Body)
    $mutex = New-LifecycleMutex
    $acquired = $false
    $lockStream = $null
    $lockPath = Join-Path $script:RepoRoot 'data\cache\swinglens-lifecycle.lock'
    [IO.Directory]::CreateDirectory((Split-Path -Parent $lockPath)) | Out-Null
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    try {
        do {
            try {
                $lockStream = [IO.File]::Open($lockPath, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None)
            }
            catch [IO.IOException] {
                Start-Sleep -Milliseconds 100
            }
        } while ($null -eq $lockStream -and [DateTime]::UtcNow -lt $deadline)
        if ($null -eq $lockStream) {
            throw ("Another SwingLens lifecycle operation is already running. {0} could not acquire the repository lock within {1} seconds." -f $Action.ToUpperInvariant(), $TimeoutSeconds)
        }
        $details = [Text.Encoding]::UTF8.GetBytes(("{0} pid={1} acquired={2:o}`n" -f $Action.ToUpperInvariant(), $PID, [DateTime]::UtcNow))
        $lockStream.SetLength(0); $lockStream.Write($details, 0, $details.Length); $lockStream.Flush($true)
        try { $acquired = $mutex.WaitOne([TimeSpan]::FromSeconds($TimeoutSeconds)) }
        catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) {
            throw ("Another SwingLens lifecycle operation is already running. {0} could not acquire the repository lock within {1} seconds." -f $Action.ToUpperInvariant(), $TimeoutSeconds)
        }
        $result = & $Body
        return $result
    }
    finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
        if ($null -ne $lockStream) { $lockStream.Dispose() }
    }
}

function Assert-PostgresServiceConfiguration {
    param($Config)
    $service = Get-CimInstance Win32_Service -Filter ("Name='{0}'" -f $Config.postgres.service.Replace("'", "''")) -ErrorAction SilentlyContinue
    if ($null -eq $service) { throw 'Configured authoritative PostgreSQL Windows service was not found.' }
    $path = [string]$service.PathName
    if ($path -notmatch '^\s*(?:"([^"]+)"|(\S+))') { throw 'Configured PostgreSQL service executable could not be parsed.' }
    $serviceExecutable = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
    if ([IO.Path]::GetFullPath($serviceExecutable) -ne [IO.Path]::GetFullPath([string]$Config.postgres.serviceExecutable)) {
        throw 'Configured PostgreSQL service executable does not match the canonical setting.'
    }
    if ($path -notmatch '(?i)(?:^|\s)-D\s+(?:"([^"]+)"|(\S+))') { throw 'Configured PostgreSQL service data directory could not be parsed.' }
    $serviceData = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
    if ([IO.Path]::GetFullPath($serviceData) -ne [IO.Path]::GetFullPath([string]$Config.postgres.dataDirectory)) {
        throw 'Configured PostgreSQL service data directory does not match the canonical setting.'
    }
    return $service
}

function Wait-DatabaseReady {
    param([int]$TimeoutSeconds = 60)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $report = Invoke-LifecycleProbe -Command 'database'
        if ($report.reachable) { return $report }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'The configured authoritative local PostgreSQL database did not become reachable.'
}

function Start-AuthoritativeDatabase {
    param($Config)
    $database = Invoke-LifecycleProbe -Command 'database'
    if (-not $database.reachable) {
        if (-not $Config.postgres.managementEnabled) {
            throw 'The authoritative local PostgreSQL database is unavailable and lifecycle service management is disabled.'
        }
        $service = Assert-PostgresServiceConfiguration -Config $Config
        if ($service.State -ne 'Running') { Start-Service -Name $Config.postgres.service }
        $database = Wait-DatabaseReady
    }
    $provenance = Invoke-LifecycleProbe -Command 'provenance'
    if (-not $provenance.verified) {
        throw ('CONFLICT: PostgreSQL is reachable, but authoritative service/cluster identity could not be verified. Alembic was not executed. ' + $provenance.error)
    }
    Write-Host ('Local PostgreSQL: verified {0} PostgreSQL {1}, PID {2}' -f $provenance.service, $provenance.version, $provenance.listenerPid)
    return $database
}

function Invoke-AlembicUpgrade {
    $result = Invoke-LifecycleProbe -Command 'migrate'
    if (-not $result.migrated) {
        $detail = if ($result.PSObject.Properties.Name -contains 'error') { $result.error } else { $result.output }
        throw ('Alembic migration failed or was refused: ' + (Protect-SwingLensText $detail))
    }
    Write-Host 'Schema: Alembic head verified/applied under provenance and advisory locks'
}

function Save-WebRuntimeState {
    param($Launch, [int]$WebPort)
    $state = [ordered]@{
        version = 3
        runtimeInstanceId = [string]$Launch.runtimeInstanceId
        repoRoot = $script:RepoRoot
        gitCommit = Get-GitCommit
        recordedAtUtc = [DateTime]::UtcNow.ToString('o')
        web = [ordered]@{
            pid = [int]$Launch.pid
            createdAt = $(if ($Launch.createdAt -is [DateTime]) { $Launch.createdAt.ToUniversalTime().ToString('o') } else { [string]$Launch.createdAt })
            launcherPid = [int]$Launch.launcherPid
            launcherCreatedAt = $(if ($Launch.launcherCreatedAt -is [DateTime]) { $Launch.launcherCreatedAt.ToUniversalTime().ToString('o') } else { [string]$Launch.launcherCreatedAt })
            role = 'web'
            module = 'app.serve'
            repoRoot = $script:RepoRoot
            runtimeInstanceId = [string]$Launch.runtimeInstanceId
            port = $WebPort
        }
    }
    if ($Launch.PSObject.Properties.Name -contains 'supervisorPid') {
        $state['supervisor'] = [ordered]@{
            pid = [int]$Launch.supervisorPid
            createdAt = $(if ($Launch.supervisorCreatedAt -is [DateTime]) { $Launch.supervisorCreatedAt.ToUniversalTime().ToString('o') } else { [string]$Launch.supervisorCreatedAt })
            role = 'supervisor'
            module = 'app.worker_supervisor'
            repoRoot = $script:RepoRoot
            runtimeInstanceId = [string]$Launch.runtimeInstanceId
        }
    }
    $json = $state | ConvertTo-Json -Depth 6 -Compress
    $null = Invoke-LifecycleProbe -Command 'write-state' -Arguments @('--json', $json)
}

function Start-SwingLensWeb {
    param($Config)
    $runtime = Get-ValidatedRuntime -WebPort ([int]$Config.web.port)
    if ($null -ne $runtime) {
        if ([string]$runtime.state.gitCommit -ne (Get-GitCommit)) {
            throw 'CONFLICT: an older SwingLens code generation is running. Use controlled restart; migrations were not run.'
        }
        $readiness = Get-ReadinessState -WebPort ([int]$Config.web.port)
        if ($readiness -in @('ok', 'degraded', 'optional_unavailable')) {
            Write-Host ('Web/API: reusing strongly verified PID {0}' -f $runtime.state.web.pid)
            return
        }
        throw 'Verified SwingLens runtime is failed; use restart after reviewing status.'
    }
    $stdout = Join-Path $script:RepoRoot 'logs\lifecycle-web.out.log'
    $stderr = Join-Path $script:RepoRoot 'logs\lifecycle-web.err.log'
    $launch = Invoke-LifecycleProbe -Command 'launch-web' -Arguments @('--stdout', $stdout, '--stderr', $stderr)
    Save-WebRuntimeState -Launch $launch -WebPort ([int]$Config.web.port)
    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        $owner = Get-WebOwner -Port ([int]$Config.web.port)
        if ($null -ne $owner -and [int]$owner.ProcessId -eq [int]$launch.pid) {
            $runtime = Get-ValidatedRuntime -WebPort ([int]$Config.web.port)
            $readiness = Get-ReadinessState -WebPort ([int]$Config.web.port)
            if ($null -ne $runtime -and $readiness -in @('ok', 'degraded', 'optional_unavailable')) {
                Write-Host ('Web/API: ready on strongly verified PID {0}' -f $launch.pid)
                return
            }
        }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'SwingLens core did not become ready within 90 seconds.'
}

function Test-DockerEngine {
    return [bool](Invoke-LifecycleProbe -Command 'observability-info').ok
}

function Start-SwingLensObservability {
    param($Config)
    if (-not $Config.grafanaPasswordConfigured) { Write-Warning 'Observability DEGRADED: Grafana password is not configured.'; return $false }
    $result = Invoke-LifecycleProbe -Command 'observability-start'
    if (-not $result.ok) { Write-Warning ('Observability DEGRADED: startup failed; core remains running. ' + $result.error) }
    return [bool]$result.ok
}

function Stop-SwingLensObservability {
    $result = Invoke-LifecycleProbe -Command 'observability-stop'
    if (-not $result.ok) { Write-Warning ('Observability DEGRADED: stop incomplete; core lifecycle continues. ' + $result.error) }
    return [bool]$result.ok
}

function Request-WorkerQuiesce {
    param([int]$TimeoutSeconds = 20)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $report = Invoke-LifecycleProbe -Command 'quiesce'
        if (-not $report.reachable) { throw 'Cannot prove durable worker quiescence while the database is unavailable.' }
        if ([int]$report.activeCount -gt 0) {
            $null = Invoke-LifecycleProbe -Command 'resume'
            $summary = @($report.active | ForEach-Object { '{0}:{1}:{2}' -f $_.id, $_.job_type, $_.status }) -join ', '
            throw ('Active durable jobs prevent a safe stop: ' + $summary)
        }
        if ($report.acknowledged) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    $null = Invoke-LifecycleProbe -Command 'resume'
    throw 'Worker did not acknowledge durable quiesce before timeout; stop aborted and claims resumed.'
}

function Wait-PortReleased {
    param([int]$Port, [int]$TimeoutSeconds = 30)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if (@(Get-PortOwners -Port $Port).Count -eq 0) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw ("SwingLens web port {0} was not released." -f $Port)
}

function Wait-ProcessExit {
    param([int]$ProcessId, [int]$TimeoutSeconds = 25)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Stop-RegisteredRemainders {
    $processReport = Invoke-LifecycleProbe -Command 'processes'
    foreach ($role in @('supervisor','worker')) {
        $matching = @($processReport.processes | Where-Object { $_.role -eq $role })
        if ($matching.Count -gt 1) { throw ("CONFLICT: multiple {0} processes belong to this checkout." -f $role) }
        if ($matching.Count -eq 1) {
            $signal = Invoke-LifecycleProbe -Command 'signal-registered' -Arguments @('--role', $role)
            if (-not $signal.signaled) { throw ('CONFLICT: ' + $signal.error) }
            if (-not (Wait-ProcessExit -ProcessId ([int]$signal.pid))) {
                throw ("Verified {0} PID {1} did not exit; no forced PID-only kill was attempted." -f $role, $signal.pid)
            }
        }
    }
}

function Stop-SwingLensCore {
    param($Config)
    $owner = Get-WebOwner -Port ([int]$Config.web.port)
    if ($null -eq $owner) {
        if (Test-Path -LiteralPath $script:RuntimeStatePath) {
            $runtime = Invoke-LifecycleProbe -Command 'runtime-state'
            if ($runtime.stale) { Remove-Item -LiteralPath $script:RuntimeStatePath -Force }
            elseif ($runtime.conflict) { throw ('CONFLICT: ' + $runtime.error) }
        }
        return
    }
    $runtime = Get-ValidatedRuntime -WebPort ([int]$Config.web.port)
    $signal = Invoke-LifecycleProbe -Command 'signal-break' -Arguments @('--pid', [string]$runtime.state.web.pid, '--listener-pid', [string]$owner.ProcessId)
    if (-not $signal.signaled) { throw ('CONFLICT: verified runtime was not signaled: ' + $signal.error) }
    Wait-PortReleased -Port ([int]$Config.web.port)
    Stop-RegisteredRemainders
    if (Test-Path -LiteralPath $script:RuntimeStatePath) { Remove-Item -LiteralPath $script:RuntimeStatePath -Force }
}

function Stop-AuthoritativeDatabase {
    param($Config)
    if (-not $Config.postgres.managementEnabled) { Write-Host 'Local PostgreSQL: left running by canonical settings'; return }
    $null = Invoke-LifecycleProbe -Command 'provenance' | ForEach-Object {
        if (-not $_.verified) { throw 'Authoritative PostgreSQL provenance was lost; service was not stopped.' }
    }
    Stop-Service -Name $Config.postgres.service
}

function Get-SwingLensStatusReport {
    param($Config)
    $database = Invoke-LifecycleProbe -Command 'database'
    $owner = Get-WebOwner -Port ([int]$Config.web.port)
    $runtimeValid = $false
    $readiness = 'failed'
    $conflict = $false
    if ($null -ne $owner) {
        $runtime = Invoke-LifecycleProbe -Command 'runtime-state' -Arguments @('--listener-pid', [string]$owner.ProcessId)
        $runtimeValid = [bool]$runtime.valid
        $conflict = -not $runtimeValid
        if ($runtimeValid) { $readiness = Get-ReadinessState -WebPort ([int]$Config.web.port) }
    }
    elseif (Test-Path -LiteralPath $script:RuntimeStatePath) {
        $state = Invoke-LifecycleProbe -Command 'runtime-state'
        $conflict = -not [bool]$state.stale
    }
    $docker = Test-DockerEngine
    $prometheus = $docker -and (Invoke-HttpProbe -Uri 'http://127.0.0.1:9090/-/ready').StatusCode -eq 200
    $grafana = $docker -and (Invoke-HttpProbe -Uri 'http://127.0.0.1:3000/api/health').StatusCode -eq 200
    $roleProcesses = @((Invoke-LifecycleProbe -Command 'processes').processes | Where-Object { $_.role -in @('worker','supervisor') })
    if ($conflict) { $overall = 'CONFLICT' }
    elseif ($null -eq $owner -and $roleProcesses.Count -gt 0) { $overall = 'FAILED' }
    elseif ($null -eq $owner) { $overall = 'STOPPED' }
    elseif (-not $database.reachable -or -not $database.schemaAtHead -or $readiness -eq 'failed') { $overall = 'FAILED' }
    elseif ($readiness -in @('degraded', 'optional_unavailable') -or -not $prometheus -or -not $grafana) { $overall = 'DEGRADED' }
    else { $overall = 'HEALTHY' }
    return [pscustomobject]@{ Database=$database; Owner=$owner; RuntimeValid=$runtimeValid; Readiness=$readiness; Docker=$docker; Prometheus=$prometheus; Grafana=$grafana; Overall=$overall }
}

function Write-SwingLensStatus {
    param($Config)
    $status = Get-SwingLensStatusReport -Config $Config
    Write-Host ''
    Write-Host 'SwingLens Local Stack'
    Write-Host '----------------------------------------'
    Write-Host ('Database           {0}' -f $(if ($status.Database.reachable) { 'READY' } else { 'UNAVAILABLE' }))
    Write-Host ('Schema             {0}' -f $(if ($status.Database.schemaAtHead) { 'HEAD' } else { 'MISMATCH/UNAVAILABLE' }))
    Write-Host ('Web/API            {0}' -f $(if ($status.RuntimeValid) { $status.Readiness.ToUpperInvariant() } elseif ($status.Owner) { 'CONFLICT' } else { 'STOPPED' }))
    Write-Host ('Prometheus         {0}' -f $(if ($status.Prometheus) { 'READY' } else { 'UNAVAILABLE' }))
    Write-Host ('Grafana            {0}' -f $(if ($status.Grafana) { 'READY' } else { 'UNAVAILABLE' }))
    Write-Host ('OVERALL            {0}' -f $status.Overall)
    return $status
}

function Start-SwingLensStack {
    param($Config)
    $existing = Get-ValidatedRuntime -WebPort ([int]$Config.web.port)
    if ($null -ne $existing) {
        Start-SwingLensWeb -Config $Config
    }
    else {
        $null = Start-AuthoritativeDatabase -Config $Config
        Invoke-AlembicUpgrade
        $verified = Invoke-LifecycleProbe -Command 'database'
        if (-not $verified.reachable -or -not $verified.schemaAtHead) { throw 'Mandatory database/Alembic gate failed.' }
        Start-SwingLensWeb -Config $Config
    }
    $null = Start-SwingLensObservability -Config $Config
    $status = Write-SwingLensStatus -Config $Config
    if ($status.Overall -eq 'HEALTHY') { return 0 }
    if ($status.Overall -eq 'DEGRADED') { return 2 }
    throw ('Start certification failed with state ' + $status.Overall)
}

function Stop-SwingLensStack {
    param($Config)
    $owner = Get-WebOwner -Port ([int]$Config.web.port)
    $roleProcesses = @((Invoke-LifecycleProbe -Command 'processes').processes | Where-Object { $_.role -in @('worker','supervisor') })
    if ($null -ne $owner -or $roleProcesses.Count -gt 0) {
        Request-WorkerQuiesce
        try {
            if ($null -ne $owner) { Stop-SwingLensCore -Config $Config }
            else { Stop-RegisteredRemainders }
        }
        catch { $null = Invoke-LifecycleProbe -Command 'resume'; throw }
    }
    elseif (Test-Path -LiteralPath $script:RuntimeStatePath) { Stop-SwingLensCore -Config $Config }
    $null = Stop-SwingLensObservability
    Stop-AuthoritativeDatabase -Config $Config
    $status = Write-SwingLensStatus -Config $Config
    if ($status.Overall -eq 'STOPPED' -or ($null -eq $status.Owner -and -not $Config.postgres.managementEnabled)) { return 0 }
    throw 'Stop was incomplete or unsafe.'
}

function Invoke-SwingLensLifecycle {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][ValidateSet('start','stop','restart','status')][string]$Action)
    Push-Location $script:RepoRoot
    try {
        $config = Get-LifecycleConfig
        if ($Action -eq 'status') {
            $status = Write-SwingLensStatus -Config $config
            if ($status.Overall -in @('HEALTHY','STOPPED')) { return 0 }
            if ($status.Overall -eq 'DEGRADED') { return 2 }
            return 1
        }
        return Invoke-WithLifecycleLock -Action $Action -TimeoutSeconds ([int]$config.lockTimeoutSeconds) -Body {
            switch ($Action) {
                'start' { Start-SwingLensStack -Config $config }
                'stop' { Stop-SwingLensStack -Config $config }
                'restart' {
                    $null = Stop-SwingLensStack -Config $config
                    Start-SwingLensStack -Config $config
                }
            }
        }
    }
    finally { Pop-Location }
}

Export-ModuleMember -Function Invoke-SwingLensLifecycle
