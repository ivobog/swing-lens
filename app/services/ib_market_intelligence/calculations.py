from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from datetime import date, datetime
from typing import Any

from app.services.ib_market_intelligence.dtos import FeatureResult, HistogramLevel
from app.services.ib_market_intelligence.enums import AvailabilityStatus, Confidence


def finite_number(value: Any, *, positive: bool = False, nonnegative: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if positive and number <= 0:
        return None
    if nonnegative and number < 0:
        return None
    return number


def safe_ratio(numerator: Any, denominator: Any) -> float | None:
    left = finite_number(numerator, nonnegative=True)
    right = finite_number(denominator, positive=True)
    return left / right if left is not None and right is not None else None


def empirical_percentile(values: Sequence[float], current: float) -> float | None:
    clean = sorted(value for raw in values if (value := finite_number(raw)) is not None)
    if not clean:
        return None
    below = sum(value < current for value in clean)
    equal = sum(value == current for value in clean)
    return 100.0 * (below + 0.5 * equal) / len(clean)


def calculate_liquidity(
    bars: Sequence[Any],
    *,
    as_of: date,
    dollar_volume: float | None = None,
    config: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> FeatureResult:
    config = config or {}
    thresholds = config.get("spread_grade_pct") or {}
    valid: list[dict[str, float | date | str]] = []
    rejected = 0
    for bar in bars:
        bid = finite_number(_field(bar, "open_value"), positive=True)
        ask = finite_number(_field(bar, "close_value"), positive=True)
        if bid is None or ask is None or ask < bid:
            rejected += 1
            continue
        midpoint = (bid + ask) / 2
        if midpoint <= 0:
            rejected += 1
            continue
        spread_amount = ask - bid
        valid.append(
            {
                "date": _field(bar, "session_date") or as_of,
                "bid": bid,
                "ask": ask,
                "midpoint": midpoint,
                "spread_amount": spread_amount,
                "spread_pct": 100.0 * spread_amount / midpoint,
                "evidence_hash": str(_field(bar, "data_hash") or ""),
            }
        )
    valid.sort(key=lambda item: item["date"])
    if not valid:
        return FeatureResult(
            module="LIQUIDITY",
            classification="INSUFFICIENT",
            score=None,
            confidence=Confidence.INSUFFICIENT,
            freshness_status=AvailabilityStatus.UNAVAILABLE,
            coverage_status=AvailabilityStatus.UNAVAILABLE,
            components={"valid_bars": 0, "rejected_bars": rejected},
            reasons=("SPREAD_UNAVAILABLE",),
            warnings=("BID_ASK_DATA_UNAVAILABLE",),
        )
    latest = valid[-1]
    short_window = int(config.get("short_window_sessions", 5))
    lookback = int(config.get("lookback_sessions", 20))
    percentile_window = int(config.get("percentile_window_sessions", 60))
    spreads = [float(row["spread_pct"]) for row in valid]
    spread_5d = statistics.median(spreads[-short_window:])
    spread_20d = statistics.median(spreads[-lookback:])
    percentile = empirical_percentile(spreads[-percentile_window:], float(latest["spread_pct"]))
    spread_stability = (
        statistics.pstdev(spreads[-lookback:]) if len(spreads[-lookback:]) > 1 else 0.0
    )
    grade = _liquidity_grade(spread_20d, thresholds)
    minimum_dollar_volume = (
        finite_number(config.get("minimum_dollar_volume"), nonnegative=True) or 0
    )
    reasons = ["TIGHT_SPREAD" if grade in {"EXCELLENT", "GOOD"} else "WIDE_SPREAD"]
    warnings: list[str] = []
    if dollar_volume is not None and dollar_volume < minimum_dollar_volume:
        reasons.append("LOW_DOLLAR_VOLUME")
        warnings.append("TRADEABILITY_LOW_DOLLAR_VOLUME")
        grade = _worse_liquidity_grade(grade)
    freshness, stale = _historical_freshness(latest["date"], as_of, config)
    if stale:
        reasons.append("STALE_BID_ASK")
        warnings.append("STALE_BID_ASK")
    grade_score = {"EXCELLENT": 10, "GOOD": 8, "ACCEPTABLE": 6, "POOR": 3, "VERY_POOR": 1}
    return FeatureResult(
        module="LIQUIDITY",
        classification=grade,
        score=float(grade_score[grade]),
        confidence=Confidence.HIGH if len(valid) >= lookback and not stale else Confidence.LOW,
        freshness_status=freshness,
        coverage_status=AvailabilityStatus.AVAILABLE,
        components={
            "representative_bid": latest["bid"],
            "representative_ask": latest["ask"],
            "midpoint": latest["midpoint"],
            "spread_amount": latest["spread_amount"],
            "spread_pct": latest["spread_pct"],
            "median_spread_5d": spread_5d,
            "median_spread_20d": spread_20d,
            "spread_percentile_60d": percentile,
            "spread_stability": spread_stability,
            "dollar_volume": dollar_volume,
            "valid_bars": len(valid),
            "rejected_bars": rejected,
        },
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        evidence_hashes=tuple(str(row["evidence_hash"]) for row in valid if row["evidence_hash"]),
    )


def calculate_short_pressure(
    fee_bars: Sequence[Any],
    *,
    as_of: date,
    shortable_shares: float | None = None,
    shortable_state: str | None = None,
    availability_status: str = AvailabilityStatus.AVAILABLE,
    config: dict[str, Any] | None = None,
) -> FeatureResult:
    config = config or {}
    fees = [
        (value, _field(bar, "session_date"), str(_field(bar, "data_hash") or ""))
        for bar in fee_bars
        if (value := finite_number(_field(bar, "close_value"), nonnegative=True)) is not None
    ]
    fees.sort(key=lambda item: item[1] or date.min)
    current = fees[-1][0] if fees else None
    change_5d = current - fees[-6][0] if current is not None and len(fees) >= 6 else None
    change_20d = current - fees[-21][0] if current is not None and len(fees) >= 21 else None
    acceleration = (
        change_5d - change_20d / 4 if change_5d is not None and change_20d is not None else None
    )
    high_fee = float(config.get("high_fee_rate_pct", 10))
    extreme_fee = float(config.get("extreme_fee_rate_pct", 25))
    low_shares = float(config.get("low_availability_shares", 100_000))
    very_low_shares = float(config.get("very_low_availability_shares", 25_000))
    components: dict[str, float] = {}
    max_available = 0.0
    if current is not None:
        fee_component = (
            5.0
            if current >= extreme_fee
            else 3.5
            if current >= high_fee
            else 1.0
            if current > 0
            else 0.0
        )
        components["fee_level_component"] = fee_component
        max_available += 5
    if acceleration is not None:
        accel_component = 2.0 if acceleration >= 5 else 1.0 if acceleration > 0 else 0.0
        components["fee_acceleration_component"] = accel_component
        max_available += 2
    shares = finite_number(shortable_shares, nonnegative=True)
    normalized_state = (shortable_state or "").upper()
    if availability_status == AvailabilityStatus.AVAILABLE and (
        shares is not None or normalized_state
    ):
        if normalized_state in {"NOT_SHORTABLE", "NO", "0"}:
            availability_component = 3.0
        elif shares is not None and shares <= very_low_shares:
            availability_component = 3.0
        elif shares is not None and shares <= low_shares:
            availability_component = 2.0
        else:
            availability_component = 0.0
        components["availability_component"] = availability_component
        max_available += 3
    raw = sum(components.values())
    score = 10.0 * raw / max_available if max_available else None
    if normalized_state in {"NOT_SHORTABLE", "NO", "0"}:
        classification = "NOT_SHORTABLE"
    elif current is not None and current >= extreme_fee:
        classification = "EXTREME_BORROW_COST"
    elif current is not None and current >= high_fee:
        classification = "HIGH_BORROW_COST"
    elif score is not None and score >= 5:
        classification = "LOCATE_MAY_BE_REQUIRED"
    elif score is not None:
        classification = "EASY_TO_BORROW"
    else:
        classification = "INSUFFICIENT"
    reasons: list[str] = []
    if current is None:
        reasons.append("FEE_RATE_UNAVAILABLE")
    elif current >= high_fee:
        reasons.append("BORROW_FEE_ELEVATED")
    if acceleration is not None and acceleration > 0:
        reasons.append("BORROW_FEE_RISING")
    if shares is not None and shares <= low_shares:
        reasons.append("SHORTABLE_SHARES_TIGHT")
    warnings = ["NOT_OFFICIAL_SHORT_INTEREST"]
    if availability_status != AvailabilityStatus.AVAILABLE:
        warnings.append(
            "SHORTABLE_DATA_STALE"
            if availability_status == AvailabilityStatus.STALE
            else "SHORTABLE_DATA_UNAVAILABLE"
        )
    fee_freshness, fee_stale = (
        _historical_freshness(fees[-1][1], as_of, config)
        if fees
        else (AvailabilityStatus.UNAVAILABLE, False)
    )
    if fee_stale:
        warnings.append("BORROW_DATA_STALE")
    freshness = (
        AvailabilityStatus.STALE
        if fee_stale or availability_status == AvailabilityStatus.STALE
        else AvailabilityStatus.AVAILABLE
        if fees or shares is not None
        else availability_status
    )
    return FeatureResult(
        module="SHORT_PRESSURE",
        classification=classification,
        score=score,
        confidence=Confidence.NORMAL
        if len(components) >= 2 and freshness != AvailabilityStatus.STALE
        else Confidence.LOW
        if components
        else Confidence.INSUFFICIENT,
        freshness_status=freshness,
        coverage_status=AvailabilityStatus.AVAILABLE if components else availability_status,
        components={
            "fee_rate": current,
            "fee_change_5d": change_5d,
            "fee_change_20d": change_20d,
            "fee_acceleration": acceleration,
            "shortable_shares": shares,
            "shortable_state": shortable_state,
            **components,
        },
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        evidence_hashes=tuple(item[2] for item in fees if item[2]),
    )


def calculate_volatility(
    hv_bars: Sequence[Any],
    iv_bars: Sequence[Any],
    *,
    as_of: date,
    config: dict[str, Any] | None = None,
    iv_availability: str = AvailabilityStatus.AVAILABLE,
) -> FeatureResult:
    config = config or {}
    hv = _metric_values(hv_bars)
    iv = _metric_values(iv_bars)
    current_hv = hv[-1][0] if hv else None
    current_iv = iv[-1][0] if iv else None
    ratio = safe_ratio(current_iv, current_hv)
    premium = current_iv - current_hv if current_iv is not None and current_hv is not None else None
    window = int(config.get("iv_expansion_window", 20))
    iv_change = (
        current_iv - iv[-(window + 1)][0] if current_iv is not None and len(iv) > window else None
    )
    rank_window = int(config.get("lookback_sessions", 252))
    iv_rank = (
        empirical_percentile([item[0] for item in iv[-rank_window:]], current_iv)
        if current_iv is not None
        else None
    )
    high_ratio = float(config.get("iv_hv_high_ratio", 1.5))
    extreme_ratio = float(config.get("iv_hv_extreme_ratio", 2.0))
    if current_iv is None:
        classification = "INSUFFICIENT"
    elif ratio is not None and ratio >= extreme_ratio:
        classification = "EXTREME_IV_PREMIUM"
    elif ratio is not None and ratio >= high_ratio:
        classification = "ELEVATED_IV_PREMIUM"
    elif iv_change is not None and iv_change > 0:
        classification = "IV_EXPANDING"
    elif iv_change is not None and iv_change < 0:
        classification = "IV_CONTRACTING"
    else:
        classification = "IV_NORMAL"
    reasons: list[str] = []
    if current_iv is None:
        reasons.append("IV_UNAVAILABLE")
    elif ratio is not None and ratio >= high_ratio:
        reasons.append("IV_PREMIUM_HIGH")
    elif ratio is not None:
        reasons.append("IV_NORMAL")
    if iv_change is not None:
        reasons.append(
            "IV_EXPANDING" if iv_change > 0 else "IV_CONTRACTING" if iv_change < 0 else "IV_STABLE"
        )
    warnings: list[str] = []
    if current_hv is None or current_hv <= 0:
        warnings.append("HV_UNAVAILABLE_OR_ZERO")
    if iv_availability != AvailabilityStatus.AVAILABLE:
        warnings.append(
            "OPTIONS_SUBSCRIPTION_REQUIRED"
            if iv_availability == AvailabilityStatus.SUBSCRIPTION_REQUIRED
            else "IV_UNAVAILABLE"
        )
    freshness_candidates = [item[1] for item in (*hv, *iv)]
    stale = any(
        _historical_freshness(last_date, as_of, config)[1] for last_date in freshness_candidates
    )
    if stale:
        warnings.append("VOLATILITY_DATA_STALE")
    freshness = (
        AvailabilityStatus.STALE
        if stale
        else AvailabilityStatus.AVAILABLE
        if hv or iv
        else AvailabilityStatus.UNAVAILABLE
    )
    score = min(10.0, max(0.0, (ratio - 1.0) * 5.0)) if ratio is not None else None
    return FeatureResult(
        module="VOLATILITY",
        classification=classification,
        score=score,
        confidence=Confidence.HIGH
        if current_hv is not None and current_iv is not None and len(iv) >= window and not stale
        else Confidence.LOW
        if current_iv is not None
        else Confidence.INSUFFICIENT,
        freshness_status=freshness,
        coverage_status=AvailabilityStatus.AVAILABLE
        if current_hv is not None and current_iv is not None
        else iv_availability
        if current_iv is None
        else AvailabilityStatus.UNAVAILABLE,
        components={
            "historical_volatility": current_hv,
            "implied_volatility": current_iv,
            "iv_hv_ratio": ratio,
            "iv_premium": premium,
            "iv_20d_change": iv_change,
            "iv_rank_252": iv_rank,
        },
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        evidence_hashes=tuple(item[2] for item in (*hv, *iv) if item[2]),
    )


def options_event_premium_score(feature: FeatureResult, *, maximum: float = 1.5) -> float:
    ratio = finite_number(feature.components.get("iv_hv_ratio"), nonnegative=True)
    change = finite_number(feature.components.get("iv_20d_change"))
    rank = finite_number(feature.components.get("iv_rank_252"), nonnegative=True)
    if ratio is None:
        return 0.0
    if ratio < 1.25 and (change is None or change <= 0):
        return 0.0
    if ratio < 1.5:
        base = 0.25
    elif ratio < 2.0:
        base = 0.75
    else:
        base = maximum
    if rank is not None and rank >= 90 and change is not None and change > 0:
        base = max(base, maximum)
    return min(maximum, base)


def calculate_options_activity(
    values: dict[str, Any],
    *,
    availability_status: str = AvailabilityStatus.AVAILABLE,
    config: dict[str, Any] | None = None,
) -> FeatureResult:
    config = config or {}
    call_volume = finite_number(values.get("call_volume"), nonnegative=True)
    put_volume = finite_number(values.get("put_volume"), nonnegative=True)
    call_oi = finite_number(values.get("call_open_interest"), nonnegative=True)
    put_oi = finite_number(values.get("put_open_interest"), nonnegative=True)
    average_volume = finite_number(values.get("average_option_volume"), positive=True)
    volume_ratio = safe_ratio(put_volume, call_volume)
    oi_ratio = safe_ratio(put_oi, call_oi)
    total_volume = (
        call_volume + put_volume if call_volume is not None and put_volume is not None else None
    )
    activity_multiple = safe_ratio(total_volume, average_volume)
    abnormal = float(config.get("abnormal_activity_multiple", 2.0))
    if activity_multiple is not None and activity_multiple >= abnormal:
        classification = "ABNORMAL_OPTION_ACTIVITY"
    elif volume_ratio is not None and volume_ratio <= float(
        config.get("call_heavy_ratio_max", 0.7)
    ):
        classification = "CALL_HEAVY"
    elif volume_ratio is not None and volume_ratio >= float(config.get("put_heavy_ratio_min", 1.3)):
        classification = "PUT_HEAVY"
    elif volume_ratio is not None:
        classification = "BALANCED"
    else:
        classification = "INSUFFICIENT"
    warnings: list[str] = []
    if availability_status != AvailabilityStatus.AVAILABLE:
        warnings.append(
            "OPTIONS_ACTIVITY_SUBSCRIPTION_REQUIRED"
            if availability_status == AvailabilityStatus.SUBSCRIPTION_REQUIRED
            else "OPTIONS_ACTIVITY_UNAVAILABLE"
        )
    reasons = (
        [classification] if classification != "INSUFFICIENT" else ["OPTIONS_ACTIVITY_UNAVAILABLE"]
    )
    score = min(10.0, activity_multiple * 3.0) if activity_multiple is not None else None
    return FeatureResult(
        module="OPTIONS_ACTIVITY",
        classification=classification,
        score=score,
        confidence=Confidence.HIGH
        if all(value is not None for value in (call_volume, put_volume, call_oi, put_oi))
        else Confidence.LOW
        if any(value is not None for value in (call_volume, put_volume, call_oi, put_oi))
        else Confidence.INSUFFICIENT,
        freshness_status=availability_status,
        coverage_status=availability_status,
        components={
            "call_volume": call_volume,
            "put_volume": put_volume,
            "call_open_interest": call_oi,
            "put_open_interest": put_oi,
            "average_option_volume": average_volume,
            "put_call_volume_ratio": volume_ratio,
            "put_call_oi_ratio": oi_ratio,
            "option_activity_multiple": activity_multiple,
        },
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


def calculate_histogram(
    levels: Iterable[HistogramLevel | tuple[float, float] | Any],
    *,
    reference_price: float | None,
    config: dict[str, Any] | None = None,
) -> FeatureResult:
    config = config or {}
    clean: list[tuple[float, float]] = []
    for level in levels:
        raw_price = level[0] if isinstance(level, tuple) else _field(level, "price")
        raw_count = (
            level[1]
            if isinstance(level, tuple)
            else (
                _field(level, "activity_count")
                if _field(level, "activity_count") is not None
                else _field(level, "size")
            )
        )
        price = finite_number(raw_price, positive=True)
        count = finite_number(raw_count, nonnegative=True)
        if price is not None and count is not None:
            clean.append((price, count))
    clean.sort()
    total = sum(count for _, count in clean)
    if len(clean) < 2 or total <= 0:
        return FeatureResult(
            module="HISTOGRAM",
            classification="INSUFFICIENT",
            score=None,
            confidence=Confidence.INSUFFICIENT,
            freshness_status=AvailabilityStatus.UNAVAILABLE,
            coverage_status=AvailabilityStatus.UNAVAILABLE,
            components={
                "bin_count": len(clean),
                "source_semantics": "IBKR_HISTOGRAM_PRICE_LEVEL_ACTIVITY",
            },
            reasons=("HISTOGRAM_INSUFFICIENT",),
            warnings=("NOT_EXCHANGE_VOLUME_PROFILE",),
        )
    poc_price, poc_count = max(clean, key=lambda item: (item[1], -item[0]))
    fraction = min(1.0, max(0.01, float(config.get("high_activity_fraction", 0.70))))
    selected: list[tuple[float, float]] = []
    running = 0.0
    for level in sorted(clean, key=lambda item: (-item[1], item[0])):
        selected.append(level)
        running += level[1]
        if running / total >= fraction:
            break
    zone_low = min(price for price, _ in selected)
    zone_high = max(price for price, _ in selected)
    counts = [count for _, count in clean]
    cutoff = _quantile(counts, float(config.get("low_activity_percentile", 0.20)))
    low_activity = [price for price, count in clean if count <= cutoff]
    price = finite_number(reference_price, positive=True)
    support_candidates = [
        level_price for level_price, _ in clean if price is not None and level_price < price
    ]
    resistance_candidates = [
        level_price for level_price, _ in clean if price is not None and level_price > price
    ]
    nearest_support = max(support_candidates) if support_candidates else None
    nearest_resistance = min(resistance_candidates) if resistance_candidates else None
    if price is None:
        classification = "NEUTRAL"
    elif zone_low <= price <= zone_high:
        classification = "INSIDE_HIGH_ACTIVITY_ZONE"
    elif price > zone_high:
        classification = "ABOVE_DOMINANT_AREA"
    else:
        classification = "BELOW_DOMINANT_AREA"
    return FeatureResult(
        module="HISTOGRAM",
        classification=classification,
        score=None,
        confidence=Confidence.NORMAL if len(clean) >= 5 else Confidence.LOW,
        freshness_status=AvailabilityStatus.AVAILABLE,
        coverage_status=AvailabilityStatus.AVAILABLE,
        components={
            "poc_like_price": poc_price,
            "poc_like_activity_count": poc_count,
            "high_activity_zone_low": zone_low,
            "high_activity_zone_high": zone_high,
            "low_activity_prices": low_activity,
            "concentration": poc_count / total,
            "reference_price": price,
            "distance_to_poc_pct": (100 * (price - poc_price) / poc_price)
            if price is not None
            else None,
            "nearest_activity_support": nearest_support,
            "nearest_activity_resistance": nearest_resistance,
            "bin_count": len(clean),
            "source_semantics": "IBKR_HISTOGRAM_PRICE_LEVEL_ACTIVITY",
        },
        reasons=(classification,),
        warnings=("NOT_EXCHANGE_VOLUME_PROFILE",),
    )


def _field(value: Any, name: str) -> Any:
    if isinstance(value, dict):
        return value.get(name)
    return getattr(value, name, None)


def _metric_values(bars: Sequence[Any]) -> list[tuple[float, Any, str]]:
    values = [
        (value, _field(bar, "session_date"), str(_field(bar, "data_hash") or ""))
        for bar in bars
        if (value := finite_number(_field(bar, "close_value"), nonnegative=True)) is not None
    ]
    return sorted(values, key=lambda item: item[1] or date.min)


def _liquidity_grade(spread_pct: float, thresholds: dict[str, Any]) -> str:
    if spread_pct <= float(thresholds.get("excellent_max", 0.10)):
        return "EXCELLENT"
    if spread_pct <= float(thresholds.get("good_max", 0.25)):
        return "GOOD"
    if spread_pct <= float(thresholds.get("acceptable_max", 0.50)):
        return "ACCEPTABLE"
    if spread_pct <= float(thresholds.get("poor_max", 1.00)):
        return "POOR"
    return "VERY_POOR"


def _worse_liquidity_grade(grade: str) -> str:
    order = ["EXCELLENT", "GOOD", "ACCEPTABLE", "POOR", "VERY_POOR"]
    return order[min(len(order) - 1, order.index(grade) + 1)]


def _historical_freshness(last_date: Any, as_of: date, config: dict[str, Any]) -> tuple[str, bool]:
    if not isinstance(last_date, date):
        return AvailabilityStatus.UNKNOWN, True
    stale = (as_of - last_date).days > int(config.get("historical_max_age_days", 5))
    return (AvailabilityStatus.STALE if stale else AvailabilityStatus.AVAILABLE), stale


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return ordered[index]
