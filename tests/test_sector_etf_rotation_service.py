from datetime import date, timedelta

import pandas as pd

from app.services.sector_etf_rotation_service import SectorEtfRotationService
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_rotation_dtos import SectorUniverseMetrics


def test_sector_etf_rotation_service_scores_proxy_and_relative_strength(monkeypatch) -> None:
    config = _etf_config()
    frames = {
        "XLK": _bars(start=100, daily_step=1.2),
        "SPY": _bars(start=100, daily_step=0.4),
    }

    monkeypatch.setattr(
        "app.services.sector_etf_rotation_service.load_preferred_ohlcv_frames",
        lambda _db, ticker: (frames.get(ticker, pd.DataFrame()), None),
    )

    rows = SectorEtfRotationService().build(
        object(),
        universe_rows=[_metrics("Technology", score=8.0)],
        config=config,
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.proxy_ticker == "XLK"
    assert row.benchmark_ticker == "SPY"
    assert row.etf_rotation_score is not None
    assert row.etf_rotation_score > 5
    assert row.component_scores["relative_strength"] > 0
    assert row.metrics["above_sma50"] is True
    assert row.metrics["rs_roc21"] is not None
    assert row.warnings == []


def test_sector_etf_rotation_service_missing_proxy_yields_null_score(monkeypatch) -> None:
    config = _etf_config()
    monkeypatch.setattr(
        "app.services.sector_etf_rotation_service.load_preferred_ohlcv_frames",
        lambda _db, _ticker: (pd.DataFrame(), None),
    )

    rows = SectorEtfRotationService().build(
        object(),
        universe_rows=[_metrics("Technology", score=8.0)],
        config=config,
    )

    assert rows[0].etf_rotation_score is None
    assert "missing_xlk_etf_data" in rows[0].warnings


def test_sector_etf_rotation_service_missing_benchmark_warns_without_nulling_score(
    monkeypatch,
) -> None:
    config = _etf_config()

    def fake_frames(_db, ticker):
        if ticker == "XLK":
            return _bars(start=100, daily_step=1.0), None
        return pd.DataFrame(), None

    monkeypatch.setattr(
        "app.services.sector_etf_rotation_service.load_preferred_ohlcv_frames",
        fake_frames,
    )

    rows = SectorEtfRotationService().build(
        object(),
        universe_rows=[_metrics("Technology", score=8.0)],
        config=config,
    )

    assert rows[0].etf_rotation_score is not None
    assert "missing_spy_benchmark_data" in rows[0].warnings


def _etf_config() -> dict:
    config = load_sector_rotation_config()
    config["etf_score"]["enabled"] = True
    return config


def _metrics(sector: str, score: float) -> SectorUniverseMetrics:
    return SectorUniverseMetrics(
        sector=sector,
        sector_slug=sector.lower().replace(" ", "-"),
        ticker_count=12,
        universe_share=1.0,
        average_fundamental_score=score,
        average_technical_score=score,
        average_final_score=score,
        average_profile_score=score,
        top_counts={"top_10": 1, "top_25": 2, "top_50": 4},
        setup_distribution={},
        warning_distribution={},
        buyable_count=2,
        watch_count=0,
        danger_count=0,
        buyable_share=0.1667,
        watch_share=0.0,
        danger_share=0.0,
        clean_pullback_count=0,
        breakout_count=1,
        vcp_count=0,
        tight_base_breakout_count=0,
        extended_or_overheated_count=0,
        missing_fundamental_count=0,
        missing_technical_count=0,
        universe_leadership_score=score,
        confidence="high",
    )


def _bars(start: float, daily_step: float, count: int = 280) -> pd.DataFrame:
    start_date = date(2025, 1, 1)
    rows = []
    for index in range(count):
        close = start + daily_step * index
        rows.append(
            {
                "date": start_date + timedelta(days=index),
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 1_000_000 + index,
            }
        )
    return pd.DataFrame(rows)
