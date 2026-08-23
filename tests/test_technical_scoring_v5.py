from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from app.models.tables import TechnicalScore
from app.services import technical_score_service
from app.services.entry_quality_v5 import (
    calculate_entry_quality,
    calculate_execution_quality,
    combine_risk,
    stock_specific_base_risk,
)
from app.services.leadership_v5 import rank_leadership_v5
from app.services.pine_replica_engine import PineReplicaScore
from app.services.sector_benchmark_service import resolve_sector_benchmark
from app.services.setup_quality_v5 import calculate_setup_quality, select_setup
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_score_service import build_technical_score
from app.services.technical_score_v4 import technical_score_v4_from_base_score
from app.services.technical_score_v5 import technical_score_v5_from_base_score
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.services.technical_strength_v5 import calculate_technical_strength
from app.services.trigger_quality import calculate_trigger_quality
from app.settings import Settings


def test_v5_config_has_validated_weight_groups() -> None:
    config = load_technical_scoring_v5_config()
    assert config["engine"]["version"] == "5.0.0"
    assert sum(config["technical_strength"]["weights"].values()) == 1
    assert sum(config["leadership"]["weights"].values()) == 1
    assert sum(config["composite"]["risk_off"].values()) == 1


def test_v5_settings_and_migration_are_shadow_safe_by_default() -> None:
    settings = Settings(_env_file=None)
    assert settings.technical_v5_enabled is False
    assert settings.technical_v5_shadow_compare_enabled is True
    assert settings.technical_v5_persist_shadow_results is True
    columns = TechnicalScore.__table__.columns
    for name in (
        "technical_strength_score",
        "setup_quality_score",
        "entry_quality_score",
        "technical_composite_score",
        "confidence_adjusted_score",
        "leadership_v5_score",
        "residual_momentum_score",
        "trigger_distance_atr",
        "stop_distance_atr",
        "setup_type",
        "sector_benchmark_symbol",
        "stage_modifier",
        "v5_debug_json",
    ):
        assert name in columns
    migration = Path("alembic/versions/20260823_0052_add_technical_scoring_v5.py").read_text(
        encoding="utf-8"
    )
    assert 'down_revision: str | None = "0051_worker_progress_reliability"' in migration


def test_sector_metadata_is_queried_once_and_each_etf_is_loaded_once(monkeypatch) -> None:
    class SectorRows:
        def all(self):
            return [
                ("AAA", "Technology"),
                ("BBB", "Technology"),
                ("CCC", "Financials"),
            ]

    class FakeSession:
        execute_count = 0

        def execute(self, _statement):
            self.execute_count += 1
            return SectorRows()

    loaded: list[str] = []

    def fake_load_price_frame(_db, ticker):
        loaded.append(ticker)
        return pd.DataFrame({"close": range(100, 230)})

    monkeypatch.setattr(technical_score_service, "_load_price_frame", fake_load_price_frame)
    db = FakeSession()

    context = technical_score_service._technical_v5_run_context(
        db,
        1,
        ["AAA", "BBB", "CCC"],
        load_technical_scoring_v5_config(),
    )

    assert db.execute_count == 1
    assert loaded == ["XLF", "XLK"]
    assert set(context.sector_features) == {"XLF", "XLK"}


def test_leadership_excludes_composites_and_consumes_residual_momentum() -> None:
    config = load_technical_scoring_v5_config()["leadership"]
    rows = [
        {
            "ticker": "AAA",
            "roc21": 5,
            "roc63": 5,
            "roc126": 5,
            "benchmark_rs_score": 5,
            "residual_momentum_score": 1,
            "dual_score": 10,
            "setup_score": 10,
        },
        {
            "ticker": "BBB",
            "roc21": 5,
            "roc63": 5,
            "roc126": 5,
            "benchmark_rs_score": 5,
            "residual_momentum_score": 9,
            "dual_score": 1,
            "setup_score": 1,
        },
    ]
    first = rank_leadership_v5(rows, config)
    changed_composites = rank_leadership_v5(
        [{**row, "dual_score": 0, "setup_score": 0} for row in rows], config
    )
    assert first["AAA"].leadership_score == changed_composites["AAA"].leadership_score
    assert first["BBB"].leadership_score > first["AAA"].leadership_score
    assert "residual_momentum" in first["BBB"].weighted_components


