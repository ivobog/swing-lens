from datetime import date

from app.services.ohlcv_coverage_service import (
    BarSeriesCoverage,
    OhlcvCoverageStatus,
    _coverage_item,
    _required_rows,
)


def test_required_rows_uses_pine_defaults_shape() -> None:
    required = _required_rows(
        {
            "trend": {"smaSlowLen": 200, "highLow52Len": 252},
            "market_rs": {"rocLongLen": 126},
        }
    )

    assert required == 252


def test_coverage_item_classifies_ready_insufficient_and_missing() -> None:
    ready = _coverage_item(
        "MSFT",
        {
            ("MSFT", "ADJUSTED_LAST"): BarSeriesCoverage(
                count=252,
                latest_date=date(2026, 7, 2),
            ),
            ("MSFT", "TRADES"): BarSeriesCoverage(
                count=252,
                latest_date=date(2026, 7, 2),
            ),
        },
        required_rows=252,
        today=date(2026, 7, 3),
    )
    insufficient = _coverage_item(
        "AAPL",
        {
            ("AAPL", "ADJUSTED_LAST"): BarSeriesCoverage(
                count=100,
                latest_date=date(2026, 7, 1),
            )
        },
        required_rows=252,
        today=date(2026, 7, 3),
    )
    missing = _coverage_item("NVDA", {}, required_rows=252, today=date(2026, 7, 3))

    assert ready.status == OhlcvCoverageStatus.READY
    assert ready.has_price is True
    assert ready.has_volume is True
    assert ready.latest_bar_current is True
    assert insufficient.status == OhlcvCoverageStatus.INSUFFICIENT_HISTORY
    assert insufficient.has_price is True
    assert insufficient.has_volume is False
    assert missing.status == OhlcvCoverageStatus.MISSING
    assert missing.reason == "No adjusted or trades price bars are cached."


def test_coverage_item_classifies_missing_volume_stale_and_contract_failed() -> None:
    missing_volume = _coverage_item(
        "MSFT",
        {
            ("MSFT", "ADJUSTED_LAST"): BarSeriesCoverage(
                count=252,
                latest_date=date(2026, 7, 2),
            )
        },
        required_rows=252,
        today=date(2026, 7, 3),
    )
    stale = _coverage_item(
        "AAPL",
        {
            ("AAPL", "ADJUSTED_LAST"): BarSeriesCoverage(
                count=252,
                latest_date=date(2026, 6, 1),
            ),
            ("AAPL", "TRADES"): BarSeriesCoverage(
                count=252,
                latest_date=date(2026, 6, 1),
            ),
        },
        required_rows=252,
        stale_after_days=3,
        today=date(2026, 7, 3),
    )
    contract_failed = _coverage_item(
        "NVDA",
        {},
        required_rows=252,
        contract_failed=True,
        today=date(2026, 7, 3),
    )

    assert missing_volume.status == OhlcvCoverageStatus.MISSING_VOLUME
    assert missing_volume.has_adjusted_price is True
    assert missing_volume.has_trades_volume is False
    assert stale.status == OhlcvCoverageStatus.STALE
    assert stale.latest_bar_current is False
    assert contract_failed.status == OhlcvCoverageStatus.CONTRACT_FAILED
