from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.services import pipeline_executor
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    fetch_plan_to_dict,
)

SESSION = date(2026, 11, 9)


def _case(monkeypatch):
    monkeypatch.setattr(pipeline_executor, "latest_completed_us_trading_day", lambda: SESSION)
    pipeline = SimpleNamespace(
        id=56, upload_run_id=55, scope_id="scope-56",
        result_json={
            "ib_api_available_at_execution": False,
            "ib_execution_error_code": "IB_GATEWAY_API_UNREACHABLE",
            "ib_planned_requests": 0,
            "ib_executed_requests": 0,
        },
    )
    return pipeline, SimpleNamespace(
        latest_completed_session=SESSION, context_id=100,
        cutoff_at=datetime(2026, 11, 9, 22, 5, tzinfo=UTC),
    )


def _plan():
    return FetchPlan(
        run_id=55, requested_tickers=["MSFT"], symbols_including_benchmarks=["MSFT"],
        items=[FetchPlanItem(
            ticker="MSFT", contract_status="RESOLVED", what_to_show="TRADES",
            action=FetchAction.FULL_BACKFILL, duration="3 Y", bar_size="1 day",
            current_bar_count=0, first_bar_date=None, latest_bar_date=None, required_bars=252,
            reason="First acquisition", estimated_request_count=1,
            freshness_threshold_date=SESSION, request_end_date=SESSION,
            reviewed_session_expiry=SESSION,
        )],
        estimated_request_count=1, estimated_full_backfills=1, estimated_top_ups=0,
        estimated_refreshes=0, estimated_skips=0, warnings=[],
    )


