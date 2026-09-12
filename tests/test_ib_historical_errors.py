from app.services.ib_historical_errors import (
    IBHistoricalErrorCategory,
    classify_ib_historical_error,
)


def test_eodchart_unavailable_162_is_non_retryable() -> None:
    result = classify_ib_historical_error(
        162,
        "No DATA of type   EODChart is available for the exchange 'BEST'",
    )

    assert result.category is IBHistoricalErrorCategory.HISTORICAL_DATA_UNAVAILABLE
    assert result.retryable is False
    assert result.systemic is True


def test_pacing_162_remains_retryable() -> None:
    result = classify_ib_historical_error(162, "Historical data request pacing violation")

    assert result.category is IBHistoricalErrorCategory.HISTORICAL_PACING
    assert result.retryable is True


def test_transient_162_remains_bounded_retryable() -> None:
    result = classify_ib_historical_error(
        162,
        "Historical market data service is currently unavailable",
    )

    assert result.category is IBHistoricalErrorCategory.HISTORICAL_TRANSIENT
    assert result.retryable is True


def test_unknown_162_uses_conservative_bounded_retry() -> None:
    result = classify_ib_historical_error(162, "Some new provider wording")

    assert result.category is IBHistoricalErrorCategory.HISTORICAL_UNKNOWN_162
    assert result.retryable is True


def test_321_retains_non_retryable_provider_rejection() -> None:
    result = classify_ib_historical_error(321, "Error validating request")

    assert result.category is IBHistoricalErrorCategory.PROVIDER_REJECTED
    assert result.retryable is False