def test_leadership_missing_component_is_not_zero_filled() -> None:
    config = load_technical_scoring_v5_config()["leadership"]
    result = rank_leadership_v5(
        [
            {
                "ticker": "AAA",
                "roc21": 1,
                "roc63": 2,
                "roc126": 3,
                "benchmark_rs_score": 5,
                "residual_momentum_score": None,
            }
        ],
        config,
    )["AAA"]
    assert result.leadership_score == 10.0
    assert result.missing_components == ("residual_momentum",)
    assert sum(result.weighted_components.values()) == 10.0


def test_sector_mapping_and_fallback_never_return_qqq() -> None:
    mapping = load_technical_scoring_v5_config()["sector_benchmarks"]["mapping"]
    assert resolve_sector_benchmark("Technology", mapping).benchmark_symbol == "XLK"
    assert resolve_sector_benchmark("Energy", mapping).benchmark_symbol == "XLE"
    assert resolve_sector_benchmark("Financial", mapping).benchmark_symbol == "XLF"
    fallback = resolve_sector_benchmark("Marine Shipping", mapping)
    assert fallback.benchmark_symbol is None
    assert fallback.status == "UNSUPPORTED_SECTOR"
    assert fallback.benchmark_symbol != "QQQ"


def test_trigger_states_are_atr_normalized_and_invalidated_wins() -> None:
    config = load_technical_scoring_v5_config()["trigger"]
    near = calculate_trigger_quality(
        close=99.8,
        atr14=1,
        trigger_price=100,
        invalidation_price=95,
        invalidated=False,
        config=config,
    )
    fresh = calculate_trigger_quality(
        close=100.3,
        atr14=1,
        trigger_price=100,
        invalidation_price=95,
        invalidated=False,
        config=config,
    )
    invalid = calculate_trigger_quality(
        close=94,
        atr14=1,
        trigger_price=100,
        invalidation_price=95,
        invalidated=False,
        config=config,
    )
    assert (near.state, near.distance_atr, near.quality) == ("at_trigger", 0.2, 9.0)
    assert fresh.state == "freshly_triggered" and fresh.quality == 10
    assert invalid.state == "invalidated" and invalid.quality == 0


def test_setup_selection_precedes_scoring_and_ignores_irrelevant_high_model() -> None:
    config = load_technical_scoring_v5_config()
    base = _base(classification="Clean bull pullback", setup=7.0, vcp=10.0, vcp_detected=False)
    selection = select_setup(base, base.debug["explainability"], config)
    result = calculate_setup_quality(
        base=base,
        explainability=base.debug["explainability"],
        selection=selection,
        trigger_quality=9.0,
        execution_readiness=8.0,
        config=config,
    )
    assert selection.setup_type == "pullback"
    assert result.components["primary"] == 7.0
    assert result.score_before_stage != 10.0
    assert result.stage_modifier == 0.25


def test_secondary_risk_channel_always_has_measurable_effect() -> None:
    assert combine_risk(6, 0, 0.2) == 6
    assert combine_risk(6, 2, 0.2) == 6.4
    assert combine_risk(2, 6, 0.2) == 6.4


def test_v5_stock_risk_is_independent_of_market_regime() -> None:
    base = _base()
    risk_a = stock_specific_base_risk(
        base, use_sector_evidence=False, pine_config=load_pine_defaults()
    )
    risk_off = replace(
        base,
        debug={
            **base.debug,
            "derived": {
                **base.debug["derived"],
                "market_risk_off": True,
                "market_regime": "Bearish",
            },
        },
    )
    risk_b = stock_specific_base_risk(
        risk_off, use_sector_evidence=False, pine_config=load_pine_defaults()
    )
    assert risk_a == risk_b


