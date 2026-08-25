from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

import app.services.pipeline_executor as pipeline_executor
from app.models.ceri_tables import CeriCompany
from app.models.tables import CombinedResult, IBFetchRun, PipelineRun, PipelineStep, UploadRun
from app.services.background_job_service import JobLeaseLost
from app.services.ceri.enums import CeriDataset
from app.services.ceri.feature_flags import CeriFeatureFlags
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.services.pipeline_executor import (
    PipelineCancelled,
    PipelineExecutionDependencies,
    _capture_ceri_snapshot,
    _schedule_ceri_provider_ingest,
    execute_full_pipeline,
)
from app.services.pipeline_prerequisites import CeriBootstrapRequiredError
from app.services.pipeline_service import PipelineStatus, PipelineStepStatus, pipeline_step_names
from app.services.ranking_profile_service import RankingPipelineResult
from app.services.sector_rotation_dtos import SectorRotationSnapshotDto


@pytest.fixture(autouse=True)
def _disable_optional_runtime_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    flags = CeriFeatureFlags(True, False, False, False, False, False, False)
    monkeypatch.setattr("app.services.pipeline_executor.ceri_flags", lambda: flags)
    monkeypatch.setattr("app.services.pipeline_service.ceri_flags", lambda: flags)
    monkeypatch.setattr(
        "app.services.pipeline_executor.get_settings",
        lambda: type(
            "SettingsStub",
            (),
            {
                "setup_lifecycle_pipeline_step_enabled": False,
                "setup_capture_handoff_enabled": False,
                "winner_probability_capture_in_pipeline": False,
                "fetch_technical_overlap_enabled": False,
            },
        )(),
    )


def test_ceri_provider_schedule_prioritizes_score_inputs_before_sec_guidance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[tuple[str, int]] = []
    added: list[object] = []

    class Db:
        def scalars(self, statement):
            entity = statement.column_descriptions[0].get("entity")
            if entity is CeriCompany:
                return []
            return [SimpleNamespace(ticker="MSFT")]

        def add(self, value) -> None:
            added.append(value)

        def flush(self) -> None:
            pass

    class Registry:
        def capabilities(self, _provider):
            return SimpleNamespace(datasets=frozenset(CeriDataset))

    def capture_enqueue(_db, _job_type, payload, **kwargs):
        scheduled.append((payload["dataset"], kwargs["priority"]))
        return SimpleNamespace(id=len(scheduled))

    monkeypatch.setattr(
        "app.services.ceri.provider_registry.CeriProviderRegistry", lambda: Registry()
    )
    monkeypatch.setattr(pipeline_executor, "enqueue_job", capture_enqueue)

    assert _schedule_ceri_provider_ingest(Db(), run_id=7) == 4
    assert dict(scheduled) == {
        "estimates": 80,
        "earnings": 90,
        "catalysts": 100,
        "guidance": 120,
    }
    assert [(company.ticker, company.exchange) for company in added] == [("MSFT", "US")]
    assert added[0].current_provider_ids_json == {"eodhd": "MSFT.US"}


def test_ceri_provider_schedule_routes_exclusively_to_batched_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []
    monkeypatch.setattr(
        pipeline_executor,
        "get_settings",
        lambda: SimpleNamespace(
            ceri_batched_workflow_enabled=True,
            ceri_legacy_pipeline_scheduling_enabled=False,
        ),
    )
    monkeypatch.setattr(
        "app.services.ceri.batched_workflow.schedule_ceri_batched_workflow",
        lambda _db, run_id: calls.append(run_id)
        or SimpleNamespace(provider_batches=68),
    )

    assert _schedule_ceri_provider_ingest(object(), run_id=95) == 68
    assert calls == [95]


def _fake_sec_repair_schedule(_db, *, pipeline, **_kwargs):
    pipeline.status = PipelineStatus.PREPARING
    pipeline.completed_at = None
    pipeline.message = "Preparing SEC evidence automatically."
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "repair_scheduled": True,
    }
    return SimpleNamespace(id=99)


