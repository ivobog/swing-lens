from decimal import Decimal

from app.models.tables import FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.cockpit_sorting import cockpit_sort_key
from app.services.combined_decision import combine_row_decision


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


def _row(ticker: str) -> RawCompanyRow:
    return RawCompanyRow(
        run_id=1,
        row_number=1,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        raw_json={"Symbol": ticker},
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