def test_v5_stock_risk_evidence_points_are_configurable() -> None:
    config = load_technical_scoring_v5_config()
    base = _scenario_base({"liquidity_warning": True})
    default_risk = stock_specific_base_risk(
        base,
        use_sector_evidence=False,
        pine_config=load_pine_defaults(),
        v5_risk_config=config["risk"],
    )
    adjusted_risk_config = {
        **config["risk"],
        "stock_specific_points": {
            **config["risk"]["stock_specific_points"],
            "liquidity_warning": 0.2,
        },
    }

    adjusted_risk = stock_specific_base_risk(
        base,
        use_sector_evidence=False,
        pine_config=load_pine_defaults(),
        v5_risk_config=adjusted_risk_config,
    )

    assert default_risk - adjusted_risk == pytest.approx(0.6)


def test_danger_caps_entry_but_not_strength() -> None:
    config = load_technical_scoring_v5_config()
    execution = calculate_execution_quality(_base(), config["execution"])
    normal = calculate_entry_quality(
        base_risk=1,
        climax_risk=1,
        execution=execution,
        trigger_quality=10,
        danger_state=None,
        config=config,
    )
    danger = calculate_entry_quality(
        base_risk=1,
        climax_risk=1,
        execution=execution,
        trigger_quality=10,
        danger_state="Failed breakout",
        config=config,
    )
    strength = calculate_technical_strength(
        local_trend_score=9,
        htf_score=9,
        htf_available=True,
        base_momentum_score=9,
        roc10=10,
        roc63=20,
        leadership_score=9,
        config=config,
    )
    assert danger.score == 3.5 < normal.score
    assert strength.score > 8


def test_missing_htf_uses_local_trend_and_low_confidence_adjusts_only_presentation() -> None:
    config = load_technical_scoring_v5_config()
    strength = calculate_technical_strength(
        local_trend_score=8,
        htf_score=5,
        htf_available=False,
        base_momentum_score=7,
        roc10=5,
        roc63=10,
        leadership_score=7,
        config=config,
    )
    assert strength.trend_quality == 8
    assert "missing_htf_data" in strength.missing_evidence
    assert 0 < strength.score <= 10


def test_v5_score_is_reconstructable_and_deterministic() -> None:
    config = load_technical_scoring_v5_config()
    leadership = rank_leadership_v5(
        [
            {
                "ticker": "AAA",
                "roc21": 5,
                "roc63": 7,
                "roc126": 9,
                "benchmark_rs_score": 7,
                "residual_momentum_score": 8,
            }
        ],
        config["leadership"],
    )["AAA"]
    sector = resolve_sector_benchmark("Technology", config["sector_benchmarks"]["mapping"])
    first = technical_score_v5_from_base_score(
        _base(),
        leadership=leadership,
        sector_resolution=sector,
        v5_config=config,
        pine_config=load_pine_defaults(),
    )
    second = technical_score_v5_from_base_score(
        _base(),
        leadership=leadership,
        sector_resolution=sector,
        v5_config=config,
        pine_config=load_pine_defaults(),
    )
    assert first == second
    ts = first.debug["technical_strength"]
    reconstructed_ts = sum(
        ts["components"][key] * ts["applied_weights"][key] for key in ts["applied_weights"]
    )
    tcs = first.debug["composite"]
    reconstructed_tcs = (
        first.technical_strength_score * tcs["weights"]["technical_strength"]
        + first.setup_quality_score * tcs["weights"]["setup_quality"]
        + first.entry_quality_score * tcs["weights"]["entry_quality"]
    )
    assert reconstructed_ts == pytest.approx(first.technical_strength_score, abs=1e-4)
    assert reconstructed_tcs == pytest.approx(first.technical_composite_score, abs=1e-4)
    assert first.debug["sector_benchmark"]["benchmark_symbol"] == "XLK"


