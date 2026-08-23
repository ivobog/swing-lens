from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

CALIBRATION_SCHEMA_VERSION = "technical-v5-shadow-calibration-v1"

CALIBRATION_COLUMNS = (
    "run_id",
    "ticker",
    "decision_date",
    "v4_engine_version",
    "technical_engine_version",
    "v5_config_hash",
    "input_signature",
    "v4_score",
    "v4_classification",
    "v4_action",
    "v5_TS",
    "v5_SQ",
    "v5_EQ",
    "v5_TCS",
    "v5_confidence_adjusted",
    "v5_classification",
    "v5_action",
    "setup_type",
    "market_regime",
    "sector",
    "sector_benchmark_symbol",
    "stage",
    "stage_modifier",
    "technical_confidence",
    "data_quality_score",
    "trend_quality",
    "momentum_quality",
    "leadership_quality",
    "residual_momentum_score",
    "trigger_quality",
    "trigger_state",
    "trigger_distance_atr",
    "base_risk",
    "climax_risk",
    "combined_risk",
    "risk_control",
    "execution_quality",
    "stop_distance_atr",
    "reward_risk",
    "target_source",
    "danger_state",
    "danger_cap",
    "warning_flags",
    "missing_evidence",
    "explainability_reasons",
    "liquidity_bucket",
    "atr_percentile_bucket",
    "close_1d",
    "close_3d",
    "close_5d",
    "close_10d",
    "max_high_5d",
    "min_low_5d",
    "max_high_10d",
    "min_low_10d",
    "forward_return_5d",
    "forward_return_10d",
    "MFE_5d",
    "MAE_5d",
    "MFE_10d",
    "MAE_10d",
    "target_hit_5d",
    "stop_hit_5d",
    "target_hit_10d",
    "stop_hit_10d",
    "first_hit_5d",
    "first_hit_10d",
    "time_to_target",
    "time_to_stop",
    "outcome_price_basis",
    "stop_target_outcome_status",
    "reconstruction_status",
    "reconstruction_reason",
)

HISTORICAL_COMPARISON_COLUMNS = CALIBRATION_COLUMNS

SUPPORTED_ABLATIONS = (
    "A0_baseline_v5",
    "A1_no_leadership",
    "A2_no_residual_momentum",
    "A3_no_stage_modifier",
    "A4_no_htf",
    "A5_no_trigger_quality",
    "A6_no_climax_risk",
    "A7_old_max_setup_logic",
    "A8_no_confidence_adjustment",
    "A9_fixed_regime_weights",
    "A10_no_momentum_acceleration",
    "A11_no_sector_rs",
    "A12_no_danger_caps",
    "A13_no_roc126",
    "A14_no_benchmark_rs",
)


@dataclass(frozen=True)
class SequenceOutcome:
    target_hit: bool | None
    stop_hit: bool | None
    first_hit: str | None
    time_to_target: int | None
    time_to_stop: int | None


