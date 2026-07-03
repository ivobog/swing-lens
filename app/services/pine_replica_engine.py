from dataclasses import dataclass
from typing import Any

from app.services.technical_indicators import TechnicalFeatureResult, load_pine_defaults

ENGINE_VERSION = "3.2.0"

CLASS_PRIME_PULLBACK = "Prime clean pullback"
CLASS_CLEAN_PULLBACK = "Clean bull pullback"
CLASS_FRESH_BREAKOUT = "Fresh breakout"
CLASS_MOMENTUM_CONTINUATION = "Momentum continuation"
CLASS_EXTENDED_MOMENTUM = "Extended momentum"
CLASS_OVERHEATED_MOMENTUM = "Overheated momentum"
CLASS_FILTERED_PULLBACK = "Filtered pullback"
CLASS_FILTERED_MOMENTUM = "Filtered momentum"
CLASS_TREND_REPAIR = "Trend repair"
CLASS_DISTRIBUTION_RISK = "Distribution risk"
CLASS_BLOWOFF_TOP = "Blowoff top"
CLASS_FAILED_BREAKOUT = "Failed breakout"
CLASS_NO_TRADE = "No trade"

PULLBACK_HEALTHY = "Healthy"
PULLBACK_MIXED = "Mixed"
PULLBACK_DANGEROUS = "Dangerous"

MARKET_BULLISH = "Bullish"
MARKET_MIXED = "Mixed"
MARKET_BEARISH = "Bearish"
MARKET_RISK_OFF = "Risk-off"

RS_STRONG = "Strong"
RS_NEUTRAL = "Neutral"
RS_WEAK = "Weak"

HTF_STRONG = "Strong"
HTF_NEUTRAL = "Neutral"
HTF_WEAK = "Weak"


@dataclass(frozen=True)
class PineReplicaScore:
    ticker: str
    local_trend_score: float
    trend_score: float
    momentum_score: float
    setup_score: float
    risk_score: float
    market_score: float
    relative_strength_score: float
    sector_relative_strength_score: float
    combined_relative_strength_score: float
    htf_score: float
    dual_score: float
    classification: str
    action_bias: str
    pullback_health: str
    suggested_stop: float | None
    suggested_target: float | None
    reward_risk: float | None
    entry_risk_pct: float | None
    insufficient_data: bool
    missing_data: dict[str, Any]
    debug: dict[str, Any]


def engine_version() -> str:
    return ENGINE_VERSION


def clamp_score(value: float) -> float:
    return max(0.0, min(10.0, round(value, 4)))


def relative_strength_score(
    rs_above_sma: bool,
    rs_roc_short: float,
    rs_roc_medium: float,
    rs_roc_long: float,
    beats_short: bool,
    beats_medium: bool,
    beats_long: bool,
    rs_new_high_value: bool,
) -> float:
    score = 0.0
    score += 2.0 if rs_above_sma else 0.0
    score += 1.5 if rs_roc_short > 0.0 else 0.0
    score += 1.5 if rs_roc_medium > 0.0 else 0.0
    score += 1.0 if rs_roc_long > 0.0 else 0.0
    score += 1.5 if beats_short else 0.0
    score += 1.5 if beats_medium else 0.0
    score += 0.5 if beats_long else 0.0
    score += 0.5 if rs_new_high_value else 0.0
    return clamp_score(score)


def combined_relative_strength_score(
    benchmark_score: float,
    sector_score: float,
    use_sector_benchmark: bool,
) -> float:
    if use_sector_benchmark:
        return clamp_score(benchmark_score * 0.70 + sector_score * 0.30)
    return benchmark_score


def relative_strength_status(score: float) -> str:
    if score >= 7.0:
        return RS_STRONG
    if score >= 4.5:
        return RS_NEUTRAL
    return RS_WEAK


def htf_score(
    data_ready: bool,
    close_above_slow: bool,
    close_above_mid: bool,
    fast_above_mid: bool,
    mid_above_slow: bool,
    mid_slope_pct: float,
    slow_slope_pct: float,
    htf_roc: float,
    stack_strong: bool,
    stack_basic: bool,
) -> float:
    score = 0.0
    score += 2.00 if data_ready and close_above_slow else 0.0
    score += 1.50 if data_ready and close_above_mid else 0.0
    score += 1.25 if data_ready and fast_above_mid else 0.0
    score += 1.50 if data_ready and mid_above_slow else 0.0
    score += 1.25 if mid_slope_pct > 0.0 else 0.0
    score += 1.00 if slow_slope_pct > 0.0 else 0.0
    score += 1.00 if htf_roc > 0.0 else 0.0
    score += 0.50 if stack_strong else 0.25 if stack_basic else 0.0
    return clamp_score(score)


def htf_status(score: float) -> str:
    if score >= 7.0:
        return HTF_STRONG
    if score >= 4.5:
        return HTF_NEUTRAL
    return HTF_WEAK


