from dataclasses import replace
from datetime import date
from decimal import Decimal

from app.models.tables import FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.ranking_profile_config import RankingProfileConfig, get_ranking_profile
from app.services.ranking_profile_engine import (
    calculate_profile_score,
    decision_from_score,
    rank_profile,
    rank_single_row,
)

TODAY = date(2026, 7, 7)


def test_calculate_profile_score_rescales_available_score() -> None:
    profile = _profile("momentum_swing")

    score = calculate_profile_score(
        technical_profile_score=None,
        fundamental_score=8.0,
        profile=profile,
    )

    assert score == 8.0


def test_momentum_swing_ranks_technical_leader_first() -> None:
    rows = [_row("MOMO"), _row("COMP"), _row("MISS")]
    fundamentals = {
        "MOMO": _fundamental("MOMO", 7.8),
        "COMP": _fundamental("COMP", 7.2),
        "MISS": _fundamental("MISS", 9.8),
    }
    technicals = {
        "MOMO": _technical(
            "MOMO",
            trend=8.2,
            momentum=9.2,
            setup=8.4,
            risk=1.5,
            rs=9.4,
            breakout=8.8,
            vcp=8.2,
            box_tightness=8.0,
            derived={"rs_new_high": True},
        ),
        "COMP": _technical(
            "COMP",
            trend=7.0,
            momentum=5.6,
            setup=6.0,
            risk=2.5,
            rs=6.5,
            breakout=5.5,
            vcp=6.0,
            box_tightness=6.0,
        ),
    }

    decisions = rank_profile(
        profile=_profile("momentum_swing"),
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        config=_config(),
        today=TODAY,
    )

    assert [decision.ticker for decision in decisions] == ["MOMO", "COMP", "MISS"]
    assert decisions[0].profile_rank == 1
    assert decisions[0].decision_label == "Strong candidate"
    assert decisions[2].decision_label == "Low confidence"
    assert "missing_technical" in decisions[2].warning_flags


def test_clean_compounder_ranks_fundamental_pullback_above_early_breakout() -> None:
    rows = [_row("COMP"), _row("ROKT")]
    fundamentals = {
        "COMP": _fundamental("COMP", 9.4),
        "ROKT": _fundamental("ROKT", 5.2),
    }
    technicals = {
        "COMP": _technical(
            "COMP",
            trend=7.7,
            momentum=6.6,
            setup=7.0,
            risk=1.8,
            rs=7.2,
            pullback_health="Healthy",
        ),
        "ROKT": _technical(
            "ROKT",
            trend=8.5,
            momentum=9.3,
            setup=8.6,
            risk=2.0,
            rs=9.0,
            classification="Fresh breakout",
            breakout=9.2,
            vcp=7.0,
        ),
    }

    decisions = rank_profile(
        profile=_profile("clean_compounder_pullback"),
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        config=_config(),
        today=TODAY,
    )

    assert [decision.ticker for decision in decisions] == ["COMP", "ROKT"]
    assert decisions[1].decision_label == "Watchlist"
    assert "fundamental_floor_failed" in decisions[1].warning_flags


def test_early_rocket_favors_trend_repair_breakout_candidate() -> None:
    rows = [_row("ROKT"), _row("QUAL")]
    fundamentals = {
        "ROKT": _fundamental("ROKT", 6.5),
        "QUAL": _fundamental("QUAL", 8.8),
    }
    technicals = {
        "ROKT": _technical(
            "ROKT",
            trend=8.7,
            momentum=9.4,
            setup=8.8,
            risk=1.8,
            rs=9.5,
            classification="Trend repair",
            breakout=9.0,
            vcp=7.2,
            derived={
                "rs_roc_short": 4.0,
                "rs_roc_medium": 3.0,
                "rs_new_high": True,
                "bullish_volume_bar": True,
                "breakout_volume_confirmed": True,
                "green_beats_red": True,
            },
        ),
        "QUAL": _technical(
            "QUAL",
            trend=6.8,
            momentum=6.2,
            setup=6.0,
            risk=2.2,
            rs=6.8,
            breakout=5.8,
            vcp=6.0,
        ),
    }

    decisions = rank_profile(
        profile=_profile("early_rocket"),
        rows=rows,
        fundamentals=fundamentals,
        technicals=technicals,
        config=_config(),
        today=TODAY,
    )

    assert decisions[0].ticker == "ROKT"
    assert decisions[0].decision_label == "Strong candidate"
    assert decisions[0].component_scores["trend_repair"] > 8.0


def test_defensive_quality_caps_danger_classification_to_avoid() -> None:
    decision = rank_single_row(
        profile=_profile("defensive_quality"),
        row=_row("RISK"),
        fundamental=_fundamental("RISK", 9.2),
        technical=_technical(
            "RISK",
            trend=9.0,
            momentum=9.0,
            setup=9.0,
            risk=1.2,
            rs=9.0,
            classification="Failed breakout",
            breakout=9.0,
        ),
        config=_config(),
        today=TODAY,
    )

    assert decision.decision_label == "Avoid"
    assert decision.sort_bucket == 3
    assert "danger_classification" in decision.warning_flags
    assert decision.gates["danger_gate"] is True


def test_profile_penalties_apply_quality_risk_label() -> None:
    decision = rank_single_row(
        profile=_profile("momentum_swing"),
        row=_row("QUAL"),
        fundamental=_fundamental("QUAL", 9.0, label="Quality risk"),
        technical=_technical("QUAL", trend=9.0, momentum=9.0, setup=9.0, risk=1.0, rs=9.0),
        config=_config(),
        today=TODAY,
    )

    assert decision.profile_score == 6.8854
    assert decision.decision_label == "Candidate"
    assert decision.penalties["quality_risk"] == 1.5
    assert "quality_risk" in decision.warning_flags