def test_execute_full_pipeline_completes_when_cached_market_data_is_ready() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0, estimated_skips=2),
        technical_scores=[SimpleNamespace(ticker="MSFT", insufficient_data=False)],
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert calls == [
        "fundamentals",
        "build_fetch_plan",
        "technicals",
        "market_regime",
        "combined",
        "rankings",
        "sector_rotation",
    ]
    assert result.status == PipelineStatus.COMPLETED
    assert result.ib_planned_requests == 0
    assert result.ib_skipped_count == 2
    assert result.market_regime_snapshots == 1
    assert result.market_regime == "Confirmed Uptrend"
    assert result.market_risk_state == "green"
    assert result.market_regime_confidence == "normal"
    assert result.market_regime_warning_count == 0
    assert result.combined_results == 1
    assert result.sector_rotation_snapshots == 1
    assert result.sector_rotation_sector_count == 2
    assert result.sector_rotation_leading_sector == "Technology"
    assert result.sector_rotation_warning_count == 0
    assert result.winner_prediction_capture_skipped == 1
    assert db.pipeline.status == PipelineStatus.COMPLETED
    assert db.pipeline.current_step is None
    assert db.pipeline.result_json["combined_results"] == 1
    assert db.pipeline.result_json["market_regime"] == "Confirmed Uptrend"
    assert db.pipeline.result_json["market_risk_state"] == "green"
    assert db.pipeline.result_json["market_regime_confidence"] == "normal"
    assert db.pipeline.result_json["market_regime_warning_count"] == 0
    assert db.pipeline.result_json["sector_rotation_snapshots"] == 1
    assert db.pipeline.result_json["sector_rotation_sector_count"] == 2
    assert db.pipeline.result_json["sector_rotation_leading_sector"] == "Technology"
    assert db.pipeline.result_json["sector_rotation_warning_count"] == 0
    assert db.pipeline.result_json["performance"]["phase"] == 1
    assert "SCORING_TECHNICALS" in db.pipeline.result_json["performance"]["step_durations_ms"]
    assert "pipeline_queue_delay_ms" in db.pipeline.result_json["performance"]
    assert db.pipeline.result_json["performance"]["pipeline_execution_ms"] >= 0
    assert "pipeline_total_wall_ms" in db.pipeline.result_json["performance"]
    assert "technical_worker_processes" in db.pipeline.result_json["performance"]
    assert "technical_cache_hits" in db.pipeline.result_json["performance"]
    assert result.performance["phase"] == 1
    assert {step.status for step in db.steps} == {PipelineStepStatus.COMPLETED}