def local_trend_score(
    above_pullback_ema: bool,
    pullback_above_mid: bool,
    mid_above_trend: bool,
    trend_above_slow: bool,
    above_slow: bool,
    ma_stack_strong: bool,
    mid_slope_pct: float,
    slow_slope_pct: float,
    mid_slope_atr: float,
    slow_slope_atr: float,
    adx_value: float,
    min_adx_trend: float,
    plus_di_above_minus: bool,
    adx_rising: bool,
    plus_di_rising: bool,
    position52: float,
    near52_high: bool,
    higher_high: bool,
    higher_low: bool,
    above_close20_high: bool,
) -> float:
    score = 0.0
    score += 0.50 if above_pullback_ema else 0.0
    score += 0.50 if pullback_above_mid else 0.0
    score += 0.60 if mid_above_trend else 0.0
    score += 0.60 if trend_above_slow else 0.0
    score += 0.40 if above_slow else 0.0
    score += 0.40 if ma_stack_strong else 0.0
    score += 0.50 if mid_slope_pct > 0.0 else 0.0
    score += 0.40 if slow_slope_pct > 0.0 else 0.0
    score += 0.50 if mid_slope_atr > 0.50 else 0.30 if mid_slope_atr > 0.20 else 0.0
    score += 0.40 if slow_slope_atr > 0.25 else 0.20 if slow_slope_atr > 0.10 else 0.0
    score += 0.20 if mid_slope_pct > slow_slope_pct else 0.0
    score += 0.70 if adx_value >= min_adx_trend else 0.35 if adx_value >= 14.0 else 0.0
    score += 0.70 if plus_di_above_minus else 0.0
    score += 0.30 if adx_rising else 0.0
    score += 0.30 if plus_di_rising else 0.0
    score += 0.80 if position52 >= 70.0 else 0.40 if position52 >= 50.0 else 0.0
    score += 0.70 if near52_high else 0.0
    score += 0.50 if higher_high else 0.0
    score += 0.50 if higher_low else 0.0
    score += 0.50 if above_close20_high else 0.0
    return clamp_score(score)


def blended_trend_score(
    local_score: float,
    htf_score_value: float,
    blend_htf_into_trend_score: bool,
    htf_data_ready: bool,
) -> float:
    if blend_htf_into_trend_score and htf_data_ready:
        return clamp_score(local_score * 0.80 + htf_score_value * 0.20)
    return local_score


def momentum_score(
    combined_rs_score: float,
    rs_roc_short: float,
    rs_roc_medium: float,
    rs_new_high_value: bool,
    stock_roc_short: float,
    stock_roc_medium: float,
    stock_roc_long: float,
    beats_benchmark_short: bool,
    beats_benchmark_medium: bool,
    rsi_value: float,
    rsi_rising: bool,
    rsi_pullback_low: float,
    green_beats_red: bool,
    obv_rising: bool,
    bullish_volume_bar: bool,
    breakout_volume_confirmed: bool,
    volume_dry_up: bool,
    strong_close: float,
    price_rising: bool,
    above_fast_ema: bool,
) -> float:
    score = 0.0
    if combined_rs_score >= 8.0:
        score += 1.40
    elif combined_rs_score >= 6.5:
        score += 1.00
    elif combined_rs_score >= 5.0:
        score += 0.50
    score += 0.50 if rs_roc_short > 0.0 else 0.0
    score += 0.50 if rs_roc_medium > 0.0 else 0.0
    score += 0.60 if rs_new_high_value else 0.0
    score += 0.60 if stock_roc_short > 3.0 else 0.30 if stock_roc_short > 0.0 else 0.0
    score += 0.70 if stock_roc_medium > 8.0 else 0.35 if stock_roc_medium > 0.0 else 0.0
    score += 0.50 if stock_roc_long > 15.0 else 0.25 if stock_roc_long > 0.0 else 0.0
    score += 0.35 if beats_benchmark_short else 0.0
    score += 0.35 if beats_benchmark_medium else 0.0
    score += 0.70 if 55.0 <= rsi_value <= 70.0 else 0.40 if 50.0 <= rsi_value <= 75.0 else 0.0
    score += 0.30 if rsi_rising else 0.0
    score += 0.30 if rsi_pullback_low > 40.0 else 0.0
    score += 0.20 if rsi_pullback_low < 50.0 and rsi_value > 55.0 else 0.0
    score += 0.45 if green_beats_red else 0.0
    score += 0.45 if obv_rising else 0.0
    score += 0.40 if bullish_volume_bar else 0.0
    score += 0.40 if breakout_volume_confirmed else 0.0
    score += 0.30 if volume_dry_up else 0.0
    score += 0.40 if strong_close >= 0.70 else 0.20 if strong_close >= 0.60 else 0.0
    score += 0.30 if price_rising else 0.0
    score += 0.30 if above_fast_ema else 0.0
    return clamp_score(score)


