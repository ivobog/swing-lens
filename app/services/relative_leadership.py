from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class LeadershipResult:
    ticker: str
    roc21_run_percentile: float | None
    roc63_run_percentile: float | None
    roc126_run_percentile: float | None
    benchmark_rs_run_percentile: float | None
    dual_score_run_percentile: float | None
    setup_score_run_percentile: float | None
    leadership_score: float | None
    leadership_tags: list[str]


def calculate_beta_adjusted_rs(
    stock_df: pd.DataFrame,
    benchmark_df: pd.DataFrame,
    params: dict[str, Any],
) -> dict[str, Any]:
    if stock_df.empty or benchmark_df.empty:
        return {}

    aligned = _aligned_close(stock_df, benchmark_df)
    if aligned.empty:
        return {}

    stock_ret = aligned["close_stock"].pct_change()
    benchmark_ret = aligned["close_benchmark"].pct_change()
    result: dict[str, Any] = {}
    for lookback in params.get("beta_lookbacks", [63, 126]):
        lookback_int = int(lookback)
        beta = _rolling_beta(stock_ret, benchmark_ret, lookback_int)
        result[f"rolling_beta_{lookback_int}"] = _last(beta)

    beta_63 = _rolling_beta(stock_ret, benchmark_ret, 63)
    residual = stock_ret - beta_63 * benchmark_ret
    result["residual_return_21"] = _compounded_return(residual, 21)
    result["residual_return_63"] = _compounded_return(residual, 63)
    result["residual_momentum_score"] = _residual_momentum_score(
        result["residual_return_21"],
        result["residual_return_63"],
    )
    return result


def rank_technical_universe(
    score_rows: list[dict[str, Any]],
    params: dict[str, Any],
) -> dict[str, LeadershipResult]:
    if not score_rows or not params.get("run_percentiles", True):
        return {}

    frame = pd.DataFrame(score_rows).copy()
    if "ticker" not in frame:
        return {}
    frame["ticker"] = frame["ticker"].astype(str).str.upper()

    percentile_columns = {
        "roc21": "roc21_run_percentile",
        "roc63": "roc63_run_percentile",
        "roc126": "roc126_run_percentile",
        "benchmark_rs_score": "benchmark_rs_run_percentile",
        "dual_score": "dual_score_run_percentile",
        "setup_score": "setup_score_run_percentile",
    }
    for source, target in percentile_columns.items():
        frame[target] = _run_percentile(frame[source]) if source in frame else None

    component_cols = list(percentile_columns.values())
    frame["leadership_score"] = frame[component_cols].mean(axis=1, skipna=True) / 10
    frame.loc[frame[component_cols].isna().all(axis=1), "leadership_score"] = None

    min_percentile = float(params.get("leadership_min_percentile", 70))
    results: dict[str, LeadershipResult] = {}
    for row in frame.to_dict(orient="records"):
        tags = []
        if _num(row.get("leadership_score")) >= min_percentile / 10:
            tags.append("rs_leader")
        ticker = str(row["ticker"]).upper()
        results[ticker] = LeadershipResult(
            ticker=ticker,
            roc21_run_percentile=_optional_float(row.get("roc21_run_percentile")),
            roc63_run_percentile=_optional_float(row.get("roc63_run_percentile")),
            roc126_run_percentile=_optional_float(row.get("roc126_run_percentile")),
            benchmark_rs_run_percentile=_optional_float(
                row.get("benchmark_rs_run_percentile")
            ),
            dual_score_run_percentile=_optional_float(row.get("dual_score_run_percentile")),
            setup_score_run_percentile=_optional_float(row.get("setup_score_run_percentile")),
            leadership_score=_optional_float(row.get("leadership_score")),
            leadership_tags=tags,
        )
    return results


def _aligned_close(stock_df: pd.DataFrame, benchmark_df: pd.DataFrame) -> pd.DataFrame:
    stock = stock_df.loc[:, ["date", "close"]].copy()
    benchmark = benchmark_df.loc[:, ["date", "close"]].copy()
    stock["date"] = pd.to_datetime(stock["date"])
    benchmark["date"] = pd.to_datetime(benchmark["date"])
    return stock.merge(
        benchmark,
        on="date",
        suffixes=("_stock", "_benchmark"),
    ).sort_values("date")


def _rolling_beta(
    stock_ret: pd.Series,
    benchmark_ret: pd.Series,
    lookback: int,
) -> pd.Series:
    covariance = stock_ret.rolling(lookback, min_periods=lookback).cov(benchmark_ret)
    variance = benchmark_ret.rolling(lookback, min_periods=lookback).var()
    return covariance / variance.replace(0, pd.NA)


def _compounded_return(series: pd.Series, lookback: int) -> float | None:
    window = series.dropna().tail(lookback)
    if len(window) < lookback:
        return None
    return round(float(((1 + window).prod() - 1) * 100), 4)


def _residual_momentum_score(
    residual_return_21: float | None,
    residual_return_63: float | None,
) -> float | None:
    if residual_return_21 is None and residual_return_63 is None:
        return None
    short = _num(residual_return_21)
    medium = _num(residual_return_63)
    score = 5.0 + short * 0.20 + medium * 0.08
    return max(0.0, min(10.0, round(score, 4)))


def _run_percentile(series: pd.Series) -> pd.Series:
    numeric = pd.to_numeric(series, errors="coerce")
    return numeric.rank(pct=True, method="average") * 100


def _last(series: pd.Series) -> float | None:
    valid = series.dropna()
    if valid.empty:
        return None
    return round(float(valid.iloc[-1]), 4)


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)


def _num(value: Any) -> float:
    if value is None or pd.isna(value):
        return 0.0
    return float(value)
