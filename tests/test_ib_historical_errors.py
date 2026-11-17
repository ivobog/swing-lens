import pytest

from app.services.ib_historical_errors import (
    IBHistoricalErrorCategory,
    classify_ib_historical_error,
)


def test_best_query_rejection_is_instrument_specific_and_not_retried() -> None:
    result = classify_ib_historical_error(
        162,
        "Historical Market Data Service error message:BEST queries are not supported "
        "for this contract",
    )
    assert result.category is IBHistoricalErrorCategory.PROVIDER_REJECTED
    assert result.retryable is False
    assert result.systemic is False


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


def test_200_missing_security_definition_is_permanent_for_exact_contract() -> None:
    result = classify_ib_historical_error(
        200, "No security definition has been found for the request"
    )
    assert result.category is IBHistoricalErrorCategory.CONTRACT_NOT_FOUND
    assert result.retryable is False


def test_162_no_historical_data_for_qualified_contract_is_not_replayed() -> None:
    result = classify_ib_historical_error(
        162,
        "Historical Market Data Service error message:"
        "No historical market data for 2674/STK@NASDAQBBO Last 1d",
    )
    assert result.category is IBHistoricalErrorCategory.HISTORICAL_DATA_UNAVAILABLE
    assert result.retryable is False
    assert result.systemic is False


@pytest.mark.parametrize(
    "message",
    [
        "Historical Market Data Service error message:HMDS query returned no data: HLT@SMART",
        "HMDS query returned no data",
        " hMdS   query RETURNED no   DATA: CLBK/STK ",
    ],
)
def test_exact_hmds_empty_query_is_instrument_specific_unavailable(message):
    result = classify_ib_historical_error(162, message)
    assert result.category is IBHistoricalErrorCategory.HISTORICAL_DATA_UNAVAILABLE
    assert result.retryable is False and result.systemic is False


@pytest.mark.parametrize(
    "message,category",
    [
        (
            "HMDS query returned no data: historical data request pacing violation",
            IBHistoricalErrorCategory.HISTORICAL_PACING,
        ),
        (
            "HMDS query returned no data: HMDS data farm connection is broken",
            IBHistoricalErrorCategory.HISTORICAL_TRANSIENT,
        ),
        (
            "HMDS query returned an unexpected empty response",
            IBHistoricalErrorCategory.HISTORICAL_UNKNOWN_162,
        ),
        (
            "HMDS query returned no data because feed is still loading",
            IBHistoricalErrorCategory.HISTORICAL_UNKNOWN_162,
        ),
    ],
)
def test_empty_wording_does_not_hide_retryable_or_unknown_162(message, category):
    result = classify_ib_historical_error(162, message)
    assert result.category is category
    assert result.retryable is True


def test_hmds_empty_phrase_requires_162_code():
    result = classify_ib_historical_error(200, "HMDS query returned no data")
    assert result.category is IBHistoricalErrorCategory.PROVIDER_ERROR
    assert result.retryable is True