def test_sec_preflight_schedules_automatic_repair_before_expensive_pipeline_stages() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"], ceri_provider_ingest_enabled=True)
    calls: list[str] = []
    dependencies = replace(
        _dependencies(calls),
        ceri_provider_ingest_enabled=True,
        validate_pipeline_preflight=lambda *_args: (_ for _ in ()).throw(
            CeriBootstrapRequiredError(
                "bootstrap required",
                diagnostics={"readiness": {"ready_tickers": 0}},
            )
        ),
        schedule_sec_readiness_repair=_fake_sec_repair_schedule,
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert calls == []
    assert result.status == PipelineStatus.PREPARING
    assert db.pipeline.status == PipelineStatus.PREPARING
    assert db.pipeline.result_json["repair_scheduled"] is True
    assert db.steps[0].status == PipelineStepStatus.PENDING
    assert all(step.status == PipelineStepStatus.PENDING for step in db.steps[1:])


def test_repaired_initial_preflight_restarts_same_pipeline_from_validation() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"], ceri_provider_ingest_enabled=True)
    db.steps[0].status = PipelineStepStatus.BLOCKED
    db.pipeline.status = PipelineStatus.BLOCKED
    calls: list[str] = []
    dependencies = replace(
        _dependencies(
            calls,
            combined_results=[
                CombinedResult(
                    run_id=7,
                    ticker="MSFT",
                    is_complete=True,
                    has_warning=False,
                )
            ],
        ),
        ceri_provider_ingest_enabled=True,
        validate_pipeline_preflight=lambda *_args: {"complete": True},
        schedule_ceri_provider_ingest=lambda *_args: calls.append("ceri_schedule") or 1,
    )

    result = execute_full_pipeline(
        db,
        pipeline_run_id=3,
        dependencies=dependencies,
        resume_from_step="VALIDATING_RUN",
    )

    assert result.status == PipelineStatus.COMPLETED
    assert calls[0] == "fundamentals"
    assert "ceri_schedule" in calls
    assert db.steps[0].retry_count == 1


def test_resume_from_ceri_does_not_reexecute_completed_expensive_stages() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"], ceri_provider_ingest_enabled=True)
    ceri_index = next(
        index for index, step in enumerate(db.steps) if step.step_name == "CERI_PROVIDER_INGEST"
    )
    for step in db.steps[:ceri_index]:
        step.status = PipelineStepStatus.COMPLETED
    db.steps[ceri_index].status = PipelineStepStatus.BLOCKED
    db.pipeline.status = PipelineStatus.BLOCKED
    db.pipeline.result_json.update(
        {
            "fundamental_scores": 1,
            "technical_scores": 1,
            "combined_results": 1,
            "ranking_results": 5,
            "ranking_profiles": 5,
            "ranking_status": "COMPLETED",
            "market_data_mode": "IB_GATEWAY",
        }
    )
    calls: list[str] = []

    def schedule(_db, _run_id):
        calls.append("ceri_schedule")
        return 4

    dependencies = replace(
        _dependencies(calls, winner_capture_enabled=True),
        ceri_provider_ingest_enabled=True,
        schedule_ceri_provider_ingest=schedule,
        validate_pipeline_preflight=lambda *_args: {"complete": True},
        validate_resume_checkpoint=lambda *_args: {"validated": True},
    )

    result = execute_full_pipeline(
        db,
        pipeline_run_id=3,
        dependencies=dependencies,
        resume_from_step="CERI_PROVIDER_INGEST",
    )

    assert calls == ["ceri_schedule", "winner_capture"]
    assert result.status == PipelineStatus.COMPLETED
    assert all(
        step.retry_count == 0
        for step in db.steps
        if step.step_order < db.steps[ceri_index].step_order
    )


def test_resume_preflight_schedules_repair_without_ceri_enqueue() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"], ceri_provider_ingest_enabled=True)
    ceri_index = next(
        index for index, step in enumerate(db.steps) if step.step_name == "CERI_PROVIDER_INGEST"
    )
    for step in db.steps[:ceri_index]:
        step.status = PipelineStepStatus.COMPLETED
    db.steps[ceri_index].status = PipelineStepStatus.FAILED
    db.pipeline.status = PipelineStatus.FAILED
    calls: list[str] = []
    dependencies = replace(
        _dependencies(calls),
        ceri_provider_ingest_enabled=True,
        validate_resume_checkpoint=lambda *_args: {"validated": True},
        validate_pipeline_preflight=lambda *_args: (_ for _ in ()).throw(
            CeriBootstrapRequiredError("still cold")
        ),
        schedule_ceri_provider_ingest=lambda *_args: calls.append("enqueue"),
        schedule_sec_readiness_repair=_fake_sec_repair_schedule,
    )

    result = execute_full_pipeline(
        db,
        pipeline_run_id=3,
        dependencies=dependencies,
        resume_from_step="CERI_PROVIDER_INGEST",
    )

    assert calls == []
    assert result.status == PipelineStatus.PREPARING
    assert db.pipeline.status == PipelineStatus.PREPARING
    assert db.steps[ceri_index].status == PipelineStepStatus.PENDING


def test_execute_full_pipeline_runs_winner_capture_when_enabled() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
        winner_capture_enabled=True,
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert calls == [
        "fundamentals",
        "build_fetch_plan",
        "technicals",
        "market_regime",
        "combined",
        "rankings",
        "sector_rotation",
        "winner_capture",
    ]
    assert result.status == PipelineStatus.COMPLETED
    assert result.winner_prediction_inserted == 1
    assert result.winner_prediction_pending_outcomes == 10
    assert result.winner_prediction_decision_time_estimates == 1


def test_execute_full_pipeline_marks_ranking_skipped_without_profiles() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    dependencies = _dependencies(
        [],
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
    )
    dependencies = replace(
        dependencies,
        refresh_rankings=lambda *_: RankingPipelineResult(
            status="SKIPPED",
            profile_count=0,
            result_count=0,
            reason="no_configured_ranking_profiles",
            results=(),
        ),
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    ranking_step = next(step for step in db.steps if step.step_name == "RANKING_PROFILES")
    assert ranking_step.status == PipelineStepStatus.SKIPPED
    assert ranking_step.message == "no_configured_ranking_profiles"
    assert result.ranking_status == "SKIPPED"


def test_execute_full_pipeline_runs_setup_lifecycle_steps_when_enabled() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"], setup_lifecycle_enabled=True)
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
        setup_lifecycle_enabled=True,
        setup_capture_result={
            "snapshots_captured": 1,
            "canonical_snapshots": 1,
            "low_confidence": 0,
            "failed": 0,
        },
        setup_evaluation_result={
            "change_events": 2,
            "lifecycle_transitions": 1,
            "alerts": 1,
            "active_episodes": 1,
            "low_confidence": 0,
            "failed": 0,
        },
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert calls == [
        "fundamentals",
        "build_fetch_plan",
        "technicals",
        "market_regime",
        "combined",
        "rankings",
        "sector_rotation",
        "setup_capture",
        "setup_evaluate",
    ]
    assert result.status == PipelineStatus.COMPLETED
    assert result.setup_lifecycle_snapshots_captured == 1
    assert result.setup_lifecycle_canonical_snapshots == 1
    assert result.setup_lifecycle_change_events == 2
    assert result.setup_lifecycle_transitions == 1
    assert result.setup_lifecycle_alerts == 1
    assert result.setup_lifecycle_active_episodes == 1
    assert result.setup_lifecycle_capture_skipped == 0
    assert result.setup_lifecycle_evaluation_skipped == 0


def test_execute_full_pipeline_passes_setup_capture_handoff_when_enabled() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"], setup_lifecycle_enabled=True)
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
        setup_lifecycle_enabled=True,
        setup_capture_handoff_enabled=True,
        setup_capture_result={"snapshots_captured": 1},
        setup_evaluation_result={"canonical_snapshots": 1},
    )

    execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert "setup_evaluate_handoff" in calls


def test_execute_full_pipeline_runs_ceri_before_setup_lifecycle_when_enabled(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.pipeline_executor.ceri_flags",
        lambda: SimpleNamespace(enabled=True, run_capture=True, provider_ingest=False),
    )
    monkeypatch.setattr(
        "app.services.pipeline_service.ceri_flags",
        lambda: SimpleNamespace(enabled=True, run_capture=True, provider_ingest=False),
    )
    db = PipelineExecutorFakeDb(
        tickers=["MSFT"],
        ceri_enabled=True,
        setup_lifecycle_enabled=True,
    )
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
        ceri_enabled=True,
        ceri_capture_result={
            "score_snapshots": 1,
            "change_events": 2,
            "alerts": 1,
            "unrated": 0,
            "quarantined": 0,
            "conflicted": 0,
            "stale": 0,
            "failed": 0,
        },
        setup_lifecycle_enabled=True,
        setup_capture_result={
            "snapshots_captured": 1,
            "canonical_snapshots": 1,
            "low_confidence": 0,
            "failed": 0,
        },
        setup_evaluation_result={
            "change_events": 1,
            "lifecycle_transitions": 1,
            "alerts": 0,
            "active_episodes": 1,
            "low_confidence": 0,
            "failed": 0,
        },
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert calls == [
        "fundamentals",
        "build_fetch_plan",
        "technicals",
        "market_regime",
        "combined",
        "rankings",
        "sector_rotation",
        "ceri_capture",
        "setup_capture",
        "setup_evaluate",
    ]
    assert result.status == PipelineStatus.COMPLETED
    assert result.ceri_score_snapshots == 1
    assert result.ceri_change_events == 2
    assert result.ceri_alerts == 1
    assert result.setup_lifecycle_snapshots_captured == 1
    assert result.setup_lifecycle_transitions == 1


def test_execute_full_pipeline_marks_partial_for_ceri_capture_failures(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.pipeline_executor.ceri_flags",
        lambda: SimpleNamespace(enabled=True, run_capture=True, provider_ingest=False),
    )
    monkeypatch.setattr(
        "app.services.pipeline_service.ceri_flags",
        lambda: SimpleNamespace(enabled=True, run_capture=True, provider_ingest=False),
    )
    db = PipelineExecutorFakeDb(tickers=["MSFT"], ceri_enabled=True)
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
        ceri_enabled=True,
        ceri_capture_result={
            "score_snapshots": 1,
            "failed": 1,
            "quarantined": 2,
            "conflicted": 1,
            "stale": 3,
        },
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert "ceri_capture" in calls
    assert result.status == PipelineStatus.PARTIAL
    assert result.ceri_score_snapshots == 1
    assert result.ceri_failed == 1
    assert result.ceri_quarantined == 2
    assert result.ceri_conflicted == 1
    assert result.ceri_stale == 3


def test_default_ceri_capture_hook_returns_skip_when_no_run_rows() -> None:
    db = PipelineExecutorFakeDb(tickers=[])

    result = _capture_ceri_snapshot(db, run_id=7)

    assert result.as_dict()["skipped"] == 1


def test_execute_full_pipeline_uses_default_setup_lifecycle_hooks_when_enabled(
    monkeypatch,
) -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"], setup_lifecycle_enabled=True)
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
        setup_lifecycle_enabled=True,
    )

    def capture(_db, _run_id):
        calls.append("default_setup_capture")
        return {"snapshots_captured": 1, "canonical_snapshots": 0, "failed": 0}

    def evaluate(_db, _run_id):
        calls.append("default_setup_evaluate")
        return {"canonical_snapshots": 1, "change_events": 0, "failed": 0}

    monkeypatch.setattr(pipeline_executor, "_capture_setup_signals", capture)
    monkeypatch.setattr(pipeline_executor, "_evaluate_setup_lifecycles", evaluate)

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert "default_setup_capture" in calls
    assert "default_setup_evaluate" in calls
    assert result.setup_lifecycle_capture_skipped == 0
    assert result.setup_lifecycle_evaluation_skipped == 0
    assert result.setup_lifecycle_canonical_snapshots == 1


