from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.services.ceri.sec.coverage import (
    build_sec_coverage,
    load_sec_coverage,
    sec_coverage_blocking_tickers,
    sec_coverage_can_continue,
    sec_coverage_current_gaps,
    sec_coverage_ticker_scope,
    validate_sec_coverage,
)
from app.services.ceri.sec.readiness_diagnostics import (
    SecTickerReadiness,
    SecTickerReadinessCategory,
    SecUniverseReadiness,
)

SIGNATURE = "sec-guidance:coverage-test"
ATTEMPTED_AT = datetime(2026, 11, 9, 22, 5, 50, tzinfo=UTC)


def _pipeline():
    return SimpleNamespace(
        id=56,
        scope_id="scope-164",
        refresh_cycle_id="cycle-55",
        acquisition_plan_id="plan-55",
        result_json={},
    )


def _readiness(
    category=SecTickerReadinessCategory.UNRESOLVED_MAPPING, *, ciks=()
):
    return SecUniverseReadiness(
        SIGNATURE,
        (
            SecTickerReadiness("GOOD", SecTickerReadinessCategory.READY, ciks=("0000000001",)),
            SecTickerReadiness("NOSEC", category, ciks=ciks, reason="No company mapping"),
            SecTickerReadiness("TOWN", SecTickerReadinessCategory.SEC_NOT_APPLICABLE),
        ),
    )


def _coverage(pipeline=None, readiness=None, *, status="UNRESOLVED"):
    return build_sec_coverage(
        pipeline=pipeline or _pipeline(),
        readiness=readiness or _readiness(),
        identity_results={
            "NOSEC": SimpleNamespace(
                status=status,
                cik=None,
                reason="SEC company-ticker metadata has no exact ticker match.",
            )
        },
        attempted_at=ATTEMPTED_AT,
    )


def test_retained_no_match_allows_continuation_without_claiming_ready():
    readiness = _readiness()
    coverage = _coverage(readiness=readiness)

    assert readiness.complete is False
    assert readiness.ready_tickers == 2  # Existing readiness includes the reviewed exemption.
    assert readiness.blocking_tickers == ("NOSEC",)
    assert coverage["ready_tickers"] == 1
    assert coverage["exempt_tickers"] == 1
    assert coverage["unresolved_tickers"] == 1
    assert sec_coverage_can_continue(readiness, coverage)
    assert sec_coverage_ticker_scope(readiness, coverage) == ("GOOD",)
    gap = coverage["gaps"][0]
    assert gap["reason_code"] == "SEC_IDENTITY_UNRESOLVED"
    assert gap["resolution_status"] == "UNRESOLVED"
    assert gap["source"] == "SEC/company_tickers"
    assert gap["attempted_at"] == ATTEMPTED_AT.isoformat()


def test_unattempted_missing_identity_still_requires_bounded_repair():
    readiness = _readiness()

    assert not sec_coverage_can_continue(readiness, None)
    assert sec_coverage_blocking_tickers(readiness, None) == ("NOSEC",)
    assert sec_coverage_ticker_scope(readiness, None) == ("GOOD", "NOSEC")


def test_ambiguous_identity_is_not_recorded_as_terminal_no_match():
    readiness = _readiness()
    coverage = _coverage(readiness=readiness, status="AMBIGUOUS")

    assert coverage["gaps"] == []
    assert not sec_coverage_can_continue(readiness, coverage)


@pytest.mark.parametrize(
    "category",
    [
        SecTickerReadinessCategory.SYNC_STATE_MISSING,
        SecTickerReadinessCategory.SIGNATURE_MISMATCH,
        SecTickerReadinessCategory.INVALID_TICKER,
        SecTickerReadinessCategory.OTHER_BLOCKING_REASON,
    ],
)
def test_other_readiness_failures_cannot_be_waived_by_an_identity_gap(category):
    retained = _coverage()
    readiness = _readiness(category)

    assert sec_coverage_blocking_tickers(readiness, retained) == ("NOSEC",)
    assert not sec_coverage_can_continue(readiness, retained)
    assert sec_coverage_current_gaps(readiness, retained) == ()
    assert sec_coverage_ticker_scope(readiness, retained) == ("GOOD", "NOSEC")
    assert _coverage(readiness=readiness)["gaps"] == []


def test_mapping_created_by_another_provider_does_not_repeat_identity_lookup():
    retained = _coverage()
    current = _readiness(SecTickerReadinessCategory.CIK_MISSING)

    assert sec_coverage_can_continue(current, retained)
    assert sec_coverage_ticker_scope(current, retained) == ("GOOD",)
    assert sec_coverage_current_gaps(current, retained)[0]["category"] == "UNRESOLVED_MAPPING"


def test_a_new_cik_requires_its_own_bootstrap_without_using_old_gap():
    retained = _coverage()
    current = _readiness(
        SecTickerReadinessCategory.SYNC_STATE_MISSING, ciks=("0000000002",)
    )

    assert not sec_coverage_can_continue(current, retained)
    assert sec_coverage_current_gaps(current, retained) == ()
    assert sec_coverage_ticker_scope(current, retained) == ("GOOD", "NOSEC")


