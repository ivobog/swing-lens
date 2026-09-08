from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd

from app.services.pine_replica_engine import PineReplicaScore, score_from_feature_result
from app.services.redaction import redact_text
from app.services.relative_leadership import calculate_beta_adjusted_rs
from app.services.technical_indicators import (
    TechnicalFeatureResult,
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
)

OHLCV_COLUMNS = ("date", "open", "high", "low", "close", "volume")
UNBOUNDED_CUTOFF_AT = datetime.max.replace(tzinfo=UTC)


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
    input_as_of_session: date = date.max
    calculation_cutoff_at: datetime = field(default_factory=lambda: UNBOUNDED_CUTOFF_AT)
    calculation_context_id: int | None = None
    price_basis: str = "UNKNOWN"
    volume_basis: str = "UNKNOWN"
    input_signature: str = ""
    artifact_key: dict[str, Any] | None = None
    cached_local_artifact: dict[str, Any] | None = None
    shadow_local_artifact: dict[str, Any] | None = None


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
    shadow_score: PineReplicaScore | None = None
    shadow_error: str | None = None


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
    input_as_of_session: date = date.max,
    calculation_cutoff_at: datetime = UNBOUNDED_CUTOFF_AT,
    calculation_context_id: int | None = None,
    price_basis: str = "UNKNOWN",
    volume_basis: str = "UNKNOWN",
    input_signature: str = "",
    artifact_key: dict[str, Any] | None = None,
    cached_local_artifact: dict[str, Any] | None = None,
    shadow_local_artifact: dict[str, Any] | None = None,
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
        input_as_of_session=input_as_of_session,
        calculation_cutoff_at=calculation_cutoff_at,
        calculation_context_id=calculation_context_id,
        price_basis=price_basis,
        volume_basis=volume_basis,
        input_signature=input_signature,
        artifact_key=artifact_key,
        cached_local_artifact=cached_local_artifact,
        shadow_local_artifact=shadow_local_artifact,
    )


def execute_technical_work_item(item: TechnicalWorkItem) -> TechnicalWorkResult:
    """Calculate one ticker without a database connection or ORM instance."""

    try:
        price = _records_frame(item.price_records)
        trades = _records_frame(item.trade_records)
        benchmark = _records_frame(item.benchmark_records)
        sector = None if item.sector_records is None else _records_frame(item.sector_records)
        _assert_temporal_boundary(item, price, trades, benchmark, sector)
        if price.empty:
            raise ValueError(f"No cached OHLCV bars for {item.ticker}. Fetch IB data first.")

        if item.cached_local_artifact is None:
            feature_result = calculate_technical_features(
                price,
                trades if not trades.empty else None,
                ticker=item.ticker,
                params=item.pine_config,
                v4_params=item.technical_config,
            )
            htf_features = calculate_htf_trend_features(
                price,
                params=item.pine_config,
                latest_completed_session=item.input_as_of_session,
            )
        else:
            feature_result, htf_features = _artifact_features(item.cached_local_artifact)
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
        source_sessions = {
            "ticker": _latest_session(price),
            "trades": _latest_session(trades),
            "benchmark": _latest_session(benchmark),
            "sector": _latest_session(sector),
        }
        score = _with_temporal_lineage(item, score, source_sessions)
        shadow_score = None
        shadow_error = None
        if item.shadow_local_artifact is not None:
            try:
                shadow_features, shadow_htf_features = _artifact_features(
                    item.shadow_local_artifact
                )
                shadow_score = score_from_feature_result(
                    shadow_features,
                    htf_features=shadow_htf_features,
                    relative_strength_features=relative_strength_features,
                    market_features=item.market_features,
                    qqq_market_features=item.qqq_market_features,
                    params=item.pine_config,
                    v4_params=item.technical_config,
                )
                shadow_score = _with_temporal_lineage(item, shadow_score, source_sessions)
            except Exception as exc:
                shadow_error = redact_text(str(exc))
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
            shadow_score=shadow_score,
            shadow_error=shadow_error,
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
            error=redact_text(str(exc)),
            artifact_key=item.artifact_key,
            shadow_score=None,
            shadow_error=None,
        )


def _artifact_features(
    artifact: dict[str, Any],
) -> tuple[TechnicalFeatureResult, dict[str, Any]]:
    return (
        TechnicalFeatureResult(**artifact["feature_result"]),
        dict(artifact["htf_features"]),
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


def _assert_temporal_boundary(
    item: TechnicalWorkItem,
    price: pd.DataFrame,
    trades: pd.DataFrame,
    benchmark: pd.DataFrame,
    sector: pd.DataFrame | None,
) -> None:
    for source, frame in (
        ("ticker_price", price),
        ("ticker_trades", trades),
        ("benchmark", benchmark),
        ("sector", sector),
    ):
        latest = _latest_session(frame)
        if latest is not None and latest > item.input_as_of_session:
            raise ValueError(
                f"temporal integrity violation: {source} session {latest} exceeds "
                f"input_as_of_session {item.input_as_of_session}; "
                f"context_id={item.calculation_context_id}; "
                f"cutoff_at={item.calculation_cutoff_at.isoformat()}; "
                "policy=daily-bar-at-or-before-cutoff-v1"
            )


def _latest_session(frame: pd.DataFrame | None) -> date | None:
    if frame is None or frame.empty or "date" not in frame:
        return None
    value = pd.Timestamp(frame["date"].iloc[-1])
    if value.tzinfo is not None:
        value = value.tz_convert("America/New_York")
    return value.date()


def _with_temporal_lineage(
    item: TechnicalWorkItem,
    score: PineReplicaScore,
    source_sessions: dict[str, date | None],
) -> PineReplicaScore:
    if item.input_as_of_session == date.max:
        return score
    lineage = {
        "calculation_context_id": item.calculation_context_id,
        "calculation_cutoff_at": item.calculation_cutoff_at.isoformat(),
        "input_as_of_session": item.input_as_of_session.isoformat(),
        "source_latest_sessions": {
            key: value.isoformat() if value is not None else None
            for key, value in source_sessions.items()
        },
        "session_lags": {
            key: None if value is None else _weekday_safe_lag(value, item.input_as_of_session)
            for key, value in source_sessions.items()
        },
        "price_basis": item.price_basis,
        "volume_basis": item.volume_basis,
    }
    return replace(score, debug={**(score.debug or {}), "temporal_lineage": lineage})


def _weekday_safe_lag(older: date, newer: date) -> int | None:
    if newer == date.max:
        return None
    from app.services.market_clock_service import MarketClockService

    return MarketClockService().trading_session_distance(older, newer)