def setup_score(
    had_pullback: bool,
    not_too_deep: bool,
    held_near_support: bool,
    pullback_depth_pct: float,
    min_pullback_pct: float,
    volume_dry_up: bool,
    red_vol_declining: bool,
    above_pullback_ema: bool,
    above_mid_ma: bool,
    price_rising: bool,
    strong_close: float,
    fresh_breakout: bool,
    above_close10_high: bool,
    bullish_breakout_volume: bool,
    above_fast_ema: bool,
    extension_mid_pct: float,
    extension_warn_pct: float,
    reward_risk_ok: bool,
    rsi_value: float,
    atr_pct: float,
    atr_warn_pct: float,
    heavy_red_now: bool,
    distribution_count: float,
    failed_breakout: bool,
    gap_exhaustion: bool,
) -> float:
    score = 0.0
    score += 1.00 if had_pullback and not_too_deep else 0.0
    score += 0.80 if held_near_support else 0.0
    score += 0.50 if min_pullback_pct <= pullback_depth_pct <= 10.0 else 0.0
    score += 0.40 if volume_dry_up else 0.0
    score += 0.30 if red_vol_declining else 0.0
    score += 0.50 if above_pullback_ema else 0.0
    score += 0.60 if above_mid_ma else 0.0
    score += 0.40 if price_rising else 0.0
    score += 0.50 if strong_close >= 0.65 else 0.0
    score += 0.80 if fresh_breakout else 0.0
    score += 0.40 if above_close10_high else 0.0
    score += 0.40 if bullish_breakout_volume else 0.0
    score += 0.40 if above_fast_ema else 0.0
    score += 0.80 if extension_mid_pct <= extension_warn_pct else 0.0
    score += 0.50 if reward_risk_ok else 0.0
    score += 0.50 if 50.0 <= rsi_value <= 70.0 else 0.0
    score += 0.20 if atr_pct <= atr_warn_pct else 0.0
    score += 0.30 if not heavy_red_now else 0.0
    score += 0.30 if distribution_count < 3.0 else 0.0
    score += 0.40 if not failed_breakout and not gap_exhaustion else 0.0
    return clamp_score(score)


def risk_score(
    extension_mid_pct: float,
    extension_warn_pct: float,
    extension_danger_pct: float,
    rsi_value: float,
    near_resistance: bool,
    fresh_breakout: bool,
    heavy_red_now: bool,
    distribution_count: float,
    failed_breakout: bool,
    gap_exhaustion: bool,
    liquidity_warning: bool,
    atr_pct: float,
    atr_warn_pct: float,
    atr_danger_pct: float,
    market_risk_off_value: bool,
    market_regime_value: str,
    relative_strength_status_value: str,
    use_sector_benchmark: bool,
    sector_rs_score: float,
    sector_min_score: float,
    htf_status_value: str,
    heavy_mid_ma_break: bool,
) -> float:
    score = 0.0
    score += 1.0 if extension_mid_pct > extension_warn_pct else 0.0
    score += 1.5 if extension_mid_pct > extension_danger_pct else 0.0
    score += 0.8 if rsi_value > 75.0 else 0.0
    score += 0.7 if rsi_value > 80.0 else 0.0
    score += 0.7 if near_resistance and not fresh_breakout else 0.0
    score += 1.5 if heavy_red_now else 0.0
    score += 1.5 if distribution_count >= 3.0 else 0.0
    score += 2.0 if failed_breakout else 0.0
    score += 1.5 if gap_exhaustion else 0.0
    score += 0.8 if liquidity_warning else 0.0
    score += 0.7 if atr_pct > atr_warn_pct else 0.0
    score += 1.0 if atr_pct > atr_danger_pct else 0.0
    if market_risk_off_value:
        score += 1.5
    elif market_regime_value == MARKET_BEARISH:
        score += 1.0
    elif market_regime_value == MARKET_MIXED:
        score += 0.4

    if relative_strength_status_value == RS_WEAK:
        score += 1.0
    elif relative_strength_status_value == RS_NEUTRAL:
        score += 0.4

    score += 0.7 if use_sector_benchmark and sector_rs_score < sector_min_score else 0.0
    if htf_status_value == HTF_WEAK:
        score += 1.0
    elif htf_status_value == HTF_NEUTRAL:
        score += 0.3
    score += 1.0 if heavy_mid_ma_break else 0.0
    return clamp_score(score)


def dual_score(
    trend_score_value: float,
    momentum_score_value: float,
    setup_score_value: float,
    risk_score_value: float,
    market_score_value: float,
    combined_rs_score: float,
    htf_score_value: float,
    scoring_mode: str,
) -> float:
    risk_control_score = 10.0 - risk_score_value
    if scoring_mode == "Early Rocket":
        weights = (0.20, 0.30, 0.20, 0.10, 0.05, 0.10, 0.05)
    elif scoring_mode == "Conservative":
        weights = (0.25, 0.20, 0.15, 0.15, 0.10, 0.10, 0.05)
    else:
        weights = (0.25, 0.25, 0.20, 0.10, 0.08, 0.08, 0.04)
    return clamp_score(
        trend_score_value * weights[0]
        + momentum_score_value * weights[1]
        + setup_score_value * weights[2]
        + risk_control_score * weights[3]
        + market_score_value * weights[4]
        + combined_rs_score * weights[5]
        + htf_score_value * weights[6]
    )


def blowoff_top(
    trend_ok: bool,
    extension_mid_pct: float,
    extension_danger_pct: float,
    rsi_value: float,
    vol_ratio: float,
    upper_wick_pct: float,
    candle_range: float,
    atr_value: float,
) -> bool:
    return (
        trend_ok
        and extension_mid_pct > extension_danger_pct
        and rsi_value > 72.0
        and vol_ratio > 1.30
        and (upper_wick_pct > 35.0 or candle_range > atr_value * 1.50)
    )


def distribution_risk(
    distribution_count: float,
    heavy_red_now: bool,
    gap_exhaustion: bool,
    heavy_mid_ma_break_with_weak_rsi: bool,
) -> bool:
    return (
        distribution_count >= 3.0
        or heavy_red_now
        or gap_exhaustion
        or heavy_mid_ma_break_with_weak_rsi
    )


