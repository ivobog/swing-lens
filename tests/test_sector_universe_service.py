from copy import deepcopy
from decimal import Decimal

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    TechnicalScore,
)
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_universe_service import SectorUniverseService


def test_universe_metrics_groups_sector_metrics_and_profile_distribution(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [
            _row("TECH1", "Information Technology"),
            _row("TECH2", "Technology"),
            _row("HEAL1", "Health Care"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [
            _fundamental("TECH1", "8.0"),
            _fundamental("TECH2", "7.0"),
            _fundamental("HEAL1", "5.0"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [
            _technical("TECH1", "Prime clean pullback", "9.0", warnings=["liquidity_warning"]),
            _technical("TECH2", "Fresh breakout", "8.0"),
            _technical("HEAL1", "Failed breakout", "4.0", warnings=["failed_breakout"]),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [
            _combined("TECH1", "Technology", 1, "8.5"),
            _combined("TECH2", "Technology", 12, "7.5"),
            _combined("HEAL1", "Healthcare", 40, "4.0"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [
            _ranking("momentum_swing", "TECH1", "Technology", 1, "9.0"),
            _ranking("momentum_swing", "TECH2", "Technology", 12, "8.0"),
            _ranking("momentum_swing", "HEAL1", "Healthcare", 40, "5.0"),
            _ranking("defensive_quality", "TECH1", "Technology", 3, "8.5"),
            _ranking("defensive_quality", "HEAL1", "Healthcare", 15, "6.0"),
        ],
    )

    rows = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )

    assert [row.sector for row in rows] == ["Healthcare", "Technology"]
    technology = rows[1]
    assert technology.ticker_count == 2
    assert technology.universe_share == 0.6667
    assert technology.average_fundamental_score == 7.5
    assert technology.average_technical_score == 8.5
    assert technology.average_final_score == 8.0
    assert technology.average_profile_score == 8.5
    assert technology.top_counts == {"top_10": 1, "top_25": 2, "top_50": 2}
    assert technology.buyable_count == 2
    assert technology.buyable_share == 1.0
    assert technology.clean_pullback_count == 1
    assert technology.breakout_count == 1
    assert technology.warning_distribution == {"liquidity_warning": 1}
    assert technology.raw_sector_distribution == {
        "Information Technology": 1,
        "Technology": 1,
    }
    assert technology.sector_mapping_status_counts == {"canonical": 1, "mapped": 1}
    assert technology.profile_distribution["momentum_swing"] == {
        "average_profile_score": 8.5,
        "top_10_count": 1,
        "top_25_count": 2,
        "best_rank": 1,
        "best_ticker": "TECH1",
    }
    assert technology.profile_distribution["defensive_quality"]["top_10_count"] == 1
    assert technology.component_scores == {
        "average_technical_score": 8.5,
        "average_profile_score": 8.5,
        "top_candidate_share": 5.0,
        "setup_density": 10.0,
        "risk_control": 10.0,
    }
    assert technology.universe_leadership_score == 8.325
    assert technology.confidence == "low"
    assert technology.reason_codes == [
        "strong_average_technical_score",
        "high_setup_density",
        "low_danger_density",
        "low_confidence_sector",
    ]

    healthcare = rows[0]
    assert healthcare.sector == "Healthcare"
    assert healthcare.danger_count == 1
    assert healthcare.danger_share == 1.0
    assert healthcare.warning_distribution == {"failed_breakout": 1}
    assert healthcare.component_scores["risk_control"] == 0.0
    assert healthcare.reason_codes == [
        "high_danger_density",
        "low_confidence_sector",
    ]


def test_universe_metrics_groups_missing_sector_as_unknown(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [_row("MISS", None)],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [_technical("MISS", "No trade", "5.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    rows = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )

    assert len(rows) == 1
    row = rows[0]
    assert row.sector == "Unknown"
    assert row.sector_slug == "unknown"
    assert row.missing_fundamental_count == 1
    assert row.missing_technical_count == 0
    assert row.average_profile_score is None
    assert row.top_counts == {"top_10": 0, "top_25": 0, "top_50": 0}
    assert row.warnings == [
        "missing_sector",
        "missing_fundamental_scores",
        "missing_ranking_profile_results",
        "low_confidence_sector",
    ]
    assert row.debug["raw_missing_sector_tickers"] == ["MISS"]
    assert row.raw_sector_distribution == {"(missing)": 1}
    assert row.sector_mapping_status_counts == {"missing": 1}


def test_universe_metrics_maps_tradingview_sectors_before_grouping(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [
            _row("NVDA", "Electronic technology"),
            _row("MSFT", "Technology services"),
            _row("XOM", "Energy minerals"),
            _row("ODD", "Made Up Sector"),
            _row("MISS", None),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    rows = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )

    by_sector = {row.sector: row for row in rows}
    assert sorted(by_sector) == ["Energy", "Technology", "Unknown"]
    assert by_sector["Technology"].ticker_count == 2
    assert by_sector["Technology"].raw_sector_distribution == {
        "Electronic technology": 1,
        "Technology services": 1,
    }
    assert by_sector["Technology"].sector_mapping_status_counts == {"mapped": 2}
    assert by_sector["Energy"].ticker_count == 1
    assert by_sector["Unknown"].ticker_count == 2
    assert by_sector["Unknown"].raw_sector_distribution == {
        "(missing)": 1,
        "Made Up Sector": 1,
    }
    assert by_sector["Unknown"].sector_mapping_status_counts == {
        "missing": 1,
        "unmapped": 1,
    }
    assert "unmapped_sector" in by_sector["Unknown"].warnings


def test_universe_metrics_deduplicates_raw_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [
            _row("DUP", "Technology"),
            _row("dup", "Healthcare"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [_fundamental("DUP", "8.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [_technical("DUP", "Volatility contraction setup", "7.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    rows = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )

    assert len(rows) == 1
    assert rows[0].sector == "Technology"
    assert rows[0].ticker_count == 1
    assert rows[0].watch_count == 1
    assert rows[0].watch_share == 1.0


def test_universe_metrics_falls_back_to_combined_top_counts_without_rankings(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [_row("TOP", "Technology"), _row("LATE", "Technology")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [
            _combined("TOP", "Technology", 4, "7.0"),
            _combined("LATE", "Technology", 30, "5.0"),
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    rows = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )

    assert rows[0].top_counts == {"top_10": 1, "top_25": 1, "top_50": 2}
    assert rows[0].average_final_score == 6.0
    assert rows[0].warnings == [
        "missing_technical_scores",
        "missing_fundamental_scores",
        "missing_ranking_profile_results",
        "low_confidence_sector",
    ]


def test_universe_metrics_empty_run_returns_empty_list(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    assert (
        SectorUniverseService().build(
            object(),
            run_id=7,
            config=load_sector_rotation_config(),
        )
        == []
    )


def test_universe_score_uses_final_score_fallback_when_profile_is_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [_row("FALL", "Technology")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [_fundamental("FALL", "7.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [_technical("FALL", "Clean bull pullback", "8.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [_combined("FALL", "Technology", 1, "6.5")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )

    row = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )[0]

    assert row.average_profile_score is None
    assert row.component_scores["average_profile_score"] == 6.5
    assert row.debug["component_debug"]["average_profile_score_source"] == (
        "final_score_fallback"
    )
    assert 0 <= row.universe_leadership_score <= 10


def test_universe_score_rewards_top_candidate_overrepresentation(monkeypatch) -> None:
    tech = [f"TECH{i}" for i in range(1, 11)]
    other = [f"OTHER{i}" for i in range(1, 31)]
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [
            *[_row(ticker, "Technology") for ticker in tech],
            *[_row(ticker, "Healthcare") for ticker in other],
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [_fundamental(ticker, "5.0") for ticker in [*tech, *other]],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [
            _technical(ticker, "No trade", "5.0")
            for ticker in [*tech, *other]
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [
            *[
                _ranking("momentum_swing", ticker, "Technology", index, "5.0")
                for index, ticker in enumerate(tech, start=1)
            ],
            *[
                _ranking("momentum_swing", ticker, "Healthcare", index + 10, "5.0")
                for index, ticker in enumerate(other, start=1)
            ],
        ],
    )

    rows = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )
    row = next(row for row in rows if row.sector == "Technology")

    assert row.top_counts["top_25"] == 10
    assert row.component_scores["top_candidate_share"] == 8.0
    assert row.confidence == "high"
    assert "top_candidate_overrepresentation" in row.reason_codes


def test_universe_score_does_not_reward_large_sector_for_size_alone(
    monkeypatch,
) -> None:
    big = [f"BIG{i}" for i in range(1, 31)]
    small = [f"SMALL{i}" for i in range(1, 6)]
    all_tickers = [*big, *small]
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [
            *[_row(ticker, "Technology") for ticker in big],
            *[_row(ticker, "Healthcare") for ticker in small],
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [_fundamental(ticker, "5.0") for ticker in all_tickers],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [
            *[_technical(ticker, "No trade", "5.0") for ticker in big],
            *[_technical(ticker, "Fresh breakout", "8.0") for ticker in small],
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [
            *[
                _ranking("momentum_swing", ticker, "Technology", index + 25, "5.0")
                for index, ticker in enumerate(big, start=1)
            ],
            *[
                _ranking("momentum_swing", ticker, "Healthcare", index, "8.0")
                for index, ticker in enumerate(small, start=1)
            ],
        ],
    )

    rows = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )

    healthcare = next(row for row in rows if row.sector == "Healthcare")
    technology = next(row for row in rows if row.sector == "Technology")
    assert technology.ticker_count > healthcare.ticker_count
    assert technology.universe_leadership_score < healthcare.universe_leadership_score


def test_universe_confidence_uses_ticker_count_and_technical_availability(
    monkeypatch,
) -> None:
    tickers = [f"NORM{i}" for i in range(1, 7)]
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [_row(ticker, "Technology") for ticker in tickers],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [_fundamental(ticker, "6.0") for ticker in tickers],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [
            _technical(ticker, "No trade", "6.0")
            for ticker in tickers[:3]
        ],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [
            _ranking("momentum_swing", ticker, "Technology", index, "6.0")
            for index, ticker in enumerate(tickers, start=1)
        ],
    )

    row = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )[0]

    assert row.confidence == "normal"
    assert row.debug["technical_availability"] == 0.5


def test_universe_confidence_low_when_technical_availability_is_poor(
    monkeypatch,
) -> None:
    tickers = [f"LOW{i}" for i in range(1, 7)]
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [_row(ticker, "Technology") for ticker in tickers],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [_fundamental(ticker, "6.0") for ticker in tickers],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [_technical(tickers[0], "No trade", "6.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [
            _ranking("momentum_swing", ticker, "Technology", index, "6.0")
            for index, ticker in enumerate(tickers, start=1)
        ],
    )

    row = SectorUniverseService().build(
        object(),
        run_id=7,
        config=load_sector_rotation_config(),
    )[0]

    assert row.confidence == "low"
    assert "low_confidence_sector" in row.warnings


def test_universe_score_weights_are_configurable(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.services.sector_universe_service._raw_rows_for_run",
        lambda _db, _run_id: [_row("CFG", "Technology")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._fundamentals_for_run",
        lambda _db, _run_id: [_fundamental("CFG", "5.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._technicals_for_run",
        lambda _db, _run_id: [_technical("CFG", "No trade", "7.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._combined_results_for_run",
        lambda _db, _run_id: [_combined("CFG", "Technology", 1, "3.0")],
    )
    monkeypatch.setattr(
        "app.services.sector_universe_service._ranking_results_for_run",
        lambda _db, _run_id: [],
    )
    config = deepcopy(load_sector_rotation_config())
    config["universe_score"]["weights"] = {
        "average_technical_score": 1.0,
        "average_profile_score": 0.0,
        "top_candidate_share": 0.0,
        "setup_density": 0.0,
        "risk_control": 0.0,
    }

    row = SectorUniverseService().build(object(), run_id=7, config=config)[0]

    assert row.universe_leadership_score == 7.0


def _row(ticker: str, sector: str | None) -> RawCompanyRow:
    return RawCompanyRow(
        run_id=7,
        row_number=1,
        ticker=ticker,
        company_name=f"{ticker.upper()} Corp",
        sector=sector,
        raw_json={"Symbol": ticker},
    )


def _fundamental(ticker: str, score: str) -> FundamentalScore:
    return FundamentalScore(
        run_id=7,
        ticker=ticker,
        fundamental_score=Decimal(score),
    )


def _technical(
    ticker: str,
    classification: str,
    dual: str,
    warnings: list[str] | None = None,
) -> TechnicalScore:
    return TechnicalScore(
        run_id=7,
        ticker=ticker,
        classification=classification,
        dual_score=Decimal(dual),
        warning_flags_json=warnings or [],
    )


def _combined(
    ticker: str,
    sector: str | None,
    rank: int,
    score: str,
    warnings: list[str] | None = None,
) -> CombinedResult:
    return CombinedResult(
        run_id=7,
        ticker=ticker,
        company_name=f"{ticker.upper()} Corp",
        sector=sector,
        final_rank=rank,
        final_score=Decimal(score),
        warning_flags_json=warnings or [],
    )


def _ranking(
    profile: str,
    ticker: str,
    sector: str | None,
    rank: int,
    score: str,
) -> RankingResult:
    return RankingResult(
        run_id=7,
        ticker=ticker,
        company_name=f"{ticker.upper()} Corp",
        sector=sector,
        ranking_profile=profile,
        ranking_label=profile.replace("_", " ").title(),
        profile_rank=rank,
        profile_score=Decimal(score),
        decision_label="Candidate",
    )