def test_execute_full_pipeline_runs_fetch_before_technicals_and_finishes_partial() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT", "AAPL"])
    calls = []
    fetch_run = IBFetchRun(
        id=11,
        run_id=7,
        requested_tickers=["MSFT", "AAPL"],
        status="PARTIAL",
        planned_request_count=2,
        executed_request_count=2,
        success_count=1,
        failure_count=1,
        skipped_count=0,
    )
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=2),
        fetch_run=fetch_run,
        technical_scores=[
            SimpleNamespace(ticker="MSFT", insufficient_data=False),
            SimpleNamespace(ticker="AAPL", insufficient_data=True),
        ],
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False),
            CombinedResult(run_id=7, ticker="AAPL", is_complete=False, has_warning=True),
        ],
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert calls == [
        "fundamentals",
        "build_fetch_plan",
        "fetch",
        "technicals",
        "market_regime",
        "combined",
        "rankings",
        "sector_rotation",
    ]
    assert result.status == PipelineStatus.PARTIAL
    assert result.ib_executed_requests == 2
    assert result.ib_failure_count == 1
    assert result.technical_scores == 2
    assert result.incomplete_rows == 1
    assert result.warning_rows == 1
    assert db.pipeline.status == PipelineStatus.PARTIAL


def test_execute_full_pipeline_keeps_low_confidence_market_snapshot_nonfatal() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        market_regime_snapshot=SimpleNamespace(
            regime="Unknown",
            risk_state="gray",
            confidence="low",
            warnings=["missing_spy_market_data"],
        ),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert calls == [
        "fundamentals",
        "build_fetch_plan",
        "technicals",
        "market_regime",
        "combined",
        "rankings",
        "sector_rotation",
    ]
    assert result.status == PipelineStatus.PARTIAL
    assert result.market_regime == "Unknown"
    assert result.market_risk_state == "gray"
    assert result.market_regime_confidence == "low"
    assert result.market_regime_warning_count == 1
    assert db.pipeline.result_json["market_regime_snapshots"] == 1
    assert db.pipeline.result_json["market_regime"] == "Unknown"
    assert db.pipeline.result_json["market_risk_state"] == "gray"
    assert db.pipeline.result_json["market_regime_confidence"] == "low"
    assert db.pipeline.result_json["market_regime_warning_count"] == 1


