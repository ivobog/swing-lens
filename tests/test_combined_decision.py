from datetime import date
from decimal import Decimal

from app.models.tables import FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.cockpit_sorting import cockpit_sort_key
from app.services.combined_decision import _to_model, combine_row_decision


TODAY = date(2026, 7, 7)


def test_combined_decision_marks_aligned_buyable_setup_strong() -> None:
    decision = combine_row_decision(
        _row("MSFT"),
        _fundamental("MSFT", "Clean compounder", "8.8"),
        _technical("MSFT", "Prime clean pullback", "8.6", risk_score="2.5"),
        config=_config(),
    )

    assert decision.final_score == 8.71
    assert decision.combined_decision == "Strong candidate"
    assert decision.position_size_hint == "Full starter"
    assert decision.notes == "aligned"


def test_combined_decision_danger_classification_overrides_score() -> None:
    decision = combine_row_decision(
        _row("NVDA"),
        _fundamental("NVDA", "Clean compounder", "9.0"),
        _technical("NVDA", "Failed breakout", "8.5", risk_score="6.0"),
        config=_config(),
    )

    assert decision.final_score == 5.775
    assert decision.combined_decision == "Avoid"
    assert decision.position_size_hint == "Avoid"
    assert "failed breakout" in decision.notes


def test_combined_decision_treats_v4_danger_as_avoid() -> None:
    decision = combine_row_decision(
        _row("TSLA"),
        _fundamental("TSLA", "Quality growth", "8.7"),
        _technical("TSLA", "Climax reversal risk", "8.2", risk_score="4.0"),
        config=_config(),
    )

    assert decision.final_score == 5.475
    assert decision.combined_decision == "Avoid"
    assert decision.position_size_hint == "Avoid"
    assert "climax reversal risk" in decision.notes


def test_combined_decision_treats_v4_buyable_as_full_starter() -> None:
    decision = combine_row_decision(
        _row("SHOP"),
        _fundamental("SHOP", "Clean compounder", "8.8"),
        _technical("SHOP", "Tight base breakout", "8.6", risk_score="2.5"),
        config=_config(),
    )

    assert decision.combined_decision == "Strong candidate"
    assert decision.position_size_hint == "Full starter"


def test_combined_decision_carries_v4_warning_flags_to_cockpit_payload() -> None:
    technical = _technical("SHOP", "Tight base breakout", "8.6", risk_score="2.5")
    technical.warning_flags_json = ["market_risk_off", "stage_4_downtrend"]

    decision = combine_row_decision(
        _row("SHOP"),
        _fundamental("SHOP", "Clean compounder", "8.8"),
        technical,
        config=_config(),
    )

    assert "market_risk_off" in decision.warning_flags
    assert "stage_4_downtrend" in decision.warning_flags
    assert decision.has_warning is True


def test_combined_decision_missing_technical_waits_for_data() -> None:
    decision = combine_row_decision(
        _row("ADBE"),
        _fundamental("ADBE", "Quality growth", "8.0"),
        None,
        config=_config(),
    )

    assert decision.final_score == 7.0
    assert decision.combined_decision == "Incomplete data"
    assert decision.position_size_hint == "Wait"
    assert "technical missing" in decision.notes
    assert decision.warning_flags == ["incomplete_data", "missing_technical"]
    assert not decision.is_complete
    assert decision.sort_bucket == 50


def test_complete_candidate_sorts_above_higher_scoring_incomplete_result() -> None:
    complete = combine_row_decision(
        _row("COMP"),
        _fundamental("COMP", "Mixed but interesting", "6.9"),
        _technical("COMP", "Clean bull pullback", "6.9", risk_score="3.0"),
        config=_config(),
    )
    incomplete = combine_row_decision(
        _row("MISS"),
        _fundamental("MISS", "Clean compounder", "10.0"),
        None,
        config=_config(),
    )

    ranked = sorted([incomplete, complete], key=cockpit_sort_key)

    assert complete.final_score < incomplete.final_score
    assert ranked == [complete, incomplete]


def test_blocked_earnings_overrides_strong_candidate() -> None:
    decision = combine_row_decision(
        _row("AAPL", earnings_date=date(2026, 7, 8), raw_earnings="2026-07-08"),
        _fundamental("AAPL", "Clean compounder", "8.8"),
        _technical("AAPL", "Prime clean pullback", "8.6", risk_score="2.5"),
        config=_config_with_earnings_gate(),
        today=TODAY,
    )

    assert decision.final_score == 5.71
    assert decision.combined_decision == "Blocked by earnings gate"
    assert decision.position_size_hint == "No new entry"
    assert decision.earnings_risk_level == "blocked"
    assert decision.days_until_earnings == 1
    assert decision.earnings_warning_flags == ["earnings_blocked"]
    assert "earnings_blocked" in decision.warning_flags
    assert decision.has_warning is True
    assert decision.sort_bucket == 40


def test_high_earnings_risk_applies_penalty_without_blocking() -> None:
    decision = combine_row_decision(
        _row("AAPL", earnings_date=date(2026, 7, 11), raw_earnings="2026-07-11"),
        _fundamental("AAPL", "Clean compounder", "9.0"),
        _technical("AAPL", "Prime clean pullback", "9.0", risk_score="2.5"),
        config=_config_with_earnings_gate(),
        today=TODAY,
    )

    assert decision.final_score == 7.0
    assert decision.combined_decision == "Candidate"
    assert decision.position_size_hint == "Half starter"
    assert decision.earnings_risk_level == "high"
    assert decision.earnings_warning_flags == ["earnings_high_risk"]
    assert "earnings_high_risk" in decision.warning_flags


