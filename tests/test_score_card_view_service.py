from datetime import date
from decimal import Decimal

from app.models.tables import CombinedResult, FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.score_card_view_service import (
    build_score_cards,
    risk_tone,
    score_tone,
    text_tone,
    warning_badges,
)


def test_score_tone_thresholds() -> None:
    assert score_tone(Decimal("7.5")) == "good"
    assert score_tone(Decimal("5.0")) == "neutral"
    assert score_tone(Decimal("4.99")) == "bad"
    assert score_tone(None) == "neutral"


def test_risk_tone_inverts_score_thresholds() -> None:
    assert risk_tone(Decimal("2.0")) == "good"
    assert risk_tone(Decimal("4.0")) == "neutral"
    assert risk_tone(Decimal("4.01")) == "bad"
    assert risk_tone(None) == "neutral"


def test_text_tone_classifies_common_decision_language() -> None:
    assert text_tone("Decision", "Strong candidate") == "good"
    assert text_tone("Action", "Best buyable, R/R ok") == "good"
    assert text_tone("Decision", "Blocked by earnings gate") == "bad"
    assert text_tone("Stage", "Stage 4 downtrend") == "bad"
    assert text_tone("Sector", "Technology") == "neutral"


def test_warning_badges_map_clear_medium_and_severe_states() -> None:
    assert warning_badges([]) == [{"flag": "clear", "label": "Clear", "tone": "success"}]
    assert warning_badges(["earnings_medium_risk", "failed_breakout", "unknown_flag"]) == [
        {"flag": "earnings_medium_risk", "label": "Earnings medium", "tone": "warning"},
        {"flag": "failed_breakout", "label": "Failed Breakout", "tone": "danger"},
        {"flag": "unknown_flag", "label": "Unknown Flag", "tone": "muted"},
    ]


def test_build_score_cards_groups_combined_fundamental_technical_risk_and_warnings() -> None:
    cards = build_score_cards(
        raw_row=_raw_row("MSFT"),
        fundamental=_fundamental("MSFT"),
        technical=_technical("MSFT"),
        combined=_combined("MSFT"),
    )

    assert [card["title"] for card in cards] == [
        "Combined Decision",
        "Fundamentals",
        "Technicals",
        "Risk Context",
        "Warnings and Missing Data",
    ]

    combined_items = _items(cards[0])
    assert combined_items["Ticker"]["value"] == "MSFT"
    assert combined_items["Company"]["value"] == "Microsoft Corporation"
    assert combined_items["Final Score"]["value"] == "8.20"
    assert combined_items["Final Score"]["tone"] == "good"
    assert combined_items["Decision"]["tone"] == "good"
    assert combined_items["Complete"]["value"] == "Yes"

    fundamental_items = _items(cards[1])
    assert fundamental_items["Model"]["value"] == "fundamentals_v2.0"
    assert fundamental_items["Score"]["tone"] == "neutral"
    assert fundamental_items["Growth"]["tone"] == "good"
    assert fundamental_items["Liquidity Risk"]["tone"] == "bad"
    assert fundamental_items["Missing Core"]["value"] == "fcf_ttm"
    assert fundamental_items["Warnings"]["value"] == "high_accrual_risk"

    technical_items = _items(cards[2])
    assert technical_items["Model"]["value"] == "4.0.0"
    assert technical_items["Risk"]["tone"] == "neutral"
    assert technical_items["Climax Risk"]["tone"] == "bad"
    assert technical_items["Stage"]["value"] == "Stage 2"
    assert technical_items["Action"]["tone"] == "good"

    risk_items = _items(cards[3])
    assert risk_items["Stop"]["value"] == "92.25"
    assert risk_items["Target"]["value"] == "111.50"
    assert risk_items["Entry Risk"]["tone"] == "bad"
    assert risk_items["Earnings Risk"]["tone"] == "bad"

    warning_items = _items(cards[4])
    assert warning_items["Overall"]["value"] == "4 warning(s)"
    assert warning_items["Overall"]["tone"] == "bad"
    assert cards[4]["badges"] == [
        {"flag": "earnings_blocked", "label": "Earnings block", "tone": "danger"},
        {"flag": "high_accrual_risk", "label": "Accrual risk", "tone": "danger"},
        {"flag": "failed_breakout", "label": "Failed Breakout", "tone": "danger"},
        {"flag": "earnings_high_risk", "label": "Earnings high", "tone": "warning"},
    ]


