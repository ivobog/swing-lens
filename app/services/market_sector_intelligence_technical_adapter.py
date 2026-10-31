"""Pure ETF-scoped use of the existing Pine, V4 and V5 engines.

No run context, artifact cache operations, ORM objects or scoring formula copies.
Readiness is deliberately stricter than a routine returning a numerical default.
"""

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, replace

from app.services.leadership_v5 import rank_leadership_v5
from app.services.market_sector_intelligence_rotation_service import fingerprint
from app.services.pine_replica_engine import score_from_feature_result
from app.services.relative_leadership import calculate_beta_adjusted_rs
from app.services.sector_benchmark_service import MISSING_SECTOR, SectorBenchmarkResolution
from app.services.technical_indicators import (
    calculate_htf_trend_features,
    calculate_relative_strength_features,
    calculate_technical_features,
    load_pine_defaults,
)
from app.services.technical_score_v4 import technical_score_v4_from_base_score
from app.services.technical_score_v5 import technical_score_v5_from_base_score
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.services.us_market_calendar import CALENDAR_VERSION

ADAPTER_VERSION = "etf-technical-diagnostics-v2"
LEADERSHIP_SCOPE = "fixed_11_etf_universe"
SECTOR_POLICY = "NOT_APPLICABLE_ETF"
SECTOR_RESOLUTION = SectorBenchmarkResolution(
    sector=None,
    benchmark_symbol=None,
    status=MISSING_SECTOR,
    reason="sector benchmarking not applicable for a sector ETF",
)


class TechnicalEvidenceError(ValueError):
    """Only stable, public reason codes belong in this exception."""


@dataclass(frozen=True)
class ETFTechnicalPolicy:
    pine: dict
    v4: dict
    v5: dict
    identity: dict
    requires_qqq: bool


def scoped_policy(symbols, *, pine=None, v4=None, v5=None):
    pine = deepcopy(load_pine_defaults() if pine is None else pine)
    v4 = deepcopy(load_technical_scoring_v4_config() if v4 is None else v4)
    v5 = deepcopy(load_technical_scoring_v5_config() if v5 is None else v5)
    if any(pine["market_rs"].get(key) != "SPY" for key in ("marketSymbol", "benchmarkSymbol")):
        raise ValueError("ETF_REQUIRES_SPY_BENCHMARK")
    pine["market_rs"]["useSectorBenchmark"] = False
    v5["leadership"]["scope"] = LEADERSHIP_SCOPE
    v5["sector_benchmarks"]["required_for_confidence"] = False
    identity = {
        "adapter_version": ADAPTER_VERSION,
        "sector_benchmark_policy": SECTOR_POLICY,
        "fixed_cohort_symbols": sorted(symbols),
        "pine_engine": deepcopy(pine["engine"]),
        "v4_engine": deepcopy(v4["engine"]),
        "v5_engine": deepcopy(v5["engine"]),
        "pine_config_digest": fingerprint(pine),
        "v4_config_digest": fingerprint(v4),
        "v5_config_digest": fingerprint(v5),
        "leadership_scope": LEADERSHIP_SCOPE,
        "leadership_weights": deepcopy(v5["leadership"]["weights"]),
        "leadership_renormalize_missing": v5["leadership"]["renormalize_missing"],
        "readiness_policy": (
            "bounded_daily_ohlc; effective_volume; finite_enabled_features; "
            "confirmed_htf; full_v5_cohort"
        ),
        "history_window_sessions": technical_history_sessions(pine, v4),
        "v4_leadership_policy": "native_broad_market_rs_fallback_without_stock_run_percentiles",
    }
    identity["adapter_policy_digest"] = fingerprint(identity)
    regime = v4["market_regime_v4"]
    return ETFTechnicalPolicy(
        pine,
        v4,
        v5,
        identity,
        bool(regime.get("enabled", True) and regime.get("use_qqq", True)),
    )


def technical_history_sessions(pine, v4):
    """Three exchange years, enlarged for configured finite feature prerequisites.

    This is a maximum input window, not a readiness certificate. Native finite,
    adaptive, confirmed-week and current-beta gates still decide availability.
    """
    trend, momentum, rs, htf = (pine[key] for key in ("trend", "momentum", "market_rs", "htf"))
    daily = max(
        int(trend["smaSlowLen"]) + int(trend["slowSlopeLookback"]),
        int(trend["smaMidLen"]) + int(trend["midSlopeLookback"]),
        int(trend["smaTrendLen"]),
        int(trend["highLow52Len"]),
        int(trend["adxLen"]) + int(trend["adxSmoothing"]),
        *(int(momentum[key]) for key in ("rsiLen", "atrLen", "volLen", "obvSmaLen")),
        *(int(rs[key]) + 1 for key in ("rocLongLen", "rsSmaLen", "rsNewHighLookback")),
    )
    adaptive = v4["adaptive_percentiles"]
    extra = (
        max(int(adaptive[key]) for key in ("long_lookback", "medium_lookback"))
        if adaptive.get("enabled", True)
        else 0
    )
    contraction = v4["volatility_contraction"]
    band = int(contraction.get("bb_width_percentile_lookback", 252)) + int(contraction["bb_length"])
    weekly = 5 * (
        max(int(htf[key]) for key in ("htfFastLen", "htfMidLen", "htfSlowLen"))
        + int(htf["htfSlopeLookback"]) + 2
    )
    beta = max([63, *v4["relative_leadership"].get("beta_lookbacks", [])]) + 64
    return max(756, daily + extra, band, weekly, beta)


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def checked_payload(payload):
    # Canonical serialization rejects NaN/Inf anywhere, including optional components.
    try:
        fingerprint(payload)
    except (ValueError, TypeError, OverflowError) as exc:
        raise TechnicalEvidenceError("NONFINITE_ENGINE_OUTPUT") from exc
    return payload


