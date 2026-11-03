from copy import deepcopy
from dataclasses import replace

import pytest
from market_sector_technical_helpers import CUTOFF, frame, native_inputs, symbols

from app.services import market_sector_intelligence_technical_adapter as adapter
from app.services.entry_quality_v5 import stock_specific_base_risk
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_score_v4 import technical_score_v4_from_base_score
from app.services.technical_score_v5 import technical_score_v5_from_base_score
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config


def test_scoped_config_copies_preserve_stock_defaults_and_weights():
    pine, v4, v5 = (
        load_pine_defaults(),
        load_technical_scoring_v4_config(),
        load_technical_scoring_v5_config(),
    )
    before = deepcopy((pine, v4, v5))
    policy = adapter.scoped_policy(symbols(), pine=pine, v4=v4, v5=v5)
    assert (pine, v4, v5) == before
    assert pine["market_rs"]["useSectorBenchmark"] is True
    assert pine["market_rs"]["sectorSymbol"] == "QQQ"
    assert policy.pine["market_rs"]["useSectorBenchmark"] is False
    assert policy.v5["leadership"]["scope"] == "fixed_11_etf_universe"
    assert policy.v5["leadership"]["weights"] == v5["leadership"]["weights"]
    assert policy.v4 == v4
    assert policy.requires_qqq
    assert not adapter.scoped_policy(
        symbols(),
        v4={
            **v4,
            "market_regime_v4": {**v4["market_regime_v4"], "use_qqq": False},
        },
    ).requires_qqq


def test_actual_pure_v4_and_v5_outputs_share_identical_base():
    policy, base, _ = native_inputs()
    before = deepcopy(base)
    v4 = adapter.v4_payload(base, policy)
    native4 = technical_score_v4_from_base_score(base, v4_params=policy.v4)
    assert v4["final_v4_score"] == native4.final_v4_score
    assert v4["classification"] == native4.final_v4_classification
    assert v4["action_bias"] == native4.final_v4_action
    assert v4["warnings"] == native4.warning_flags
    assert v4["confidence"] == base.technical_confidence
    inputs = [
        dict(adapter.leadership_input(base, policy)[0], ticker=symbol) for symbol in symbols()
    ]
    ranked = adapter.fixed_leadership(inputs, symbols(), policy)
    v5 = adapter.v5_payload(base, ranked[base.ticker], policy)
    native5 = technical_score_v5_from_base_score(
        base,
        leadership=ranked[base.ticker],
        sector_resolution=adapter.SECTOR_RESOLUTION,
        v5_config=policy.v5,
        pine_config=policy.pine,
    )
    assert v5["technical_composite_score"] == native5.technical_composite_score
    assert v5["confidence_adjusted_score"] == native5.confidence_adjusted_score
    assert v5["classification"] == native5.classification
    assert v5["action_bias"] == native5.action_bias
    assert v5["technical_strength_score"] == native5.technical_strength_score
    assert v5["entry_quality_score"] == native5.entry_quality_score
    assert v5["setup_quality_score"] == native5.setup_quality_score
    assert v5["components"] == native5.debug
    assert native4.base_score is native5.base_score is base
    assert base == before
    assert v4["adapter_readiness"]["htf_confirmation"] == {
        "htf_source_week": "2026-10-16",
        "htf_confirmed": True,
        "htf_latest_week_excluded": True,
    }

    normal = technical_score_v5_from_base_score(
        replace(base, technical_confidence="normal"),
        leadership=ranked[base.ticker],
        sector_resolution=adapter.SECTOR_RESOLUTION,
        v5_config=policy.v5,
        pine_config=policy.pine,
    )
    normal_payload = adapter.v5_payload(
        replace(base, technical_confidence="normal"), ranked[base.ticker], policy
    )
    assert normal_payload["confidence_adjusted_score"] == normal.confidence_adjusted_score
    assert (
        normal_payload["confidence_adjusted_score"] != normal_payload["technical_composite_score"]
    )


