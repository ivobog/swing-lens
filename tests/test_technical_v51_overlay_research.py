from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from app.services.technical_v5_calibration import time_ordered_split_labels
from app.services.technical_v51_overlay_research import (
    add_extension_states,
    build_candidate_rankings,
    candidate_evaluation,
    classify_extension_row,
    load_research_config,
    normalize_action,
    normalize_trigger_state,
    stable_hash,
    validate_research_frame,
)


def test_extension_state_is_deterministic_and_uses_documented_override() -> None:
    config = load_research_config()
    thresholds = config["extension_threshold_sets"]["balanced_v1"]
    inputs = {
        "ema20_extension_pct": 3.0,
        "sma50_extension_pct": 6.0,
        "ema20_extension_atr": 0.8,
        "sma50_extension_atr": 1.8,
        "extension_percentile": 45.0,
        "rsi": 58.0,
        "roc10": 3.0,
        "roc21": 5.0,
        "climax_risk": 0.0,
        "trigger_distance_atr": 0.2,
        "stage": "Stage 2",
    }

    first = classify_extension_row(inputs, thresholds, threshold_set_id="balanced_v1")
    second = classify_extension_row(
        dict(reversed(list(inputs.items()))),
        thresholds,
        threshold_set_id="balanced_v1",
    )
    stage_four = classify_extension_row(
        {**inputs, "stage": "Stage 4"}, thresholds, threshold_set_id="balanced_v1"
    )

    assert first == second
    assert first[0] == "HEALTHY"
    assert stage_four[0] == "EXTREME"


def test_historical_extension_reconstruction_ignores_future_outcomes() -> None:
    config = load_research_config()
    frame = validate_research_frame(_frame())
    first = add_extension_states(frame, config)
    changed = frame.copy()
    changed["forward_return_5d"] = -changed.forward_return_5d * 100
    changed["MFE_10d"] = 9999.0
    second = add_extension_states(changed, config)

    pd.testing.assert_frame_equal(
        first[["run_id", "ticker", "extension_state", "extension_maturity_score"]],
        second[["run_id", "ticker", "extension_state", "extension_maturity_score"]],
    )


def test_historical_trigger_reconstruction_accepts_only_canonical_states() -> None:
    assert normalize_trigger_state("freshly triggered") == "FRESHLY_TRIGGERED"
    assert normalize_trigger_state("extended-beyond-trigger") == "EXTENDED_BEYOND_TRIGGER"
    assert normalize_trigger_state("invented") is None

    bad = _frame()
    bad.loc[0, "trigger_state"] = "invented"
    try:
        validate_research_frame(bad)
    except ValueError as exc:
        assert "unknown historical trigger states" in str(exc)
    else:
        raise AssertionError("unknown trigger history must fail closed")


def test_candidate_policy_is_deterministic_and_has_no_future_data_leakage() -> None:
    config = load_research_config()
    source = add_extension_states(validate_research_frame(_frame()), config)
    config_digest = stable_hash(config)
    first = build_candidate_rankings(
        source, config, candidate_config_hash=config_digest, git_commit="abc123"
    )
    changed = source.copy()
    for column in (
        "forward_return_1d",
        "forward_return_3d",
        "forward_return_5d",
        "forward_return_10d",
    ):
        changed[column] = changed[column] * -500
    second = build_candidate_rankings(
        changed, config, candidate_config_hash=config_digest, git_commit="abc123"
    )
    identity = [
        "candidate_id",
        "danger_variant",
        "run_id",
        "ticker",
        "candidate_rank",
        "candidate_rank_pct",
    ]

    pd.testing.assert_frame_equal(first[identity], second[identity])
    assert set(first.candidate_id) == {
        "M0_V4",
        "M1A_V4_FILTER_EXTREME",
        "M1B_V4_FILTER_EXTENDED_EXTREME",
        "M1C_V4_EXTENSION_SECONDARY",
        "M2_V4_TRIGGER",
        "M3_V4_EXTENSION_TRIGGER",
        "M4_TS_GATE_6.0_V4",
        "M4_TS_GATE_6.5_V4",
        "M4_TS_GATE_7.0_V4",
        "V5_BASELINE",
    }


