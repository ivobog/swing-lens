from datetime import date
from types import SimpleNamespace

import pytest

from app.models.tables import InstrumentLifecycleRecord
from app.services.instrument_lifecycle_service import record_instrument_lifecycle
from app.services.market_data_session_readiness import (
    MarketDataDisposition,
    SessionReadinessResult,
    classify_session_readiness,
)

SESSION = date(2026, 10, 6)
OLDER = date(2026, 10, 5)


def test_current_bar_is_ready_without_assuming_lifecycle_from_provider() -> None:
    result = classify_session_readiness(
        ticker="LIVE", expected_session=SESSION, latest_bar_session=SESSION, lifecycle=None
    )

    assert result["disposition"] == MarketDataDisposition.READY
    assert result["technical_eligible"] is True
    assert result["downstream_eligible"] is True
    assert result["lifecycle_state"] == "UNKNOWN"


def test_contract_not_found_without_lifecycle_evidence_blocks_as_unknown() -> None:
    failure = SimpleNamespace(
        id=2513,
        what_to_show="ADJUSTED_LAST",
        status="FAILED",
        error_message="IB error 200",
        decision_metadata_json={
            "provider_error_code": 200,
            "provider_error_category": "CONTRACT_NOT_FOUND",
            "retryable": False,
        },
    )

    result = classify_session_readiness(
        ticker="BLFS",
        expected_session=SESSION,
        latest_bar_session=OLDER,
        lifecycle=None,
        fetch_items=(failure,),
    )

    assert result["disposition"] == MarketDataDisposition.UNKNOWN
    assert result["reason_code"] == "INSTRUMENT_STATE_UNRESOLVED"
    assert result["technical_eligible"] is False
    assert result["downstream_eligible"] is False


def test_explicit_merger_evidence_allows_auditable_terminal_exclusion() -> None:
    lifecycle = InstrumentLifecycleRecord(
        id=9,
        ticker="BLFS",
        lifecycle_state="MERGED",
        effective_date=SESSION,
        last_trading_date=OLDER,
        successor_ticker="SNY",
        reason="Merger closed",
        evidence_json={"source": "issuer"},
        revision=1,
        is_current_revision=True,
    )

    result = classify_session_readiness(
        ticker="BLFS",
        expected_session=SESSION,
        latest_bar_session=OLDER,
        lifecycle=lifecycle,
    )

    assert result["disposition"] == MarketDataDisposition.TERMINAL_INACTIVE
    assert result["reason_code"] == "TERMINAL_INSTRUMENT_EXCLUDED"
    assert result["technical_eligible"] is False
    assert result["downstream_eligible"] is False
    assert result["evidence_json"]["successor_ticker"] == "SNY"


def test_terminal_ticker_is_absent_from_technical_and_downstream_populations() -> None:
    ready = SimpleNamespace(
        ticker="LIVE",
        disposition="READY",
        technical_eligible=True,
        downstream_eligible=True,
    )
    terminal = SimpleNamespace(
        ticker="BLFS",
        disposition="TERMINAL_INACTIVE",
        technical_eligible=False,
        downstream_eligible=False,
    )
    readiness = SessionReadinessResult(rows=(ready, terminal))

    assert readiness.technical_tickers == ("LIVE",)
    assert readiness.downstream_tickers == ("LIVE",)
    assert readiness.accepted_inactive_tickers == ("BLFS",)
    assert readiness.blockers == ()


def test_stale_active_instrument_is_required_data_unavailable() -> None:
    lifecycle = InstrumentLifecycleRecord(
        ticker="LIVE",
        lifecycle_state="ACTIVE",
        effective_date=OLDER,
        evidence_json={},
        revision=1,
        is_current_revision=True,
    )

    result = classify_session_readiness(
        ticker="LIVE",
        expected_session=SESSION,
        latest_bar_session=OLDER,
        lifecycle=lifecycle,
    )

    assert result["disposition"] == MarketDataDisposition.REQUIRED_DATA_UNAVAILABLE
    assert result["technical_eligible"] is False


def test_failed_cached_contract_without_fetch_item_is_unknown_not_excluded() -> None:
    result = classify_session_readiness(
        ticker="BLFS",
        expected_session=SESSION,
        latest_bar_session=OLDER,
        lifecycle=None,
        contract_resolution_status="FAILED",
    )

    assert result["disposition"] == MarketDataDisposition.UNKNOWN
    assert result["reason_code"] == "INSTRUMENT_STATE_UNRESOLVED"


def test_retryable_failure_has_distinct_transient_disposition() -> None:
    failure = SimpleNamespace(
        id=1,
        what_to_show="TRADES",
        status="FAILED",
        error_message="timeout",
        decision_metadata_json={"provider_error_category": "TIMEOUT", "retryable": True},
    )

    result = classify_session_readiness(
        ticker="LIVE",
        expected_session=SESSION,
        latest_bar_session=OLDER,
        lifecycle=None,
        fetch_items=(failure,),
    )

    assert result["disposition"] == MarketDataDisposition.TRANSIENT_FAILURE
    assert result["reason_code"] == "TRANSIENT_MARKET_DATA_FAILURE"


def test_terminal_lifecycle_record_requires_evidence_and_effective_boundary() -> None:
    with pytest.raises(ValueError, match="EVIDENCE_REQUIRED"):
        record_instrument_lifecycle(
            object(),
            ticker="BLFS",
            lifecycle_state="MERGED",
            evidence_json={},
            effective_date=SESSION,
        )

    with pytest.raises(ValueError, match="EFFECTIVE_BOUNDARY_REQUIRED"):
        record_instrument_lifecycle(
            object(),
            ticker="BLFS",
            lifecycle_state="MERGED",
            evidence_json={"source": "issuer"},
        )
