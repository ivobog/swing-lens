Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script:RepoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$script:ObservabilityCompose = Join-Path $script:RepoRoot 'docker-compose.observability.yml'
$script:RuntimeStatePath = Join-Path $script:RepoRoot 'data\cache\swinglens-lifecycle.json'
$script:WebPort = 8000
$script:WorkerPort = 9101
$script:SupervisorPort = 9102

function Protect-SwingLensText {
    param([AllowNull()][string]$Text)
    if ($null -eq $Text) { return '' }
    $safe = $Text -replace '(?i)([a-z][a-z0-9+.-]*://)[^/@\s]+@', '$1<redacted>@'
    return $safe -replace '(?i)(password|passwd|pwd|secret|token)\s*[=:]\s*[^\s,;]+', '$1=<redacted>'
}

function Import-SwingLensEnvironment {
    $envPath = Join-Path $script:RepoRoot '.env'
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) { return }
    foreach ($line in Get-Content -LiteralPath $envPath) {
        if ($line -notmatch '^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { continue }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if ($value.Length -ge 2 -and (($value[0] -eq '"' -and $value[-1] -eq '"') -or ($value[0] -eq "'" -and $value[-1] -eq "'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        if ($null -eq [Environment]::GetEnvironmentVariable($name, 'Process')) {
            [Environment]::SetEnvironmentVariable($name, $value, 'Process')
        }
    }
}

function Get-SwingLensPython {
    $python = Join-Path $script:RepoRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        throw 'SwingLens virtual environment is missing. Run uv sync --frozen --extra dev.'
    }
    return $python
}

function Invoke-LifecycleProbe {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Arguments = @()
    )
    $python = Get-SwingLensPython
    $probe = Join-Path $script:RepoRoot 'scripts\ops\lifecycle_probe.py'
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $python $probe $Command @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
    }
    $text = ($output | ForEach-Object { [string]$_ }) -join "`n"
    if ($exitCode -ne 0) {
        throw ('Lifecycle probe failed: ' + (Protect-SwingLensText $text))
    }
    try {
        return $text | ConvertFrom-Json
    }
    catch {
        throw ('Lifecycle probe returned invalid output: ' + (Protect-SwingLensText $text))
    }
}

function Get-DatabaseReport {
    return Invoke-LifecycleProbe -Command 'database'
}

function Get-DatabaseEndpoint {
    param($DatabaseReport)
    return '{0}:{1}/{2}' -f $DatabaseReport.host, $DatabaseReport.port, $DatabaseReport.database
}

function Test-LocalDatabaseHost {
    param([string]$HostName)
    return $HostName -in @('127.0.0.1', 'localhost', '::1', '')
}

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

function Test-ProcessRole {
    param($Process, [ValidateSet('web', 'supervisor', 'worker')][string]$Role)
    if ($null -eq $Process) { return $false }
    $patterns = @{
        web = '(?i)(?:^|\s)-m\s+app\.serve(?:\s|$)'
        supervisor = '(?i)(?:^|\s)-m\s+app\.worker_supervisor(?:\s|$)'
        worker = '(?i)(?:^|\s)-m\s+app\.worker(?:\s|$)'
    }
    return [string]$Process.CommandLine -match $patterns[$Role]
}

function Get-RoleLauncherProcess {
    param($Process, [ValidateSet('web', 'supervisor', 'worker')][string]$Role)
    $candidate = $Process
    for ($index = 0; $index -lt 6; $index++) {
        if (-not (Test-ProcessRole -Process $candidate -Role $Role)) { break }
        $parent = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $candidate.ParentProcessId) -ErrorAction SilentlyContinue
        if ($null -eq $parent -or -not (Test-ProcessRole -Process $parent -Role $Role)) { break }
        $candidate = $parent
    }
    return $candidate
}

