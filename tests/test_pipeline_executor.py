from types import SimpleNamespace

import pytest

from app.models.tables import CombinedResult, IBFetchRun, PipelineRun, PipelineStep, UploadRun
from app.services.ib_fetch_plan_service import FetchPlan
from app.services.pipeline_executor import (
    PipelineCancelled,
    PipelineExecutionDependencies,
    execute_full_pipeline,
)
from app.services.pipeline_service import PIPELINE_STEP_NAMES, PipelineStatus, PipelineStepStatus


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

    assert calls == ["fundamentals", "build_fetch_plan", "technicals", "combined"]
    assert result.status == PipelineStatus.COMPLETED
    assert result.ib_planned_requests == 0
    assert result.ib_skipped_count == 2
    assert result.combined_results == 1
    assert db.pipeline.status == PipelineStatus.COMPLETED
    assert db.pipeline.current_step is None
    assert db.pipeline.result_json["combined_results"] == 1
    assert {step.status for step in db.steps} == {PipelineStepStatus.COMPLETED}


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

    assert calls == ["fundamentals", "build_fetch_plan", "fetch", "technicals", "combined"]
    assert result.status == PipelineStatus.PARTIAL
    assert result.ib_executed_requests == 2
    assert result.ib_failure_count == 1
    assert result.technical_scores == 2
    assert result.incomplete_rows == 1
    assert result.warning_rows == 1
    assert db.pipeline.status == PipelineStatus.PARTIAL


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
        if step.step_name in {"FETCHING_MARKET_DATA", "SCORING_TECHNICALS", "COMBINING_RESULTS"}
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
    combined_results: list[CombinedResult] | None = None,
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
    combined_results = combined_results or []

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

    def combined(_db, _run_id):
        calls.append("combined")
        return combined_results

    return PipelineExecutionDependencies(
        recalculate_fundamentals=fundamentals,
        build_fetch_plan=fetch_plan,
        execute_fetch_plan=fetch,
        score_technicals=technicals,
        refresh_combined=combined,
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
    def __init__(self, tickers: list[str]) -> None:
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
            for index, step_name in enumerate(PIPELINE_STEP_NAMES, start=1)
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
