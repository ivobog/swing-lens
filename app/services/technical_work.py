from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import pandas as pd

from app.services.pine_replica_engine import PineReplicaScore, score_from_feature_result
from app.services.relative_leadership import calculate_beta_adjusted_rs
from app.services.technical_indicators import (
    TechnicalFeatureResult,
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
)

OHLCV_COLUMNS = ("date", "open", "high", "low", "close", "volume")


@dataclass(frozen=True)
class TechnicalWorkItem:
    """Database-free input contract for one ticker's technical calculation."""

    ticker: str
    price_records: tuple[tuple[Any, ...], ...]
    trade_records: tuple[tuple[Any, ...], ...]
    benchmark_records: tuple[tuple[Any, ...], ...]
    sector_records: tuple[tuple[Any, ...], ...] | None
    technical_config: dict[str, Any]
    pine_config: dict[str, Any]
    relative_config: dict[str, Any]
    market_features: dict[str, Any]
    qqq_market_features: dict[str, Any]
    input_signature: str = ""
    artifact_key: dict[str, Any] | None = None
    cached_local_artifact: dict[str, Any] | None = None


@dataclass(frozen=True)
class TechnicalWorkResult:
    """Picklable result contract returned by sequential or process execution."""

    ticker: str
    input_signature: str
    feature_result: dict[str, Any]
    htf_features: dict[str, Any]
    relative_strength_features: dict[str, Any]
    warnings: tuple[str, ...]
    score: PineReplicaScore | None
    error: str | None
    artifact_key: dict[str, Any] | None = None


def build_technical_work_item(
    *,
    ticker: str,
    price: pd.DataFrame,
    trades: pd.DataFrame | None,
    benchmark_price: pd.DataFrame,
    sector_price: pd.DataFrame | None,
    technical_config: dict[str, Any],
    pine_config: dict[str, Any],
    relative_config: dict[str, Any],
    market_features: dict[str, Any],
    qqq_market_features: dict[str, Any] | None = None,
    input_signature: str = "",
    artifact_key: dict[str, Any] | None = None,
    cached_local_artifact: dict[str, Any] | None = None,
) -> TechnicalWorkItem:
    return TechnicalWorkItem(
        ticker=ticker.upper(),
        price_records=_frame_records(price),
        trade_records=_frame_records(trades),
        benchmark_records=_frame_records(benchmark_price),
        sector_records=None if sector_price is None else _frame_records(sector_price),
        technical_config=technical_config,
        pine_config=pine_config,
        relative_config=relative_config,
        market_features=market_features,
        qqq_market_features=qqq_market_features or {},
        input_signature=input_signature,
        artifact_key=artifact_key,
        cached_local_artifact=cached_local_artifact,
    )


def execute_technical_work_item(item: TechnicalWorkItem) -> TechnicalWorkResult:
    """Calculate one ticker without a database connection or ORM instance."""

    try:
        price = _records_frame(item.price_records)
        trades = _records_frame(item.trade_records)
        benchmark = _records_frame(item.benchmark_records)
        sector = (
            None
            if item.sector_records is None
            else _records_frame(item.sector_records)
        )
        if price.empty:
            raise ValueError(
                f"No cached OHLCV bars for {item.ticker}. Fetch IB data first."
            )

        if item.cached_local_artifact is None:
            feature_result = calculate_technical_features(
                price,
                trades if not trades.empty else None,
                ticker=item.ticker,
                params=item.pine_config,
                v4_params=item.technical_config,
            )
            htf_features = calculate_htf_trend_features(price, params=item.pine_config)
        else:
            feature_result = TechnicalFeatureResult(
                **item.cached_local_artifact["feature_result"]
            )
            htf_features = dict(item.cached_local_artifact["htf_features"])
        relative_strength_features = _relative_strength_features(
            price,
            benchmark,
            sector,
            item.pine_config,
            item.relative_config,
        )
        score = score_from_feature_result(
            feature_result,
            htf_features=htf_features,
            relative_strength_features=relative_strength_features,
            market_features=item.market_features,
            qqq_market_features=item.qqq_market_features,
            params=item.pine_config,
            v4_params=item.technical_config,
        )
        return TechnicalWorkResult(
            ticker=item.ticker,
            input_signature=item.input_signature,
            feature_result=asdict(feature_result),
            htf_features=htf_features,
            relative_strength_features=relative_strength_features,
            warnings=tuple(score.warning_flags),
            score=score,
            error=None,
            artifact_key=item.artifact_key,
        )
    except Exception as exc:
        return TechnicalWorkResult(
            ticker=item.ticker,
            input_signature=item.input_signature,
            feature_result={},
            htf_features={},
            relative_strength_features={},
            warnings=(),
            score=None,
            error=str(exc),
            artifact_key=item.artifact_key,
        )


def _relative_strength_features(
    price: pd.DataFrame,
    benchmark: pd.DataFrame,
    sector: pd.DataFrame | None,
    pine_config: dict[str, Any],
    relative_config: dict[str, Any],
) -> dict[str, Any]:
    if price.empty or benchmark.empty:
        return {}
    features = calculate_relative_strength_features(
        price,
        benchmark,
        sector,
        params=pine_config,
    )
    if relative_config.get("beta_adjusted_rs", False):
        features.update(calculate_beta_adjusted_rs(price, benchmark, relative_config))
    return features


def _frame_records(frame: pd.DataFrame | None) -> tuple[tuple[Any, ...], ...]:
    if frame is None or frame.empty:
        return ()
    return tuple(frame.loc[:, list(OHLCV_COLUMNS)].itertuples(index=False, name=None))


def _records_frame(records: tuple[tuple[Any, ...], ...]) -> pd.DataFrame:
    return pd.DataFrame.from_records(records, columns=list(OHLCV_COLUMNS))
