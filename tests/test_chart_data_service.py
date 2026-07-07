from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from app.models.tables import TechnicalScore
from app.services import chart_data_service
from app.services.chart_data_service import (
    EMPTY_CHART_MESSAGE,
    build_ticker_chart_payload,
    calculate_sma_points,
)


def test_calculate_sma_points_returns_points_after_window_is_full() -> None:
    bars = [
        {"time": date(2026, 1, 1) + timedelta(days=index), "close": float(index + 1)}
        for index in range(5)
    ]

    points = calculate_sma_points(bars, 3)

    assert points == [
        {"time": "2026-01-03", "value": 2.0},
        {"time": "2026-01-04", "value": 3.0},
        {"time": "2026-01-05", "value": 4.0},
    ]


def test_build_ticker_chart_payload_maps_bars_volume_sma_and_levels(monkeypatch) -> None:
    price = pd.DataFrame(
        [
            _bar(date(2026, 1, 3), 12.0, 13.0, 11.0, 12.5, 0),
            _bar(date(2026, 1, 1), 10.0, 11.0, 9.0, 10.5, 0),
            _bar(date(2026, 1, 2), 11.0, 12.0, 10.0, 11.5, 0),
        ]
    )
    trades = price.assign(volume=[3000, 1000, 2000])
    technical = TechnicalScore(
        run_id=7,
        ticker="MSFT",
        suggested_stop=Decimal("9.75"),
        suggested_target=Decimal("15.50"),
    )

    monkeypatch.setattr(
        chart_data_service,
        "load_preferred_ohlcv_frames",
        lambda db, ticker: (price, trades),
    )

    payload = build_ticker_chart_payload(_FakeDb(technical), 7, "msft")

    assert payload["ticker"] == "MSFT"
    assert payload["timeframe"] == "1D"
    assert payload["bars"] == [
        {"time": "2026-01-01", "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5},
        {"time": "2026-01-02", "open": 11.0, "high": 12.0, "low": 10.0, "close": 11.5},
        {"time": "2026-01-03", "open": 12.0, "high": 13.0, "low": 11.0, "close": 12.5},
    ]
    assert payload["volume"] == [
        {"time": "2026-01-01", "value": 1000},
        {"time": "2026-01-02", "value": 2000},
        {"time": "2026-01-03", "value": 3000},
    ]
    assert payload["overlays"] == {"sma20": [], "sma50": [], "sma200": []}
    assert payload["levels"] == {
        "stop": 9.75,
        "target": 15.5,
        "entry_reference": 12.5,
    }
    assert payload["markers"] == []
    assert payload["message"] is None


def test_build_ticker_chart_payload_returns_empty_state_when_no_price_bars(
    monkeypatch,
) -> None:
    empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    monkeypatch.setattr(
        chart_data_service,
        "load_preferred_ohlcv_frames",
        lambda db, ticker: (empty, None),
    )

    payload = build_ticker_chart_payload(_FakeDb(), 7, "nvda")

    assert payload == {
        "ticker": "NVDA",
        "timeframe": "1D",
        "bars": [],
        "volume": [],
        "overlays": {"sma20": [], "sma50": [], "sma200": []},
        "levels": {},
        "markers": [],
        "message": EMPTY_CHART_MESSAGE,
    }


def test_build_ticker_chart_payload_populates_all_sma_overlays(monkeypatch) -> None:
    frame = pd.DataFrame(
        [
            _bar(
                date(2026, 1, 1) + timedelta(days=index),
                float(index + 1),
                float(index + 2),
                float(index),
                float(index + 1),
                1_000 + index,
            )
            for index in range(205)
        ]
    )
    monkeypatch.setattr(
        chart_data_service,
        "load_preferred_ohlcv_frames",
        lambda db, ticker: (frame, frame),
    )

    payload = build_ticker_chart_payload(_FakeDb(), 7, "msft")

    assert len(payload["overlays"]["sma20"]) == 186
    assert len(payload["overlays"]["sma50"]) == 156
    assert len(payload["overlays"]["sma200"]) == 6
    assert payload["overlays"]["sma20"][-1] == {"time": "2026-07-24", "value": 195.5}
    assert payload["overlays"]["sma50"][-1] == {"time": "2026-07-24", "value": 180.5}
    assert payload["overlays"]["sma200"][-1] == {"time": "2026-07-24", "value": 105.5}


def _bar(
    bar_date: date,
    open_value: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> dict[str, object]:
    return {
        "date": bar_date,
        "open": open_value,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


class _FakeDb:
    def __init__(self, technical: TechnicalScore | None = None) -> None:
        self.technical = technical

    def scalar(self, statement):
        self.statement = statement
        return self.technical