MANDATORY_GOLDEN_SCORES = {
    "strong_stage2_pullback": (8.4554, 8.3, 8.55, 8.4199),
    "high_quality_vcp_near_trigger": (8.4554, 9.175, 8.95, 8.8062),
    "fresh_breakout_strong_volume": (8.4554, 9.125, 8.55, 8.7087),
    "momentum_continuation": (8.4554, 8.5417, 9.15, 8.6245),
    "extended_momentum": (8.4554, 8.5, 8.45, 8.4699),
    "failed_breakout_strong_trend": (8.4554, 7.25, 3.5, 7.0424),
    "distribution_risk_high_ts": (8.4554, 8.3, 4.5, 7.6099),
    "climax_reversal": (8.4554, 8.3, 4.0, 7.5099),
    "stage4_false_vcp": (8.4554, 7.725, 8.55, 8.2187),
    "low_liquidity_strong_chart": (8.4554, 8.3, 7.99, 8.3079),
    "missing_sector_benchmark": (8.4554, 8.3, 8.55, 8.4199),
    "missing_htf": (8.5204, 8.3, 8.55, 8.4492),
    "high_beta_weak_residual": (8.3204, 8.3, 8.55, 8.3592),
    "moderate_roc_strong_residual": (8.4104, 8.3, 8.55, 8.3997),
}


@pytest.mark.parametrize(
    ("name", "changes", "expected_setup", "expected_classification", "expected_cap"),
    [
        ("strong_stage2_pullback", {}, "pullback", "Clean bull pullback", None),
        (
            "high_quality_vcp_near_trigger",
            {
                "classification": "No trade",
                "had_pullback": False,
                "vcp": 9.0,
                "vcp_detected": True,
                "box_high": 100.4,
            },
            "vcp",
            "Volatility contraction setup",
            None,
        ),
        (
            "fresh_breakout_strong_volume",
            {
                "classification": "Fresh breakout",
                "had_pullback": False,
                "box_breakout": True,
                "breakout_quality": 9.0,
                "fresh_breakout": True,
                "volume_percentile": 95.0,
            },
            "breakout",
            "Tight base breakout",
            None,
        ),
        (
            "momentum_continuation",
            {"classification": "Momentum continuation", "had_pullback": False},
            "momentum_continuation",
            "Momentum continuation",
            None,
        ),
        (
            "extended_momentum",
            {"classification": "Extended momentum", "had_pullback": False, "extension": 12.0},
            "extended_momentum",
            "Extended momentum",
            None,
        ),
        (
            "failed_breakout_strong_trend",
            {"failed_breakout": True},
            "pullback",
            "Failed breakout",
            3.5,
        ),
        (
            "distribution_risk_high_ts",
            {"classification": "Distribution risk"},
            "pullback",
            "Distribution risk",
            4.5,
        ),
        ("climax_reversal", {"climax": 8.5}, "pullback", "Climax reversal risk", 4.0),
        (
            "stage4_false_vcp",
            {
                "classification": "No trade",
                "had_pullback": False,
                "vcp": 9.0,
                "vcp_detected": True,
                "stage": "Stage 4",
            },
            "vcp",
            "Filtered pullback",
            None,
        ),
        (
            "low_liquidity_strong_chart",
            {"liquidity_warning": True},
            "pullback",
            "Clean bull pullback",
            None,
        ),
        (
            "missing_sector_benchmark",
            {"sector": "unsupported"},
            "pullback",
            "Clean bull pullback",
            None,
        ),
        ("missing_htf", {"missing_htf": True}, "pullback", "Clean bull pullback", None),
        (
            "high_beta_weak_residual",
            {"residual": 1.0, "beta": 2.0},
            "pullback",
            "Clean bull pullback",
            None,
        ),
        (
            "moderate_roc_strong_residual",
            {"residual": 9.0, "roc10": 2.0},
            "pullback",
            "Clean bull pullback",
            None,
        ),
    ],
)
def test_v5_mandatory_golden_scenarios(
    name: str,
    changes: dict,
    expected_setup: str,
    expected_classification: str,
    expected_cap: float | None,
) -> None:
    config = load_technical_scoring_v5_config()
    base = _scenario_base(changes)
    leadership = rank_leadership_v5(
        [
            {
                "ticker": "REF",
                "roc21": 4,
                "roc63": 8,
                "roc126": 12,
                "benchmark_rs_score": 5,
                "residual_momentum_score": 5,
            },
            {
                "ticker": "AAA",
                "roc21": base.debug["derived"]["stock_roc_short"],
                "roc63": base.debug["derived"]["stock_roc_medium"],
                "roc126": base.debug["derived"]["stock_roc_long"],
                "benchmark_rs_score": base.relative_strength_score,
                "residual_momentum_score": base.debug["derived"]["residual_momentum_score"],
            },
        ],
        config["leadership"],
    )["AAA"]
    mapping = config["sector_benchmarks"]["mapping"]
    sector = resolve_sector_benchmark(
        "Marine Shipping" if changes.get("sector") == "unsupported" else "Technology",
        mapping,
    )
    score = technical_score_v5_from_base_score(
        base,
        leadership=leadership,
        sector_resolution=sector,
        v5_config=config,
        pine_config=load_pine_defaults(),
    )
    assert name
    assert score.setup_type == expected_setup
    assert score.classification == expected_classification
    assert score.entry_quality.danger_cap == expected_cap
    assert 0 <= score.technical_strength_score <= 10
    assert 0 <= score.setup_quality_score <= 10
    assert 0 <= score.entry_quality_score <= 10
    assert 0 <= score.technical_composite_score <= 10
    assert (
        score.technical_strength_score,
        score.setup_quality_score,
        score.entry_quality_score,
        score.technical_composite_score,
    ) == MANDATORY_GOLDEN_SCORES[name]
    assert score.action_bias
    assert score.technical_confidence in {"high", "normal", "low"}
    assert score.debug["technical_strength"]["score"] == score.technical_strength_score
    assert score.debug["setup_quality"]["selection"]["reasons"]
    if expected_cap is not None:
        assert score.entry_quality_score <= expected_cap
    if changes.get("missing_htf"):
        assert "missing_htf_data" in score.warning_flags
    if changes.get("sector") == "unsupported":
        assert score.debug["sector_benchmark"]["fallback_to_market_only"] is True


