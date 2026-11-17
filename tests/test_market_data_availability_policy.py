from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services.market_data_availability_policy import (
    CONFIGURATION_NAMESPACE,
    MarketDataAvailabilityMode,
    availability_diagnostics,
    hard_readiness_blockers,
    known_instrument_unavailability,
    new_availability_policy,
    parse_availability_policy,
    pipeline_availability_policy,
    processing_tickers,
)
from app.services.market_data_session_readiness import classify_session_readiness
from app.services.pipeline_outcome import derive_pipeline_outcome
from app.services.technical_score_service import unavailable_source_technical_score


def unavailable(ticker="CLBK", category="HISTORICAL_DATA_UNAVAILABLE", **extra):
    return SimpleNamespace(
        ticker=ticker,
        disposition="REQUIRED_DATA_UNAVAILABLE",
        downstream_eligible=False,
        reason_code="EXPECTED_SESSION_UNAVAILABLE",
        expected_session=date(2026, 11, 9),
        latest_bar_session=None,
        evidence_json={
            "fetch_failures": [{"provider_error_category": category, "retryable": False, **extra}]
        },
    )


def test_absent_legacy_marker_is_strict_despite_new_admission_default(monkeypatch):
    monkeypatch.setattr(
        "app.services.configuration_delivery.current_delivery",
        lambda: SimpleNamespace(configurations={}),
    )
    assert new_availability_policy().allow_history_gaps is True
    policy = pipeline_availability_policy(SimpleNamespace(result_json={}))
    assert policy.mode == MarketDataAvailabilityMode.STRICT_V1
    assert hard_readiness_blockers((unavailable(),), policy)


def test_new_policy_matches_exact_frozen_configuration(monkeypatch):
    policy = new_availability_policy()
    monkeypatch.setattr(
        "app.services.configuration_delivery.current_delivery",
        lambda: SimpleNamespace(
            configurations={CONFIGURATION_NAMESPACE: SimpleNamespace(values=policy.as_dict())}
        ),
    )
    owner = SimpleNamespace(result_json={"market_data_availability_policy": policy.as_dict()})
    assert pipeline_availability_policy(owner) == policy
    owner.result_json["market_data_availability_policy"] = parse_availability_policy().as_dict()
    with pytest.raises(ValueError, match="CONFIGURATION_MISMATCH"):
        pipeline_availability_policy(owner)


def test_new_marker_cannot_execute_under_unrelated_legacy_anchor(monkeypatch):
    monkeypatch.setattr(
        "app.services.configuration_delivery.current_delivery",
        lambda: SimpleNamespace(configurations={}),
    )
    owner = SimpleNamespace(
        result_json={
            "market_data_availability_policy": new_availability_policy().as_dict(),
        }
    )
    with pytest.raises(ValueError, match="MISSING_FROZEN"):
        pipeline_availability_policy(owner)


def test_certification_remains_strict_and_rejects_partial_policy():
    assert new_availability_policy(certification=True).allow_history_gaps is False
    with pytest.raises(ValueError, match="CERTIFICATION_REQUIRES_STRICT"):
        new_availability_policy(
            MarketDataAvailabilityMode.CONTINUE_KNOWN_DATA_GAPS_V1, certification=True
        )


@pytest.mark.parametrize(
    "marker",
    [
        {},
        {"contract_version": "future", "mode": "STRICT_V1"},
        {"contract_version": "market-data-availability-policy-v1", "mode": "UNKNOWN"},
    ],
)
def test_unknown_policy_fails_closed(marker):
    with pytest.raises(ValueError, match="POLICY_INVALID"):
        parse_availability_policy(marker)


def test_known_no_data_keeps_requested_member_for_null_diagnostic_output():
    ready = SimpleNamespace(
        ticker="LIVE", disposition="READY", downstream_eligible=True, evidence_json={}
    )
    rows = (ready, unavailable())
    policy = new_availability_policy()
    assert hard_readiness_blockers(rows, policy) == ()
    assert processing_tickers(rows, policy) == ("LIVE", "CLBK")
    assert availability_diagnostics(rows, policy)["unavailable_tickers"] == ["CLBK"]


@pytest.mark.parametrize(
    "category,extra",
    [
        ("CONTRACT_NOT_FOUND", {}),
        ("APPLICATION_ERROR", {}),
        ("HISTORICAL_DATA_UNAVAILABLE", {"dependency_roles": ["BENCHMARK"]}),
    ],
)
def test_integrity_unknown_and_benchmark_failures_are_not_optional(category, extra):
    row = unavailable(category=category, **extra)
    assert known_instrument_unavailability(row) is False
    assert hard_readiness_blockers((row,), new_availability_policy()) == (row,)