function Invoke-HttpProbe {
    param([Parameter(Mandatory = $true)][string]$Uri, [int]$TimeoutSeconds = 2)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -SkipHttpErrorCheck -Uri $Uri -TimeoutSec $TimeoutSeconds
        $payload = $null
        try { $payload = $response.Content | ConvertFrom-Json } catch { $payload = $null }
        return [pscustomobject]@{
            Reachable = $true
            StatusCode = [int]$response.StatusCode
            Payload = $payload
        }
    }
    catch {
        return [pscustomobject]@{ Reachable = $false; StatusCode = 0; Payload = $null }
    }
}

function Test-VerifiedWeb {
    param($Process)
    if (-not (Test-ProcessRole -Process $Process -Role 'web')) { return $false }
    $health = Invoke-HttpProbe -Uri 'http://127.0.0.1:8000/health'
    return $health.StatusCode -eq 200 -and $null -ne $health.Payload -and $health.Payload.app -eq 'SwingLens'
}

function Normalize-LocalPath {
    param([AllowNull()][string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    try { return [IO.Path]::GetFullPath($Path).TrimEnd('\', '/').Replace('\', '/').ToLowerInvariant() }
    catch { return $Path.TrimEnd('\', '/').Replace('\', '/').ToLowerInvariant() }
}

function Get-ServiceDataDirectory {
    param([string]$PathName)
    if ($PathName -match '(?i)(?:^|\s)-D\s+(?:"([^"]+)"|([^\s]+))') {
        $value = if ($Matches[1]) { $Matches[1] } else { $Matches[2] }
        return $value
    }
    return $null
}

function Get-PostgresConfiguredPort {
    param([AllowNull()][string]$DataDirectory)
    if ([string]::IsNullOrWhiteSpace($DataDirectory)) { return $null }
    $configPath = Join-Path $DataDirectory 'postgresql.conf'
    if (-not (Test-Path -LiteralPath $configPath -PathType Leaf)) { return $null }
    foreach ($line in Get-Content -LiteralPath $configPath -ErrorAction SilentlyContinue) {
        if ($line -match '^\s*port\s*=\s*''?([0-9]+)''?\s*(?:#.*)?$') { return [int]$Matches[1] }
    }
    return 5432
}

function Test-ServiceOwnsProcess {
    param([int]$ServiceProcessId, [int]$ListenerProcessId)
    $current = $ListenerProcessId
    for ($index = 0; $index -lt 10 -and $current -gt 0; $index++) {
        if ($current -eq $ServiceProcessId) { return $true }
        $process = Get-CimInstance Win32_Process -Filter "ProcessId=$current" -ErrorAction SilentlyContinue
        if ($null -eq $process) { break }
        $current = [int]$process.ParentProcessId
    }
    return $false
}

function Resolve-AuthoritativePostgresService {
    param($DatabaseReport)
    if (-not (Test-LocalDatabaseHost -HostName ([string]$DatabaseReport.host))) { return $null }
    $services = @(
        Get-CimInstance Win32_Service |
            Where-Object { $_.PathName -match '(?i)pg_ctl(?:\.exe)?' -and $_.PathName -match '(?i)runservice' }
    )
    $matches = @()
    $expectedDirectory = Normalize-LocalPath ([string]$DatabaseReport.dataDirectory)
    $listenerPids = @(Get-PortOwners -Port ([int]$DatabaseReport.port) | Select-Object -ExpandProperty ProcessId)
    foreach ($service in $services) {
        $dataDirectory = Get-ServiceDataDirectory -PathName ([string]$service.PathName)
        $normalizedDirectory = Normalize-LocalPath $dataDirectory
        $evidence = @()
        if ($DatabaseReport.reachable -and $expectedDirectory -and $normalizedDirectory -eq $expectedDirectory) {
            $evidence += 'data-directory'
        }
        if ([int]$service.ProcessId -gt 0) {
            foreach ($listenerPid in $listenerPids) {
                if (Test-ServiceOwnsProcess -ServiceProcessId ([int]$service.ProcessId) -ListenerProcessId ([int]$listenerPid)) {
                    $evidence += 'listener-process-chain'
                }
            }
        }
        $configuredPort = Get-PostgresConfiguredPort -DataDirectory $dataDirectory
        if ($null -ne $configuredPort -and [int]$configuredPort -eq [int]$DatabaseReport.port) {
            $evidence += 'configured-port'
        }
        if ($evidence.Count -gt 0) {
            $matches += [pscustomobject]@{
                Name = [string]$service.Name
                DisplayName = [string]$service.DisplayName
                State = [string]$service.State
                StartMode = [string]$service.StartMode
                ProcessId = [int]$service.ProcessId
                PathName = [string]$service.PathName
                DataDirectory = $dataDirectory
                Evidence = @($evidence | Select-Object -Unique)
            }
        }
    }
    $strong = @($matches | Where-Object { $_.Evidence -contains 'data-directory' -or $_.Evidence -contains 'listener-process-chain' })
    if ($strong.Count -eq 1) { return $strong[0] }
    if ($strong.Count -gt 1) { throw 'PostgreSQL service identity is ambiguous; no service action was taken.' }
    if ($matches.Count -eq 1) { return $matches[0] }
    if ($matches.Count -gt 1) { throw 'PostgreSQL service identity is ambiguous; no service action was taken.' }
    return $null
}

function Wait-DatabaseReady {
    param([int]$TimeoutSeconds = 60)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $report = Get-DatabaseReport
        if ($report.reachable) { return $report }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'The configured authoritative local PostgreSQL database did not become reachable.'
}

function Start-AuthoritativeDatabase {
    param($DatabaseReport)
    if ($DatabaseReport.reachable) {
        Write-Host ('Local PostgreSQL: reusing reachable {0}' -f (Get-DatabaseEndpoint $DatabaseReport))
        return $DatabaseReport
    }
    if (-not (Test-LocalDatabaseHost -HostName ([string]$DatabaseReport.host))) {
        throw ('Configured database {0} is unreachable and is not a local Windows service.' -f (Get-DatabaseEndpoint $DatabaseReport))
    }
    $service = Resolve-AuthoritativePostgresService -DatabaseReport $DatabaseReport
    if ($null -eq $service) {
        throw 'Configured local database is unreachable and no authoritative PostgreSQL service was confidently identified.'
    }
    if ($service.State -ne 'Running') {
        Write-Host ('Local PostgreSQL: starting verified service {0}' -f $service.Name)
        Start-Service -Name $service.Name
    }
    return Wait-DatabaseReady
}

function Invoke-AlembicUpgrade {
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -eq $uv) { throw 'uv is required to apply the repository Alembic migrations.' }
    Write-Host 'Schema: applying Alembic migrations to the configured local database'
    Push-Location $script:RepoRoot
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $output = & $uv.Source run alembic upgrade head 2>&1
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
        Pop-Location
    }
    if ($exitCode -ne 0) {
        throw ('Alembic migration failed before web startup: ' + (Protect-SwingLensText (($output | ForEach-Object { [string]$_ }) -join "`n")))
    }
}

function Save-WebRuntimeState {
    param([int]$LauncherPid)
    $state = [ordered]@{
        version = 1
        repoRoot = $script:RepoRoot
        webLauncherPid = $LauncherPid
        recordedAtUtc = [DateTime]::UtcNow.ToString('o')
    }
    $parent = Split-Path -Parent $script:RuntimeStatePath
    [IO.Directory]::CreateDirectory($parent) | Out-Null
    $state | ConvertTo-Json | Set-Content -LiteralPath $script:RuntimeStatePath -Encoding UTF8
}

function Get-WebRuntimeState {
    if (-not (Test-Path -LiteralPath $script:RuntimeStatePath -PathType Leaf)) { return $null }
    try { return Get-Content -LiteralPath $script:RuntimeStatePath -Raw | ConvertFrom-Json }
    catch { return $null }
}

function Start-SwingLensWeb {
    $owners = @(Get-PortOwners -Port $script:WebPort)
    if ($owners.Count -gt 1) { throw 'Port 8000 has multiple listeners; no process action was taken.' }
    if ($owners.Count -eq 1) {
        $owner = $owners[0]
        if (-not (Test-VerifiedWeb -Process $owner)) {
            throw ('Port 8000 is owned by another process: PID {0} ({1}).' -f $owner.ProcessId, $owner.Name)
        }
        $ready = Invoke-HttpProbe -Uri 'http://127.0.0.1:8000/ready' -TimeoutSeconds 5
        if ($ready.StatusCode -ne 200) {
            throw ('Verified SwingLens PID {0} is unhealthy; use restart after reviewing status.' -f $owner.ProcessId)
        }
        Write-Host ('Web/API: reusing verified healthy SwingLens PID {0}' -f $owner.ProcessId)
        return
    }

    Write-Host 'Web/API: launching with JOB_WORKER_ENABLED=true'
    $stdout = Join-Path $script:RepoRoot 'logs\lifecycle-web.out.log'
    $stderr = Join-Path $script:RepoRoot 'logs\lifecycle-web.err.log'
    $launch = Invoke-LifecycleProbe -Command 'launch-web' -Arguments @('--stdout', $stdout, '--stderr', $stderr)
    Save-WebRuntimeState -LauncherPid ([int]$launch.launcherPid)

    $deadline = [DateTime]::UtcNow.AddSeconds(90)
    do {
        $owners = @(Get-PortOwners -Port $script:WebPort)
        if ($owners.Count -eq 1 -and (Test-VerifiedWeb -Process $owners[0])) {
            $ready = Invoke-HttpProbe -Uri 'http://127.0.0.1:8000/ready' -TimeoutSeconds 5
            if ($ready.StatusCode -eq 200) {
                Write-Host ('Web/API: ready on PID {0}; supervisor and worker are ready' -f $owners[0].ProcessId)
                return
            }
        }
        Start-Sleep -Seconds 1
    } while ([DateTime]::UtcNow -lt $deadline)
    throw 'SwingLens web, supervisor, and worker did not become ready within 90 seconds.'
}

function Test-DockerEngine {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) { return $false }
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try { $null = & $docker.Source info --format '{{.ServerVersion}}' 2>$null; return $LASTEXITCODE -eq 0 }
    finally { $ErrorActionPreference = $previousPreference }
}

function Get-PrometheusTargetReport {
    $expected = @('swinglens-web', 'swinglens-worker', 'swinglens-supervisor')
    $probe = Invoke-HttpProbe -Uri 'http://127.0.0.1:9090/api/v1/targets' -TimeoutSeconds 3
    if ($probe.StatusCode -ne 200 -or $null -eq $probe.Payload -or $probe.Payload.status -ne 'success') {
        return [pscustomobject]@{ Up = 0; Expected = 3 }
    }
    $active = @($probe.Payload.data.activeTargets)
    $up = 0
    foreach ($job in $expected) {
        if (@($active | Where-Object { $_.labels.job -eq $job -and $_.health -eq 'up' }).Count -gt 0) { $up++ }
    }
    return [pscustomobject]@{ Up = $up; Expected = 3 }
}

function Start-SwingLensObservability {
    $grafanaPassword = [Environment]::GetEnvironmentVariable('GRAFANA_ADMIN_PASSWORD', 'Process')
    if ([string]::IsNullOrWhiteSpace($grafanaPassword)) {
        Write-Warning 'Observability DEGRADED: GRAFANA_ADMIN_PASSWORD is not configured.'
        return
    }
    if (-not (Test-DockerEngine)) {
        Write-Warning 'Observability DEGRADED: Docker Engine is unavailable; core remains running.'
        return
    }
    Push-Location $script:RepoRoot
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $configOk = $false
    $startupOk = $false
    try {
        $null = & docker compose -f $script:ObservabilityCompose config --quiet 2>$null
        $configOk = $LASTEXITCODE -eq 0
        if ($configOk) {
            $null = & docker compose -f $script:ObservabilityCompose up -d 2>$null
            $startupOk = $LASTEXITCODE -eq 0
        }
    }
    finally {
        $ErrorActionPreference = $previousPreference
        Pop-Location
    }
    if (-not $configOk) {
        Write-Warning 'Observability DEGRADED: Compose validation failed; core remains running.'
        return
    }
    if (-not $startupOk) {
        Write-Warning 'Observability DEGRADED: Prometheus/Grafana startup failed; core remains running.'
        return
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    do {
        $prometheus = Invoke-HttpProbe -Uri 'http://127.0.0.1:9090/-/ready'
        $grafana = Invoke-HttpProbe -Uri 'http://127.0.0.1:3000/api/health'
        $targets = Get-PrometheusTargetReport
        if ($prometheus.StatusCode -eq 200 -and $grafana.StatusCode -eq 200 -and $targets.Up -eq 3) {
            Write-Host 'Observability: Prometheus and Grafana ready; 3/3 SwingLens targets UP'
            return
        }
        Start-Sleep -Seconds 2
    } while ([DateTime]::UtcNow -lt $deadline)
    Write-Warning ('Observability DEGRADED: readiness incomplete; Prometheus targets {0}/3 UP.' -f $targets.Up)
}

function Get-ManagePostgres {
    $configured = [Environment]::GetEnvironmentVariable('SWINGLENS_MANAGE_POSTGRES', 'Process')
    if ([string]::IsNullOrWhiteSpace($configured)) { return $false }
    return $configured.Trim().ToLowerInvariant() -notin @('0', 'false', 'no', 'off')
}

function Get-SwingLensStatusReport {
    $database = Get-DatabaseReport
    $service = $null
    $serviceError = $null
    try { $service = Resolve-AuthoritativePostgresService -DatabaseReport $database }
    catch { $serviceError = Protect-SwingLensText $_.Exception.Message }

    $webOwners = @(Get-PortOwners -Port $script:WebPort)
    $workerOwners = @(Get-PortOwners -Port $script:WorkerPort)
    $supervisorOwners = @(Get-PortOwners -Port $script:SupervisorPort)
    $webVerified = $webOwners.Count -eq 1 -and (Test-VerifiedWeb -Process $webOwners[0])
    $webConflict = $webOwners.Count -gt 0 -and -not $webVerified
    $webReady = $false
    if ($webVerified) { $webReady = (Invoke-HttpProbe -Uri 'http://127.0.0.1:8000/ready' -TimeoutSeconds 5).StatusCode -eq 200 }
    $workerReady = $workerOwners.Count -eq 1 -and (Test-ProcessRole -Process $workerOwners[0] -Role 'worker') -and (Invoke-HttpProbe -Uri 'http://127.0.0.1:9101/metrics').StatusCode -eq 200
    $supervisorReady = $supervisorOwners.Count -eq 1 -and (Test-ProcessRole -Process $supervisorOwners[0] -Role 'supervisor') -and (Invoke-HttpProbe -Uri 'http://127.0.0.1:9102/metrics').StatusCode -eq 200
    $dockerReady = Test-DockerEngine
    $prometheusReady = $dockerReady -and (Invoke-HttpProbe -Uri 'http://127.0.0.1:9090/-/ready').StatusCode -eq 200
    $grafanaReady = $dockerReady -and (Invoke-HttpProbe -Uri 'http://127.0.0.1:3000/api/health').StatusCode -eq 200
    $targets = if ($prometheusReady) { Get-PrometheusTargetReport } else { [pscustomobject]@{ Up = 0; Expected = 3 } }
    $durableReady = -not [bool]$database.useDurablePipeline -or ($supervisorReady -and $workerReady)

    if ($webConflict) { $overall = 'CONFLICT' }
    elseif (-not $webVerified) {
        if ($workerOwners.Count -gt 0 -or $supervisorOwners.Count -gt 0) { $overall = 'FAILED' }
        elseif ($prometheusReady -or $grafanaReady) { $overall = 'DEGRADED' }
        else { $overall = 'STOPPED' }
    }
    elseif (-not $database.reachable -or -not $database.schemaAtHead -or -not $webReady -or -not $durableReady) { $overall = 'FAILED' }
    elseif (-not $dockerReady -or -not $prometheusReady -or -not $grafanaReady -or $targets.Up -ne 3) { $overall = 'DEGRADED' }
    else { $overall = 'HEALTHY' }

    return [pscustomobject]@{
        Database = $database
        Service = $service
        ServiceError = $serviceError
        WebOwners = $webOwners
        WebVerified = $webVerified
        WebReady = $webReady
        WebConflict = $webConflict
        WorkerOwners = $workerOwners
        WorkerReady = $workerReady
        SupervisorOwners = $supervisorOwners
        SupervisorReady = $supervisorReady
        DockerReady = $dockerReady
        PrometheusReady = $prometheusReady
        GrafanaReady = $grafanaReady
        Targets = $targets
        Overall = $overall
    }
}

function Format-State {
    param([bool]$Ready, [string]$ReadyText = 'READY', [string]$NotReadyText = 'STOPPED')
    if ($Ready) { return $ReadyText }
    return $NotReadyText
}

function Write-SwingLensStatus {
    $status = Get-SwingLensStatusReport
    $database = $status.Database
    $serviceText = if ($null -ne $status.Service) {
        '{0}   {1}' -f (Format-State ($status.Service.State -eq 'Running')), $status.Service.Name
    } elseif ($status.ServiceError) { 'AMBIGUOUS' } else { 'NOT IDENTIFIED' }
    $webPid = if ($status.WebOwners.Count -eq 1) { ' PID ' + $status.WebOwners[0].ProcessId } else { '' }
    $supervisorPid = if ($status.SupervisorOwners.Count -eq 1) { ' PID ' + $status.SupervisorOwners[0].ProcessId } else { '' }
    $workerPid = if ($status.WorkerOwners.Count -eq 1) { ' PID ' + $status.WorkerOwners[0].ProcessId } else { '' }

    Write-Host ''
    Write-Host 'SwingLens Local Stack'
    Write-Host '----------------------------------------'
    Write-Host 'CORE'
    Write-Host ('Local PostgreSQL   {0}' -f $serviceText)
    Write-Host ('Database           {0}   {1}' -f (Format-State ([bool]$database.reachable) 'READY' 'UNAVAILABLE'), (Get-DatabaseEndpoint $database))
    Write-Host ('Schema             {0}' -f (Format-State ([bool]$database.schemaAtHead) 'HEAD' 'MISMATCH/UNAVAILABLE'))
    $webLabel = if ($status.WebConflict) {
        'CONFLICT'
    } elseif ($status.WebVerified) {
        Format-State $status.WebReady 'READY' 'UNHEALTHY'
    } else {
        'STOPPED'
    }
    Write-Host ('Web/API            {0}{1}' -f $webLabel, $webPid)
    Write-Host ('Supervisor         {0}{1}' -f (Format-State $status.SupervisorReady), $supervisorPid)
    Write-Host ('Worker             {0}{1}' -f (Format-State $status.WorkerReady), $workerPid)
    Write-Host ''
    Write-Host 'OBSERVABILITY'
    Write-Host ('Docker Engine      {0}' -f (Format-State $status.DockerReady 'READY' 'UNAVAILABLE'))
    Write-Host ('Prometheus         {0}   :9090' -f (Format-State $status.PrometheusReady))
    Write-Host ('Grafana            {0}   :3000' -f (Format-State $status.GrafanaReady))
    Write-Host ('Prometheus targets {0}/{1} UP' -f $status.Targets.Up, $status.Targets.Expected)
    Write-Host ''
    Write-Host ('OVERALL            {0}' -f $status.Overall)
    return $status
}

function Assert-SafeProcessTopology {
    $roles = @(
        @{ Port = $script:WebPort; Role = 'web' },
        @{ Port = $script:SupervisorPort; Role = 'supervisor' },
        @{ Port = $script:WorkerPort; Role = 'worker' }
    )
    foreach ($item in $roles) {
        $owners = @(Get-PortOwners -Port $item.Port)
        if ($owners.Count -gt 1) { throw ('Port {0} has multiple listeners; stop aborted.' -f $item.Port) }
        if ($owners.Count -eq 1 -and -not (Test-ProcessRole -Process $owners[0] -Role $item.Role)) {
            throw ('Port {0} is owned by unverified PID {1}; stop aborted.' -f $item.Port, $owners[0].ProcessId)
        }
        if ($item.Role -eq 'web' -and $owners.Count -eq 1 -and -not (Test-VerifiedWeb -Process $owners[0])) {
            throw ('Port 8000 process PID {0} did not verify as SwingLens; stop aborted.' -f $owners[0].ProcessId)
        }
    }
}

function Wait-ProcessExit {
    param([int]$ProcessId, [int]$TimeoutSeconds)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ($null -eq (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)) { return $true }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    return $false
}

function Stop-VerifiedProcess {
    param($Process, [ValidateSet('web', 'supervisor', 'worker')][string]$Role)
    $launcher = Get-RoleLauncherProcess -Process $Process -Role $Role
    if (-not (Test-ProcessRole -Process $launcher -Role $Role)) {
        throw ('PID {0} no longer matches the expected {1} identity.' -f $Process.ProcessId, $Role)
    }
    $signal = Invoke-LifecycleProbe -Command 'signal-break' -Arguments @('--pid', [string]$launcher.ProcessId)
    if ($signal.signaled -and (Wait-ProcessExit -ProcessId ([int]$launcher.ProcessId) -TimeoutSeconds 25)) { return }
    $current = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $launcher.ProcessId) -ErrorAction SilentlyContinue
    if ($null -ne $current -and (Test-ProcessRole -Process $current -Role $Role)) {
        Write-Warning ('Graceful {0} shutdown timed out; terminating verified PID tree {1}.' -f $Role, $launcher.ProcessId)
        $previousPreference = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try { $null = & taskkill /PID ([string]$launcher.ProcessId) /T /F 2>$null }
        finally { $ErrorActionPreference = $previousPreference }
    }
}

function Wait-PortsReleased {
    param([int[]]$Ports, [int]$TimeoutSeconds = 30)
    $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        $occupied = @($Ports | Where-Object { @(Get-PortOwners -Port $_).Count -gt 0 })
        if ($occupied.Count -eq 0) { return }
        Start-Sleep -Milliseconds 500
    } while ([DateTime]::UtcNow -lt $deadline)
    throw ('Verified SwingLens ports were not released: {0}' -f ($occupied -join ', '))
}

function Stop-SwingLensProcesses {
    Assert-SafeProcessTopology
    $owners = @(Get-PortOwners -Port $script:WebPort)
    if ($owners.Count -eq 1) {
        Write-Host ('Web/API: requesting controlled shutdown of PID {0}' -f $owners[0].ProcessId)
        $state = Get-WebRuntimeState
        if ($null -ne $state -and $state.repoRoot -eq $script:RepoRoot) {
            $stateProcess = Get-CimInstance Win32_Process -Filter ("ProcessId={0}" -f $state.webLauncherPid) -ErrorAction SilentlyContinue
            if ($null -ne $stateProcess -and (Test-ProcessRole -Process $stateProcess -Role 'web')) {
                Stop-VerifiedProcess -Process $stateProcess -Role 'web'
            } else {
                Stop-VerifiedProcess -Process $owners[0] -Role 'web'
            }
        } else {
            Stop-VerifiedProcess -Process $owners[0] -Role 'web'
        }
    }
    Start-Sleep -Milliseconds 500
    $supervisors = @(Get-PortOwners -Port $script:SupervisorPort)
    if ($supervisors.Count -eq 1) {
        Write-Warning 'Supervisor remained after web shutdown; stopping its verified PID.'
        Stop-VerifiedProcess -Process $supervisors[0] -Role 'supervisor'
    }
    Start-Sleep -Milliseconds 500
    $workers = @(Get-PortOwners -Port $script:WorkerPort)
    if ($workers.Count -eq 1) {
        Write-Warning 'Worker remained after supervisor shutdown; stopping its verified PID.'
        Stop-VerifiedProcess -Process $workers[0] -Role 'worker'
    }
    Wait-PortsReleased -Ports @($script:WebPort, $script:WorkerPort, $script:SupervisorPort)
    if (Test-Path -LiteralPath $script:RuntimeStatePath) {
        Remove-Item -LiteralPath $script:RuntimeStatePath -Force
    }
}

function Stop-SwingLensObservability {
    if (-not (Test-DockerEngine)) {
        Write-Host 'Observability: Docker Engine unavailable; no Docker action taken.'
        return
    }
    Push-Location $script:RepoRoot
    $previousPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $null = & docker compose -f $script:ObservabilityCompose stop grafana 2>$null
        $grafanaExit = $LASTEXITCODE
        $null = & docker compose -f $script:ObservabilityCompose stop prometheus 2>$null
        $prometheusExit = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousPreference
        Pop-Location
    }
    if ($grafanaExit -ne 0 -or $prometheusExit -ne 0) {
        throw 'Prometheus/Grafana stop did not complete successfully.'
    }
    Write-Host 'Observability: Grafana and Prometheus stopped; Docker Desktop remains running'
}

function Stop-AuthoritativeDatabase {
    param($DatabaseReport)
    if (-not (Get-ManagePostgres)) {
        Write-Host 'Local PostgreSQL: left running by SWINGLENS_MANAGE_POSTGRES=false'
        return
    }
    $service = Resolve-AuthoritativePostgresService -DatabaseReport $DatabaseReport
    if ($null -eq $service) {
        if ($DatabaseReport.reachable) {
            throw 'Authoritative PostgreSQL service could not be identified; database was not stopped.'
        }
        Write-Host 'Local PostgreSQL: already unavailable'
        return
    }
    $current = Get-Service -Name $service.Name
    if ($current.Status -ne 'Stopped') {
        Write-Host ('Local PostgreSQL: stopping verified service {0}' -f $service.Name)
        Stop-Service -Name $service.Name
        $current.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(60))
    } else {
        Write-Host ('Local PostgreSQL: verified service {0} already stopped' -f $service.Name)
    }
}