def test_execute_full_pipeline_marks_pipeline_cancelled_before_next_step() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    calls = []
    checks = iter([False, False, True])
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
    )

    with pytest.raises(PipelineCancelled):
        execute_full_pipeline(
            db,
            pipeline_run_id=3,
            should_cancel=lambda: next(checks),
            dependencies=dependencies,
        )

    assert calls == ["fundamentals"]
    assert db.pipeline.status == PipelineStatus.CANCELLED
    assert db.pipeline.completed_at is not None
    unfinished = [
        step.status
        for step in db.steps
        if step.step_name
        in {
            "FETCHING_MARKET_DATA",
            "SCORING_TECHNICALS",
            "MARKET_REGIME_SNAPSHOT",
            "COMBINING_RESULTS",
            "SECTOR_ROTATION_SNAPSHOT",
        }
    ]
    assert set(unfinished) == {PipelineStepStatus.CANCELLED}


def test_execute_full_pipeline_fails_when_run_has_no_tickers() -> None:
    db = PipelineExecutorFakeDb(tickers=[])

    with pytest.raises(ValueError, match="No uploaded tickers"):
        execute_full_pipeline(db, pipeline_run_id=3, dependencies=_dependencies([]))

    assert db.pipeline.status == PipelineStatus.FAILED
    assert db.pipeline.error_message == "No uploaded tickers are available for this run."
    assert db.steps[0].status == PipelineStepStatus.FAILED
    assert db.rollbacks == 1