def classify_setup(
    trend_score_value: float,
    momentum_score_value: float,
    setup_score_value: float,
    risk_score_value: float,
    had_pullback: bool,
    not_too_deep: bool,
    held_near_support: bool,
    above_mid_ma: bool,
    rsi_value: float,
    extension_mid_pct: float,
    extension_warn_pct: float,
    extension_danger_pct: float,
    entry_filters_ok: bool,
    all_filters_ok: bool,
    distribution_risk_value: bool,
    blowoff_top_value: bool,
    failed_breakout: bool,
    fresh_breakout: bool,
    strong_close: float,
    above_slow_ma: bool,
    mid_slope_pct: float,
    combined_rs_score: float,
) -> str:
    prime_clean_pullback = (
        trend_score_value > 8.0
        and momentum_score_value >= 7.0
        and setup_score_value >= 8.0
        and risk_score_value <= 3.0
        and had_pullback
        and not_too_deep
        and held_near_support
        and above_mid_ma
        and 50.0 <= rsi_value <= 70.0
        and extension_mid_pct <= extension_warn_pct
        and entry_filters_ok
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
    )
    clean_bull_pullback = (
        not prime_clean_pullback
        and trend_score_value >= 7.0
        and momentum_score_value >= 6.5
        and setup_score_value >= 7.5
        and risk_score_value <= 4.0
        and had_pullback
        and not_too_deep
        and held_near_support
        and above_mid_ma
        and 50.0 <= rsi_value <= 70.0
        and entry_filters_ok
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
    )
    fresh_breakout_signal = (
        trend_score_value >= 7.0
        and momentum_score_value >= 8.0
        and setup_score_value >= 6.5
        and risk_score_value <= 4.5
        and fresh_breakout
        and strong_close >= 0.65
        and entry_filters_ok
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
    )
    momentum_continuation = (
        trend_score_value >= 7.5
        and momentum_score_value >= 8.0
        and risk_score_value <= 5.0
        and not prime_clean_pullback
        and not clean_bull_pullback
        and not fresh_breakout_signal
        and all_filters_ok
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
    )
    extended_momentum = (
        trend_score_value >= 6.5
        and momentum_score_value >= 7.0
        and risk_score_value <= 6.0
        and not prime_clean_pullback
        and not clean_bull_pullback
        and not fresh_breakout_signal
        and not momentum_continuation
        and all_filters_ok
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
        and (
            extension_mid_pct > extension_warn_pct
            or not had_pullback
            or rsi_value > 68.0
            or fresh_breakout
        )
    )
    overheated_momentum = (
        trend_score_value >= 6.5
        and momentum_score_value >= 7.0
        and not prime_clean_pullback
        and not clean_bull_pullback
        and not fresh_breakout_signal
        and not momentum_continuation
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
        and (
            extension_mid_pct > extension_danger_pct
            or rsi_value > 75.0
            or (extension_mid_pct > extension_warn_pct and rsi_value > 70.0)
            or risk_score_value >= 5.5
        )
    )
    filtered_pullback = (
        not entry_filters_ok
        and trend_score_value >= 7.0
        and setup_score_value >= 7.0
        and had_pullback
        and not_too_deep
        and held_near_support
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
    )
    filtered_momentum = (
        not entry_filters_ok
        and trend_score_value >= 6.5
        and momentum_score_value >= 7.0
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
    )
    trend_repair = (
        not prime_clean_pullback
        and not clean_bull_pullback
        and not fresh_breakout_signal
        and not momentum_continuation
        and not extended_momentum
        and not overheated_momentum
        and not filtered_pullback
        and not filtered_momentum
        and not distribution_risk_value
        and not blowoff_top_value
        and not failed_breakout
        and 4.5 <= trend_score_value < 7.0
        and momentum_score_value >= 4.5
        and above_slow_ma
        and (above_mid_ma or mid_slope_pct > 0.0 or combined_rs_score >= 5.0)
    )

    if blowoff_top_value:
        return CLASS_BLOWOFF_TOP
    if failed_breakout:
        return CLASS_FAILED_BREAKOUT
    if distribution_risk_value:
        return CLASS_DISTRIBUTION_RISK
    if overheated_momentum:
        return CLASS_OVERHEATED_MOMENTUM
    if prime_clean_pullback:
        return CLASS_PRIME_PULLBACK
    if clean_bull_pullback:
        return CLASS_CLEAN_PULLBACK
    if fresh_breakout_signal:
        return CLASS_FRESH_BREAKOUT
    if momentum_continuation:
        return CLASS_MOMENTUM_CONTINUATION
    if extended_momentum:
        return CLASS_EXTENDED_MOMENTUM
    if filtered_pullback:
        return CLASS_FILTERED_PULLBACK
    if filtered_momentum:
        return CLASS_FILTERED_MOMENTUM
    if trend_repair:
        return CLASS_TREND_REPAIR
    return CLASS_NO_TRADE


def pullback_health_status(
    heavy_red_now: bool,
    rsi_collapse: bool,
    had_pullback: bool,
    not_too_deep: bool,
    held_near_support: bool,
    above_mid_ma: bool,
) -> str:
    if heavy_red_now or rsi_collapse or (had_pullback and not not_too_deep):
        return PULLBACK_DANGEROUS
    if had_pullback and not_too_deep and held_near_support and above_mid_ma and not heavy_red_now:
        return PULLBACK_HEALTHY
    return PULLBACK_MIXED


