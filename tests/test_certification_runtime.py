from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.observability.correlation import CausalityContext
from app.services.background_worker import run_worker_once
from app.services.certification_runtime import (
    CERTIFICATION_DISABLED_AUTOMATIC_WORKFLOWS,
    CertificationRuntimeViolation,
    effective_runtime_configuration,
    require_enqueue_authorized,
)
from app.settings import RuntimeMode, Settings


def certification_settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "runtime_mode": RuntimeMode.CERTIFICATION,
        "use_durable_pipeline": True,
        "job_worker_enabled": True,
        "winner_probability_auto_maturation_enabled": False,
        "winner_probability_auto_cohort_refresh_enabled": False,
        "market_data_prewarm_enabled": False,
    }
    values.update(overrides)
    return Settings(**values)


def test_certification_profile_exposes_effective_isolation() -> None:
    summary = effective_runtime_configuration(certification_settings())

    assert summary["runtime_mode"] == "CERTIFICATION"
    assert summary["certification_isolation_active"] is True
    assert summary["authorized_root_job_type"] == "FULL_PIPELINE"
    assert summary["effective_disabled_automatic_workflows"] == list(
        CERTIFICATION_DISABLED_AUTOMATIC_WORKFLOWS
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("winner_probability_auto_maturation_enabled", True),
        ("winner_probability_auto_cohort_refresh_enabled", True),
        ("market_data_prewarm_enabled", True),
        ("job_worker_enabled", False),
        ("use_durable_pipeline", False),
    ],
)
def test_certification_conflicts_fail_closed(name: str, value: bool) -> None:
    with pytest.raises(ValidationError, match="CERTIFICATION runtime"):
        certification_settings(**{name: value})


def test_certification_enqueue_allowlist_accepts_only_explicit_root_or_lineage() -> None:
    context = CausalityContext("root-1", "cause-1")
    settings = certification_settings()

    require_enqueue_authorized(
        SimpleNamespace(),
        job_type="FULL_PIPELINE",
        payload={
            "certification_authorized": True,
            "transition_preflight_plan_id": 3,
        },
        causality=context,
        settings=settings,
    )

    with pytest.raises(CertificationRuntimeViolation, match="not part of"):
        require_enqueue_authorized(
            SimpleNamespace(scalar=lambda _query: None),
            job_type="WINNER_OUTCOME_MATURATION",
            payload={},
            causality=context,
            settings=settings,
        )

    require_enqueue_authorized(
        SimpleNamespace(scalar=lambda _query: 99),
        job_type="CERI_PROVIDER_INGEST",
        payload={},
        causality=context,
        settings=settings,
    )


def test_ten_certification_worker_cycles_never_schedule_or_claim_unrelated_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claims: list[bool] = []
    sessions: list[FakeWorkerDb] = []
    monkeypatch.setattr(
        "app.services.background_worker.recover_abandoned_jobs_for_worker",
        lambda *_args, **_kwargs: pytest.fail("recovery must be suppressed"),
    )
    monkeypatch.setattr(
        "app.services.background_worker.recover_stale_jobs",
        lambda *_args, **_kwargs: pytest.fail("stale recovery must be suppressed"),
    )
    monkeypatch.setattr(
        "app.services.background_worker.register_worker",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.heartbeat_worker_control_loop",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "app.services.background_worker.claim_next_job",
        lambda *_args, **kwargs: claims.append(kwargs["certification_only"]),
    )

    for _cycle in range(10):
        db = FakeWorkerDb()
        sessions.append(db)
        ran = run_worker_once(
            worker_id="certification-worker",
            stale_after_seconds=60,
            session_factory=lambda db=db: db,
            handlers={},
            schedule_winner_probability=True,
            certification_mode=True,
        )
        assert ran is False

    assert claims == [True] * 10
    assert all(db.commits == 2 and db.closed for db in sessions)


class FakeWorkerDb:
    def __init__(self) -> None:
        self.commits = 0
        self.closed = False

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        pytest.fail("certification idle cycle must not roll back")

    def close(self) -> None:
        self.closed = True