def test_require_ib_stops_before_technicals_when_gateway_closes_after_preflight() -> None:
    calls: list[str] = []
    db = PipelineExecutorFakeDb(["MSFT"])
    dependencies = replace(
        _dependencies(calls, combined_results=[CombinedResult(ticker="MSFT")]),
        check_ib_gateway=lambda: SimpleNamespace(
            status="NOT_RUNNING_OR_UNREACHABLE",
            checked_at=datetime.now(UTC),
            api_connected=False,
            error_code="IB_GATEWAY_API_UNREACHABLE",
            message="offline",
        ),
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert result.status == PipelineStatus.FAILED
    assert "fundamentals" in calls
    assert "build_fetch_plan" not in calls
    assert "technicals" not in calls
    assert "combined" not in calls
    assert "winner_capture" not in calls
    assert db.pipeline.result_json["failure_reason"] == "IB_GATEWAY_UNAVAILABLE"
    assert db.pipeline.result_json["market_data_mode"] == "BLOCKED"
    assert db.pipeline.current_step == "FETCHING_MARKET_DATA"
    fetch_step = next(step for step in db.steps if step.step_name == "FETCHING_MARKET_DATA")
    assert fetch_step.status == PipelineStepStatus.FAILED


def test_allow_cache_fallback_persists_degraded_metadata_and_skips_winner_capture() -> None:
    calls: list[str] = []
    db = PipelineExecutorFakeDb(["MSFT"])
    db.pipeline.result_json = {
        "background_job_id": 99,
        "market_data_policy": "ALLOW_CACHE_FALLBACK",
    }
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT"],
        items=[
            FetchPlanItem(
                ticker="MSFT",
                contract_status="RESOLVED",
                what_to_show="TRADES",
                action=FetchAction.TOP_UP_RECENT,
                duration="10 D",
                bar_size="1 day",
                current_bar_count=300,
                first_bar_date=date(2025, 1, 2),
                latest_bar_date=date(2026, 8, 14),
                required_bars=252,
                reason="top up",
                estimated_request_count=1,
                freshness_threshold_date=date(2026, 8, 14),
            )
        ],
        estimated_request_count=1,
        estimated_full_backfills=0,
        estimated_top_ups=1,
        estimated_refreshes=0,
        estimated_skips=0,
        warnings=[],
    )
    technical = SimpleNamespace(
        insufficient_data=False,
        technical_confidence="high",
        data_quality_score=Decimal("10.0"),
        warning_flags_json=[],
        missing_data_json={},
    )
    combined = CombinedResult(ticker="MSFT", is_complete=True, has_warning=False)
    dependencies = replace(
        _dependencies(
            calls,
            plan=plan,
            technical_scores=[technical],
            combined_results=[combined],
            winner_capture_enabled=True,
        ),
        check_ib_gateway=lambda: SimpleNamespace(
            status="NOT_RUNNING_OR_UNREACHABLE",
            checked_at=datetime.now(UTC),
            api_connected=False,
            error_code="IB_GATEWAY_API_UNREACHABLE",
            message="offline",
        ),
    )

    result = execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert result.status == PipelineStatus.PARTIAL
    assert "fetch" not in calls
    assert "technicals" in calls
    assert "winner_capture" not in calls
    assert technical.technical_confidence == "low"
    assert technical.data_quality_score == Decimal("6.0")
    assert technical.warning_flags_json == ["cache_fallback_market_data"]
    assert technical.missing_data_json["market_data"]["mode"] == "CACHE_FALLBACK"
    audit = db.pipeline.result_json
    assert audit["market_data_policy"] == "ALLOW_CACHE_FALLBACK"
    assert audit["market_data_mode"] == "CACHE_FALLBACK"
    assert audit["degraded"] is True
    assert audit["ib_api_available_at_execution"] is False
    assert audit["fresh_fetch_count"] == 0
    assert audit["cache_used_count"] == 1
    assert audit["latest_expected_market_session"] == "2026-08-14"
    assert audit["actual_latest_data_session"] == "2026-08-14"
    assert audit["winner_prediction_capture_skip_reason"] == "CACHE_FALLBACK_MARKET_DATA"