function Start-SwingLensStack {
    $database = Start-AuthoritativeDatabase -DatabaseReport (Get-DatabaseReport)
    Invoke-AlembicUpgrade
    $verified = Get-DatabaseReport
    if (-not $verified.reachable -or -not $verified.schemaAtHead) {
        throw 'Mandatory database/Alembic gate failed before web startup.'
    }
    Start-SwingLensWeb
    Start-SwingLensObservability
    $null = Write-SwingLensStatus
}

function Stop-SwingLensStack {
    $database = Get-DatabaseReport
    $active = Invoke-LifecycleProbe -Command 'active-jobs'
    if (-not $active.reachable -and @(Get-PortOwners -Port $script:WebPort).Count -gt 0) {
        throw 'Cannot verify durable job state while the database is unavailable; stop aborted.'
    }
    if ($null -ne $active.activeCount -and [int]$active.activeCount -gt 0) {
        $summary = @($active.active | ForEach-Object { '{0}:{1}:{2}' -f $_.id, $_.job_type, $_.status }) -join ', '
        throw ('Active durable jobs prevent a safe stop: {0}' -f $summary)
    }
    Stop-SwingLensProcesses
    Stop-SwingLensObservability
    Stop-AuthoritativeDatabase -DatabaseReport $database
    $null = Write-SwingLensStatus
}

function Invoke-SwingLensLifecycle {
    [CmdletBinding()]
    param([Parameter(Mandatory = $true)][ValidateSet('start', 'stop', 'restart', 'status')][string]$Action)
    Import-SwingLensEnvironment
    Push-Location $script:RepoRoot
    try {
        switch ($Action) {
            'start' { Start-SwingLensStack }
            'stop' { Stop-SwingLensStack }
            'restart' { Stop-SwingLensStack; Start-SwingLensStack }
            'status' { $null = Write-SwingLensStatus }
        }
    }
    finally { Pop-Location }
}

Export-ModuleMember -Function Invoke-SwingLensLifecycle