@pytest.mark.parametrize(
    "category",
    [SecTickerReadinessCategory.READY, SecTickerReadinessCategory.SEC_NOT_APPLICABLE],
)
def test_resolved_or_reviewed_source_drops_current_gap_without_erasing_history(category):
    retained = _coverage()
    original = deepcopy(retained)
    current = _readiness(category)

    assert sec_coverage_can_continue(current, retained)
    assert sec_coverage_current_gaps(current, retained) == ()
    expected = ("GOOD", "NOSEC") if category is SecTickerReadinessCategory.READY else ("GOOD",)
    assert sec_coverage_ticker_scope(current, retained) == expected
    assert retained == original


def test_a_claimed_missing_category_with_a_known_cik_cannot_use_retained_gap():
    retained = _coverage()
    current = _readiness(ciks=("0000000002",))

    assert not sec_coverage_can_continue(current, retained)
    assert _coverage(readiness=current)["gaps"] == []


def test_valid_child_or_parent_record_loads_without_mutable_aliasing():
    pipeline = _pipeline()
    coverage = _coverage(pipeline)
    pipeline.result_json["sec_coverage"] = coverage

    loaded = load_sec_coverage(
        pipeline, processor_signature=SIGNATURE, tickers=["TOWN", "NOSEC", "GOOD"]
    )
    assert loaded == coverage
    loaded["gaps"][0]["reason"] = "changed copy"
    assert pipeline.result_json["sec_coverage"]["gaps"][0]["reason"] != "changed copy"
    assert validate_sec_coverage(
        coverage, pipeline=pipeline, processor_signature=SIGNATURE
    ) == coverage


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("id", 57),
        ("scope_id", "another-scope"),
        ("refresh_cycle_id", "another-cycle"),
        ("acquisition_plan_id", "another-plan"),
    ],
)
def test_reuse_under_another_execution_owner_is_rejected(field, changed):
    pipeline = _pipeline()
    pipeline.result_json["sec_coverage"] = _coverage(pipeline)
    setattr(pipeline, field, changed)

    assert load_sec_coverage(pipeline, processor_signature=SIGNATURE) is None


def test_signature_and_exact_requested_universe_are_bound_to_the_attempt():
    pipeline = _pipeline()
    coverage = _coverage(pipeline)
    pipeline.result_json["sec_coverage"] = coverage

    assert load_sec_coverage(pipeline, processor_signature="new-processor") is None
    assert load_sec_coverage(pipeline, processor_signature=SIGNATURE, tickers=["GOOD"]) is None
    changed = SecUniverseReadiness("new-processor", _readiness().tickers)
    assert not sec_coverage_can_continue(changed, coverage)
    subset = SecUniverseReadiness(SIGNATURE, _readiness().tickers[1:])
    assert not sec_coverage_can_continue(subset, coverage)


@pytest.mark.parametrize("mutation", ["reason", "source", "status", "category", "count"])
def test_tampered_disposition_cannot_grant_continuation(mutation):
    pipeline = _pipeline()
    coverage = _coverage(pipeline)
    if mutation == "count":
        coverage["unresolved_tickers"] = 0
    else:
        key = {"status": "resolution_status"}.get(mutation, mutation)
        coverage["gaps"][0][key] = "tampered"
    pipeline.result_json["sec_coverage"] = coverage

    assert load_sec_coverage(pipeline, processor_signature=SIGNATURE) is None
    assert not sec_coverage_can_continue(_readiness(), coverage)


def test_missing_record_and_missing_owner_never_grant_an_exemption():
    assert load_sec_coverage(None, processor_signature=SIGNATURE) is None
    assert load_sec_coverage(_pipeline(), processor_signature=SIGNATURE) is None
    assert validate_sec_coverage(_coverage(), pipeline=None, processor_signature=SIGNATURE) is None


def test_naive_attempt_time_is_not_valid_retained_provenance():
    with pytest.raises(ValueError, match="AWARE_TIMESTAMP"):
        build_sec_coverage(
            pipeline=_pipeline(),
            readiness=_readiness(),
            identity_results={},
            attempted_at=datetime(2026, 11, 9),
        )


def test_retry_preserves_original_lookup_time_in_new_coverage_snapshot():
    pipeline = _pipeline()
    original_time = datetime(2026, 11, 9, 22, 0, tzinfo=UTC)
    identity_result = {
        "status": "UNRESOLVED",
        "cik": None,
        "reason": "No exact ticker match",
        "attempted_at": original_time.isoformat(),
    }
    coverage = build_sec_coverage(
        pipeline=pipeline,
        readiness=_readiness(),
        identity_results={"NOSEC": identity_result},
        attempted_at=ATTEMPTED_AT,
    )

    assert coverage["gaps"][0]["attempted_at"] == original_time.isoformat()
    assert coverage["recorded_at"] == ATTEMPTED_AT.isoformat()
    assert validate_sec_coverage(
        coverage, pipeline=pipeline, processor_signature=SIGNATURE
    ) == coverage


def test_a_lookup_cannot_be_recorded_before_it_was_attempted():
    with pytest.raises(ValueError, match="ATTEMPT_AFTER_RECORDING"):
        build_sec_coverage(
            pipeline=_pipeline(),
            readiness=_readiness(),
            identity_results={
                "NOSEC": {
                    "status": "UNRESOLVED",
                    "reason": "No exact ticker match",
                    "attempted_at": datetime(2026, 11, 10, tzinfo=UTC).isoformat(),
                }
            },
            attempted_at=ATTEMPTED_AT,
        )
