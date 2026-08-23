from datetime import date, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.services.leadership_v5 import rank_leadership_v5
from app.services.technical_indicators import (
    _calculate_feature_frame,
    calculate_htf_trend_features,
    load_pine_defaults,
    prepare_ohlcv_frame,
)
from app.services.technical_v5_calibration import (
    CALIBRATION_COLUMNS,
    CALIBRATION_SCHEMA_VERSION,
    component_ablation_record,
    construct_forward_outcomes,
    historical_comparison_rows,
    stop_target_sequence,
    time_ordered_split_labels,
)
from scripts.run_technical_v5_shadow_evaluation import (
    _ablation_leadership_variants,
    _bars_as_of,
    _bootstrap_delta,
    _variant_configs,
)


def test_calibration_schema_is_stable_and_contains_required_columns() -> None:
    required = {
        "run_id",
        "ticker",
        "decision_date",
        "v4_score",
        "v4_classification",
        "v4_action",
        "v5_TS",
        "v5_SQ",
        "v5_EQ",
        "v5_TCS",
        "v5_confidence_adjusted",
        "forward_return_5d",
        "forward_return_10d",
        "MFE_5d",
        "MAE_5d",
        "MFE_10d",
        "MAE_10d",
    }
    assert CALIBRATION_SCHEMA_VERSION == "technical-v5-shadow-calibration-v1"
    assert required <= set(CALIBRATION_COLUMNS)
    assert tuple(historical_comparison_rows([{}])[0]) == CALIBRATION_COLUMNS


def test_forward_outcomes_reject_decision_or_prior_bars() -> None:
    bars = pd.DataFrame([{"date": "2026-08-01", "open": 100, "high": 101, "low": 99, "close": 100}])
    with pytest.raises(ValueError, match="strictly after"):
        construct_forward_outcomes(
            decision_date=date(2026, 8, 1),
            entry_price=100,
            future_bars=bars,
            stop_price=95,
            target_price=110,
            horizons=(1,),
        )


def test_as_of_bars_exclude_late_backfill_and_restore_later_revision() -> None:
    source = pd.DataFrame(
        [
            {
                "id": 1,
                "date": pd.Timestamp("2026-08-01"),
                "open": 50.0,
                "high": 51.0,
                "low": 49.0,
                "close": 50.0,
                "volume": 2_000.0,
                "first_seen_at": pd.Timestamp("2026-08-02", tz="UTC"),
            },
            {
                "id": 2,
                "date": pd.Timestamp("2026-08-02"),
                "open": 60.0,
                "high": 61.0,
                "low": 59.0,
                "close": 60.0,
                "volume": 3_000.0,
                "first_seen_at": pd.Timestamp("2026-08-10", tz="UTC"),
            },
        ]
    )
    revisions = [
        {
            "price_bar_id": 1,
            "observed_at": pd.Timestamp("2026-08-07", tz="UTC"),
            "previous_values_json": {
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
                "volume": 1_000.0,
            },
        }
    ]
    restored = _bars_as_of(
        source,
        revisions,
        cutoff=pd.Timestamp("2026-08-05", tz="UTC"),
        end_date=date(2026, 8, 2),
    )
    assert restored.close.tolist() == [100.0]
    assert restored.volume.tolist() == [1_000.0]


def test_stop_target_same_bar_is_ambiguous_without_guessing_order() -> None:
    bars = pd.DataFrame([{"date": "2026-08-02", "open": 100, "high": 111, "low": 94, "close": 105}])
    outcome = stop_target_sequence(bars, stop_price=95, target_price=110)
    assert outcome.target_hit is True
    assert outcome.stop_hit is True
    assert outcome.first_hit == "AMBIGUOUS"
    assert outcome.time_to_target == outcome.time_to_stop == 1


def test_walk_forward_splits_are_strictly_time_ordered() -> None:
    dates = [date(2026, 7, 1) + timedelta(days=index) for index in range(10)]
    labels = time_ordered_split_labels(dates)
    calibration = [
        value for value, label in zip(dates, labels, strict=True) if label == "calibration"
    ]
    validation = [
        value for value, label in zip(dates, labels, strict=True) if label == "validation"
    ]
    holdout = [value for value, label in zip(dates, labels, strict=True) if label == "holdout"]
    assert max(calibration) < min(validation) < min(holdout)


def test_ablation_record_is_reproducible() -> None:
    variants = {"A1_no_leadership": 7.25, "A4_no_htf": 7.5}
    first = component_ablation_record(ticker="abc", baseline_tcs=8.0, variants=variants)
    second = component_ablation_record(ticker="ABC", baseline_tcs=8.0, variants=variants)
    assert first == second


def test_bootstrap_resampling_handles_duplicate_sampled_rows() -> None:
    frame = pd.DataFrame(
        {
            "run_id": np.repeat([1, 2], 20),
            "v5_TCS": np.linspace(1.0, 10.0, 40),
            "v4_score": np.linspace(1.5, 9.5, 40),
            "forward_return_5d": np.sin(np.arange(40)),
        }
    )
    result = _bootstrap_delta(frame, "v5_TCS")
    assert result["bootstrap_delta_low"] is not None
    assert result["bootstrap_delta_high"] is not None
    assert _bootstrap_delta(frame, "v4_score") == {
        "bootstrap_delta_low": 0.0,
        "bootstrap_delta_high": 0.0,
    }


