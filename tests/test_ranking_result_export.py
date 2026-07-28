import csv
from datetime import date
from decimal import Decimal
from io import StringIO

from app.models.tables import RankingResult
from app.services import ranking_result_export
from app.services.ranking_result_export import (
    RANKING_RESULT_HEADERS,
    export_all_ranking_profiles_csv,
    export_ranking_profile_csv,
)


def test_export_ranking_profile_csv_uses_profile_rank_order(monkeypatch) -> None:
    rows = [
        _ranking_result("MSFT", profile="momentum_swing", rank=1, score="8.75"),
        _ranking_result("AAPL", profile="momentum_swing", rank=2, score="8.25"),
    ]
    monkeypatch.setattr(
        ranking_result_export,
        "get_ranking_results",
        lambda _db, run_id, profile_name: rows,
    )

    csv_text = export_ranking_profile_csv(object(), run_id=7, profile_name="momentum_swing")
    exported = list(csv.DictReader(StringIO(csv_text)))

    assert csv_text.startswith(",".join(RANKING_RESULT_HEADERS))
    assert [row["ticker"] for row in exported] == ["MSFT", "AAPL"]
    assert exported[0]["rank"] == "1"
    assert exported[0]["profile_name"] == "momentum_swing"
    assert exported[0]["profile_label"] == "Momentum Swing"
    assert exported[0]["profile_score"] == "8.75"
    assert exported[0]["technical_profile_score"] == "8.44"
    assert exported[0]["fundamental_score"] == "7.90"
    assert exported[0]["base_technical_score"] == "8.20"
    assert exported[0]["decision"] == "Strong candidate"


def test_export_all_ranking_profiles_csv_includes_all_profiles(monkeypatch) -> None:
    rows = [
        _ranking_result("ROKT", profile="early_rocket", label="Early Rocket", rank=1),
        _ranking_result(
            "COMP",
            profile="clean_compounder_pullback",
            label="Clean Compounder Pullback",
            rank=1,
        ),
    ]
    monkeypatch.setattr(
        ranking_result_export,
        "get_all_ranking_results",
        lambda _db, run_id: rows,
    )

    exported = list(csv.DictReader(StringIO(export_all_ranking_profiles_csv(object(), run_id=7))))

    assert [row["profile_name"] for row in exported] == [
        "early_rocket",
        "clean_compounder_pullback",
    ]
    assert [row["ticker"] for row in exported] == ["ROKT", "COMP"]


def test_ranking_result_export_renders_warning_flags_and_earnings_context(monkeypatch) -> None:
    rows = [
        _ranking_result(
            "MSFT",
            warning_flags=["earnings_medium_risk", "liquidity_warning"],
            earnings_date=date(2026, 7, 14),
            days_until_earnings=7,
            earnings_risk="medium",
        )
    ]
    monkeypatch.setattr(
        ranking_result_export,
        "get_ranking_results",
        lambda _db, run_id, profile_name: rows,
    )

    row = next(
        csv.DictReader(
            StringIO(export_ranking_profile_csv(object(), run_id=7, profile_name="momentum_swing"))
        )
    )

    assert row["warning_flags"] == "earnings_medium_risk; liquidity_warning"
    assert row["earnings_date"] == "2026-07-14"
    assert row["days_until_earnings"] == "7"
    assert row["earnings_risk"] == "medium"


def test_ranking_result_export_outputs_blanks_for_missing_optional_fields(monkeypatch) -> None:
    rows = [
        _ranking_result(
            "MISS",
            company_name=None,
            sector=None,
            technical_profile_score=None,
            fundamental_score=None,
            base_technical_score=None,
            technical_classification=None,
            fundamental_label=None,
            position_size_hint=None,
            notes=None,
            warning_flags=None,
            earnings_date=None,
            days_until_earnings=None,
            earnings_risk=None,
        )
    ]
    monkeypatch.setattr(
        ranking_result_export,
        "get_ranking_results",
        lambda _db, run_id, profile_name: rows,
    )

    row = next(
        csv.DictReader(
            StringIO(export_ranking_profile_csv(object(), run_id=7, profile_name="momentum_swing"))
        )
    )

    assert row["company_name"] == ""
    assert row["sector"] == ""
    assert row["technical_profile_score"] == ""
    assert row["fundamental_score"] == ""
    assert row["base_technical_score"] == ""
    assert row["technical_classification"] == ""
    assert row["fundamental_label"] == ""
    assert row["position_size_hint"] == ""
    assert row["notes"] == ""
    assert row["warning_flags"] == ""
    assert row["earnings_date"] == ""
    assert row["days_until_earnings"] == ""
    assert row["earnings_risk"] == ""


def _ranking_result(
    ticker: str,
    *,
    profile: str = "momentum_swing",
    label: str = "Momentum Swing",
    rank: int = 1,
    score: str = "8.75",
    company_name: str | None = "Microsoft",
    sector: str | None = "Technology",
    technical_profile_score: Decimal | None = Decimal("8.44"),
    fundamental_score: Decimal | None = Decimal("7.90"),
    base_technical_score: Decimal | None = Decimal("8.20"),
    technical_classification: str | None = "Prime clean pullback",
    fundamental_label: str | None = "High-quality quant",
    decision: str = "Strong candidate",
    position_size_hint: str | None = "Full starter",
    notes: str | None = "aligned",
    warning_flags: list[str] | None = None,
    earnings_date: date | None = None,
    days_until_earnings: int | None = None,
    earnings_risk: str | None = None,
) -> RankingResult:
    return RankingResult(
        run_id=7,
        ticker=ticker,
        company_name=company_name,
        sector=sector,
        ranking_profile=profile,
        ranking_label=label,
        profile_rank=rank,
        profile_score=Decimal(score),
        technical_profile_score=technical_profile_score,
        fundamental_score=fundamental_score,
        base_technical_score=base_technical_score,
        technical_classification=technical_classification,
        fundamental_label=fundamental_label,
        decision_label=decision,
        position_size_hint=position_size_hint,
        notes=notes,
        warning_flags_json=warning_flags if warning_flags is not None else [],
        penalties_json={},
        gates_json={},
        component_scores_json={},
        debug_json={},
        upcoming_earnings_date=earnings_date,
        days_until_earnings=days_until_earnings,
        earnings_risk_level=earnings_risk,
        is_complete=True,
        has_warning=bool(warning_flags),
        has_fundamental=fundamental_score is not None,
        has_technical=technical_profile_score is not None,
        sort_bucket=0,
    )
