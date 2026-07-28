from copy import deepcopy

from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_rotation_dtos import SectorUniverseMetrics
from app.services.sector_rotation_policy import (
    PERMISSION_AVOID_NEW_LONGS,
    PERMISSION_FULL_ALLOWED,
    PERMISSION_REDUCED_SIZE,
    PERMISSION_WATCH_ONLY,
    STATE_CROWDED_RISK,
    STATE_FADING,
    STATE_IMPROVING,
    STATE_INSUFFICIENT_DATA,
    STATE_LAGGING,
    STATE_LEADING,
    STATE_NEUTRAL,
    STATE_RISK_OFF,
    SectorRotationPolicyService,
)


def test_policy_assigns_leading_state_and_full_permission_in_supportive_market() -> None:
    decision = _decide(_metrics(score=8.2), market={"risk_state": "Green"})

    assert decision.rotation_state == STATE_LEADING
    assert decision.permission == PERMISSION_FULL_ALLOWED
    assert decision.position_size_multiplier == 1.0
    assert decision.final_score == 8.2
    assert decision.universe_score == 8.2
    assert decision.etf_score is None
    assert "state_leading" in decision.reasons
    assert "market_bucket_supportive" in decision.reasons


def test_policy_assigns_improving_before_leading_when_score_change_is_meaningful() -> None:
    decision = _decide(
        _metrics(score=7.2),
        previous={"current_rank": 5, "sector_final_score": 6.0},
        market={"risk_state": "Green"},
    )

    assert decision.rotation_state == STATE_IMPROVING
    assert decision.permission == PERMISSION_REDUCED_SIZE
    assert decision.previous_rank == 5
    assert decision.score_change == 1.2
    assert "score_improved" in decision.reasons


def test_policy_assigns_fading_on_negative_score_change() -> None:
    decision = _decide(
        _metrics(score=6.4),
        previous={"current_rank": 2, "sector_final_score": 7.4},
        market={"risk_state": "Green"},
    )

    assert decision.rotation_state == STATE_FADING
    assert decision.permission == PERMISSION_WATCH_ONLY
    assert decision.score_change == -1.0
    assert "score_declined" in decision.reasons


def test_policy_assigns_neutral_and_watch_only() -> None:
    decision = _decide(_metrics(score=5.6), market={"risk_state": "Green"})

    assert decision.rotation_state == STATE_NEUTRAL
    assert decision.permission == PERMISSION_WATCH_ONLY
    assert decision.position_size_multiplier == 0.25


def test_policy_assigns_lagging_and_avoid() -> None:
    decision = _decide(_metrics(score=4.2), market={"risk_state": "Green"})

    assert decision.rotation_state == STATE_LAGGING
    assert decision.permission == PERMISSION_AVOID_NEW_LONGS
    assert decision.position_size_multiplier == 0.0


def test_policy_assigns_crowded_risk_for_high_score_with_concentrated_top25() -> None:
    decision = _decide(
        _metrics(score=8.4, ticker_count=20, top_25_count=10),
        market={"risk_state": "Green"},
    )

    assert decision.rotation_state == STATE_CROWDED_RISK
    assert decision.permission == PERMISSION_REDUCED_SIZE
    assert "sector_crowded_risk" in decision.warnings
    assert decision.debug["top_25_share"] == 0.5


def test_policy_assigns_risk_off_for_high_danger_share() -> None:
    decision = _decide(
        _metrics(score=8.5, ticker_count=10, danger_share=0.4),
        market={"risk_state": "Green"},
    )

    assert decision.rotation_state == STATE_RISK_OFF
    assert decision.permission == PERMISSION_AVOID_NEW_LONGS
    assert "sector_risk_off" in decision.warnings


def test_policy_assigns_insufficient_data_separately_from_lagging() -> None:
    decision = _decide(_metrics(score=2.0, confidence="insufficient"))

    assert decision.rotation_state == STATE_INSUFFICIENT_DATA
    assert decision.permission == PERMISSION_WATCH_ONLY
    assert decision.confidence == "insufficient"