def filter_problem_text(
    market_risk_off_value: bool,
    market_gate_ok: bool,
    sector_relative_strength_gate_ok: bool,
    relative_strength_gate_ok: bool,
    htf_gate_ok: bool,
    reward_risk_ok: bool,
) -> str:
    if market_risk_off_value:
        return "Market risk-off"
    if not market_gate_ok:
        return "Market failed"
    if not sector_relative_strength_gate_ok:
        return "Sector RS failed"
    if not relative_strength_gate_ok:
        return "RS failed"
    if not htf_gate_ok:
        return "HTF failed"
    if not reward_risk_ok:
        return "R/R failed"
    return "Filter failed"


def action_bias_text(classification: str, buyable_text: str, filter_problem: str) -> str:
    if classification == CLASS_PRIME_PULLBACK:
        return "Best buyable, " + buyable_text
    if classification == CLASS_CLEAN_PULLBACK:
        return "Buyable, " + buyable_text
    if classification == CLASS_FRESH_BREAKOUT:
        return "Breakout buy, " + buyable_text
    if classification == CLASS_MOMENTUM_CONTINUATION:
        return "Watch / trail / smaller size"
    if classification == CLASS_EXTENDED_MOMENTUM:
        return "Do not chase, wait mini-pullback"
    if classification == CLASS_OVERHEATED_MOMENTUM:
        return "Overheated, avoid chasing"
    if classification == CLASS_FILTERED_PULLBACK:
        return "Good chart, " + filter_problem
    if classification == CLASS_FILTERED_MOMENTUM:
        return "Momentum, " + filter_problem
    if classification == CLASS_TREND_REPAIR:
        return "Improving, not ready"
    if classification == CLASS_DISTRIBUTION_RISK:
        return "Avoid / exit risk"
    if classification == CLASS_FAILED_BREAKOUT:
        return "Avoid"
    if classification == CLASS_BLOWOFF_TOP:
        return "Avoid chasing"
    return "No clear trade"