@pytest.mark.parametrize(
    "metadata",
    [
        {"provider_error_category": "CONTRACT_NOT_FOUND"},
        {"failure_classification": "HISTORICAL_BAR_ADMISSION_REJECTED", "boundary_status": "FAIL"},
        {
            "provider_error_category": "HISTORICAL_DATA_UNAVAILABLE",
            "dependency_roles": ["BENCHMARK"],
        },
    ],
)
def test_cached_current_bar_cannot_bypass_new_policy_integrity_guard(metadata):
    failure = SimpleNamespace(
        id=1,
        what_to_show="TRADES",
        status="FAILED",
        error_message="rejected",
        decision_metadata_json=metadata,
    )
    arguments = dict(
        ticker="LIVE",
        expected_session=date(2026, 11, 9),
        latest_bar_session=date(2026, 11, 9),
        lifecycle=None,
        fetch_items=(failure,),
    )
    assert classify_session_readiness(**arguments)["disposition"] == "READY"
    actual = classify_session_readiness(**arguments, enforce_response_integrity=True)
    assert actual["disposition"] == "UNKNOWN"
    assert actual["reason_code"] == "MARKET_DATA_RESPONSE_INTEGRITY_BLOCKED"


def test_unavailable_source_score_has_no_numeric_technical_and_no_runtime_error():
    score = unavailable_source_technical_score(7, "CLBK", unavailable())
    assert score.dual_score is None
    assert score.insufficient_data is True
    assert score.technical_confidence == "low"
    assert score.missing_data_json["market_data_unavailable"] is True
    assert "technical_error" not in score.warning_flags_json
    assert score.warning_flags_json == ["insufficient_history"]
    assert "error" not in score.debug_json


def test_outcome_keeps_unavailable_and_history_gaps_separate_from_scope_exclusions():
    outcome = derive_pipeline_outcome(
        "PARTIAL",
        {
            "uploaded_rows": 466,
            "combined_results": 466,
            "market_data_unavailable_count": 1,
            "market_data_history_gap_count": 1,
            "market_data_availability": {
                "unavailable_tickers": ["CLBK"],
                "history_gap_tickers": ["HLT"],
            },
        },
    )
    assert outcome["execution_outcome"] == "SUCCEEDED_WITH_GAPS"
    assert outcome["coverage"]["expected_rows"] == outcome["coverage"]["combined_rows"] == 466
    assert outcome["coverage"]["excluded_tickers"] == []
    assert outcome["coverage"]["unavailable_tickers"] == ["CLBK"]
    assert outcome["coverage"]["history_gap_tickers"] == ["HLT"]


def _empty_request():
    from app.models.tables import IBFetchItem
    from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
    from app.services.ib_fetch_plan_service import (
        FetchAction,
        FetchPlan,
        FetchPlanItem,
        fetch_plan_to_dict,
    )
    from app.services.ib_historical_request_scope import (
        DAILY_BAR_ADMISSION_V4,
        build_historical_request_scope,
    )

    identity = {"conId": 1, "symbol": "VOID", "secType": "STK", "currency": "USD"}
    scope = build_historical_request_scope(
        required_start_date=None,
        required_end_date=None,
        duration="5 D",
        bar_size="1 day",
        what_to_show="TRADES",
        reviewed_end_date=date(2026, 11, 9),
        response_admission_policy=DAILY_BAR_ADMISSION_V4,
    )
    pinned = FetchPlanItem(
        ticker="VOID",
        contract_status="RESOLVED",
        what_to_show="TRADES",
        action=FetchAction.FULL_BACKFILL,
        duration="5 D",
        bar_size="1 day",
        current_bar_count=0,
        first_bar_date=None,
        latest_bar_date=None,
        required_bars=252,
        reason="missing",
        estimated_request_count=1,
        contract_identity=identity,
        contract_fingerprint=Canonical.fingerprint(identity),
        request_end_date=date(2026, 11, 9),
        response_admission_policy=DAILY_BAR_ADMISSION_V4,
    )
    plan = FetchPlan(7, ["VOID"], ["VOID"], [pinned], 1, 1, 0, 0, 0, [])
    pipeline = SimpleNamespace(
        upload_run_id=7, result_json={"ib_fetch_plan": fetch_plan_to_dict(plan)}
    )
    item = IBFetchItem(
        ticker="VOID",
        what_to_show="TRADES",
        status="FAILED",
        duration="5 D",
        bar_size="1 day",
        attempt_count=1,
        started_at=datetime(2026, 11, 10, 1, tzinfo=UTC),
        completed_at=datetime(2026, 11, 10, 1, 1, tzinfo=UTC),
        fetched=0,
        decision_metadata_json={
            "provider_result": "PROVIDER_NO_DATA",
            "boundary_status": "PASS",
            "received_bar_count": 0,
            "response_admission_policy": DAILY_BAR_ADMISSION_V4,
            "runtime_contract_identity": identity,
            "reviewed_start_date": scope.reviewed_start_date.isoformat(),
            "reviewed_end_date": "2026-11-09",
            "request_end_datetime": scope.end_datetime,
            "request_end_mode": scope.end_mode.value,
        },
    )
    return pipeline, item


