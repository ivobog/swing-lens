import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from app.models.tables import FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.ranking_profile_config import load_ranking_profiles
from app.services.ranking_profile_engine import rank_profile, rank_single_row

TODAY = date(2026, 7, 7)
FIXTURE_PATH = Path("tests/fixtures/ranking_profiles_golden.json")


def test_ranking_profiles_golden_universe_directional_behavior() -> None:
    fixture = _fixture()
    rows = _golden_rows()
    fundamentals = _golden_fundamentals()
    technicals = _golden_technicals()
    profiles = {profile.name: profile for profile in load_ranking_profiles()}

    assert list(profiles) == fixture["profile_order"]

    ranked_by_profile = {
        profile_name: rank_profile(
            profile=profiles[profile_name],
            rows=rows,
            fundamentals=fundamentals,
            technicals=technicals,
            config=_config(),
            today=TODAY,
        )
        for profile_name in fixture["profile_order"]
    }

    for profile_name, expected_ticker in fixture["expected_top_tickers"].items():
        assert ranked_by_profile[profile_name][0].ticker == expected_ticker

    for expectation in fixture["directional_expectations"]:
        profile_results = _by_ticker(ranked_by_profile[expectation["profile"]])
        higher = profile_results[expectation["higher"]]
        lower = profile_results[expectation["lower"]]
        assert higher.profile_rank < lower.profile_rank
        assert higher.profile_score > lower.profile_score


def test_ranking_profiles_golden_gate_outcomes() -> None:
    fixture = _fixture()
    profiles = load_ranking_profiles()
    rows_by_ticker = {row.ticker: row for row in [*_golden_rows(), _earnings_row()]}
    fundamentals = {
        **_golden_fundamentals(),
        "EARN": _fundamental("EARN", 9.0),
    }
    technicals = {
        **_golden_technicals(),
        "EARN": _technical(
            "EARN",
            trend=9.0,
            momentum=9.0,
            setup=9.0,
            risk=1.0,
            rs=9.0,
            breakout=9.0,
            vcp=8.8,
            box_tightness=8.8,
            derived={"rs_new_high": True},
        ),
    }

    for profile in profiles:
        for ticker, expectation in fixture["gate_expectations"].items():
            decision = rank_single_row(
                profile=profile,
                row=rows_by_ticker[ticker],
                fundamental=fundamentals.get(ticker),
                technical=technicals.get(ticker),
                config=_config(),
                today=TODAY,
            )

            assert decision.decision_label == expectation["decision"]
            assert expectation["required_warning"] in decision.warning_flags


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _golden_rows() -> list[RawCompanyRow]:
    return [
        _row("MOMO", row_number=1),
        _row("QUAL", row_number=2),
        _row("ROKT", row_number=3),
        _row("COMP", row_number=4),
        _row("RISK", row_number=5),
        _row("MISS", row_number=6),
    ]


def _golden_fundamentals() -> dict[str, FundamentalScore]:
    return {
        "MOMO": _fundamental("MOMO", 8.1),
        "QUAL": _fundamental("QUAL", 9.4),
        "ROKT": _fundamental("ROKT", 5.8),
        "COMP": _fundamental("COMP", 9.2),
        "RISK": _fundamental("RISK", 9.0),
        "MISS": _fundamental("MISS", 8.4),
    }


def _golden_technicals() -> dict[str, TechnicalScore]:
    return {
        "MOMO": _technical(
            "MOMO",
            trend=8.6,
            momentum=9.4,
            setup=8.7,
            risk=1.4,
            rs=9.6,
            breakout=8.8,
            vcp=8.4,
            box_tightness=8.3,
            derived={"rs_new_high": True},
        ),
        "QUAL": _technical(
            "QUAL",
            trend=8.1,
            momentum=8.0,
            setup=7.8,
            risk=1.5,
            rs=8.3,
            breakout=7.6,
            vcp=7.8,
            box_tightness=8.0,
        ),
        "ROKT": _technical(
            "ROKT",
            trend=8.8,
            momentum=9.5,
            setup=8.9,
            risk=1.7,
            rs=9.7,
            classification="Trend repair",
            breakout=9.4,
            vcp=7.6,
            box_tightness=7.4,
            derived={
                "rs_roc_short": 5.0,
                "rs_roc_medium": 4.0,
                "rs_new_high": True,
                "bullish_volume_bar": True,
                "breakout_volume_confirmed": True,
                "green_beats_red": True,
            },
        ),
        "COMP": _technical(
            "COMP",
            trend=8.2,
            momentum=7.0,
            setup=7.5,
            risk=1.3,
            rs=7.8,
            breakout=6.8,
            vcp=8.0,
            box_tightness=8.5,
            pullback_health="Healthy",
        ),
        "RISK": _technical(
            "RISK",
            trend=9.2,
            momentum=9.2,
            setup=9.0,
            risk=1.0,
            rs=9.1,
            classification="Failed breakout",
            breakout=9.0,
            vcp=8.8,
            box_tightness=8.6,
        ),
    }


def _earnings_row() -> RawCompanyRow:
    return _row(
        "EARN",
        row_number=7,
        earnings_date=date(2026, 7, 8),
        raw_earnings="2026-07-08",
    )


def _row(
    ticker: str,
    *,
    row_number: int,
    earnings_date: date | None = None,
    raw_earnings: str | None = None,
) -> RawCompanyRow:
    raw_json = {"Symbol": ticker}
    if raw_earnings is not None:
        raw_json["upcoming_earnings_date"] = raw_earnings
    return RawCompanyRow(
        run_id=1,
        row_number=row_number,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        upcoming_earnings_date=earnings_date,
        raw_json=raw_json,
    )


def _fundamental(ticker: str, score: float) -> FundamentalScore:
    return FundamentalScore(
        run_id=1,
        ticker=ticker,
        fundamental_score=Decimal(str(score)),
        fundamental_label="High-quality quant",
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
    breakout: float,
    vcp: float,
    box_tightness: float,
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
        dual_score=Decimal(str(round((trend + momentum + setup + rs) / 4.0, 4))),
        classification=classification,
        pullback_health=pullback_health,
        technical_confidence="normal",
        vcp_score=Decimal(str(vcp)),
        box_tightness_score=Decimal(str(box_tightness)),
        breakout_quality_score=Decimal(str(breakout)),
        climax_risk_score=Decimal("1.4"),
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


def _by_ticker(decisions) -> dict:
    return {decision.ticker: decision for decision in decisions}