def test_sector_not_applicable_has_no_false_risk_or_confidence_penalty():
    policy, base, _ = native_inputs()
    inputs = [
        dict(adapter.leadership_input(base, policy)[0], ticker=symbol) for symbol in symbols()
    ]
    rank = adapter.fixed_leadership(inputs, symbols(), policy)[base.ticker]
    results = []
    for sector_value in (0.0, 10.0):
        evidence = replace(
            base,
            debug={
                **deepcopy(base.debug),
                "derived": {**base.debug["derived"], "v5_sector_rs_score": sector_value},
            },
        )
        results.append(
            technical_score_v5_from_base_score(
                evidence,
                leadership=rank,
                sector_resolution=adapter.SECTOR_RESOLUTION,
                v5_config=policy.v5,
                pine_config=policy.pine,
            )
        )
    assert results[0].technical_composite_score == results[1].technical_composite_score
    assert (
        results[0].technical_confidence
        == results[1].technical_confidence
        == base.technical_confidence
    )
    assert results[0].sector_benchmark_symbol is None
    assert "sector_benchmark_missing_sector" in results[0].warning_flags
    kwargs = dict(pine_config=policy.pine, v5_risk_config=policy.v5["risk"])
    weak = replace(
        base, debug={**base.debug, "derived": {**base.debug["derived"], "v5_sector_rs_score": 0}}
    )
    assert stock_specific_base_risk(
        weak, use_sector_evidence=True, **kwargs
    ) > stock_specific_base_risk(
        weak,
        use_sector_evidence=False,
        **kwargs,
    )


def test_fixed_leadership_ties_order_and_missing_inputs_are_explicit():
    policy, base, _ = native_inputs()
    row, missing = adapter.leadership_input(base, policy)
    assert missing == []
    rows = [{**row, "ticker": symbol} for symbol in symbols()]
    first = adapter.fixed_leadership(rows, symbols(), policy)
    assert first == adapter.fixed_leadership(list(reversed(rows)), symbols(), policy)
    assert all(
        value.scope == "fixed_11_etf_universe" and value.universe_size == 11
        for value in first.values()
    )
    assert all(value.percentiles["roc21"] == pytest.approx(54.5455) for value in first.values())
    with pytest.raises(adapter.TechnicalEvidenceError, match="V5_COHORT_NOT_COMPARABLE"):
        adapter.fixed_leadership(rows[:-1], symbols(), policy)
    bad = replace(
        base,
        debug={**base.debug, "derived": {**base.debug["derived"], "residual_momentum_score": None}},
    )
    assert "residual_momentum" in adapter.leadership_input(bad, policy)[1]


@pytest.mark.parametrize(
    "count,code",
    [
        (64, "INSUFFICIENT_ROC126"),
        (200, "INSUFFICIENT_HISTORY"),
        (252, "INSUFFICIENT_ADAPTIVE_HISTORY"),
    ],
)
def test_d2_window_and_incomplete_adaptive_history_are_not_technical_readiness(count, code):
    policy = adapter.scoped_policy(symbols())
    with pytest.raises(adapter.TechnicalEvidenceError, match=code):
        adapter.local_features(
            frame("XLB", count), None, "XLB", CUTOFF.latest_completed_session, policy
        )


def test_htf_requires_finite_confirmed_weekly_history():
    policy, _, market = native_inputs()
    pine = deepcopy(policy.pine)
    pine["htf"]["htfSlowLen"] = 80
    with pytest.raises(adapter.TechnicalEvidenceError, match="INSUFFICIENT_CONFIRMED_HTF"):
        adapter.build_base(
            "XLB",
            frame("XLB"),
            None,
            frame("SPY"),
            market["SPY"],
            market["QQQ"],
            CUTOFF.latest_completed_session,
            replace(policy, pine=pine),
        )
    htf = adapter.calculate_htf_trend_features(
        frame("XLB"), params=policy.pine, latest_completed_session=CUTOFF.latest_completed_session
    )
    assert htf["htf_confirmed"] and htf["htf_latest_week_excluded"]
    assert htf["htf_source_week"] == "2026-10-16"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_nonfinite_engine_output_never_becomes_json_or_favorable_default(value):
    with pytest.raises(adapter.TechnicalEvidenceError, match="NONFINITE_ENGINE_OUTPUT"):
        adapter.checked_payload({"components": {"risk": value}})


def test_missing_current_beta_never_reuses_last_historical_beta_or_residual():
    policy, _, market = native_inputs()
    spy = frame("SPY")
    for column, offset in [("open", -0.15), ("high", 1.1), ("low", -1.2), ("close", 0.0)]:
        spy.loc[spy.index[-140:], column] = 110.0 + offset
    base = adapter.build_base(
        "XLB",
        frame("XLB"),
        None,
        spy,
        market["SPY"],
        market["QQQ"],
        CUTOFF.latest_completed_session,
        policy,
    )
    assert adapter.v4_payload(base, policy)["status"] == "READY"
    assert base.debug["derived"]["rolling_beta_63"] is None
    assert base.debug["derived"]["residual_momentum_score"] is None
    assert "residual_momentum" in adapter.leadership_input(base, policy)[1]
