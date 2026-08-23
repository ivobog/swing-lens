from __future__ import annotations

import pandas as pd
import pytest

from app.services.sector_benchmark_service import SectorBenchmarkResolution
from app.services.technical_v5_forensics import (
    action_bucket,
    candidate_study,
    component_forensics,
    danger_cap_variants,
    setup_model_scores,
    time_split_labels,
    transition_matrix,
)
from scripts.run_technical_v5_shadow_evaluation import _sector_resolution_with_data


def _frame() -> pd.DataFrame:
    rows = []
    dates = pd.date_range("2026-01-01", periods=10, freq="D")
    for run_id, decision_date in enumerate(dates, start=1):
        for ticker_index in range(10):
            score = float(ticker_index + 1)
            rows.append(
                {
                    "run_id": run_id,
                    "ticker": f"T{ticker_index}",
                    "decision_date": decision_date.date(),
                    "v4_score": score,
                    "v5_TS": score,
                    "v5_SQ": score,
                    "v5_EQ": score,
                    "v5_TCS": score,
                    "technical_confidence": "high",
                    "market_regime": "Bull trend",
                    "forward_return_1d": score,
                    "forward_return_3d": score,
                    "forward_return_5d": score,
                    "forward_return_10d": score,
                    "MFE_1d": score + 1,
                    "MFE_3d": score + 1,
                    "MFE_5d": score + 1,
                    "MFE_10d": score + 1,
                    "MAE_1d": -1.0,
                    "MAE_3d": -1.0,
                    "MAE_5d": -1.0,
                    "MAE_10d": -1.0,
                    "first_hit_10d": "TARGET",
                    "time_to_target": 2,
                    "time_to_MFE_5d": 3,
                    "time_to_MFE_10d": 4,
                }
            )
    return pd.DataFrame(rows)


def test_component_forensics_has_deciles_top_selections_and_cluster_ci() -> None:
    result = component_forensics(_frame(), {"TS": "v5_TS"}, bootstrap_samples=20)

    assert set(result.analysis_kind) == {"OVERALL", "TOP_10_PCT", "TOP_20_PCT", "DECILE"}
    overall = result[result.analysis_kind == "OVERALL"].iloc[0]
    assert overall.spearman_5d == 1.0
    assert overall.decile_monotonicity_5d == pytest.approx(1.0)
    assert overall.bootstrap_clusters == 10


def test_setup_hybrid_applies_stage_once_and_old_max_is_distinct() -> None:
    frame = pd.DataFrame(
        [
            {
                "v5_SQ": 8.0,
                "base_setup_score": 7.0,
                "vcp_score": 9.0,
                "breakout_quality_score": 6.0,
                "setup_type": "pullback",
                "setup_primary": 8.0,
                "setup_confirmation_1": 6.0,
                "setup_confirmation_2": 4.0,
                "stage_modifier": 0.25,
            }
        ]
    )

    result = setup_model_scores(frame).iloc[0]

    assert result.SQ_OLD_MAX == 9.0
    assert result.SQ_HYBRID_PRIMARY_CONFIRMATION == 7.65


def test_danger_variants_separate_label_from_cap_strength() -> None:
    frame = pd.DataFrame(
        [
            {
                "risk_control": 10.0,
                "execution_quality": 10.0,
                "trigger_quality": 10.0,
                "danger_cap": 4.0,
                "v5_EQ": 4.0,
                "v5_TS": 8.0,
                "v5_SQ": 8.0,
                "market_regime": "Bull trend",
            }
        ]
    )

    result = danger_cap_variants(
        frame, {"bull trend": (0.45, 0.35, 0.20), "choppy": (0.35, 0.35, 0.30)}
    ).iloc[0]

    assert result.EQ_CURRENT_CAP == 4.0
    assert result.EQ_HALF_CAP == 7.0
    assert result.EQ_LABEL_ONLY == 10.0
    assert result.TCS_CURRENT_CAP < result.TCS_HALF_CAP < result.TCS_LABEL_ONLY


def test_candidate_study_uses_chronological_holdout_and_strength_gates() -> None:
    result = candidate_study(_frame())

    assert set(result.split) == {"calibration", "validation", "holdout"}
    holdout_dates = set(_frame().decision_date[-20:])
    labels = time_split_labels(_frame().decision_date)
    observed_holdout = set(_frame().loc[pd.Series(labels).eq("holdout"), "decision_date"])
    assert observed_holdout == holdout_dates
    gated = result[
        (result.candidate == "V5_STRENGTH_GATE_7.0")
        & (result.split == "calibration")
        & (result.selection == "ALL_ELIGIBLE")
    ].iloc[0]
    assert gated.eligible_N == 24


def test_transition_audit_distinguishes_wording_from_decision_semantics() -> None:
    frame = pd.DataFrame(
        [
            {
                "before": "Do not chase, wait mini-pullback",
                "after": "Setup candidate, wait for trigger",
                "before_bucket": "WAIT_CONFIRM",
                "after_bucket": "WAIT_CONFIRM",
                **_frame().iloc[0].to_dict(),
            }
        ]
    )

    result = transition_matrix(
        frame,
        "before",
        "after",
        source_bucket="before_bucket",
        target_bucket="after_bucket",
    ).iloc[0]

    assert action_bucket(frame.iloc[0].before) == "WAIT_CONFIRM"
    assert not bool(result.true_decision_change)


def test_shadow_reconstruction_marks_mapped_sector_without_bars_as_missing() -> None:
    resolution = SectorBenchmarkResolution("Technology", "XLK", "RESOLVED")

    corrected = _sector_resolution_with_data(resolution, {})

    assert corrected.status == "BENCHMARK_DATA_MISSING"
    assert corrected.benchmark_symbol == "XLK"
