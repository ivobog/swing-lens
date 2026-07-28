from decimal import Decimal

from app.models.tables import FundamentalScore, RankingResult, RawCompanyRow, TechnicalScore
from app.services.sector_leadership_service import SectorLeadershipService


def test_sector_leadership_groups_metrics_and_sorts_by_score(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_leadership_service._raw_rows_for_run",
        lambda _db, _run_id: [
            _row("TECH1", "Technology"),
            _row("TECH2", "Technology"),
            _row("UTIL1", "Utilities"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._technicals_for_run",
        lambda _db, _run_id: [
            _technical("TECH1", "Prime clean pullback", "9.0", vcp="8.0"),
            _technical("TECH2", "Fresh breakout", "8.0"),
            _technical("UTIL1", "Failed breakout", "3.0"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._fundamentals_for_run",
        lambda _db, _run_id: [
            _fundamental("TECH1", "8.0"),
            _fundamental("TECH2", "7.0"),
            _fundamental("UTIL1", "4.0"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._ranking_results_for_run",
        lambda _db, _run_id: [
            _ranking("TECH1", "Technology", 1),
            _ranking("TECH2", "Technology", 12),
            _ranking("UTIL1", "Utilities", 30),
        ],
    )

    rows = SectorLeadershipService().build(object(), run_id=7)

    assert [row.sector for row in rows] == ["Technology", "Utilities"]
    tech = rows[0]
    assert tech.ticker_count == 2
    assert tech.average_technical_score == 8.5
    assert tech.average_fundamental_score == 7.5
    assert tech.top_25_count == 2
    assert tech.clean_pullback_count == 1
    assert tech.breakout_count == 1
    assert tech.vcp_count == 1
    assert tech.danger_count == 0
    assert 0 <= tech.leadership_score <= 10


def test_sector_leadership_uses_unknown_for_missing_sector(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_leadership_service._raw_rows_for_run",
        lambda _db, _run_id: [_row("MISS", None)],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._technicals_for_run",
        lambda _db, _run_id: [_technical("MISS", "No trade", "5.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._fundamentals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    rows = SectorLeadershipService().build(object(), run_id=7)

    assert len(rows) == 1
    assert rows[0].sector == "Unknown"
    assert rows[0].warnings == ["missing_sector"]


def test_sector_leadership_empty_run_returns_empty_list(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_leadership_service._raw_rows_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._technicals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._fundamentals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_leadership_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    assert SectorLeadershipService().build(object(), run_id=7) == []


def _row(ticker: str, sector: str | None) -> RawCompanyRow:
    return RawCompanyRow(
        run_id=7,
        row_number=1,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector=sector,
        raw_json={"Symbol": ticker},
    )


def _technical(
    ticker: str,
    classification: str,
    dual: str,
    vcp: str | None = None,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=7,
        ticker=ticker,
        classification=classification,
        dual_score=Decimal(dual),
        vcp_score=Decimal(vcp) if vcp is not None else None,
    )


def _fundamental(ticker: str, score: str) -> FundamentalScore:
    return FundamentalScore(
        run_id=7,
        ticker=ticker,
        fundamental_score=Decimal(score),
    )


def _ranking(ticker: str, sector: str | None, rank: int) -> RankingResult:
    return RankingResult(
        run_id=7,
        ticker=ticker,
        sector=sector,
        ranking_profile="momentum_swing",
        ranking_label="Momentum Swing",
        profile_rank=rank,
        profile_score=Decimal("8.0"),
        decision_label="Candidate",
    )