def test_execute_full_pipeline_does_not_commit_step_completion_after_lease_loss() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
    )
    guard_calls = {"count": 0}

    def lease_guard() -> None:
        guard_calls["count"] += 1
        if guard_calls["count"] == 5:
            raise JobLeaseLost("lease lost")

    with pytest.raises(JobLeaseLost, match="lease lost"):
        execute_full_pipeline(
            db,
            pipeline_run_id=3,
            dependencies=dependencies,
            lease_guard=lease_guard,
        )

    assert calls == ["fundamentals"]
    assert db.commits == 4
    assert db.commit_snapshots[-1]["steps"]["SCORING_FUNDAMENTALS"] == PipelineStepStatus.RUNNING
    assert not any(
        snapshot["steps"]["SCORING_FUNDAMENTALS"] == PipelineStepStatus.COMPLETED
        for snapshot in db.commit_snapshots
    )


def test_execute_full_pipeline_records_replay_attempt_for_previously_completed_step() -> None:
    db = PipelineExecutorFakeDb(tickers=["MSFT"])
    db.steps[0].status = PipelineStepStatus.COMPLETED
    calls = []
    dependencies = _dependencies(
        calls,
        plan=_plan(estimated_request_count=0),
        combined_results=[
            CombinedResult(run_id=7, ticker="MSFT", is_complete=True, has_warning=False)
        ],
    )

    execute_full_pipeline(db, pipeline_run_id=3, dependencies=dependencies)

    assert db.steps[0].retry_count == 1
    assert db.steps[0].message == "Replaying step attempt 2."


def _dependencies(
    calls: list[str],
    plan: FetchPlan | None = None,
    fetch_run: IBFetchRun | None = None,
    technical_scores: list[object] | None = None,
    market_regime_snapshot: object | None = None,
    combined_results: list[CombinedResult] | None = None,
    sector_rotation_snapshot: SectorRotationSnapshotDto | None = None,
    ceri_enabled: bool | None = None,
    ceri_capture_result: dict[str, int] | None = None,
    setup_lifecycle_enabled: bool | None = None,
    setup_capture_handoff_enabled: bool | None = None,
    setup_capture_result: dict[str, int] | None = None,
    setup_evaluation_result: dict[str, int] | None = None,
    winner_capture_enabled: bool | None = None,
) -> PipelineExecutionDependencies:
    plan = plan or _plan(estimated_request_count=0)
    fetch_run = fetch_run or IBFetchRun(
        id=11,
        run_id=7,
        requested_tickers=["MSFT"],
        status="COMPLETED",
        planned_request_count=0,
        executed_request_count=0,
        success_count=0,
        failure_count=0,
        skipped_count=0,
    )
    technical_scores = technical_scores or []
    market_regime_snapshot = market_regime_snapshot or SimpleNamespace(
        regime="Confirmed Uptrend",
        risk_state="green",
        confidence="normal",
        warnings=[],
    )
    combined_results = combined_results or []
    sector_rotation_snapshot = sector_rotation_snapshot or _sector_snapshot()

    def fundamentals(_db, _run_id):
        calls.append("fundamentals")
        return [SimpleNamespace(ticker="MSFT")]

    def fetch_plan(**_kwargs):
        calls.append("build_fetch_plan")
        return plan

    def fetch(**_kwargs):
        calls.append("fetch")
        return fetch_run

    def technicals(_db, _run_id):
        calls.append("technicals")
        return technical_scores

    def market_regime(_db, _run_id):
        calls.append("market_regime")
        return market_regime_snapshot

    def combined(_db, _run_id):
        calls.append("combined")
        return combined_results

    def rankings(_db, _run_id):
        calls.append("rankings")
        return RankingPipelineResult(
            status="COMPLETED",
            profile_count=5,
            result_count=len(combined_results) * 5,
            reason=None,
            results=(),
        )

    def sector_rotation(_db, _run_id):
        calls.append("sector_rotation")
        return sector_rotation_snapshot

    def ceri_capture(_db, _run_id):
        calls.append("ceri_capture")
        return ceri_capture_result or {}

    def setup_capture(_db, _run_id):
        calls.append("setup_capture")
        return setup_capture_result or {}

    def setup_evaluate(_db, _run_id, *, capture_result=None):
        calls.append("setup_evaluate")
        if capture_result is not None:
            calls.append("setup_evaluate_handoff")
        return setup_evaluation_result or {}

    def winner_capture(_db, _run_id):
        calls.append("winner_capture")
        return {
            "inserted": 1,
            "pending_outcomes": 10,
            "decision_time_estimates": 1,
        }

    return PipelineExecutionDependencies(
        recalculate_fundamentals=fundamentals,
        build_fetch_plan=fetch_plan,
        execute_fetch_plan=fetch,
        score_technicals=technicals,
        build_market_regime_snapshot=market_regime,
        refresh_combined=combined,
        refresh_rankings=rankings,
        build_sector_rotation_snapshot=sector_rotation,
        capture_ceri_snapshot=ceri_capture if ceri_capture_result is not None else None,
        ceri_run_capture_enabled=ceri_enabled,
        ceri_provider_ingest_enabled=False,
        capture_setup_signals=setup_capture if setup_capture_result is not None else None,
        evaluate_setup_lifecycles=setup_evaluate if setup_evaluation_result is not None else None,
        setup_lifecycle_pipeline_step_enabled=setup_lifecycle_enabled,
        setup_capture_handoff_enabled=setup_capture_handoff_enabled,
        capture_winner_predictions=winner_capture,
        winner_probability_capture_enabled=winner_capture_enabled,
        check_ib_gateway=lambda: SimpleNamespace(
            status="READY",
            checked_at=datetime.now(UTC),
            api_connected=True,
            error_code=None,
            message="ready",
        ),
    )


