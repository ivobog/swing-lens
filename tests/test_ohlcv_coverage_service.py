from app.services.ohlcv_coverage_service import _coverage_item, _required_rows


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
        {("MSFT", "ADJUSTED_LAST"): 252, ("MSFT", "TRADES"): 252},
        required_rows=252,
    )
    insufficient = _coverage_item(
        "AAPL",
        {("AAPL", "ADJUSTED_LAST"): 100},
        required_rows=252,
    )
    missing = _coverage_item("NVDA", {}, required_rows=252)

    assert ready.status == "ready"
    assert ready.has_price is True
    assert ready.has_volume is True
    assert insufficient.status == "insufficient"
    assert insufficient.has_price is True
    assert insufficient.has_volume is False
    assert missing.status == "missing"