def test_medium_earnings_risk_applies_smaller_penalty() -> None:
    decision = combine_row_decision(
        _row("AAPL", earnings_date=date(2026, 7, 15), raw_earnings="2026-07-15"),
        _fundamental("AAPL", "Clean compounder", "9.0"),
        _technical("AAPL", "Prime clean pullback", "9.0", risk_score="2.5"),
        config=_config_with_earnings_gate(),
        today=TODAY,
    )

    assert decision.final_score == 8.0
    assert decision.combined_decision == "Strong candidate"
    assert decision.position_size_hint == "Full starter"
    assert decision.earnings_risk_level == "medium"
    assert decision.earnings_warning_flags == ["earnings_medium_risk"]


def test_clear_earnings_risk_leaves_score_unchanged() -> None:
    decision = combine_row_decision(
        _row("AAPL", earnings_date=date(2026, 7, 18), raw_earnings="2026-07-18"),
        _fundamental("AAPL", "Clean compounder", "8.8"),
        _technical("AAPL", "Prime clean pullback", "8.6", risk_score="2.5"),
        config=_config_with_earnings_gate(),
        today=TODAY,
    )

    assert decision.final_score == 8.71
    assert decision.combined_decision == "Strong candidate"
    assert decision.earnings_risk_level == "clear"
    assert decision.earnings_warning_flags == []
    assert decision.warning_flags == []


def test_missing_earnings_date_is_unknown_without_failing_combined_scoring() -> None:
    decision = combine_row_decision(
        _row("AAPL"),
        _fundamental("AAPL", "Clean compounder", "8.8"),
        _technical("AAPL", "Prime clean pullback", "8.6", risk_score="2.5"),
        config=_config_with_earnings_gate(),
        today=TODAY,
    )

    assert decision.final_score == 8.41
    assert decision.combined_decision == "Strong candidate"
    assert decision.earnings_risk_level == "unknown"
    assert decision.earnings_warning_flags == ["earnings_date_missing"]
    assert "earnings_date_missing" in decision.warning_flags


def test_unparseable_earnings_date_is_unknown_with_unparseable_warning() -> None:
    decision = combine_row_decision(
        _row("AAPL", raw_earnings="not a date"),
        _fundamental("AAPL", "Clean compounder", "8.8"),
        _technical("AAPL", "Prime clean pullback", "8.6", risk_score="2.5"),
        config=_config_with_earnings_gate(),
        today=TODAY,
    )

    assert decision.final_score == 8.41
    assert decision.earnings_risk_level == "unknown"
    assert decision.earnings_warning_flags == ["earnings_date_unparseable"]
    assert "earnings_date_unparseable" in decision.warning_flags


def test_combined_decision_model_persists_earnings_risk_fields() -> None:
    decision = combine_row_decision(
        _row("AAPL", earnings_date=date(2026, 7, 11), raw_earnings="2026-07-11"),
        _fundamental("AAPL", "Clean compounder", "9.0"),
        _technical("AAPL", "Prime clean pullback", "9.0", risk_score="2.5"),
        config=_config_with_earnings_gate(),
        today=TODAY,
    )

    model = _to_model(run_id=7, final_rank=1, decision=decision)

    assert model.upcoming_earnings_date == date(2026, 7, 11)
    assert model.days_until_earnings == 4
    assert model.earnings_risk_level == "high"
    assert model.earnings_warning_flags_json == ["earnings_high_risk"]


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
    label: str,
    score: str,
) -> FundamentalScore:
    return FundamentalScore(
        run_id=1,
        ticker=ticker,
        fundamental_label=label,
        fundamental_score=Decimal(score),
    )


def _technical(
    ticker: str,
    classification: str,
    dual_score: str,
    risk_score: str,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=1,
        ticker=ticker,
        classification=classification,
        dual_score=Decimal(dual_score),
        risk_score=Decimal(risk_score),
        debug_json={"derived": {"liquidity_warning": False}},
    )


def _config() -> dict:
    return {
        "combined_score": {
            "fundamental_score": 0.55,
            "dual_score": 0.45,
        },
        "penalties": {
            "danger_classification": 3.0,
            "overheated_momentum": 1.5,
            "value_trap_risk": 2.0,
            "growth_trap_risk": 1.5,
            "missing_data": 1.0,
            "liquidity_warning": 1.0,
        },
        "labels": {
            "strong_candidate_min_score": 8.0,
            "candidate_min_score": 6.8,
            "watch_min_score": 5.5,
        },
    }


def _config_with_earnings_gate() -> dict:
    config = _config()
    config["earnings_risk_gate"] = {
        "enabled": True,
        "block_if_within_days": 2,
        "high_risk_if_within_days": 5,
        "medium_risk_if_within_days": 10,
        "missing_date_policy": "warn",
        "apply_to_combined_score": True,
        "block_new_entries": True,
        "penalties": {
            "blocked": 3.0,
            "high": 2.0,
            "medium": 1.0,
            "unknown": 0.3,
            "clear": 0.0,
        },
    }
    return config
