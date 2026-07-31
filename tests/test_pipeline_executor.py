from types import SimpleNamespace

import pytest

from app.models.tables import CombinedResult, IBFetchRun, PipelineRun, PipelineStep, UploadRun
from app.services.ib_fetch_plan_service import FetchPlan
from app.services.pipeline_executor import (
    PipelineCancelled,
    PipelineExecutionDependencies,
    execute_full_pipeline,
)
from app.services.pipeline_service import PipelineStatus, PipelineStepStatus, pipeline_step_names
from app.services.sector_rotation_dtos import SectorRotationSnapshotDto


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
    assert {step.status for step in db.steps} == {PipelineStepStatus.COMPLETED}


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
        "sector_rotation",
        "winner_capture",
    ]
    assert result.status == PipelineStatus.COMPLETED
    assert result.winner_prediction_inserted == 1
    assert result.winner_prediction_pending_outcomes == 10
    assert result.winner_prediction_decision_time_estimates == 1


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


def _dependencies(
    calls: list[str],
    plan: FetchPlan | None = None,
    fetch_run: IBFetchRun | None = None,
    technical_scores: list[object] | None = None,
    market_regime_snapshot: object | None = None,
    combined_results: list[CombinedResult] | None = None,
    sector_rotation_snapshot: SectorRotationSnapshotDto | None = None,
    setup_lifecycle_enabled: bool | None = None,
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

    def sector_rotation(_db, _run_id):
        calls.append("sector_rotation")
        return sector_rotation_snapshot

    def setup_capture(_db, _run_id):
        calls.append("setup_capture")
        return setup_capture_result or {}

    def setup_evaluate(_db, _run_id):
        calls.append("setup_evaluate")
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
        build_sector_rotation_snapshot=sector_rotation,
        capture_setup_signals=setup_capture if setup_capture_result is not None else None,
        evaluate_setup_lifecycles=setup_evaluate
        if setup_evaluation_result is not None
        else None,
        setup_lifecycle_pipeline_step_enabled=setup_lifecycle_enabled,
        capture_winner_predictions=winner_capture,
        winner_probability_capture_enabled=winner_capture_enabled,
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
                    setup_lifecycle_pipeline_step_enabled=setup_lifecycle_enabled
                ),
                start=1,
            )
        ]
        self.flushes = 0
        self.commits = 0
        self._step_index = 0

    def get(self, model, row_id):
        if model is PipelineRun and row_id == self.pipeline.id:
            return self.pipeline
        if model is UploadRun and row_id == self.upload_run.id:
            return self.upload_run
        return None

    def scalar(self, _statement):
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