def test_empty_response_requires_exact_pinned_executed_scope_and_contract():
    from app.services.market_data_session_readiness import _pinned_empty_response

    pipeline, item = _empty_request()
    assert _pinned_empty_response(pipeline, item, date(2026, 11, 9)) is True
    assert _pinned_empty_response(pipeline, item, date(2026, 11, 10)) is False
    item.decision_metadata_json["runtime_contract_identity"] = {
        **item.decision_metadata_json["runtime_contract_identity"],
        "conId": 2,
    }
    assert _pinned_empty_response(pipeline, item, date(2026, 11, 9)) is False


@pytest.mark.parametrize("change", ["unexecuted", "nonempty", "unreviewed"])
def test_failed_metadata_alone_is_not_verified_empty_availability(change):
    from app.services.market_data_session_readiness import _pinned_empty_response

    pipeline, item = _empty_request()
    if change == "unexecuted":
        item.attempt_count = 0
    elif change == "nonempty":
        item.decision_metadata_json["received_bar_count"] = 1
    else:
        pipeline.result_json["ib_fetch_plan"]["items"] = []
    assert _pinned_empty_response(pipeline, item, date(2026, 11, 9)) is False


def test_handoff_keeps_known_unavailable_requested_member_under_new_policy(monkeypatch):
    from app.services.market_data_session_readiness import SessionReadinessResult
    from app.services.transition_preflight_plan_service import _pipeline_downstream_tickers

    owner = SimpleNamespace(
        result_json={
            "market_data_availability_policy": new_availability_policy().as_dict(),
        }
    )
    monkeypatch.setattr(
        "app.services.market_data_session_readiness.load_current_session_readiness",
        lambda *_a, **_k: SessionReadinessResult((unavailable(),)),
    )
    assert _pipeline_downstream_tickers(
        SimpleNamespace(get=lambda *_a: owner), pipeline_run_id=58
    ) == {"CLBK"}


def test_bootstrap_and_required_dependency_failures_remain_hard():
    from app.services.market_data_availability_policy import fetch_execution_hazards

    plan = SimpleNamespace(items=[])
    run = SimpleNamespace(
        status="FAILED", planned_request_count=2, executed_request_count=0, decision_counts_json={}
    )
    assert "FETCH_BOOTSTRAP_NOT_EXECUTED" in fetch_execution_hazards(run, plan, [])
    run.executed_request_count = 2
    run.decision_counts_json = {"execution_failure": {"classification": "APPLICATION_ERROR"}}
    assert "FETCH_EXECUTION_FAILURE" in fetch_execution_hazards(run, plan, [])
    run.decision_counts_json = {}
    item = SimpleNamespace(ticker="CLBK", what_to_show="TRADES", status="FAILED")
    assert fetch_execution_hazards(run, plan, [item]) == ()
    expected = SimpleNamespace(
        ticker="SPY",
        what_to_show="TRADES",
        action="TOP_UP_RECENT",
        dependency_roles=("REQUESTED", "BENCHMARK"),
        data_role="REQUESTED",
    )
    actual = SimpleNamespace(ticker="SPY", what_to_show="TRADES", status="PARTIAL")
    assert fetch_execution_hazards(run, SimpleNamespace(items=[expected]), [actual]) == (
        "REQUIRED_DEPENDENCY_FAILED:SPY:TRADES",
    )


def test_integrity_flag_cannot_be_softened_by_provider_no_data():
    row = unavailable()
    row.evidence_json["integrity_failure"] = True
    assert known_instrument_unavailability(row) is False


def test_unverified_empty_response_cannot_hide_behind_current_cache():
    failure = SimpleNamespace(
        id=1,
        what_to_show="TRADES",
        status="FAILED",
        error_message="no data",
        decision_metadata_json={"provider_result": "PROVIDER_NO_DATA", "boundary_status": "PASS"},
    )
    arguments = dict(
        ticker="VOID",
        expected_session=date(2026, 11, 9),
        latest_bar_session=date(2026, 11, 9),
        lifecycle=None,
        fetch_items=(failure,),
        enforce_response_integrity=True,
    )
    assert classify_session_readiness(**arguments)["disposition"] == "UNKNOWN"
    assert (
        classify_session_readiness(**arguments, verified_empty_item_ids=(1,))["disposition"]
        == "READY"
    )