def test_policy_reduces_leading_sector_in_choppy_market() -> None:
    decision = _decide(_metrics(score=8.0), market={"risk_state": "Yellow"})

    assert decision.rotation_state == STATE_LEADING
    assert decision.permission == PERMISSION_REDUCED_SIZE
    assert decision.position_size_multiplier == 0.5
    assert decision.debug["market_bucket"] == "choppy"


def test_policy_avoids_dangerous_sector_in_risk_off_market() -> None:
    decision = _decide(_metrics(score=5.5), market={"risk_state": "Red"})

    assert decision.rotation_state == STATE_NEUTRAL
    assert decision.permission == PERMISSION_AVOID_NEW_LONGS
    assert decision.position_size_multiplier == 0.0
    assert "market_risk_off" in decision.warnings


def test_policy_uses_unknown_market_bucket_without_market_regime() -> None:
    decision = _decide(_metrics(score=8.1), market=None)

    assert decision.rotation_state == STATE_LEADING
    assert decision.permission == PERMISSION_REDUCED_SIZE
    assert decision.debug["market_bucket"] == "unknown"


def test_policy_uses_combined_score_when_etf_mode_enabled_and_score_exists() -> None:
    config = deepcopy(load_sector_rotation_config())
    config["etf_score"]["enabled"] = True

    decision = _decide(
        _metrics(score=8.0),
        etf={"etf_rotation_score": 6.0},
        market={"risk_state": "Green"},
        config=config,
    )

    assert decision.final_score == 7.1
    assert decision.universe_score == 8.0
    assert decision.etf_score == 6.0
    assert decision.debug["score_source"] == "combined"
    assert "missing_etf_confirmation" not in decision.warnings


def test_policy_uses_universe_only_when_etf_mode_enabled_but_missing() -> None:
    config = deepcopy(load_sector_rotation_config())
    config["etf_score"]["enabled"] = True

    decision = _decide(
        _metrics(score=8.0),
        etf=None,
        market={"risk_state": "Green"},
        config=config,
    )

    assert decision.final_score == 8.0
    assert decision.debug["score_source"] == "universe_only"
    assert "missing_etf_confirmation" in decision.warnings


def test_policy_carries_etf_warnings_into_decision() -> None:
    config = deepcopy(load_sector_rotation_config())
    config["etf_score"]["enabled"] = True

    decision = _decide(
        _metrics(score=8.0),
        etf={"etf_rotation_score": None, "warnings": ["missing_xlk_etf_data"]},
        market={"risk_state": "Green"},
        config=config,
    )

    assert "missing_xlk_etf_data" in decision.warnings
    assert "missing_etf_confirmation" in decision.warnings


def _decide(
    metrics: SectorUniverseMetrics,
    etf=None,
    market=None,
    previous=None,
    config: dict | None = None,
):
    return SectorRotationPolicyService().decide(
        universe=metrics,
        etf=etf,
        market_regime=market,
        previous=previous,
        config=config or load_sector_rotation_config(),
    )


def _metrics(
    score: float | None,
    ticker_count: int = 12,
    top_25_count: int = 1,
    danger_share: float = 0.0,
    confidence: str = "normal",
) -> SectorUniverseMetrics:
    danger_count = round(ticker_count * danger_share)
    return SectorUniverseMetrics(
        sector="Technology",
        sector_slug="technology",
        ticker_count=ticker_count,
        universe_share=1.0,
        average_fundamental_score=7.0,
        average_technical_score=7.5,
        average_final_score=score,
        average_profile_score=score,
        top_counts={
            "top_10": min(top_25_count, 10),
            "top_25": top_25_count,
            "top_50": top_25_count,
        },
        setup_distribution={},
        warning_distribution={},
        buyable_count=3,
        watch_count=1,
        danger_count=danger_count,
        buyable_share=0.25,
        watch_share=0.0833,
        danger_share=danger_share,
        clean_pullback_count=1,
        breakout_count=1,
        vcp_count=0,
        tight_base_breakout_count=0,
        extended_or_overheated_count=0,
        missing_fundamental_count=0,
        missing_technical_count=0,
        component_scores={"risk_control": 10.0},
        universe_leadership_score=score,
        confidence=confidence,
        reason_codes=["low_danger_density"] if danger_share == 0 else ["high_danger_density"],
        warnings=[],
        debug={},
    )
