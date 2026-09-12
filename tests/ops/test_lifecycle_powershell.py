from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE = ROOT / "scripts" / "ops" / "SwingLensLifecycle.psm1"
LAUNCHER = ROOT / "swinglens.ps1"
PWSH = shutil.which("pwsh")
WINDOWS_POWERSHELL = shutil.which("powershell")


def _module_command(body: str) -> list[str]:
    escaped = str(MODULE).replace("'", "''")
    return [
        str(PWSH),
        "-NoProfile",
        "-NonInteractive",
        "-Command",
        f"Import-Module '{escaped}' -Force; try {{ & (Get-Module SwingLensLifecycle) "
        f"{{ {body} }} }} catch {{ [Console]::Error.WriteLine($_.Exception.Message); exit 1 }}",
    ]


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("ok", "ok"),
        ("degraded", "degraded"),
        ("failed", "failed"),
        ("optional_unavailable", "optional_unavailable"),
        ("unknown", "failed"),
    ],
)
def test_powershell_parses_readiness_payload_not_http_status(payload, expected) -> None:
    command = _module_command(
        f"$p=[pscustomobject]@{{status='{payload}'}}; "
        "Resolve-ReadinessPayloadState -Payload $p"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == expected


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_readiness_probe_retries_transient_database_failure_and_logs_recovery() -> None:
    command = _module_command(
        "$script:calls=0; function Invoke-HttpProbe { $script:calls++; "
        "if ($script:calls -eq 1) { [pscustomobject]@{Reachable=$true;"
        "Payload=[pscustomobject]@{status='failed';"
        "check_states=[pscustomobject]@{database='failed'}}} } else { "
        "[pscustomobject]@{Reachable=$true;Payload=[pscustomobject]@{status='degraded'}} } }; "
        "$probe=Get-ReadinessProbe -WebPort 8000; "
        "([string]$probe.Payload.status) + ':' + $script:calls"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "Transient readiness probe failure on attempt 1/3; retrying." in output
    assert "Readiness probe recovered on attempt 2/3." in output
    assert result.stdout.strip().endswith("degraded:2")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
@pytest.mark.parametrize("failed_component", ["migrations", "worker", "topology", "runtime", "sec"])
def test_readiness_probe_does_not_retry_semantic_failure(failed_component) -> None:
    command = _module_command(
        "$script:calls=0; function Invoke-HttpProbe { $script:calls++; "
        f"[pscustomobject]@{{Reachable=$true;Payload=[pscustomobject]@{{status='failed';"
        f"check_states=[pscustomobject]@{{database='ok';{failed_component}='failed'}}}}}} }}; "
        "$probe=Get-ReadinessProbe -WebPort 8000; $script:calls"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "1"


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_readiness_probe_persistent_database_failure_exhausts_budget_and_fails_closed() -> None:
    command = _module_command(
        "$script:calls=0; function Invoke-HttpProbe { $script:calls++; "
        "[pscustomobject]@{Reachable=$true;Payload=[pscustomobject]@{status='failed';"
        "check_states=[pscustomobject]@{database='failed'}}} }; "
        "$probe=Get-ReadinessProbe -WebPort 8000; "
        "(Resolve-ReadinessPayloadState -Payload $probe.Payload) + ':' + $script:calls"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "exhausted the 3-attempt budget" in output
    assert result.stdout.strip().endswith("failed:3")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
@pytest.mark.parametrize(
    ("owner_action", "contender_action"),
    [("start", "start"), ("start", "stop"), ("restart", "start"), ("stop", "stop")],
)
def test_repository_named_mutex_rejects_real_competing_process(
    tmp_path, owner_action, contender_action
) -> None:
    owner_ready = tmp_path / "mutex-owned"
    contender_ready = tmp_path / "contender-ready"
    owner_ready_literal = str(owner_ready).replace("'", "''")
    contender_ready_literal = str(contender_ready).replace("'", "''")
    contender = subprocess.Popen(
        _module_command(
            f"[IO.File]::WriteAllText('{contender_ready_literal}', 'ready'); "
            f"while (-not (Test-Path -LiteralPath '{owner_ready_literal}')) {{ "
            "Start-Sleep -Milliseconds 25 }; "
            f"Invoke-WithLifecycleLock -Action '{contender_action}' "
            "-TimeoutSeconds 1 -Body { 0 }"
        ),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 30
    while not contender_ready.exists() and time.monotonic() < deadline:
        time.sleep(0.025)
    assert contender_ready.exists(), "contender did not reach the contention barrier"
    owner = subprocess.Popen(
        _module_command(
            f"Invoke-WithLifecycleLock -Action '{owner_action}' -TimeoutSeconds 5 "
            f"-Body {{ [IO.File]::WriteAllText('{owner_ready_literal}', 'ready'); "
            "Start-Sleep -Seconds 3; 0 }"
        ),
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        contender_stdout, contender_stderr = contender.communicate(timeout=30)
        assert contender.returncode != 0, repr((contender_stdout, contender_stderr))
        assert "Another SwingLens lifecycle operation is already running" in contender_stderr
    finally:
        stdout, stderr = owner.communicate(timeout=30)
    assert owner.returncode == 0, stdout + stderr


def test_launcher_requires_powershell_74_before_module_import() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    version_gate = source.index("[Version]'7.4'")
    assert version_gate < source.index("Import-Module")
    assert "SwingLens lifecycle requires PowerShell 7.4 or newer" in source
    assert "pwsh .\\swinglens.ps1 start" in source


@pytest.mark.skipif(WINDOWS_POWERSHELL is None, reason="Windows PowerShell is unavailable")
def test_windows_powershell_51_is_rejected_before_module_import() -> None:
    result = subprocess.run(
        [
            str(WINDOWS_POWERSHELL),
            "-NoProfile",
            "-NonInteractive",
            "-File",
            str(LAUNCHER),
            "status",
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=20,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "SwingLens lifecycle requires PowerShell 7.4 or newer" in output


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
@pytest.mark.parametrize("role", ["worker", "supervisor"])
def test_database_failure_with_live_background_role_fails_stop_closed(role) -> None:
    command = _module_command(
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}; "
        "postgres=[pscustomobject]@{managementEnabled=$false}}; "
        "function Get-WebOwner { $null }; "
        "function Invoke-LifecycleProbe { param($Command,$Arguments); "
        "if ($Command -eq 'processes') "
        f"{{ [pscustomobject]@{{processes=@([pscustomobject]@{{role='{role}'}})}} }} else "
        "{ [pscustomobject]@{reachable=$false} } }; "
        "Stop-SwingLensStack -Config $cfg"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "Cannot prove durable worker quiescence" in result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_committed_fence_with_zero_jobs_proceeds_without_worker_ack() -> None:
    command = _module_command(
        "$script:events=@(); $env:SWINGLENS_LIFECYCLE_ACTION='stop'; "
        "function Write-LifecycleJournal { "
        "param($Action,$Stage,$Event,$Result,$ReasonCode,$DurationMs,$Message,$Details); "
        "$script:events += $Event }; "
        "function Invoke-LifecycleProbe { param($Command); if ($Command -eq 'quiesce') { "
        "[pscustomobject]@{reachable=$true;requested=$true;claimFenceEstablished=$true;"
        "workerAcknowledged=$false;safeToStop=$true;activeCount=0;active=@();"
        "workerAcknowledgedAt=$null;ackLatencySeconds=$null;workerInstanceId='instance';"
        "workerGeneration=3;workerPid=101;"
        "reasonCode='QUIESCE_SAFE_WITHOUT_WORKER_ACK';quiesceRequestedAt='2026-09-12T10:50:00Z';"
        "claimFenceEstablishedAt='2026-09-12T10:50:01Z';safeToStopAt='2026-09-12T10:50:01Z'} "
        "} elseif ($Command -eq 'resume') { $script:events += 'resume' } }; "
        "$report=Request-WorkerQuiesce -TimeoutSeconds 0; "
        "[bool]$report.safeToStop; $script:events -join ','"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "QUIESCE_SAFE_WITHOUT_WORKER_ACK" in result.stdout + result.stderr
    assert "True" in result.stdout
    assert "worker_acknowledgement_degraded,claim_fence_established" in result.stdout
    assert "resume" not in result.stdout


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
@pytest.mark.parametrize("committed,expected_resumes", [(False, 1), (True, 0)])
def test_stop_resumes_claims_only_before_shutdown_commit(
    committed: bool, expected_resumes: int
) -> None:
    committed_literal = "$true" if committed else "$false"
    command = _module_command(
        "$script:resumes=0; "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}}; "
        "function Retire-DeadStaleRuntimeState {}; "
        "function Get-WebOwner { [pscustomobject]@{ProcessId=101} }; "
        "function Request-WorkerQuiesce {}; "
        "function Invoke-LifecycleProbe { param($Command); if ($Command -eq 'processes') { "
        "[pscustomobject]@{processes=@()} } elseif ($Command -eq 'resume') { "
        "$script:resumes++ } }; "
        "function Stop-SwingLensCore { param($Config,[ref]$ShutdownCommitted); "
        f"$ShutdownCommitted.Value={committed_literal}; throw 'synthetic stop failure' }}; "
        "try { Stop-SwingLensStack -Config $cfg } catch {}; $script:resumes"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(expected_resumes)


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
@pytest.mark.parametrize("role", ["worker", "supervisor"])
def test_web_gone_with_live_background_role_is_failed(role, tmp_path) -> None:
    missing_state = str(tmp_path / "missing-state.json").replace("'", "''")
    command = _module_command(
        f"$script:RuntimeStatePath='{missing_state}'; "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}}; "
        "function Get-WebOwner { $null }; function Test-DockerEngine { $false }; "
        f"function Invoke-LifecycleProbe {{ param($Command); if ($Command -eq 'database') "
        "{ [pscustomobject]@{reachable=$true;schemaAtHead=$true} } else "
        f"{{ [pscustomobject]@{{processes=@([pscustomobject]@{{role='{role}'}})}} }} }}; "
        "(Get-SwingLensStatusReport -Config $cfg).Overall"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "FAILED"


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_observability_failure_return_does_not_block_core_restart_sequence() -> None:
    command = _module_command(
        "$script:events=@(); $script:phase='stop'; "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}; "
        "postgres=[pscustomobject]@{managementEnabled=$false}}; "
        "function Get-WebOwner { [pscustomobject]@{ProcessId=7} }; "
        "function Invoke-LifecycleProbe { param($Command,$Arguments); "
        "[pscustomobject]@{processes=@();reachable=$true;schemaAtHead=$true} }; "
        "function Request-WorkerQuiesce {}; "
        "function Stop-SwingLensCore { $script:events += 'stop-core' }; "
        "function Stop-SwingLensObservability { $script:events += 'stop-observability'; $false }; "
        "function Stop-AuthoritativeDatabase {}; "
        "function Start-AuthoritativeDatabase { $script:events += 'start-database' }; "
        "function Invoke-AlembicUpgrade { $script:events += 'migrate' }; "
        "function Set-RuntimeGeneration { [pscustomobject]@{fingerprint='test'} }; "
        "function Get-ValidatedRuntime { $null }; "
        "function Start-SwingLensWeb { $script:events += 'start-core' }; "
        "function Start-SwingLensObservability { "
        "$script:events += 'start-observability'; $false }; "
        "function Write-SwingLensStatus { if ($script:phase -eq 'stop') "
        "{ [pscustomobject]@{Overall='STOPPED';Owner=$null} } else "
        "{ [pscustomobject]@{Overall='DEGRADED';Owner=[pscustomobject]@{ProcessId=8}} } }; "
        "$null=Stop-SwingLensStack -Config $cfg; $script:phase='start'; "
        "$code=Start-SwingLensStack -Config $cfg; "
        "($script:events -join ','); $code"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "stop-core,stop-observability,start-database,migrate,start-core,start-observability" in (
        result.stdout
    )
    assert result.stdout.strip().endswith("0")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_core_stop_waits_for_supervisor_exit_before_registered_cleanup() -> None:
    command = _module_command(
        "$script:events=@(); "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}; "
        "metrics=[pscustomobject]@{enabled=$false;workerPort=0;supervisorPort=0}}; "
        "function Get-WebOwner { [pscustomobject]@{ProcessId=101} }; "
        "function Get-ValidatedRuntime { [pscustomobject]@{state=[pscustomobject]@{"
        "web=[pscustomobject]@{pid=101};supervisor=[pscustomobject]@{pid=202}}} }; "
        "function Invoke-LifecycleProbe { param($Command,$Arguments); "
        "if ($Command -eq 'signal-break') { $script:events += 'request'; "
        "[pscustomobject]@{signaled=$true} } else { [pscustomobject]@{processes=@()} } }; "
        "function Wait-PortReleased { param($Port); $script:events += ('port-' + $Port) }; "
        "function Wait-ProcessExit { param($ProcessId); "
        "$script:events += ('process-' + $ProcessId); $true }; "
        "function Stop-RegisteredRemainders { $script:events += 'remainders' }; "
        "Stop-SwingLensCore -Config $cfg; $script:events -join ','"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "request,port-8000,process-202,remainders,port-8000"


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_non_admin_service_start_failure_is_explicit() -> None:
    command = _module_command(
        "$cfg=[pscustomobject]@{postgres=[pscustomobject]@{managementEnabled=$true;"
        "service='postgresql-x64-18'}}; "
        "function Invoke-LifecycleProbe { [pscustomobject]@{reachable=$false} }; "
        "function Assert-PostgresServiceConfiguration { [pscustomobject]@{State='Stopped'} }; "
        "function Start-Service { throw 'Access is denied' }; "
        "Start-AuthoritativeDatabase -Config $cfg"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "Access is denied" in result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_foreign_web_listener_without_runtime_state_is_a_conflict() -> None:
    command = _module_command(
        "function Get-PortOwners { @([pscustomobject]@{ProcessId=444}) }; "
        "function Invoke-LifecycleProbe { [pscustomobject]@{valid=$false;"
        "missing=$true;error='runtime state is missing'} }; "
        "Get-ValidatedRuntime -WebPort 8000"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "strong SwingLens runtime identity failed" in result.stderr


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_status_reports_dead_stale_generation_as_stopped_without_mutation(tmp_path) -> None:
    state_path = tmp_path / "swinglens-lifecycle.json"
    state_path.write_text('{"runtimeInstanceId":"old-runtime"}', encoding="utf-8")
    state_literal = str(state_path).replace("'", "''")
    command = _module_command(
        f"$script:RuntimeStatePath='{state_literal}'; "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}}; "
        "function Get-WebOwner { $null }; function Test-DockerEngine { $false }; "
        "function Invoke-LifecycleProbe { param($Command,$Arguments); switch ($Command) { "
        "'database' { [pscustomobject]@{reachable=$true;schemaAtHead=$true} } "
        "'runtime-state' { [pscustomobject]@{classification='DEAD_STALE';valid=$false;"
        "stale=$true;runtimeActive=$false;conflict=$false;"
        "state=[pscustomobject]@{runtimeInstanceId='old-runtime'};"
        "recordedGitSha='old';desiredGitSha='new';recordedFingerprint='old-fp';"
        "desiredFingerprint='new-fp';staleRuntimeInstanceId='old-runtime';"
        "staleStateReason='physical evidence is quiescent'} } "
        "'prometheus-targets' { [pscustomobject]@{allUp=$false} } "
        "'processes' { [pscustomobject]@{processes=@()} } "
        "'jobs' { [pscustomobject]@{activeCount=0;active=@()} } } }; "
        "$status=Get-SwingLensStatusReport -Config $cfg; "
        "$status.Overall + ':' + $status.StaleStatePresent + ':' + "
        f"(Test-Path -LiteralPath '{state_literal}')"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "STOPPED:True:True"
    assert state_path.is_file()


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_start_retires_dead_stale_generation_before_launching_new_generation(tmp_path) -> None:
    state_path = tmp_path / "swinglens-lifecycle.json"
    state_path.write_text("{}", encoding="utf-8")
    state_literal = str(state_path).replace("'", "''")
    command = _module_command(
        f"$script:RuntimeStatePath='{state_literal}'; $script:events=@(); "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}}; "
        "function Invoke-LifecycleProbe { param($Command,$Arguments); "
        "if ($Command -eq 'retire-stale-state') { $script:events += 'retire'; "
        f"Remove-Item -LiteralPath '{state_literal}'; "
        "[pscustomobject]@{retired=$true;conflict=$false;staleRuntimeInstanceId='old';"
        "reasonCode='DEAD_GENERATION_RETIRED'} } else { "
        "[pscustomobject]@{reachable=$true;schemaAtHead=$true} } }; "
        "function Get-ValidatedRuntime { $null }; "
        "function Start-AuthoritativeDatabase { $script:events += 'database' }; "
        "function Invoke-AlembicUpgrade { $script:events += 'migrate' }; "
        "function Set-RuntimeGeneration { $script:events += 'fingerprint' }; "
        "function Start-SwingLensWeb { $script:events += 'launch' }; "
        "function Start-SwingLensObservability { $script:events += 'observability'; $true }; "
        "function Write-SwingLensStatus { [pscustomobject]@{Overall='HEALTHY'} }; "
        "$code=Start-SwingLensStack -Config $cfg; ($script:events -join ','); $code; "
        f"Test-Path -LiteralPath '{state_literal}'"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "retire,database,migrate,fingerprint,launch,observability" in result.stdout
    assert result.stdout.strip().endswith("False")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_stop_retires_dead_stale_generation_and_succeeds_idempotently(tmp_path) -> None:
    state_path = tmp_path / "swinglens-lifecycle.json"
    state_path.write_text("{}", encoding="utf-8")
    state_literal = str(state_path).replace("'", "''")
    command = _module_command(
        f"$script:RuntimeStatePath='{state_literal}'; $script:events=@(); "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000};"
        "postgres=[pscustomobject]@{managementEnabled=$false}}; "
        "function Invoke-LifecycleProbe { param($Command,$Arguments); "
        "if ($Command -eq 'retire-stale-state') { $script:events += 'retire'; "
        f"Remove-Item -LiteralPath '{state_literal}'; "
        "[pscustomobject]@{retired=$true;conflict=$false;staleRuntimeInstanceId='old';"
        "reasonCode='DEAD_GENERATION_RETIRED'} } else { [pscustomobject]@{processes=@()} } }; "
        "function Get-WebOwner { $null }; "
        "function Stop-SwingLensObservability { $script:events += 'stop-observability'; $true }; "
        "function Stop-AuthoritativeDatabase { $script:events += 'keep-database' }; "
        "function Write-SwingLensStatus { [pscustomobject]@{Overall='STOPPED';Owner=$null} }; "
        "$first=Stop-SwingLensStack -Config $cfg; $second=Stop-SwingLensStack -Config $cfg; "
        "($script:events -join ','); $first; $second"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    expected = "retire,stop-observability,keep-database,stop-observability,keep-database"
    assert expected in result.stdout
    assert result.stdout.strip().endswith("0")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_interrupted_restart_stale_record_self_recovers_as_clean_start(tmp_path) -> None:
    state_path = tmp_path / "swinglens-lifecycle.json"
    state_path.write_text('{"interruptedRestart":true}', encoding="utf-8")
    state_literal = str(state_path).replace("'", "''")
    command = _module_command(
        f"$script:RuntimeStatePath='{state_literal}'; $script:events=@(); "
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}}; "
        "function Invoke-LifecycleProbe { param($Command,$Arguments); "
        "if ($Command -eq 'retire-stale-state') { $script:events += 'retire-interrupted'; "
        f"Remove-Item -LiteralPath '{state_literal}'; "
        "[pscustomobject]@{retired=$true;conflict=$false;staleRuntimeInstanceId='old';"
        "reasonCode='DEAD_GENERATION_RETIRED'} } else { "
        "[pscustomobject]@{reachable=$true;schemaAtHead=$true} } }; "
        "function Get-ValidatedRuntime { $null }; function Start-AuthoritativeDatabase {}; "
        "function Invoke-AlembicUpgrade { $script:events += 'migrate' }; "
        "function Set-RuntimeGeneration {}; "
        "function Start-SwingLensWeb { $script:events += 'new-runtime' }; "
        "function Start-SwingLensObservability { $true }; "
        "function Write-SwingLensStatus { [pscustomobject]@{Overall='HEALTHY'} }; "
        "$null=Start-SwingLensStack -Config $cfg; $script:events -join ','"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().endswith("retire-interrupted,migrate,new-runtime")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
def test_restart_routes_dead_stale_retirement_through_stop_then_clean_start() -> None:
    command = _module_command(
        "$script:events=@(); $env:SWINGLENS_LIFECYCLE_OPERATION_ID='restart-test'; "
        "function Get-GitCommit { 'new-sha' }; function Write-LifecycleJournal {}; "
        "function Get-LifecycleConfig { [pscustomobject]@{lockTimeoutSeconds=1} }; "
        "function Set-CanonicalLifecycleEnvironment {}; function Set-RuntimeGeneration {}; "
        "function Invoke-WithLifecycleLock { param($Action,$TimeoutSeconds,$Body); & $Body }; "
        "function Stop-SwingLensStack { $script:events += 'retire-stale-stop' }; "
        "function Start-SwingLensStack { $script:events += 'clean-start'; 0 }; "
        "$code=Invoke-SwingLensLifecycle -Action restart; ($script:events -join ','); $code"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert "retire-stale-stop,clean-start" in result.stdout
    assert result.stdout.strip().endswith("0")


@pytest.mark.skipif(PWSH is None, reason="PowerShell 7 is required")
@pytest.mark.parametrize("failed_component", ["worker", "supervisor"])
def test_component_dying_before_final_certification_cannot_exit_start_zero(
    failed_component,
) -> None:
    command = _module_command(
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}}; "
        "function Get-ValidatedRuntime { $null }; "
        "function Start-AuthoritativeDatabase {}; function Invoke-AlembicUpgrade {}; "
        "function Set-RuntimeGeneration { [pscustomobject]@{fingerprint='test'} }; "
        "function Invoke-LifecycleProbe { [pscustomobject]@{reachable=$true;schemaAtHead=$true} }; "
        "function Start-SwingLensWeb {}; function Start-SwingLensObservability { $true }; "
        f"function Write-SwingLensStatus {{ [pscustomobject]@{{Overall='FAILED';"
        f"FailedComponent='{failed_component}'}} }}; Start-SwingLensStack -Config $cfg"
    )
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "Start certification failed with state FAILED" in result.stderr


def test_restart_keeps_one_lock_and_isolates_observability_failures() -> None:
    source = MODULE.read_text(encoding="utf-8")
    restart = source.split("'restart' {", 1)[1]
    assert restart.index("Stop-SwingLensStack") < restart.index("Start-SwingLensStack")
    assert source.index("Invoke-WithLifecycleLock") < source.index("'restart' {")
    assert "$null = Stop-SwingLensObservability" in source
    assert "$null = Start-SwingLensObservability" in source