def test_ablation_configs_remove_all_trigger_quality_paths() -> None:
    config = {
        "stage": {"modifiers": {"stage_2": 0.25}},
        "trend": {"local_weight": 0.75, "htf_weight": 0.25},
        "entry_quality": {"weights": {"risk_control": 0.5, "execution": 0.3, "trigger": 0.2}},
        "setup_quality": {
            "pullback": {"primary": 0.7, "trigger_readiness": 0.3},
            "vcp": {"primary": 0.8, "trigger_readiness": 0.2},
        },
        "composite": {"bull_trend": {}, "choppy": {}, "risk_off": {}},
        "momentum": {"base_weight": 0.85, "acceleration_weight": 0.15},
        "danger_caps": {"entry_quality": {"failed_breakout": 3.5}},
    }
    variants = _variant_configs(config)
    assert "A13_no_roc126" in variants
    assert "A14_no_benchmark_rs" in variants
    no_trigger = variants["A5_no_trigger_quality"]
    assert no_trigger["entry_quality"]["weights"]["trigger"] == 0.0
    assert no_trigger["setup_quality"]["pullback"]["trigger_readiness"] == 0.0
    assert no_trigger["setup_quality"]["vcp"]["trigger_readiness"] == 0.0


def test_leadership_ablation_variants_recompute_cross_sectional_ranks() -> None:
    leadership_config = {
        "leadership": {
            "weights": {
                "roc21": 0.30,
                "roc63": 0.25,
                "roc126": 0.15,
                "benchmark_rs": 0.15,
                "residual_momentum": 0.15,
            },
            "renormalize_missing": True,
            "scope": "run_universe",
        }
    }
    contexts = []
    values = [
        ("AAA", 3.0, 9.0, 1.0),
        ("BBB", 2.0, 5.0, 5.0),
        ("CCC", 1.0, 1.0, 9.0),
    ]
    for ticker, broad, sector, residual in values:
        contexts.append(
            {
                "raw": {"run_id": 7},
                "base": SimpleNamespace(
                    ticker=ticker,
                    relative_strength_score=broad,
                    debug={
                        "derived": {
                            "stock_roc_short": broad,
                            "stock_roc_medium": broad,
                            "stock_roc_long": broad,
                            "v5_sector_rs_score": sector,
                            "residual_momentum_score": residual,
                        }
                    },
                ),
            }
        )
    variants = _ablation_leadership_variants(contexts, leadership_config)
    no_residual = variants["no_residual_momentum"]
    no_sector = variants["no_sector_rs"]
    assert no_residual[(7, "AAA")].missing_components == ("residual_momentum",)
    assert variants["no_roc126"][(7, "AAA")].missing_components == ("roc126",)
    assert variants["no_benchmark_rs"][(7, "AAA")].missing_components == ("benchmark_rs",)
    assert any(
        no_residual[key].leadership_score != no_sector[key].leadership_score for key in no_residual
    )


def test_confirmed_htf_ignores_unconfirmed_current_week() -> None:
    frame = _frame(360)
    thursday = frame.iloc[:-1]
    friday = frame
    assert calculate_htf_trend_features(thursday) == calculate_htf_trend_features(friday)


def test_feature_value_at_decision_date_does_not_use_future_pivots() -> None:
    frame = _frame(320)
    params = load_pine_defaults()
    decision_index = 280
    prefix = _calculate_feature_frame(prepare_ohlcv_frame(frame.iloc[: decision_index + 1]), params)
    full = _calculate_feature_frame(prepare_ohlcv_frame(frame), params)
    columns = ["pivot_high", "pivot_low", "higher_high", "higher_low", "roc21", "atr14"]
    pd.testing.assert_series_equal(
        prefix.iloc[-1][columns],
        full.iloc[decision_index][columns],
        check_names=False,
    )


def test_leadership_uses_only_explicit_run_universe() -> None:
    config = {
        "weights": {
            "roc21": 0.3,
            "roc63": 0.25,
            "roc126": 0.15,
            "benchmark_rs": 0.15,
            "residual_momentum": 0.15,
        },
        "renormalize_missing": True,
        "scope": "run_universe",
    }
    intended = [
        {
            "ticker": "AAA",
            "roc21": 1,
            "roc63": 1,
            "roc126": 1,
            "benchmark_rs_score": 1,
            "residual_momentum_score": 1,
        },
        {
            "ticker": "BBB",
            "roc21": 2,
            "roc63": 2,
            "roc126": 2,
            "benchmark_rs_score": 2,
            "residual_momentum_score": 2,
        },
    ]
    first = rank_leadership_v5(intended, config)
    second = rank_leadership_v5(list(intended), config)
    assert first == second
    assert first["AAA"].universe_size == 2


def _frame(length: int) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=length)
    close = 100 + np.linspace(0, 40, length) + np.sin(np.arange(length) / 5)
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.2,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 1_000_000 + np.arange(length) * 100,
        }
    )
