from datetime import date

import pandas as pd

from app.services.market_regime import (
    REGIME_BULL_PULLBACK,
    REGIME_BULL_TREND,
    REGIME_UNKNOWN,
    MarketRegimeResult,
)
from app.services.market_regime_command_center import (
    MarketRegimeCommandCenterService,
)


def test_build_snapshot_missing_spy_creates_unknown_low_confidence(monkeypatch) -> None:
    _patch_market_data(monkeypatch, {"SPY": None, "QQQ": _features()})
    repo = FakeRepository()

    dto = MarketRegimeCommandCenterService(repository=repo).build_snapshot(
        FakeDb(),
        run_id=7,
        today=date(2026, 7, 28),
    )

    assert dto.regime == REGIME_UNKNOWN
    assert dto.risk_state == "Gray"
    assert dto.confidence == "low"
    assert dto.gate_ok is False
    assert "missing_spy_market_data" in dto.reasons
    assert "missing_spy_market_data" in dto.warnings
    assert repo.calls[0][0] == 7
    assert repo.calls[0][1].regime == REGIME_UNKNOWN


def test_build_snapshot_missing_qqq_lowers_confidence_when_enabled(monkeypatch) -> None:
    _patch_market_data(
        monkeypatch,
        {
            "SPY": _features(close=105, sma50=110, sma200=100, roc21=-2, roc63=5),
            "QQQ": None,
        },
    )
    repo = FakeRepository()

    dto = MarketRegimeCommandCenterService(repository=repo).build_snapshot(
        FakeDb(),
        today=date(2026, 7, 28),
    )

    assert dto.regime == REGIME_BULL_PULLBACK
    assert dto.confidence == "low"
    assert "missing_qqq_market_data" in dto.reasons
    assert "low_market_confidence" in dto.warnings
    assert "Confidence is low" in dto.action_summary


def test_build_snapshot_passes_spy_and_qqq_features_to_classifier(monkeypatch) -> None:
    captured = {}
    spy_features = _features(close=120)
    qqq_features = _features(close=130)
    _patch_market_data(monkeypatch, {"SPY": spy_features, "QQQ": qqq_features})

    def fake_classify(spy, qqq, params):
        captured["spy"] = spy
        captured["qqq"] = qqq
        captured["params"] = params
        return MarketRegimeResult(
            REGIME_BULL_TREND,
            9.0,
            False,
            True,
            "normal",
            [],
        )

    monkeypatch.setattr(
        "app.services.market_regime_command_center.classify_market_regime",
        fake_classify,
    )

    dto = MarketRegimeCommandCenterService(repository=FakeRepository()).build_snapshot(
        FakeDb(),
        today=date(2026, 7, 28),
    )

    assert dto.regime == REGIME_BULL_TREND
    assert captured["spy"]["close"] == 120
    assert captured["qqq"]["close"] == 130
    assert captured["params"]["use_qqq"] is True


def test_build_snapshot_action_summary_includes_position_size(monkeypatch) -> None:
    _patch_market_data(
        monkeypatch,
        {
            "SPY": _features(close=105, sma50=110, sma200=100, roc21=-2, roc63=5),
            "QQQ": _features(close=105, sma50=110, sma200=100, roc21=-2, roc63=5),
        },
    )

    dto = MarketRegimeCommandCenterService(repository=FakeRepository()).build_snapshot(
        FakeDb(),
        today=date(2026, 7, 28),
    )

    assert dto.regime == REGIME_BULL_PULLBACK
    assert dto.policy.position_size_multiplier == 0.75
    assert "Use 75% of normal starter size" in dto.action_summary


def test_build_snapshot_write_stores_symbols_health_and_warnings(monkeypatch) -> None:
    _patch_market_data(monkeypatch, {"SPY": _features(), "QQQ": None})
    repo = FakeRepository()

    dto = MarketRegimeCommandCenterService(repository=repo).build_snapshot(
        FakeDb(),
        run_id=11,
        today=date(2026, 7, 28),
    )

    written = repo.calls[0][1]
    assert written.input_symbols == {
        "primary_market": "SPY",
        "risk_proxy": "QQQ",
        "use_risk_proxy": True,
    }
    assert written.index_health["SPY"]["above_sma50"] is True
    assert written.index_health["SPY"]["as_of_date"] == "2026-07-27"
    assert "QQQ" in dto.index_health
    assert "missing_qqq_market_data" in written.warnings


def test_build_snapshot_stale_data_forces_gray_policy(monkeypatch) -> None:
    _patch_market_data(
        monkeypatch,
        {
            "SPY": _features(as_of_date=date(2026, 7, 1)),
            "QQQ": _features(as_of_date=date(2026, 7, 1)),
        },
    )

    dto = MarketRegimeCommandCenterService(repository=FakeRepository()).build_snapshot(
        FakeDb(),
        today=date(2026, 7, 28),
    )

    assert dto.risk_state == "Gray"
    assert dto.index_health["SPY"].stale is True
    assert "severely_stale_market_data" in dto.warnings


def _patch_market_data(monkeypatch, feature_by_symbol: dict[str, dict | None]) -> None:
    def fake_load_preferred_ohlcv_frames(_db, ticker):
        features = feature_by_symbol[ticker]
        if features is None:
            return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"]), None
        as_of_date = features.get("as_of_date", date(2026, 7, 27))
        frame = pd.DataFrame(
            [
                {
                    "date": as_of_date,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": features["close"],
                    "volume": 1_000_000,
                }
            ]
        )
        return frame, frame

    def fake_calculate_technical_features(_price, _trades=None, ticker="", **_kwargs):
        return FakeFeatureResult(ticker=ticker, latest=feature_by_symbol[ticker])

    monkeypatch.setattr(
        "app.services.market_regime_command_center.load_preferred_ohlcv_frames",
        fake_load_preferred_ohlcv_frames,
    )
    monkeypatch.setattr(
        "app.services.market_regime_command_center.calculate_technical_features",
        fake_calculate_technical_features,
    )


def _features(
    close: float = 120,
    sma50: float = 110,
    sma200: float = 100,
    sma50_slope_pct: float = 2,
    roc21: float = 3,
    roc63: float = 8,
    distribution_count: float = 0,
    donchian_20_breakout: bool = False,
    as_of_date: date = date(2026, 7, 27),
) -> dict:
    return {
        "close": close,
        "sma50": sma50,
        "sma200": sma200,
        "sma50_slope_pct": sma50_slope_pct,
        "roc21": roc21,
        "roc63": roc63,
        "distribution_count": distribution_count,
        "donchian_20_breakout": donchian_20_breakout,
        "as_of_date": as_of_date,
    }


class FakeFeatureResult:
    def __init__(self, ticker: str, latest: dict) -> None:
        self.ticker = ticker
        self.insufficient_data = False
        self.missing_data = {}
        self.latest = latest
        self.debug = {"row_count": 320}


class FakeRepository:
    def __init__(self) -> None:
        self.calls = []

    def upsert_snapshot(self, _db, dto, run_id=None):
        self.calls.append((run_id, dto))
        return object()


class FakeDb:
    pass