def historical_comparison_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return the stable calibration schema without tuning or outcome imputation."""
    return [{column: row.get(column) for column in CALIBRATION_COLUMNS} for row in rows]


def construct_forward_outcomes(
    *,
    decision_date: date,
    entry_price: float,
    future_bars: pd.DataFrame,
    stop_price: float | None,
    target_price: float | None,
    horizons: Sequence[int] = (5, 10),
) -> dict[str, Any]:
    """Build outcomes from bars strictly after scoring, preserving same-bar ambiguity."""
    bars = future_bars.copy()
    if bars.empty:
        return _empty_forward_outcomes(horizons)
    bars["date"] = pd.to_datetime(bars["date"]).dt.date
    if any(bar_date <= decision_date for bar_date in bars["date"]):
        raise ValueError("forward outcome bars must be strictly after decision_date")
    bars = bars.sort_values("date").reset_index(drop=True)
    result: dict[str, Any] = {}
    for horizon in horizons:
        window = bars.head(int(horizon))
        suffix = f"{horizon}d"
        if len(window) < horizon:
            result.update(
                {
                    f"{prefix}_{suffix}": None
                    for prefix in (
                        "close",
                        "max_high",
                        "min_low",
                        "forward_return",
                        "MFE",
                        "MAE",
                        "target_hit",
                        "stop_hit",
                        "first_hit",
                    )
                }
            )
            continue
        last_close = float(window.iloc[-1]["close"])
        max_high = float(pd.to_numeric(window["high"], errors="coerce").max())
        min_low = float(pd.to_numeric(window["low"], errors="coerce").min())
        sequence = stop_target_sequence(window, stop_price=stop_price, target_price=target_price)
        result.update(
            {
                f"close_{suffix}": last_close,
                f"max_high_{suffix}": max_high,
                f"min_low_{suffix}": min_low,
                f"forward_return_{suffix}": _pct(last_close, entry_price),
                f"MFE_{suffix}": _pct(max_high, entry_price),
                f"MAE_{suffix}": _pct(min_low, entry_price),
                f"target_hit_{suffix}": sequence.target_hit,
                f"stop_hit_{suffix}": sequence.stop_hit,
                f"first_hit_{suffix}": sequence.first_hit,
            }
        )
        if horizon == max(horizons):
            result["time_to_target"] = sequence.time_to_target
            result["time_to_stop"] = sequence.time_to_stop
    return result


def stop_target_sequence(
    bars: pd.DataFrame,
    *,
    stop_price: float | None,
    target_price: float | None,
) -> SequenceOutcome:
    if stop_price is None and target_price is None:
        return SequenceOutcome(None, None, None, None, None)
    target_hit = False if target_price is not None else None
    stop_hit = False if stop_price is not None else None
    first_hit: str | None = None
    target_at: int | None = None
    stop_at: int | None = None
    for session, row in enumerate(bars.itertuples(index=False), start=1):
        hit_target = target_price is not None and float(row.high) >= target_price
        hit_stop = stop_price is not None and float(row.low) <= stop_price
        if hit_target and target_at is None:
            target_at, target_hit = session, True
        if hit_stop and stop_at is None:
            stop_at, stop_hit = session, True
        if first_hit is None and (hit_target or hit_stop):
            first_hit = (
                "AMBIGUOUS" if hit_target and hit_stop else "TARGET" if hit_target else "STOP"
            )
    return SequenceOutcome(target_hit, stop_hit, first_hit, target_at, stop_at)


def time_ordered_split_labels(
    decision_dates: Iterable[date | str],
    *,
    calibration_fraction: float = 0.60,
    validation_fraction: float = 0.20,
) -> list[str]:
    values = [pd.Timestamp(value).date() for value in decision_dates]
    unique_dates = sorted(set(values))
    if len(unique_dates) < 3:
        raise ValueError("walk-forward validation requires at least three decision dates")
    calibration_end = max(1, int(len(unique_dates) * calibration_fraction))
    validation_end = max(
        calibration_end + 1,
        int(len(unique_dates) * (calibration_fraction + validation_fraction)),
    )
    validation_end = min(validation_end, len(unique_dates) - 1)
    calibration_dates = set(unique_dates[:calibration_end])
    validation_dates = set(unique_dates[calibration_end:validation_end])
    return [
        "calibration"
        if value in calibration_dates
        else "validation"
        if value in validation_dates
        else "holdout"
        for value in values
    ]


def component_ablation_record(
    *,
    ticker: str,
    baseline_tcs: float,
    variants: dict[str, float],
) -> dict[str, Any]:
    unsupported = sorted(set(variants) - set(SUPPORTED_ABLATIONS))
    if unsupported:
        raise ValueError(f"Unsupported v5 ablations: {', '.join(unsupported)}")
    return {
        "ticker": ticker.upper(),
        "baseline_tcs": round(float(baseline_tcs), 4),
        "ablations": {
            name: {
                "score": round(float(score), 4),
                "delta": round(float(score) - float(baseline_tcs), 4),
            }
            for name, score in sorted(variants.items())
        },
    }


def evidence_strength(sample_n: int) -> str:
    if sample_n < 30:
        return "DESCRIPTIVE_ONLY"
    if sample_n < 100:
        return "WEAK"
    if sample_n < 500:
        return "USEFUL_BUT_NOISY"
    return "STRONGER_AGGREGATE"


def _empty_forward_outcomes(horizons: Sequence[int]) -> dict[str, Any]:
    result: dict[str, Any] = {"time_to_target": None, "time_to_stop": None}
    for horizon in horizons:
        for prefix in (
            "close",
            "max_high",
            "min_low",
            "forward_return",
            "MFE",
            "MAE",
            "target_hit",
            "stop_hit",
            "first_hit",
        ):
            result[f"{prefix}_{horizon}d"] = None
    return result


def _pct(value: float, entry: float) -> float | None:
    return None if entry <= 0 else round((float(value) / float(entry) - 1.0) * 100.0, 6)