def test_candidate_comparison_is_reproducible_and_walk_forward_is_ordered() -> None:
    config = load_research_config()
    source = add_extension_states(validate_research_frame(_frame()), config)
    rankings = build_candidate_rankings(
        source, config, candidate_config_hash=stable_hash(config), git_commit="abc123"
    )
    first, first_top, first_walk = candidate_evaluation(
        rankings, source, bootstrap_samples=20
    )
    second, second_top, second_walk = candidate_evaluation(
        rankings, source, bootstrap_samples=20
    )

    pd.testing.assert_frame_equal(first, second)
    pd.testing.assert_frame_equal(first_top, second_top)
    pd.testing.assert_frame_equal(first_walk, second_walk)
    labels = time_ordered_split_labels(source.decision_date)
    dates_by_split = {
        split: set(source.loc[pd.Series(labels, index=source.index).eq(split), "decision_date"])
        for split in ("calibration", "validation", "holdout")
    }
    assert max(dates_by_split["calibration"]) < min(dates_by_split["validation"])
    assert max(dates_by_split["validation"]) < min(dates_by_split["holdout"])


def test_canonical_action_normalization_uses_required_vocabulary() -> None:
    cases = {
        "Buyable, R/R ok": "ENTER",
        "Watch / trail / smaller size": "DEFENSIVE",
        "Do not chase, wait mini-pullback": "WAIT_FOR_TRIGGER",
        "Avoid / exit risk": "AVOID",
        "No clear trade": "NO_TRADE",
        "FILTERED": "FILTERED",
    }

    assert {value: normalize_action(value) for value in cases} == cases


def _frame() -> pd.DataFrame:
    rows = []
    trigger_states = [
        "approaching",
        "near",
        "at_trigger",
        "freshly_triggered",
        "beyond_trigger",
        "extended_beyond_trigger",
        "invalidated",
        "too_far_below",
        "not_applicable",
        "freshly_triggered",
    ]
    for date_index in range(5):
        decision_date = date(2026, 1, 2) + timedelta(days=date_index)
        for ticker_index in range(10):
            score = 5.5 + ticker_index * 0.4
            extension = float(ticker_index)
            rows.append(
                {
                    "run_id": date_index + 1,
                    "ticker": f"T{ticker_index}",
                    "decision_date": decision_date,
                    "input_signature": f"sig-{date_index}-{ticker_index}",
                    "reconstruction_status": "REVISION_HISTORY_RECONCILED",
                    "v5_config_hash": "frozen",
                    "v4_score": score,
                    "v4_action": "Buyable, R/R ok" if ticker_index > 7 else "No clear trade",
                    "v5_TS": 5.0 + ticker_index * 0.5,
                    "v5_SQ": score,
                    "v5_EQ": 9.0 - ticker_index * 0.2,
                    "v5_TCS": 8.5 - ticker_index * 0.25,
                    "trigger_state": trigger_states[ticker_index],
                    "trigger_distance_atr": 1.5 - ticker_index * 0.4,
                    "danger_state": "Failed breakout" if ticker_index == 0 else None,
                    "setup_type": "breakout" if ticker_index % 2 else "pullback",
                    "market_regime": "Bull trend",
                    "sector": "Technology",
                    "stage": "Stage 2",
                    "ema20_extension_pct": extension,
                    "sma50_extension_pct": extension * 2,
                    "ema20_extension_atr": extension / 3,
                    "sma50_extension_atr": extension / 2,
                    "extension_percentile": extension * 10,
                    "rsi": 55 + extension * 2,
                    "roc10": extension * 1.5,
                    "roc21": extension * 2,
                    "climax_risk": extension / 2,
                    "volume_confirmation": ticker_index % 2 == 0,
                    "strong_close_ratio": 0.4 + ticker_index * 0.05,
                    "breakout_volume_confirmed": ticker_index % 3 == 0,
                    "breakout_volume_percentile": 40 + ticker_index * 5,
                    "gap_up_pct": ticker_index / 3,
                    "gap_exhaustion": ticker_index == 9,
                    "forward_return_1d": score / 10,
                    "forward_return_3d": score / 8,
                    "forward_return_5d": score / 6,
                    "forward_return_10d": score / 4,
                    "MFE_1d": score / 9,
                    "MFE_3d": score / 7,
                    "MFE_5d": score / 5,
                    "MFE_10d": score / 3,
                    "MAE_1d": -score / 20,
                    "MAE_3d": -score / 18,
                    "MAE_5d": -score / 16,
                    "MAE_10d": -score / 14,
                    "time_to_MFE_1d": 1,
                    "time_to_MFE_3d": 2,
                    "time_to_MFE_5d": 3,
                    "time_to_MFE_10d": 4,
                    "first_hit_10d": "TARGET",
                    "time_to_target": 3,
                }
            )
    return pd.DataFrame(rows)