def local_features(price, volume, ticker, session, policy, *, calendar_version=CALENDAR_VERSION):
    result = calculate_technical_features(
        price,
        volume,
        ticker=ticker,
        params=policy.pine,
        v4_params=policy.v4,
        expected_session=session,
        calendar_version=calendar_version,
    )
    if not finite(result.latest.get("roc126")):
        raise TechnicalEvidenceError("INSUFFICIENT_ROC126")
    if result.insufficient_data:
        raise TechnicalEvidenceError("INSUFFICIENT_HISTORY")
    required = [
        "ema10",
        "ema20",
        "sma50",
        "sma150",
        "sma200",
        "sma50_slope_pct",
        "sma200_slope_pct",
        "sma50_slope_atr",
        "sma200_slope_atr",
        "rsi14",
        "atr14",
        "adx",
        "plus_di",
        "minus_di",
        "roc10",
        "roc21",
        "roc63",
        "position_52w",
        "volume_ratio",
        "avg_volume",
    ]
    if any(not finite(result.latest.get(key)) for key in required):
        raise TechnicalEvidenceError("INSUFFICIENT_INDICATOR_HISTORY")
    if result.latest["atr14"] <= 0:
        raise TechnicalEvidenceError("INVALID_ATR")
    if policy.v4["adaptive_percentiles"].get("enabled", True):
        adaptive = (
            "atr_percentile_126",
            "atr_percentile_252",
            "volume_percentile_252",
            "notional_volume_percentile_252",
            "range_percentile_252",
            "extension_percentile_252",
        )
        if any(not finite(result.latest.get(key)) for key in adaptive):
            raise TechnicalEvidenceError("INSUFFICIENT_ADAPTIVE_HISTORY")
    if policy.v4["volatility_contraction"].get("enabled", True) and not finite(
        result.latest.get("bb_width_percentile_252")
    ):
        raise TechnicalEvidenceError("INSUFFICIENT_CONTRACTION_HISTORY")
    return result


def build_base(
    ticker, price, volume, spy, market, qqq, session, policy, *, calendar_version=CALENDAR_VERSION
):
    features = local_features(
        price, volume, ticker, session, policy, calendar_version=calendar_version
    )
    htf = calculate_htf_trend_features(
        price,
        params=policy.pine,
        latest_completed_session=session,
        calendar_version=calendar_version,
    )
    if policy.pine["htf"].get("useHtfTrendFilter", False) or policy.pine["htf"].get(
        "blendHtfIntoTrendScore", False
    ):
        if not htf.get("htf_confirmed") or any(
            not finite(htf.get(key))
            for key in (
                "htf_ema_fast",
                "htf_sma_mid",
                "htf_sma_slow",
                "htf_mid_slope_pct",
                "htf_slow_slope_pct",
                "htf_roc",
            )
        ):
            raise TechnicalEvidenceError("INSUFFICIENT_CONFIRMED_HTF")
    rs = calculate_relative_strength_features(price, spy, None, params=policy.pine)
    if (
        rs.get("insufficient_rs_history")
        or rs.get("missing_benchmark_overlap")
        or any(
            not finite(rs.get(key))
            for key in (
                "benchmark_rs_line",
                "benchmark_rs_sma",
                "benchmark_rs_roc21",
                "benchmark_rs_roc63",
                "benchmark_rs_roc126",
            )
        )
    ):
        raise TechnicalEvidenceError("INSUFFICIENT_BENCHMARK_OVERLAP")
    beta_ready = _beta_window_ready(price, spy, policy.v4["relative_leadership"])
    if policy.v4["relative_leadership"].get("beta_adjusted_rs", False) and beta_ready:
        rs.update(calculate_beta_adjusted_rs(price, spy, policy.v4["relative_leadership"]))
    base = score_from_feature_result(
        features,
        htf_features=htf,
        relative_strength_features=rs,
        market_features=market,
        qqq_market_features=qqq,
        params=policy.pine,
        v4_params=policy.v4,
    )
    if base.missing_data.get("missing_reasons"):
        raise TechnicalEvidenceError("ENGINE_NOT_READY")
    return replace(
        base,
        debug={
            **base.debug,
            "etf_adapter_evidence": {
                "htf_confirmation": {
                    key: htf.get(key)
                    for key in (
                        "htf_source_week",
                        "htf_confirmed",
                        "htf_latest_week_excluded",
                    )
                },
                "current_beta_window_complete": beta_ready,
            },
        },
    )