def score_from_feature_result(
    feature_result: TechnicalFeatureResult,
    htf_features: dict[str, Any] | None = None,
    relative_strength_features: dict[str, Any] | None = None,
    market_features: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> PineReplicaScore:
    params = params or load_pine_defaults()
    latest = feature_result.latest
    htf_features = htf_features or {}
    relative_strength_features = relative_strength_features or {}
    market_features = market_features or {}
    derived = _derive_inputs(
        latest,
        htf_features,
        relative_strength_features,
        market_features,
        params,
    )

    local_score = local_trend_score(
        derived["above_pullback_ema"],
        derived["pullback_above_mid"],
        derived["mid_above_trend"],
        derived["trend_above_slow"],
        derived["above_slow"],
        derived["ma_stack_strong"],
        derived["mid_slope_pct"],
        derived["slow_slope_pct"],
        derived["mid_slope_atr"],
        derived["slow_slope_atr"],
        derived["adx"],
        params["trend"]["minAdxTrend"],
        derived["plus_di_above_minus"],
        derived["adx_rising"],
        derived["plus_di_rising"],
        derived["position52"],
        derived["near52_high"],
        derived["higher_high"],
        derived["higher_low"],
        derived["above_close20_high"],
    )
    htf_score_value = derived["htf_score"]
    trend_score_value = blended_trend_score(
        local_score,
        htf_score_value,
        params["htf"]["blendHtfIntoTrendScore"],
        derived["htf_data_ready"],
    )
    momentum_score_value = momentum_score(
        derived["combined_rs_score"],
        derived["rs_roc_short"],
        derived["rs_roc_medium"],
        derived["rs_new_high"],
        derived["stock_roc_short"],
        derived["stock_roc_medium"],
        derived["stock_roc_long"],
        derived["beats_benchmark_short"],
        derived["beats_benchmark_medium"],
        derived["rsi"],
        derived["rsi_rising"],
        derived["rsi_pullback_low"],
        derived["green_beats_red"],
        derived["obv_rising"],
        derived["bullish_volume_bar"],
        derived["breakout_volume_confirmed"],
        derived["volume_dry_up"],
        derived["strong_close"],
        derived["price_rising"],
        derived["above_fast_ema"],
    )
    setup_score_value = setup_score(
        derived["had_pullback"],
        derived["not_too_deep"],
        derived["held_near_support"],
        derived["pullback_depth_pct"],
        params["pullback_breakout"]["minPullbackPct"],
        derived["volume_dry_up"],
        derived["red_vol_declining"],
        derived["above_pullback_ema"],
        derived["above_mid_ma"],
        derived["price_rising"],
        derived["strong_close"],
        derived["fresh_breakout"],
        derived["above_close10_high"],
        derived["bullish_breakout_volume"],
        derived["above_fast_ema"],
        derived["extension_mid_pct"],
        params["risk"]["extensionWarnPct"],
        derived["reward_risk_ok"],
        derived["rsi"],
        derived["atr_pct"],
        params["risk"]["atrWarnPct"],
        derived["heavy_red_now"],
        derived["distribution_count"],
        derived["failed_breakout"],
        derived["gap_exhaustion"],
    )
    risk_score_value = risk_score(
        derived["extension_mid_pct"],
        params["risk"]["extensionWarnPct"],
        params["risk"]["extensionDangerPct"],
        derived["rsi"],
        derived["near_resistance"],
        derived["fresh_breakout"],
        derived["heavy_red_now"],
        derived["distribution_count"],
        derived["failed_breakout"],
        derived["gap_exhaustion"],
        derived["liquidity_warning"],
        derived["atr_pct"],
        params["risk"]["atrWarnPct"],
        params["risk"]["atrDangerPct"],
        derived["market_risk_off"],
        derived["market_regime"],
        derived["relative_strength_status"],
        params["market_rs"]["useSectorBenchmark"],
        derived["sector_rs_score"],
        params["market_rs"]["sectorMinScore"],
        derived["htf_status"],
        derived["heavy_mid_ma_break"],
    )
    dual_score_value = dual_score(
        trend_score_value,
        momentum_score_value,
        setup_score_value,
        risk_score_value,
        derived["market_score"],
        derived["combined_rs_score"],
        htf_score_value,
        params["scoring"]["scoringMode"],
    )
    blowoff = blowoff_top(
        derived["trend_ok"],
        derived["extension_mid_pct"],
        params["risk"]["extensionDangerPct"],
        derived["rsi"],
        derived["volume_ratio"],
        derived["upper_wick_pct"],
        derived["candle_range"],
        derived["atr"],
    )
    distribution = distribution_risk(
        derived["distribution_count"],
        derived["heavy_red_now"],
        derived["gap_exhaustion"],
        derived["heavy_mid_ma_break"] and derived["rsi"] < 45.0,
    )
    classification = classify_setup(
        trend_score_value,
        momentum_score_value,
        setup_score_value,
        risk_score_value,
        derived["had_pullback"],
        derived["not_too_deep"],
        derived["held_near_support"],
        derived["above_mid_ma"],
        derived["rsi"],
        derived["extension_mid_pct"],
        params["risk"]["extensionWarnPct"],
        params["risk"]["extensionDangerPct"],
        derived["entry_filters_ok"],
        derived["all_filters_ok"],
        distribution,
        blowoff,
        derived["failed_breakout"],
        derived["fresh_breakout"],
        derived["strong_close"],
        derived["above_slow"],
        derived["mid_slope_pct"],
        derived["combined_rs_score"],
    )
    pullback_health = pullback_health_status(
        derived["heavy_red_now"],
        derived["rsi_pullback_low"] < 35.0,
        derived["had_pullback"],
        derived["not_too_deep"],
        derived["held_near_support"],
        derived["above_mid_ma"],
    )
    filter_problem = filter_problem_text(
        derived["market_risk_off"],
        derived["market_gate_ok"],
        derived["sector_relative_strength_gate_ok"],
        derived["relative_strength_gate_ok"],
        derived["htf_gate_ok"],
        derived["reward_risk_ok"],
    )
    buyable_text = "R/R ok" if derived["reward_risk_ok"] else "R/R below required minimum"
    action_bias = action_bias_text(classification, buyable_text, filter_problem)

    return PineReplicaScore(
        ticker=feature_result.ticker,
        local_trend_score=local_score,
        trend_score=trend_score_value,
        momentum_score=momentum_score_value,
        setup_score=setup_score_value,
        risk_score=risk_score_value,
        market_score=derived["market_score"],
        relative_strength_score=derived["benchmark_rs_score"],
        sector_relative_strength_score=derived["sector_rs_score"],
        combined_relative_strength_score=derived["combined_rs_score"],
        htf_score=htf_score_value,
        dual_score=dual_score_value,
        classification=classification,
        action_bias=action_bias,
        pullback_health=pullback_health,
        suggested_stop=latest.get("suggested_stop"),
        suggested_target=latest.get("suggested_target"),
        reward_risk=latest.get("reward_risk"),
        entry_risk_pct=latest.get("entry_risk_pct"),
        insufficient_data=feature_result.insufficient_data,
        missing_data=feature_result.missing_data,
        debug={"derived": derived, "indicator_debug": feature_result.debug},
    )


def _derive_inputs(
    latest: dict[str, Any],
    htf_features: dict[str, Any],
    rs_features: dict[str, Any],
    market_features: dict[str, Any],
    params: dict[str, Any],
) -> dict[str, Any]:
    close = _num(latest.get("close"))
    open_ = _num(latest.get("open"))
    volume_ratio = _num(latest.get("volume_ratio"), 0.0)
    benchmark_score = _rs_score_from_features(
        rs_features,
        "benchmark",
        stock_roc_short=_num(latest.get("roc21")),
        stock_roc_medium=_num(latest.get("roc63")),
        stock_roc_long=_num(latest.get("roc126")),
        comparison_roc_short=_num(market_features.get("roc21")),
        comparison_roc_medium=_num(market_features.get("roc63")),
        comparison_roc_long=_num(market_features.get("roc126")),
    )
    sector_score = _rs_score_from_features(rs_features, "sector")
    combined_rs = combined_relative_strength_score(
        benchmark_score,
        sector_score,
        params["market_rs"]["useSectorBenchmark"],
    )
    rs_status = relative_strength_status(combined_rs)
    htf_score_value = _htf_score_from_features(htf_features)
    htf_status_value = htf_status(htf_score_value)
    market = _market_from_features(market_features, params)
    reward_risk = latest.get("reward_risk")
    reward_risk_ok = (
        reward_risk is None
        or _num(reward_risk) >= params["stop_target"]["minRewardRisk"]
    )
    sector_gate_ok = (
        not params["market_rs"]["useSectorBenchmark"]
        or sector_score >= params["market_rs"]["sectorMinScore"]
    )
    rs_gate_ok = (
        not params["market_rs"]["useRelativeStrengthFilter"]
        or (combined_rs >= params["market_rs"]["rsMinScore"] and sector_gate_ok)
    )
    htf_gate_ok = (
        not params["htf"]["useHtfTrendFilter"]
        or params["scoring"]["scoringMode"] == "Early Rocket"
        or htf_score_value >= params["htf"]["htfMinScore"]
    )
    all_filters_ok = market["gate_ok"] and rs_gate_ok and htf_gate_ok

    return {
        "above_pullback_ema": close > _num(latest.get("ema20")),
        "pullback_above_mid": _num(latest.get("ema20")) > _num(latest.get("sma50")),
        "mid_above_trend": _num(latest.get("sma50")) > _num(latest.get("sma150")),
        "trend_above_slow": _num(latest.get("sma150")) > _num(latest.get("sma200")),
        "above_slow": close > _num(latest.get("sma200")),
        "above_mid_ma": close > _num(latest.get("sma50")),
        "above_fast_ema": close > _num(latest.get("ema10")),
        "ma_stack_strong": (
            close > _num(latest.get("ema20"))
            and _num(latest.get("ema20")) > _num(latest.get("sma50"))
            and _num(latest.get("sma50")) > _num(latest.get("sma150"))
            and _num(latest.get("sma150")) > _num(latest.get("sma200"))
        ),
        "trend_ok": (
            close > _num(latest.get("sma200"))
            and _num(latest.get("sma50")) > _num(latest.get("sma200"))
            and _num(latest.get("sma50_slope_pct")) > 0
            and _num(latest.get("sma200_slope_pct")) > 0
        ),
        "mid_slope_pct": _num(latest.get("sma50_slope_pct")),
        "slow_slope_pct": _num(latest.get("sma200_slope_pct")),
        "mid_slope_atr": _num(latest.get("sma50_slope_atr")),
        "slow_slope_atr": _num(latest.get("sma200_slope_atr")),
        "adx": _num(latest.get("adx")),
        "plus_di_above_minus": _num(latest.get("plus_di")) > _num(latest.get("minus_di")),
        "adx_rising": _bool(latest.get("adx_rising")),
        "plus_di_rising": _bool(latest.get("plus_di_rising")),
        "position52": _num(latest.get("position_52w")),
        "near52_high": _bool(latest.get("near_52_high")),
        "higher_high": _bool(latest.get("higher_high")),
        "higher_low": _bool(latest.get("higher_low")),
        "above_close20_high": _bool(latest.get("above_close_20_high")),
        "above_close10_high": _bool(latest.get("above_close_10_high")),
        "combined_rs_score": combined_rs,
        "benchmark_rs_score": benchmark_score,
        "sector_rs_score": sector_score,
        "relative_strength_status": rs_status,
        "rs_roc_short": _num(rs_features.get("benchmark_rs_roc21")),
        "rs_roc_medium": _num(rs_features.get("benchmark_rs_roc63")),
        "rs_new_high": _bool(rs_features.get("benchmark_rs_new_high")),
        "stock_roc_short": _num(latest.get("roc21")),
        "stock_roc_medium": _num(latest.get("roc63")),
        "stock_roc_long": _num(latest.get("roc126")),
        "beats_benchmark_short": _num(latest.get("roc21")) > _num(market_features.get("roc21")),
        "beats_benchmark_medium": _num(latest.get("roc63")) > _num(market_features.get("roc63")),
        "rsi": _num(latest.get("rsi14")),
        "rsi_rising": _bool(latest.get("rsi_rising")),
        "rsi_pullback_low": _num(latest.get("rsi_pullback_low"), 50.0),
        "green_beats_red": _bool(latest.get("green_beats_red")),
        "obv_rising": _bool(latest.get("obv_rising")),
        "bullish_volume_bar": close > open_ and volume_ratio >= 1.0,
        "bullish_breakout_volume": close > open_
        and volume_ratio >= params["pullback_breakout"]["breakoutVolRatio"],
        "breakout_volume_confirmed": _bool(latest.get("fresh_breakout"))
        and volume_ratio >= params["pullback_breakout"]["breakoutVolRatio"],
        "volume_dry_up": _bool(latest.get("volume_dry_up")),
        "strong_close": _num(latest.get("strong_close_ratio"), 0.5),
        "price_rising": _bool(latest.get("price_rising")),
        "had_pullback": _bool(latest.get("had_pullback")),
        "not_too_deep": _bool(latest.get("not_too_deep")),
        "held_near_support": _bool(latest.get("held_near_support")),
        "pullback_depth_pct": _num(latest.get("pullback_depth_pct")),
        "red_vol_declining": _bool(latest.get("red_volume_declining")),
        "fresh_breakout": _bool(latest.get("fresh_breakout")),
        "extension_mid_pct": _num(latest.get("extension_above_sma50_pct")),
        "atr_pct": _num(latest.get("atr_pct")),
        "heavy_red_now": _bool(latest.get("heavy_red_candle")),
        "distribution_count": _num(latest.get("distribution_count")),
        "failed_breakout": _bool(latest.get("failed_breakout")),
        "gap_exhaustion": _bool(latest.get("gap_exhaustion")),
        "near_resistance": _bool(latest.get("near_resistance")),
        "liquidity_warning": _bool(latest.get("liquidity_warning")),
        "market_score": market["score"],
        "market_risk_off": market["risk_off"],
        "market_regime": market["regime"],
        "market_gate_ok": market["gate_ok"],
        "htf_score": htf_score_value,
        "htf_status": htf_status_value,
        "htf_data_ready": htf_score_value > 0,
        "htf_gate_ok": htf_gate_ok,
        "relative_strength_gate_ok": rs_gate_ok,
        "sector_relative_strength_gate_ok": sector_gate_ok,
        "all_filters_ok": all_filters_ok,
        "reward_risk_ok": reward_risk_ok,
        "entry_filters_ok": all_filters_ok and reward_risk_ok,
        "heavy_mid_ma_break": close < _num(latest.get("sma50"))
        and _num(latest.get("volume_ratio")) > 1.2,
        "volume_ratio": volume_ratio,
        "upper_wick_pct": _num(latest.get("upper_wick_pct")),
        "candle_range": _num(latest.get("candle_range")),
        "atr": _num(latest.get("atr14")),
    }


def _rs_score_from_features(
    features: dict[str, Any],
    prefix: str,
    stock_roc_short: float | None = None,
    stock_roc_medium: float | None = None,
    stock_roc_long: float | None = None,
    comparison_roc_short: float | None = None,
    comparison_roc_medium: float | None = None,
    comparison_roc_long: float | None = None,
) -> float:
    if not features:
        return 5.0

    line = _num(features.get(f"{prefix}_rs_line"))
    sma = _num(features.get(f"{prefix}_rs_sma"))
    rs_roc_short = _num(features.get(f"{prefix}_rs_roc21"))
    rs_roc_medium = _num(features.get(f"{prefix}_rs_roc63"))
    rs_roc_long = _num(features.get(f"{prefix}_rs_roc126"))
    beats_short = (
        stock_roc_short > comparison_roc_short
        if stock_roc_short is not None and comparison_roc_short is not None
        else rs_roc_short > 0
    )
    beats_medium = (
        stock_roc_medium > comparison_roc_medium
        if stock_roc_medium is not None and comparison_roc_medium is not None
        else rs_roc_medium > 0
    )
    beats_long = (
        stock_roc_long > comparison_roc_long
        if stock_roc_long is not None and comparison_roc_long is not None
        else rs_roc_long > 0
    )
    return relative_strength_score(
        rs_above_sma=line > sma,
        rs_roc_short=rs_roc_short,
        rs_roc_medium=rs_roc_medium,
        rs_roc_long=rs_roc_long,
        beats_short=beats_short,
        beats_medium=beats_medium,
        beats_long=beats_long,
        rs_new_high_value=_bool(features.get(f"{prefix}_rs_new_high")),
    )


def _htf_score_from_features(features: dict[str, Any]) -> float:
    if not features:
        return 5.0
    close = _num(features.get("close"))
    fast = _num(features.get("htf_ema_fast"))
    mid = _num(features.get("htf_sma_mid"))
    slow = _num(features.get("htf_sma_slow"))
    data_ready = bool(fast and mid and slow)
    return htf_score(
        data_ready=data_ready,
        close_above_slow=close > slow,
        close_above_mid=close > mid,
        fast_above_mid=fast > mid,
        mid_above_slow=mid > slow,
        mid_slope_pct=_num(features.get("htf_mid_slope_pct")),
        slow_slope_pct=_num(features.get("htf_slow_slope_pct")),
        htf_roc=_num(features.get("htf_roc")),
        stack_strong=close > fast > mid > slow,
        stack_basic=close > mid > slow,
    )


def _market_from_features(features: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    if not features:
        return {"score": 5.0, "risk_off": False, "regime": MARKET_MIXED, "gate_ok": True}
    close = _num(features.get("close"))
    sma50 = _num(features.get("sma50"))
    sma200 = _num(features.get("sma200"))
    sma50_slope = _num(features.get("sma50_slope_pct"))
    roc_short = _num(features.get("roc21"))
    roc_medium = _num(features.get("roc63"))
    distribution_count = _num(features.get("distribution_count"))
    market_risk_off = (
        distribution_count >= params["market_rs"]["marketDistributionMax"]
        or (close < sma200 and roc_short < 0.0)
        or (
            close < sma50
            and distribution_count >= max(params["market_rs"]["marketDistributionMax"] - 1, 1)
        )
    )
    score = 0.0
    score += 2.5 if close > sma200 else 0.0
    score += 2.0 if close > sma50 else 0.0
    score += 2.0 if sma50 > sma200 else 0.0
    score += 1.5 if sma50_slope > 0.0 else 0.0
    score += 1.0 if roc_short > 0.0 else 0.0
    score += 1.0 if roc_medium > 0.0 else 0.0
    score -= params["market_rs"]["marketRiskOffPenalty"] if market_risk_off else 0.0
    score = clamp_score(score)
    if market_risk_off:
        regime = MARKET_RISK_OFF
    elif score >= 7:
        regime = MARKET_BULLISH
    elif score >= 4.5:
        regime = MARKET_MIXED
    else:
        regime = MARKET_BEARISH
    gate_ok = not params["market_rs"]["useMarketFilter"] or (
        score >= params["market_rs"]["marketMinScore"] and not market_risk_off
    )
    return {"score": score, "risk_off": market_risk_off, "regime": regime, "gate_ok": gate_ok}


def _num(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    return bool(value) if value is not None else False
