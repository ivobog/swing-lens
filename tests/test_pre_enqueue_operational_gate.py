from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services.certification_runtime import QueueIsolationStatus
from app.services.ib_gateway_health_service import IBGatewayHealthStatus
from app.services.pre_enqueue_operational_gate import (
    CONTEXT_REJECTION_SEMANTICS,
    PLAN_REJECTION_SEMANTICS,
    PreEnqueueOperationalGateError,
    validate_pre_enqueue_operational_gate,
)
from app.services.transition_preflight_plan_service import TransitionPreflightError
from app.settings import RuntimeMode, Settings

NOW = datetime(2026, 9, 10, 14, 0, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        runtime_mode=RuntimeMode.CERTIFICATION,
        use_durable_pipeline=True,
        job_worker_enabled=True,
        winner_probability_auto_maturation_enabled=False,
        winner_probability_auto_cohort_refresh_enabled=False,
        market_data_prewarm_enabled=False,
    )


def _ib_status(
    *,
    status: str = "IB_API_READY",
    api_ready: bool = True,
    checked_at: datetime = NOW,
) -> IBGatewayHealthStatus:
    return IBGatewayHealthStatus(
        status=status,
        host="127.0.0.1",
        port=4002,
        api_connected=api_ready,
        api_ready=api_ready,
        process_running=True,
        server_version=176 if api_ready else None,
        smoke_response_received=api_ready,
        checked_at=checked_at,
        expires_at=checked_at + timedelta(seconds=5),
        latency_ms=10,
        error_code=None if api_ready else "IB_GATEWAY_API_NOT_READY",
        failure_category=None if api_ready else "IB_GATEWAY_API_NOT_READY",
        message="ready" if api_ready else "API not ready; no pipeline was created.",
    )


@pytest.fixture
def gate_db() -> SimpleNamespace:
    plan = SimpleNamespace(
        id=3,
        upload_run_id=154,
        market_calculation_context_id=7,
        status="RESERVED",
        expires_at=NOW + timedelta(minutes=30),
    )
    context = SimpleNamespace(id=7, pipeline_run_id=None)

    class Db:
        added: list[object] = []

        def get(self, model, row_id):
            if model.__name__ == "TransitionPreflightPlan" and row_id == 3:
                return plan
            if model.__name__ == "MarketCalculationContext" and row_id == 7:
                return context
            return None

        def scalar(self, _statement):
            return "0072_ceri_artifact_context_lineage"

    return Db()


def _green_dependencies(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    worker = SimpleNamespace(
        worker_id="certification-worker",
        queues_json=["interactive"],
        control_loop_heartbeat_at=NOW,
    )
    verified = SimpleNamespace(plan=SimpleNamespace(id=3, market_calculation_context_id=7))
    monkeypatch.setattr(
        "app.services.pre_enqueue_operational_gate.live_workers",
        lambda *_args, **_kwargs: [worker],
    )
    monkeypatch.setattr(
        "app.services.pre_enqueue_operational_gate.queue_isolation_status",
        lambda *_args, **_kwargs: QueueIsolationStatus(True, 0, NOW),
    )
    monkeypatch.setattr(
        "app.services.pre_enqueue_operational_gate.repository_alembic_heads",
        lambda *_args, **_kwargs: ("0072_ceri_artifact_context_lineage",),
    )
    monkeypatch.setattr(
        "app.services.pre_enqueue_operational_gate.verify_transition_preflight_for_enqueue",
        lambda *_args, **_kwargs: verified,
    )
    return verified


def test_all_green_gate_returns_authoritative_observability(
    monkeypatch: pytest.MonkeyPatch,
    gate_db,
) -> None:
    verified = _green_dependencies(monkeypatch)

    result = validate_pre_enqueue_operational_gate(
        gate_db,
        upload_run_id=154,
        plan_id=3,
        settings=_settings(),
        health_probe=lambda **_kwargs: _ib_status(),
        now=NOW,
    )

    assert result.verified_preflight is verified
    assert result.to_dict()["passed"] is True
    assert result.to_dict()["worker_count"] == 1
    assert result.to_dict()["queue_isolation"]["unrelated_runnable_jobs"] == 0
    assert gate_db.added == []


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("ib_flap", "IB_API_NOT_READY"),
        ("stale_ready", "IB_API_NOT_READY"),
        ("worker_expired", "DURABLE_WORKER_ISOLATION_FAILED"),
        ("second_worker", "DURABLE_WORKER_ISOLATION_FAILED"),
        ("dirty_queue", "CERTIFICATION_QUEUE_NOT_ISOLATED"),
        ("manifest_stale", "DECISION_MANIFEST_MISMATCH"),
    ],
)
def test_gate_failure_injection_creates_no_business_state(
    monkeypatch: pytest.MonkeyPatch,
    gate_db,
    case: str,
    expected_code: str,
) -> None:
    _green_dependencies(monkeypatch)

    def health_probe(**_kwargs):
        if case == "ib_flap":
            return _ib_status(status="IB_PROCESS_RUNNING_API_NOT_READY", api_ready=False)
        if case == "stale_ready":
            return _ib_status(checked_at=NOW - timedelta(seconds=6))
        return _ib_status()

    if case == "worker_expired":
        monkeypatch.setattr(
            "app.services.pre_enqueue_operational_gate.live_workers",
            lambda *_args, **_kwargs: [],
        )
    elif case == "second_worker":
        worker = SimpleNamespace(
            worker_id="duplicate",
            queues_json=["interactive"],
            control_loop_heartbeat_at=NOW,
        )
        monkeypatch.setattr(
            "app.services.pre_enqueue_operational_gate.live_workers",
            lambda *_args, **_kwargs: [worker, worker],
        )
    elif case == "dirty_queue":
        monkeypatch.setattr(
            "app.services.pre_enqueue_operational_gate.queue_isolation_status",
            lambda *_args, **_kwargs: QueueIsolationStatus(False, 1, NOW),
        )
    elif case == "manifest_stale":
        monkeypatch.setattr(
            "app.services.pre_enqueue_operational_gate.verify_transition_preflight_for_enqueue",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                TransitionPreflightError("DECISION_MANIFEST_MISMATCH", "manifest changed")
            ),
        )

    with pytest.raises(PreEnqueueOperationalGateError) as caught:
        validate_pre_enqueue_operational_gate(
            gate_db,
            upload_run_id=154,
            plan_id=3,
            settings=_settings(),
            health_probe=health_probe,
            now=NOW,
        )

    assert caught.value.code == expected_code
    assert caught.value.to_dict()["plan_state_after_rejection"] == PLAN_REJECTION_SEMANTICS
    assert caught.value.to_dict()["context_state_after_rejection"] == (CONTEXT_REJECTION_SEMANTICS)
    assert gate_db.added == []