def test_missing_data_policy_penalty_controls_missing_data_penalty() -> None:
    profile = _profile("momentum_swing")
    profile = replace(
        profile,
        missing_data_policy=replace(profile.missing_data_policy, penalty=2.25),
        penalties={**profile.penalties, "missing_data": 0.0},
    )

    decision = rank_single_row(
        profile=profile,
        row=_row("MISS"),
        fundamental=_fundamental("MISS", 8.0),
        technical=None,
        config=_config(),
        today=TODAY,
    )

    assert decision.profile_score == 5.75
    assert decision.penalties["missing_data"] == 2.25
    assert decision.decision_label == "Low confidence"
    assert "missing_technical" in decision.warning_flags


def test_growth_trap_risk_caps_profile_decision_to_candidate() -> None:
    decision = rank_single_row(
        profile=_profile("early_rocket"),
        row=_row("GROW"),
        fundamental=_fundamental("GROW", 10.0, label="Growth trap risk"),
        technical=_technical(
            "GROW",
            trend=10.0,
            momentum=10.0,
            setup=10.0,
            risk=1.0,
            rs=10.0,
            classification="Trend repair",
            breakout=10.0,
            vcp=10.0,
            box_tightness=10.0,
            derived={
                "rs_roc_short": 4.0,
                "rs_roc_medium": 3.0,
                "rs_new_high": True,
                "bullish_volume_bar": True,
                "breakout_volume_confirmed": True,
                "green_beats_red": True,
            },
        ),
        config=_config(),
        today=TODAY,
    )

    assert decision.profile_score >= 8.2
    assert decision.decision_label == "Candidate"
    assert decision.position_size_hint == "Half starter"
    assert decision.gates["fundamental_risk_label"] == {
        "label": "Growth trap risk",
        "max_decision": "Candidate",
    }
    assert "growth_trap_risk" in decision.warning_flags


def test_profile_decision_threshold_boundaries_use_watchlist_label() -> None:
    profile = _profile("momentum_swing")

    assert decision_from_score(8.0, profile) == "Strong candidate"
    assert decision_from_score(7.9999, profile) == "Candidate"
    assert decision_from_score(6.8, profile) == "Candidate"
    assert decision_from_score(6.7999, profile) == "Watchlist"
    assert decision_from_score(5.5, profile) == "Watchlist"
    assert decision_from_score(5.4999, profile) == "Avoid"


def test_earnings_block_overrides_high_score() -> None:
    decision = rank_single_row(
        profile=_profile("momentum_swing"),
        row=_row("EARN", earnings_date=date(2026, 7, 8), raw_earnings="2026-07-08"),
        fundamental=_fundamental("EARN", 9.0),
        technical=_technical("EARN", trend=9.0, momentum=9.0, setup=9.0, risk=1.0, rs=9.0),
        config=_config(),
        today=TODAY,
    )

    assert decision.decision_label == "Blocked by earnings gate"
    assert decision.position_size_hint == "No new entry"
    assert decision.earnings_risk_level == "blocked"
    assert "earnings_blocked" in decision.warning_flags


def _profile(name: str) -> RankingProfileConfig:
    return get_ranking_profile(name)


def _row(
    ticker: str,
    earnings_date: date | None = None,
    raw_earnings: str | None = None,
) -> RawCompanyRow:
    raw_json = {"Symbol": ticker}
    if raw_earnings is not None:
        raw_json["upcoming_earnings_date"] = raw_earnings
    return RawCompanyRow(
        run_id=1,
        row_number=1,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        upcoming_earnings_date=earnings_date,
        raw_json=raw_json,
    )


def _fundamental(
    ticker: str,
    score: float,
    label: str = "High-quality quant",
) -> FundamentalScore:
    return FundamentalScore(
        run_id=1,
        ticker=ticker,
        fundamental_score=Decimal(str(score)),
        fundamental_label=label,
    )


def _technical(
    ticker: str,
    *,
    trend: float,
    momentum: float,
    setup: float,
    risk: float,
    rs: float,
    classification: str = "Prime clean pullback",
    pullback_health: str = "Healthy",
    breakout: float = 7.0,
    vcp: float = 7.0,
    box_tightness: float = 7.0,
    derived: dict | None = None,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=1,
        ticker=ticker,
        trend_score=Decimal(str(trend)),
        momentum_score=Decimal(str(momentum)),
        setup_score=Decimal(str(setup)),
        risk_score=Decimal(str(risk)),
        market_score=Decimal("8.0"),
        combined_relative_strength_score=Decimal(str(rs)),
        dual_score=Decimal(str((trend + momentum + setup + rs) / 4.0)),
        classification=classification,
        pullback_health=pullback_health,
        technical_confidence="normal",
        vcp_score=Decimal(str(vcp)),
        box_tightness_score=Decimal(str(box_tightness)),
        breakout_quality_score=Decimal(str(breakout)),
        climax_risk_score=Decimal("1.5"),
        debug_json={"derived": derived or {}},
    )


def _config() -> dict:
    return {
        "earnings_risk_gate": {
            "enabled": True,
            "block_if_within_days": 2,
            "high_risk_if_within_days": 5,
            "medium_risk_if_within_days": 10,
            "missing_date_policy": "ignore",
            "apply_to_combined_score": True,
            "block_new_entries": True,
            "penalties": {
                "blocked": 3.0,
                "high": 2.0,
                "medium": 1.0,
                "unknown": 0.0,
                "clear": 0.0,
            },
        }
    }
