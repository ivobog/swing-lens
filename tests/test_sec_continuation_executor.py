from datetime import UTC, datetime
from types import SimpleNamespace

from app.services import pipeline_executor
from app.services.ceri.sec.coverage import build_sec_coverage
from app.services.ceri.sec.readiness_diagnostics import (
    SecTickerReadiness,
    SecTickerReadinessCategory,
    SecUniverseReadiness,
)


def _case():
    pipeline = SimpleNamespace(id=56, scope_id="scope-56", status="RUNNING", result_json={})
    readiness = SecUniverseReadiness("sig", (
        SecTickerReadiness("GOOD", SecTickerReadinessCategory.READY, ciks=("0000000001",)),
        SecTickerReadiness("MISSING", SecTickerReadinessCategory.UNRESOLVED_MAPPING),
    ))
    coverage = build_sec_coverage(
        pipeline=pipeline, readiness=readiness,
        identity_results={"MISSING": {"status": "UNRESOLVED", "reason": "No exact SEC match."}},
        attempted_at=datetime(2026, 11, 9, tzinfo=UTC),
    )
    result = pipeline_executor._empty_result(pipeline, SimpleNamespace(id=55, row_count=2))
    result.update(
        combined_results=2, sec_coverage=coverage, sec_unresolved_count=1,
        sec_unresolved_tickers=coverage["gaps"],
    )
    return pipeline, result


def test_fresh_preflight_clears_invalid_coverage_from_merged_result():
    _pipeline, result = _case()
    result["sec_preflight"] = {"continuation_ready": True, "sec_unresolved_count": 0}
    pipeline_executor._retain_sec_preflight_coverage(result)
    assert result["sec_coverage"] is None
    assert result["sec_unresolved_count"] == 0
    assert result["sec_unresolved_tickers"] == []


def test_full_scope_completion_retains_source_gap_and_structured_outcome(monkeypatch):
    pipeline, result = _case()
    monkeypatch.setattr(pipeline_executor, "publish_after_commit", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_executor, "_save_progress", lambda *a, **k: None)

    def transition(_db, row, status, **kwargs):
        row.status = status
        row.message = kwargs["message"]

    monkeypatch.setattr("app.services.pipeline_state_machine.transition_pipeline", transition)
    assert pipeline_executor._final_pipeline_status(result) == "COMPLETED"
    pipeline_executor._mark_pipeline_finished(SimpleNamespace(), pipeline, "COMPLETED", result)
    assert pipeline.status == "COMPLETED"
    assert "unresolved for 1 tickers" in pipeline.message
    outcome = pipeline.result_json["pipeline_outcome_summary"]
    assert outcome["coverage"]["gaps"]["sec_unresolved_count"] == 1
    assert outcome["coverage"]["sources"]["sec"]["gaps"][0]["ticker"] == "MISSING"
    assert outcome["coverage"]["excluded_tickers"] == []
    # The worker's completion result must retain the same gap, rather than
    # making coverage disappear at the dataclass serialization boundary.
    completion = pipeline_executor._to_execution_result(pipeline, result)
    assert completion.sec_unresolved_count == 1
    assert completion.sec_unresolved_tickers[0]["ticker"] == "MISSING"
    assert completion.sec_coverage == result["sec_coverage"]
    assert completion.pipeline_outcome_summary == outcome