def _sector_snapshot(
    warnings: list[str] | None = None,
) -> SectorRotationSnapshotDto:
    return SectorRotationSnapshotDto(
        run_id=7,
        as_of_date="2026-07-28",
        mode="universe_only",
        calculation_version="sector-rotation-1.0.0",
        config_version="1.0.0",
        config_hash="hash-a",
        default_ranking_profile="momentum_swing",
        rows=[],
        summary={
            "sector_count": 2,
            "ticker_count": 5,
            "leading_sector": "Technology",
            "weakest_sector": "Utilities",
        },
        warnings=warnings or [],
        debug={},
    )


def _plan(
    estimated_request_count: int,
    estimated_skips: int = 0,
) -> FetchPlan:
    return FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT", "SPY"],
        items=[],
        estimated_request_count=estimated_request_count,
        estimated_full_backfills=estimated_request_count,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=estimated_skips,
        warnings=[],
    )


class FakeScalarResult:
    def __init__(self, rows) -> None:
        self.rows = rows

    def all(self):
        return self.rows

    def __iter__(self):
        return iter(self.rows)


class PipelineExecutorFakeDb:
    def __init__(
        self,
        tickers: list[str],
        ceri_enabled: bool = False,
        ceri_provider_ingest_enabled: bool = False,
        setup_lifecycle_enabled: bool = False,
    ) -> None:
        self.pipeline = PipelineRun(
            id=3,
            upload_run_id=7,
            status=PipelineStatus.PENDING,
            current_step="VALIDATING_RUN",
            result_json={"background_job_id": 99},
        )
        self.upload_run = UploadRun(
            id=7,
            filename="sample.csv",
            row_count=len(tickers),
            status="COMPLETED",
        )
        self.tickers = tickers
        self.steps = [
            PipelineStep(
                id=index,
                pipeline_run_id=3,
                step_name=step_name,
                step_order=index,
                status=PipelineStepStatus.PENDING,
                retry_count=0,
            )
            for index, step_name in enumerate(
                pipeline_step_names(
                    ceri_run_capture_enabled=ceri_enabled,
                    ceri_provider_ingest_enabled=ceri_provider_ingest_enabled,
                    setup_lifecycle_pipeline_step_enabled=setup_lifecycle_enabled,
                ),
                start=1,
            )
        ]
        self.flushes = 0
        self.commits = 0
        self.rollbacks = 0
        self.commit_snapshots: list[dict[str, object]] = []
        self._step_index = 0

    def get(self, model, row_id):
        if model is PipelineRun and row_id == self.pipeline.id:
            return self.pipeline
        if model is UploadRun and row_id == self.upload_run.id:
            return self.upload_run
        return None

    def scalar(self, _statement):
        params = _statement.compile().params
        requested_step = next(
            (
                value
                for value in params.values()
                if isinstance(value, str)
                and any(step.step_name == value for step in self.steps)
            ),
            None,
        )
        if requested_step is not None:
            return next(step for step in self.steps if step.step_name == requested_step)
        step = self.steps[self._step_index]
        self._step_index += 1
        return step

    def scalars(self, statement):
        text = str(statement)
        if "raw_company_rows" in text:
            return FakeScalarResult(self.tickers)
        if "pipeline_steps" in text:
            return FakeScalarResult(self.steps)
        return FakeScalarResult([])

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1
        self.commit_snapshots.append(
            {
                "pipeline_status": self.pipeline.status,
                "steps": {step.step_name: step.status for step in self.steps},
            }
        )

    def rollback(self) -> None:
        self.rollbacks += 1