def _beta_window_ready(price, spy, relative):
    # The native helper takes the last non-null beta and drops missing residual
    # rows. Require its actual end windows first so historical values cannot
    # stand in for unavailable current inputs. This gate adds no score formula.
    aligned = (
        price[["date", "close"]]
        .merge(spy[["date", "close"]], on="date", suffixes=("_etf", "_spy"))
        .sort_values("date")
    )
    returns = aligned["close_spy"].pct_change()
    for lookback in set(relative.get("beta_lookbacks", [63, 126])) | {63}:
        variance = returns.rolling(lookback, min_periods=lookback).var()
        required = variance.tail(63) if lookback == 63 else variance.tail(1)
        spread = (
            returns.rolling(lookback, min_periods=lookback).max()
            - returns.rolling(lookback, min_periods=lookback).min()
        ).tail(len(required))
        if (
            len(required) < (63 if lookback == 63 else 1)
            or any(not finite(value) or value <= 0 for value in required)
            or any(not finite(value) or value <= 0 for value in spread)
        ):
            return False
    return True


def v4_payload(base, policy):
    score = technical_score_v4_from_base_score(base, v4_params=policy.v4)
    return checked_payload(
        {
            "status": "READY",
            "final_v4_score": score.final_v4_score,
            "classification": score.final_v4_classification,
            "action_bias": score.final_v4_action,
            "confidence": base.technical_confidence,
            "engine_version": score.engine_version,
            "data_readiness": score.data_readiness,
            "adapter_readiness": base.debug.get("etf_adapter_evidence"),
            "warnings": score.warning_flags,
            "components": score.debug["explainability"]["debug"]["v4_components"],
            "regime": score.regime,
            "stage": score.stage,
            "adaptive": score.adaptive,
            "contraction": score.contraction,
            "box": score.box,
            "climax": score.climax,
            "feature_flags": score.feature_flags,
        }
    )


def leadership_input(base, policy):
    derived = base.debug["derived"]
    row = {
        "ticker": base.ticker,
        "roc21": derived.get("stock_roc_short"),
        "roc63": derived.get("stock_roc_medium"),
        "roc126": derived.get("stock_roc_long"),
        "benchmark_rs_score": derived.get("benchmark_rs_score"),
        "residual_momentum_score": derived.get("residual_momentum_score"),
    }
    columns = {
        "benchmark_rs": "benchmark_rs_score",
        "residual_momentum": "residual_momentum_score",
    }
    missing = [
        key
        for key, weight in policy.v5["leadership"]["weights"].items()
        if weight > 0 and not finite(row.get(columns.get(key, key)))
    ]
    if policy.v5["leadership"].get("require_beta_adjusted_rs", True):
        missing.extend(
            key
            for key in (
                "rolling_beta_63",
                "rolling_beta_126",
                "residual_return_21",
                "residual_return_63",
            )
            if not finite(derived.get(key))
        )
    return row, missing


def fixed_leadership(rows, symbols, policy):
    if len(rows) != 11 or {row["ticker"] for row in rows} != set(symbols):
        raise TechnicalEvidenceError("V5_COHORT_NOT_COMPARABLE")
    return rank_leadership_v5(rows, policy.v5["leadership"])


def v5_payload(base, leadership, policy):
    result = technical_score_v5_from_base_score(
        base,
        leadership=leadership,
        sector_resolution=SECTOR_RESOLUTION,
        v5_config=policy.v5,
        pine_config=policy.pine,
    )
    return checked_payload(
        {
            "status": "READY",
            "technical_composite_score": result.technical_composite_score,
            "confidence_adjusted_score": result.confidence_adjusted_score,
            "classification": result.classification,
            "action_bias": result.action_bias,
            "confidence": result.technical_confidence,
            "engine_version": result.engine_version,
            "technical_strength_score": result.technical_strength_score,
            "setup_quality_score": result.setup_quality_score,
            "entry_quality_score": result.entry_quality_score,
            "leadership_score": result.leadership_v5_score,
            "leadership_scope": leadership.scope,
            "cohort_size": leadership.universe_size,
            "leadership": asdict(leadership),
            "warnings": list(result.warning_flags),
            "missing_evidence": list(result.technical_strength.missing_evidence)
            + list(leadership.missing_components),
            "components": result.debug,
            "sector_benchmark_policy": SECTOR_POLICY,
        }
    )
