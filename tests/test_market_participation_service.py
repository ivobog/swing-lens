from decimal import Decimal

from app.models.tables import RankingResult, RawCompanyRow, TechnicalScore
from app.services.market_participation_service import MarketParticipationService


def test_market_participation_counts_setups_warnings_and_sma_metrics(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_participation_service._raw_rows_for_run",
        lambda _db, _run_id: [
            _row("PULL"),
            _row("BRK"),
            _row("VCP"),
            _row("DANG"),
        ],
    )
    monkeypatch.setattr(
        "app.services.market_participation_service._technicals_for_run",
        lambda _db, _run_id: [
            _technical(
                "PULL",
                "Prime clean pullback",
                dual="8.0",
                derived={"above_sma50": True, "above_sma200": True},
            ),
            _technical(
                "BRK",
                "Fresh breakout",
                dual="7.0",
                derived={"above_sma50": True, "above_sma200": False},
            ),
            _technical(
                "VCP",
                "No trade",
                dual="6.0",
                vcp="7.5",
                warning_flags=["market_risk_off"],
                derived={"close": 90, "sma50": 100, "sma200": 80},
            ),
            _technical(
                "DANG",
                "Failed breakout",
                dual="5.0",
                derived={"above_sma50": False, "above_sma200": False},
            ),
        ],
    )
    monkeypatch.setattr(
        "app.services.market_participation_service._ranking_results_for_run",
        lambda _db, _run_id: [
            _ranking("momentum_swing", "PULL", "8.0"),
            _ranking("momentum_swing", "BRK", "6.0"),
            _ranking("defensive_quality", "DANG", "4.0"),
        ],
    )

    dto = MarketParticipationService().build(object(), run_id=7)

    assert dto.ticker_count == 4
    assert dto.technical_count == 4
    assert dto.average_technical_score == 6.5
    assert dto.clean_pullback_count == 1
    assert dto.fresh_breakout_count == 1
    assert dto.vcp_count == 1
    assert dto.danger_count == 1
    assert dto.market_risk_warning_count == 1
    assert dto.above_sma50_pct == 50.0
    assert dto.above_sma200_pct == 50.0
    assert dto.ranking_profile_average_scores == {
        "defensive_quality": 4.0,
        "momentum_swing": 7.0,
    }


def test_market_participation_missing_debug_metrics_are_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_participation_service._raw_rows_for_run",
        lambda _db, _run_id: [_row("MISS")],
    )
    monkeypatch.setattr(
        "app.services.market_participation_service._technicals_for_run",
        lambda _db, _run_id: [_technical("MISS", "No trade", dual="5.0")],
    )
    monkeypatch.setattr(
        "app.services.market_participation_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    dto = MarketParticipationService().build(object(), run_id=7)

    assert dto.above_sma50_pct is None
    assert dto.above_sma200_pct is None
    assert "SMA50 participation is unavailable in technical debug data." in dto.notes
    assert "SMA200 participation is unavailable in technical debug data." in dto.notes


def test_market_participation_empty_run_returns_notes(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.market_participation_service._raw_rows_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.market_participation_service._technicals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.market_participation_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    dto = MarketParticipationService().build(object(), run_id=7)

    assert dto.ticker_count == 0
    assert dto.technical_count == 0
    assert dto.average_technical_score is None
    assert "No run universe rows are available." in dto.notes
    assert "No technical scores are available." in dto.notes


def _row(ticker: str) -> RawCompanyRow:
    return RawCompanyRow(
        run_id=7,
        row_number=1,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        raw_json={"Symbol": ticker},
    )


def _technical(
    ticker: str,
    classification: str,
    dual: str,
    vcp: str | None = None,
    warning_flags: list[str] | None = None,
    derived: dict | None = None,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=7,
        ticker=ticker,
        classification=classification,
        dual_score=Decimal(dual),
        vcp_score=Decimal(vcp) if vcp is not None else None,
        warning_flags_json=warning_flags or [],
        debug_json={"derived": derived} if derived is not None else {},
    )


def _ranking(profile: str, ticker: str, score: str) -> RankingResult:
    return RankingResult(
        run_id=7,
        ticker=ticker,
        ranking_profile=profile,
        ranking_label=profile.replace("_", " ").title(),
        profile_rank=1,
        profile_score=Decimal(score),
        decision_label="Candidate",
    )