def test_golden_same_stock_bull_vs_risk_off_uses_policy_weights_only() -> None:
    config = load_technical_scoring_v5_config()
    base = _base()
    leadership = rank_leadership_v5(
        [
            {
                "ticker": "AAA",
                "roc21": 10,
                "roc63": 18,
                "roc126": 25,
                "benchmark_rs_score": 8,
                "residual_momentum_score": 7,
            }
        ],
        config["leadership"],
    )["AAA"]
    sector = resolve_sector_benchmark("Technology", config["sector_benchmarks"]["mapping"])
    bull = technical_score_v5_from_base_score(
        base,
        leadership=leadership,
        sector_resolution=sector,
        v5_config=config,
        pine_config=load_pine_defaults(),
    )
    risk_debug = {
        **base.debug,
        "explainability": {
            **base.debug["explainability"],
            "regime": {"regime": "Correction", "risk_off": True},
        },
    }
    risk_off = technical_score_v5_from_base_score(
        replace(base, debug=risk_debug),
        leadership=leadership,
        sector_resolution=sector,
        v5_config=config,
        pine_config=load_pine_defaults(),
    )
    assert bull.debug["composite"]["weights"] == config["composite"]["bull_trend"]
    assert risk_off.debug["composite"]["weights"] == config["composite"]["risk_off"]
    assert bull.entry_quality.base_risk == risk_off.entry_quality.base_risk
    assert (bull.technical_strength_score, bull.setup_quality_score, bull.entry_quality_score) == (
        8.4554,
        8.3,
        8.55,
    )
    assert risk_off.technical_strength_score == bull.technical_strength_score
    assert risk_off.setup_quality_score == bull.setup_quality_score
    assert risk_off.entry_quality_score == bull.entry_quality_score
    assert bull.technical_composite_score == 8.4199
    assert risk_off.technical_composite_score == 8.4639


