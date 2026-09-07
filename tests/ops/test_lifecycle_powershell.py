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
    assert result.stdout.strip().endswith("2")


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
@pytest.mark.parametrize("failed_component", ["worker", "supervisor"])
def test_component_dying_before_final_certification_cannot_exit_start_zero(
    failed_component,
) -> None:
    command = _module_command(
        "$cfg=[pscustomobject]@{web=[pscustomobject]@{port=8000}}; "
        "function Get-ValidatedRuntime { $null }; "
        "function Start-AuthoritativeDatabase {}; function Invoke-AlembicUpgrade {}; "
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