def test_unstarted_resume_uses_retained_api_proof_after_prior_retry_cleared_reason(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    pipeline.result_json["failure_reason"] = None
    original = dict(pipeline.result_json)
    pipeline_executor._validate_unstarted_market_data_resume(
        SimpleNamespace(), pipeline, ["MSFT"], cutoff,
    )
    assert pipeline.result_json == original


@pytest.mark.parametrize("changed", [
    {"ib_api_available_at_execution": True},
    {"ib_api_available_at_execution": None},
    {"ib_api_available_at_execution": 0},
    {"ib_execution_error_code": None},
    {"ib_execution_error_code": "IB_HISTORICAL_DATA_UNAVAILABLE"},
    {"ib_planned_requests": 1},
    {"ib_executed_requests": 1},
    {"ib_fetch_authority": {"scope_id": "already-admitted"}},
    {"ib_fetch_authority": {}},
    {"ib_fetch_plan": {"retained": "plan"}},
    {"ib_fetch_plan": "corrupted checkpoint"},
    {"market_data_resume_checkpoint": {"first_plan_admitted": True}},
])
def test_absent_plan_does_not_waive_missing_proof_or_acquisition_metadata(monkeypatch, changed):
    pipeline, cutoff = _case(monkeypatch)
    pipeline.result_json.update(changed)
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_FROZEN_PLAN_REQUIRED"):
        pipeline_executor._validate_unstarted_market_data_resume(
            SimpleNamespace(), pipeline, ["MSFT"], cutoff,
        )


def test_unstarted_resume_requires_replan_after_market_session_changes(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    monkeypatch.setattr(
        pipeline_executor, "latest_completed_us_trading_day", lambda: SESSION + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_SESSION_CHANGED_REPLAN_REQUIRED"):
        pipeline_executor._validate_unstarted_market_data_resume(
            SimpleNamespace(), pipeline, ["MSFT"], cutoff,
        )


def test_retained_initial_admission_proof_survives_ready_gateway_planning_failure(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    proof = pipeline_executor._validate_unstarted_market_data_resume(
        SimpleNamespace(), pipeline, ["MSFT"], cutoff,
    )
    pipeline.result_json.update(
        ib_api_available_at_execution=True, ib_execution_error_code=None,
        market_data_resume_checkpoint={
            "validated": True, "fetch_plan_state": "NOT_ADMITTED", "initial_admission": proof,
        },
    )
    assert pipeline_executor._validate_unstarted_market_data_resume(
        SimpleNamespace(), pipeline, ["MSFT"], cutoff,
    ) == proof


@pytest.mark.parametrize("changed", [
    {"pipeline_run_id": 57},
    {"upload_run_id": 54},
    {"scope_id": "another-scope"},
    {"market_calculation_context_id": 99},
    {"market_cutoff_at": "2026-11-09T22:06:00Z"},
])
def test_initial_admission_proof_cannot_be_borrowed_from_another_owner(monkeypatch, changed):
    pipeline, cutoff = _case(monkeypatch)
    proof = pipeline_executor._validate_unstarted_market_data_resume(
        SimpleNamespace(), pipeline, ["MSFT"], cutoff,
    )
    pipeline.result_json.update(
        ib_api_available_at_execution=True, ib_execution_error_code=None,
        market_data_resume_checkpoint={
            "validated": True, "fetch_plan_state": "NOT_ADMITTED",
            "initial_admission": {**proof, **changed},
        },
    )
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_FROZEN_PLAN_REQUIRED"):
        pipeline_executor._validate_unstarted_market_data_resume(
            SimpleNamespace(), pipeline, ["MSFT"], cutoff,
        )


def test_initial_admission_proof_cannot_replace_an_already_admitted_plan(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    proof = pipeline_executor._validate_unstarted_market_data_resume(
        SimpleNamespace(), pipeline, ["MSFT"], cutoff,
    )
    pipeline.result_json.update(
        ib_api_available_at_execution=True, ib_execution_error_code=None,
        market_data_resume_checkpoint={
            "validated": True, "fetch_plan_state": "NOT_ADMITTED", "first_plan_admitted": True,
            "initial_admission": proof,
        },
    )
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_FROZEN_PLAN_REQUIRED"):
        pipeline_executor._validate_unstarted_market_data_resume(
            SimpleNamespace(), pipeline, ["MSFT"], cutoff,
        )


@pytest.mark.parametrize("retained", [{}, {"items": []}, "corrupt-plan"])
def test_malformed_retained_plan_is_not_treated_as_unstarted_acquisition(monkeypatch, retained):
    pipeline, cutoff = _case(monkeypatch)
    pipeline.result_json["ib_fetch_plan"] = retained
    monkeypatch.setattr(
        pipeline_executor, "_require_step", lambda *a: SimpleNamespace(status="COMPLETED"),
    )
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_FROZEN_PLAN_REQUIRED"):
        pipeline_executor._validate_market_data_resume(
            SimpleNamespace(), pipeline, ["MSFT"], cutoff,
        )


def test_retained_plan_cannot_belong_to_another_upload(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    pipeline.result_json["ib_fetch_plan"] = fetch_plan_to_dict(replace(_plan(), run_id=54))
    monkeypatch.setattr(
        pipeline_executor, "_require_step", lambda *a: SimpleNamespace(status="COMPLETED"),
    )
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_PLAN_SCOPE_CHANGED"):
        pipeline_executor._validate_market_data_resume(
            SimpleNamespace(), pipeline, ["MSFT"], cutoff,
        )


def test_unstarted_resume_still_checks_persisted_fundamental_presence(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    monkeypatch.setattr(
        pipeline_executor, "_require_step", lambda *a: SimpleNamespace(status="COMPLETED"),
    )
    monkeypatch.setattr(
        pipeline_executor, "_validate_unstarted_market_data_resume", lambda *a: None,
    )
    with Session() as db:
        monkeypatch.setattr(db, "scalars", lambda *a: [])
        with pytest.raises(ValueError, match="MARKET_DATA_RESUME_FUNDAMENTAL_MISSING: MSFT"):
            pipeline_executor._validate_market_data_resume(db, pipeline, ["MSFT"], cutoff)


def test_unstarted_resume_still_checks_unchanged_fundamental_evidence(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    monkeypatch.setattr(
        pipeline_executor, "_require_step", lambda *a: SimpleNamespace(status="COMPLETED"),
    )
    monkeypatch.setattr(
        pipeline_executor, "_validate_unstarted_market_data_resume", lambda *a: None,
    )

    def changed_evidence(*a, **k):
        raise ValueError("Original fundamental evidence changed")

    monkeypatch.setattr(pipeline_executor, "_require_unchanged_core_evidence", changed_evidence)
    with Session() as db:
        monkeypatch.setattr(db, "scalars", lambda *a: [SimpleNamespace(ticker="MSFT")])
        with pytest.raises(ValueError, match="Original fundamental evidence changed"):
            pipeline_executor._validate_market_data_resume(db, pipeline, ["MSFT"], cutoff)


def test_first_plan_is_bound_to_original_scope_and_session(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    pipeline_executor._validate_first_market_data_plan(_plan(), pipeline, ["MSFT"], cutoff)


@pytest.mark.parametrize("changed", [
    {"run_id": 54},
    {"requested_tickers": ["MSFT", "UNADMITTED"]},
    {"requested_tickers": []},
])
def test_first_plan_cannot_change_upload_or_admitted_ticker_scope(monkeypatch, changed):
    pipeline, cutoff = _case(monkeypatch)
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_PLAN_SCOPE_CHANGED"):
        pipeline_executor._validate_first_market_data_plan(
            replace(_plan(), **changed), pipeline, ["MSFT"], cutoff,
        )


@pytest.mark.parametrize("item_ticker,symbols", [
    (None, ["MSFT"]),
    ("SPY", ["MSFT", "SPY"]),
    ("FOREIGN", ["MSFT"]),
])
def test_first_plan_requires_requested_items_and_rejects_foreign_items(
    monkeypatch, item_ticker, symbols,
):
    pipeline, cutoff = _case(monkeypatch)
    plan = _plan()
    plan = replace(
        plan, symbols_including_benchmarks=symbols,
        items=[] if item_ticker is None else [replace(plan.items[0], ticker=item_ticker)],
    )
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_PLAN_SCOPE_CHANGED"):
        pipeline_executor._validate_first_market_data_plan(plan, pipeline, ["MSFT"], cutoff)


@pytest.mark.parametrize("changed", [
    {"freshness_threshold_date": SESSION + timedelta(days=1)},
    {"request_end_date": SESSION + timedelta(days=1)},
    {"request_end_date": None},
    {"reviewed_session_expiry": SESSION + timedelta(days=1)},
    {"request_end_mode": "CURRENT", "reviewed_session_expiry": None},
    {"duration": None, "post_resolution_duration": "3 Y", "request_end_date": None},
])
def test_first_plan_cannot_substitute_current_or_unbounded_request_dates(monkeypatch, changed):
    pipeline, cutoff = _case(monkeypatch)
    plan = _plan()
    plan = replace(plan, items=[replace(plan.items[0], **changed)])
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_PLAN_SESSION_CHANGED"):
        pipeline_executor._validate_first_market_data_plan(plan, pipeline, ["MSFT"], cutoff)


def test_first_plan_rechecks_current_session_before_admission(monkeypatch):
    pipeline, cutoff = _case(monkeypatch)
    monkeypatch.setattr(
        pipeline_executor, "latest_completed_us_trading_day", lambda: SESSION + timedelta(days=1),
    )
    with pytest.raises(ValueError, match="MARKET_DATA_RESUME_SESSION_CHANGED_REPLAN_REQUIRED"):
        pipeline_executor._validate_first_market_data_plan(_plan(), pipeline, ["MSFT"], cutoff)