def test_v5_shadow_persistence_preserves_v4_and_active_mode_is_explicit() -> None:
    config = load_technical_scoring_v5_config()
    base = _base()
    leadership = rank_leadership_v5(
        [
            {
                "ticker": "AAA",
                "roc21": 10,
                "roc63": 18,
                "roc126": 25,
                "benchmark_rs_score": 8,
                "residual_momentum_score": 7,
            }
        ],
        config["leadership"],
    )["AAA"]
    sector = resolve_sector_benchmark("Technology", config["sector_benchmarks"]["mapping"])
    v4 = technical_score_v4_from_base_score(base)
    v5 = technical_score_v5_from_base_score(
        base,
        leadership=leadership,
        sector_resolution=sector,
        v5_config=config,
        pine_config=load_pine_defaults(),
    )
    shadow = build_technical_score(1, v4, v5_score=v5, v5_active=False)
    active = build_technical_score(1, v4, v5_score=v5, v5_active=True)
    historical = build_technical_score(1, v4)
    assert float(shadow.dual_score) == v4.final_v4_score
    assert shadow.technical_engine_version == "4.0.0"
    assert float(shadow.technical_composite_score) == v5.technical_composite_score
    assert shadow.v5_debug_json["rollout"]["mode"] == "shadow"
    assert shadow.v5_debug_json["shadow_comparison"]["v4_score"] == v4.final_v4_score
    assert float(active.dual_score) == v5.technical_composite_score
    assert active.technical_engine_version == "5.0.0"
    assert active.v4_debug_json["final_v4_score"] == v4.final_v4_score
    assert float(historical.dual_score) == v4.final_v4_score
    assert historical.technical_composite_score is None
    assert historical.v5_debug_json is None


def test_v5_explainability_contains_every_required_top_level_path() -> None:
    config = load_technical_scoring_v5_config()
    leadership = rank_leadership_v5(
        [
            {
                "ticker": "AAA",
                "roc21": 10,
                "roc63": 18,
                "roc126": 25,
                "benchmark_rs_score": 8,
                "residual_momentum_score": 7,
            }
        ],
        config["leadership"],
    )["AAA"]
    score = technical_score_v5_from_base_score(
        _base(),
        leadership=leadership,
        sector_resolution=resolve_sector_benchmark(
            "Technology", config["sector_benchmarks"]["mapping"]
        ),
        v5_config=config,
        pine_config=load_pine_defaults(),
    )
    payload = score.debug
    assert set(payload) >= {
        "engine_version",
        "config_hash",
        "technical_strength",
        "leadership",
        "residual_momentum",
        "sector_benchmark",
        "setup_quality",
        "entry_quality",
        "composite",
        "confidence",
        "classification",
        "action_bias",
        "warning_flags",
        "feature_flags",
        "caps_and_modifiers",
    }
    assert set(payload["entry_quality"]) >= {
        "score_before_cap",
        "score",
        "base_risk",
        "climax_risk",
        "combined_risk",
        "risk_control",
        "execution",
        "trigger_quality",
        "danger_state",
        "danger_cap",
    }