def test_build_score_cards_handles_missing_models_with_neutral_values() -> None:
    cards = build_score_cards(
        raw_row=_raw_row("AAPL"),
        fundamental=None,
        technical=None,
        combined=None,
    )

    combined_items = _items(cards[0])
    assert combined_items["Ticker"]["value"] == "AAPL"
    assert combined_items["Final Score"]["value"] == "N/A"
    assert combined_items["Final Score"]["tone"] == "neutral"
    assert _items(cards[4])["Overall"]["value"] == "Clear"
    assert cards[4]["badges"] == [{"flag": "clear", "label": "Clear", "tone": "success"}]


def _items(card: dict) -> dict[str, dict]:
    return {item["label"]: item for item in card["items"]}


def _raw_row(ticker: str) -> RawCompanyRow:
    return RawCompanyRow(
        run_id=1,
        row_number=1,
        ticker=ticker,
        company_name="Microsoft Corporation" if ticker == "MSFT" else f"{ticker} Corp",
        sector="Technology",
        raw_json={"Symbol": ticker},
    )


def _combined(ticker: str) -> CombinedResult:
    return CombinedResult(
        run_id=1,
        ticker=ticker,
        company_name="Microsoft Corporation",
        sector="Technology",
        final_rank=3,
        final_score=Decimal("8.20"),
        fundamental_score=Decimal("7.40"),
        fundamental_label="High-quality quant",
        technical_classification="Prime clean pullback",
        dual_score=Decimal("8.44"),
        combined_decision="Strong candidate",
        position_size_hint="Full",
        upcoming_earnings_date=date(2026, 7, 14),
        days_until_earnings=7,
        earnings_risk_level="blocked",
        earnings_warning_flags_json=["earnings_high_risk"],
        warning_flags_json=["earnings_blocked"],
        notes="Watch around earnings.",
        is_complete=True,
        has_fundamental=True,
        has_technical=True,
        has_warning=True,
    )


def _fundamental(ticker: str) -> FundamentalScore:
    return FundamentalScore(
        run_id=1,
        ticker=ticker,
        fundamental_score=Decimal("7.40"),
        scoring_model_version="fundamentals_v2.0",
        growth_quality_score=Decimal("8.10"),
        profitability_quality_score=Decimal("8.20"),
        fcf_quality_score=Decimal("7.40"),
        earnings_quality_score=Decimal("7.80"),
        capital_efficiency_score=Decimal("8.00"),
        balance_sheet_quality_score=Decimal("7.10"),
        valuation_quality_score=Decimal("5.90"),
        forward_quality_score=Decimal("6.50"),
        shareholder_quality_score=Decimal("5.80"),
        liquidity_risk_score=Decimal("7.70"),
        data_coverage_score=Decimal("8.70"),
        missing_data_penalty=Decimal("0.20"),
        v2_warning_flags_json={"flags": ["high_accrual_risk"]},
        trap_flags_json={"flags": ["high_accrual_risk"]},
        debug_json={
            "coverage": {
                "missing_core_fields": ["fcf_ttm"],
                "missing_high_fields": ["quick_ratio_quarterly"],
            }
        },
    )


def _technical(ticker: str) -> TechnicalScore:
    return TechnicalScore(
        run_id=1,
        ticker=ticker,
        technical_engine_version="4.0.0",
        trend_score=Decimal("8.10"),
        momentum_score=Decimal("7.90"),
        setup_score=Decimal("7.80"),
        risk_score=Decimal("2.10"),
        market_score=Decimal("8.20"),
        combined_relative_strength_score=Decimal("8.10"),
        htf_score=Decimal("7.60"),
        dual_score=Decimal("8.44"),
        technical_confidence="normal",
        stage="Stage 2",
        market_regime="Bull trend",
        leadership_score=Decimal("9.20"),
        vcp_score=Decimal("7.40"),
        box_tightness_score=Decimal("7.20"),
        breakout_quality_score=Decimal("8.80"),
        climax_risk_score=Decimal("4.20"),
        warning_flags_json=["failed_breakout"],
        sub_tags_json=["VCP", "Stage 2"],
        action_bias="Best buyable, R/R ok",
        suggested_stop=Decimal("92.25"),
        suggested_target=Decimal("111.50"),
        reward_risk=Decimal("2.50"),
        entry_risk_pct=Decimal("4.50"),
    )
