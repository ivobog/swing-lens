import pandas as pd

from app.services.relative_leadership import (
    calculate_beta_adjusted_rs,
    rank_technical_universe,
)


def test_calculate_beta_adjusted_rs_returns_residual_features() -> None:
    stock = _price_frame(multiplier=1.01, rows=150)
    benchmark = _price_frame(multiplier=1.005, rows=150)

    result = calculate_beta_adjusted_rs(
        stock,
        benchmark,
        {"beta_lookbacks": [63, 126]},
    )

    assert result["rolling_beta_63"] is not None
    assert result["rolling_beta_126"] is not None
    assert result["residual_return_21"] is not None
    assert result["residual_return_63"] is not None
    assert 0 <= result["residual_momentum_score"] <= 10


def test_calculate_beta_adjusted_rs_returns_empty_when_missing_data() -> None:
    result = calculate_beta_adjusted_rs(
        pd.DataFrame(),
        _price_frame(multiplier=1.0, rows=10),
        {},
    )

    assert result == {}


def test_rank_technical_universe_assigns_percentiles_and_tags() -> None:
    rows = [
        {
            "ticker": "AAA",
            "roc21": 1,
            "roc63": 2,
            "roc126": 3,
            "benchmark_rs_score": 4,
            "dual_score": 5,
            "setup_score": 6,
        },
        {
            "ticker": "BBB",
            "roc21": 3,
            "roc63": 4,
            "roc126": 5,
            "benchmark_rs_score": 6,
            "dual_score": 7,
            "setup_score": 8,
        },
        {
            "ticker": "CCC",
            "roc21": 2,
            "roc63": 3,
            "roc126": 4,
            "benchmark_rs_score": 5,
            "dual_score": 6,
            "setup_score": 7,
        },
    ]

    results = rank_technical_universe(
        rows,
        {"run_percentiles": True, "leadership_min_percentile": 70},
    )

    assert results["BBB"].roc21_run_percentile == 100.0
    assert results["CCC"].roc21_run_percentile == 66.6667
    assert results["AAA"].roc21_run_percentile == 33.3333
    assert results["BBB"].leadership_score == 10.0
    assert results["BBB"].leadership_tags == ["rs_leader"]
    assert results["AAA"].leadership_tags == []


def test_rank_technical_universe_can_be_disabled() -> None:
    assert rank_technical_universe(
        [{"ticker": "AAA", "roc21": 1}],
        {"run_percentiles": False},
    ) == {}


def _price_frame(multiplier: float, rows: int) -> pd.DataFrame:
    prices = []
    close = 100.0
    for index in range(rows):
        close *= multiplier + ((index % 5) - 2) * 0.0005
        prices.append(
            {
                "date": pd.Timestamp("2025-01-01") + pd.Timedelta(days=index),
                "close": close,
            }
        )
    return pd.DataFrame(prices)