def _base(
    *,
    classification: str = "Clean bull pullback",
    setup: float = 8.0,
    vcp: float = 0.0,
    vcp_detected: bool = False,
) -> PineReplicaScore:
    derived = {
        "close": 100.0,
        "ema10": 99.0,
        "ema20": 98.0,
        "sma50": 95.0,
        "atr": 2.0,
        "stock_roc10": 6.0,
        "stock_roc_short": 10.0,
        "stock_roc_medium": 18.0,
        "stock_roc_long": 25.0,
        "residual_momentum_score": 7.0,
        "rolling_beta_63": 1.1,
        "residual_return_21": 4.0,
        "residual_return_63": 8.0,
        "htf_data_ready": True,
        "had_pullback": True,
        "held_near_support": True,
        "not_too_deep": True,
        "volume_dry_up": True,
        "red_vol_declining": True,
        "breakout_volume_confirmed": False,
        "fresh_breakout": False,
        "failed_breakout": False,
        "extension_mid_pct": 3.0,
        "rsi": 58.0,
        "atr_pct": 2.0,
        "distribution_count": 0,
        "relative_strength_status": "Strong",
        "htf_status": "Strong",
        "target_source": "ATR_TARGET",
        "stop_source": "STRUCTURE_ATR",
        "v5_sector_rs_score": 8.0,
    }
    explainability = {
        "data_readiness": {"data_quality_score": 10.0},
        "adaptive": {"volume_percentile_252": 70.0, "extension_percentile_252": 30.0},
        "contraction": {
            "vcp_score": vcp,
            "vcp_detected": vcp_detected,
            "volume_dry_up_quality": 8.0,
        },
        "box": {
            "box_breakout": False,
            "box_failure": False,
            "breakout_quality_score": 0.0,
            "box_tightness_score": 7.0,
            "box_high": 102.0,
            "box_low": 94.0,
        },
        "stage": {"stage": "Stage 2", "stage_tags": []},
        "regime": {"regime": "Bull trend", "risk_off": False},
        "climax": {"climax_risk_score": 1.0, "momentum_crash_risk": False},
        "feature_flags": [],
        "warning_flags": [],
    }
    return PineReplicaScore(
        ticker="AAA",
        local_trend_score=8.5,
        trend_score=8.5,
        momentum_score=8.0,
        setup_score=setup,
        risk_score=1.0,
        market_score=8.0,
        relative_strength_score=8.0,
        sector_relative_strength_score=5.0,
        combined_relative_strength_score=7.1,
        htf_score=8.0,
        dual_score=8.0,
        classification=classification,
        action_bias="Constructive",
        pullback_health="Healthy",
        suggested_stop=96.0,
        suggested_target=108.0,
        reward_risk=2.0,
        entry_risk_pct=4.0,
        insufficient_data=False,
        missing_data={"has_htf_data": True},
        debug={"derived": derived, "explainability": explainability},
        technical_confidence="high",
        data_quality_score=10.0,
        warning_flags=(),
    )


def _scenario_base(changes: dict) -> PineReplicaScore:
    base = _base(
        classification=changes.get("classification", "Clean bull pullback"),
        vcp=changes.get("vcp", 0.0),
        vcp_detected=changes.get("vcp_detected", False),
    )
    derived = {
        **base.debug["derived"],
        "had_pullback": changes.get("had_pullback", True),
        "fresh_breakout": changes.get("fresh_breakout", False),
        "breakout_volume_confirmed": changes.get("fresh_breakout", False),
        "failed_breakout": changes.get("failed_breakout", False),
        "extension_mid_pct": changes.get("extension", 3.0),
        "liquidity_warning": changes.get("liquidity_warning", False),
        "residual_momentum_score": changes.get("residual", 7.0),
        "rolling_beta_63": changes.get("beta", 1.1),
        "stock_roc10": changes.get("roc10", 6.0),
    }
    explainability = {
        **base.debug["explainability"],
        "adaptive": {
            **base.debug["explainability"]["adaptive"],
            "volume_percentile_252": changes.get("volume_percentile", 70.0),
        },
        "box": {
            **base.debug["explainability"]["box"],
            "box_breakout": changes.get("box_breakout", False),
            "box_high": changes.get("box_high", 102.0),
            "breakout_quality_score": changes.get("breakout_quality", 0.0),
            "box_failure": changes.get("failed_breakout", False),
        },
        "stage": {"stage": changes.get("stage", "Stage 2"), "stage_tags": []},
        "climax": {
            "climax_risk_score": changes.get("climax", 1.0),
            "momentum_crash_risk": changes.get("climax", 1.0) >= 7.0,
        },
    }
    missing = {**base.missing_data, "has_htf_data": not changes.get("missing_htf", False)}
    return replace(
        base, debug={"derived": derived, "explainability": explainability}, missing_data=missing
    )
