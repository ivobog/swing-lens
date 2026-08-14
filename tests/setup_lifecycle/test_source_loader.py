from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.dialects import postgresql

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    MarketRegimeSnapshot,
    PriceBar,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    TechnicalScore,
    UploadRun,
)
from app.services.setup_lifecycle.source_loader import (
    SetupLifecycleSourceLoader,
    _latest_price_bar_history_statement,
    _latest_price_bars_statement,
    _run_context_cutoff_date,
    _select_context_candidate,
    build_run_source_context,
    compare_latest_bar_selection,
    latest_completed_bar,
)


def test_build_run_source_context_indexes_ticker_sources_and_context() -> None:
    upload = _upload_run()
    raw = _raw_row("msft", sector="Technology")
    raw.run = upload
    market = _market_snapshot()
    sector_snapshot = _sector_snapshot()
    sector_row = _sector_row("Technology")
    low_rank = _ranking("MSFT", "growth", 8, 91)
    high_rank = _ranking("MSFT", "quality", 2, 95)

    context = build_run_source_context(
        upload_run=upload,
        raw_rows=(raw,),
        fundamental_scores=(_fundamental("MSFT"),),
        technical_scores=(_technical("MSFT"),),
        combined_results=(_combined("MSFT"),),
        ranking_results=(low_rank, high_rank),
        market_regime_snapshot=market,
        sector_rotation_snapshot=sector_snapshot,
        sector_rotation_rows=(sector_row,),
        price_bars=(_bar("MSFT", date(2026, 8, 1), close=101),),
    )

    ticker_context = context.tickers[0]

    assert ticker_context.ticker == "MSFT"
    assert ticker_context.fundamental_score is not None
    assert ticker_context.technical_score is not None
    assert ticker_context.combined_result is not None
    assert [row.ranking_profile for row in ticker_context.ranking_results] == ["quality", "growth"]
    assert ticker_context.ranking_results_by_profile["quality"].profile_rank == 2
    assert ticker_context.market_regime_snapshot is market
    assert ticker_context.sector_rotation_snapshot is sector_snapshot
    assert ticker_context.sector_rotation_row is sector_row
    assert ticker_context.latest_completed_bar is not None


def test_latest_completed_bar_prefers_latest_trade_bar() -> None:
    older = _bar("MSFT", date(2026, 7, 31), close=99, what_to_show="TRADES")
    adjusted = _bar("MSFT", date(2026, 8, 1), close=100, what_to_show="ADJUSTED_LAST")
    trades = _bar("MSFT", date(2026, 8, 1), close=101, what_to_show="TRADES")

    assert latest_completed_bar((older, adjusted, trades)) is trades


def test_latest_price_bar_projection_query_is_one_row_per_ticker() -> None:
    statement = _latest_price_bars_statement(
        ("MSFT", "AAPL"),
        cutoff=date(2026, 8, 1),
    )
    rendered = str(statement.compile(dialect=postgresql.dialect()))

    assert "DISTINCT ON (price_bars.ticker)" in rendered
    assert "price_bars.close IS NOT NULL" in rendered
    assert "price_bars.bar_date <=" in rendered
    assert "price_bars.bar_date DESC" in rendered
    assert "CASE WHEN (price_bars.what_to_show =" in rendered


def test_price_bar_history_projection_keeps_two_sessions_with_one_source_each() -> None:
    statement = _latest_price_bar_history_statement(
        ("MSFT", "AAPL"),
        cutoff=date(2026, 8, 13),
        session_count=2,
    )
    rendered = str(statement.compile(dialect=postgresql.dialect()))

    assert "dense_rank() OVER" in rendered
    assert "row_number() OVER" in rendered
    assert "date_rank <=" in rendered
    assert "source_rank =" in rendered


def test_latest_bar_projection_shadow_comparison_detects_lineage_drift() -> None:
    legacy = (_bar("MSFT", date(2026, 8, 1), close=101),)
    projected = (_bar("MSFT", date(2026, 8, 1), close=102),)

    mismatches = compare_latest_bar_selection(legacy, projected)

    assert mismatches == (
        "MSFT: legacy=(1002, datetime.date(2026, 8, 1), 'TRADES'), "
        "projected=(1003, datetime.date(2026, 8, 1), 'TRADES')",
    )


def test_run_context_cutoff_uses_earliest_ticker_source_date() -> None:
    older = _raw_row("MSFT")
    newer = _raw_row("AAPL")

    cutoff = _run_context_cutoff_date(
        upload_run=_upload_run(),
        raw_rows=(older, newer),
        technical_scores=(),
        price_bars=(
            _bar("MSFT", date(2026, 7, 29), close=101),
            _bar("AAPL", date(2026, 8, 1), close=102),
        ),
    )

    assert cutoff == date(2026, 7, 29)


def test_point_in_time_context_is_selected_per_ticker_cutoff() -> None:
    older = _market_snapshot()
    older.id = 601
    older.as_of_date = date(2026, 7, 29)
    older.regime = "NEUTRAL"
    newer = _market_snapshot()
    newer.id = 602
    newer.as_of_date = date(2026, 8, 1)
    newer.regime = "RISK_ON"

    assert _select_context_candidate((older, newer), date(2026, 7, 29), 7) is older
    assert _select_context_candidate((older, newer), date(2026, 8, 1), 7) is newer

    rows = (_raw_row("OLD"), _raw_row("NEW"))
    context = build_run_source_context(
        upload_run=_upload_run(),
        raw_rows=rows,
        market_regime_snapshot=newer,
        market_regime_snapshots_by_ticker={"OLD": older, "NEW": newer},
    )

    assert context.tickers[0].market_regime_snapshot.regime == "NEUTRAL"
    assert context.tickers[1].market_regime_snapshot.regime == "RISK_ON"


def test_source_loader_market_and_sector_context_queries_are_asof_bounded() -> None:
    loader = SetupLifecycleSourceLoader()
    db = StatementRecordingDb()

    assert loader._latest_market_snapshot(db, run_id=7, cutoff=date(2026, 8, 1)) is None
    assert loader._latest_sector_snapshot(db, run_id=7, cutoff=date(2026, 8, 1)) is None

    rendered = "\n".join(str(statement) for statement in db.statements)
    assert "market_regime_snapshots.as_of_date <= :as_of_date_1" in rendered
    assert "sector_rotation_snapshots.as_of_date <= :as_of_date_1" in rendered
    assert "market_regime_snapshots.run_id IS NULL" in rendered
    assert "sector_rotation_snapshots.run_id IS NULL" in rendered


class StatementRecordingDb:
    def __init__(self) -> None:
        self.statements = []

    def scalar(self, statement):
        self.statements.append(statement)
        return None


def _upload_run() -> UploadRun:
    return UploadRun(
        id=7,
        filename="run.csv",
        status="COMPLETED",
        uploaded_at=datetime(2026, 8, 1, 21, 30, tzinfo=UTC),
        processed_at=datetime(2026, 8, 1, 21, 35, tzinfo=UTC),
    )


def _raw_row(ticker: str, *, sector: str = "Technology") -> RawCompanyRow:
    return RawCompanyRow(
        id=101,
        run_id=7,
        row_number=1,
        ticker=ticker,
        company_name="Microsoft",
        sector=sector,
        sector_canonical=sector,
        raw_json={"pivot_price": "100", "trigger_price": "100"},
    )


def _fundamental(ticker: str) -> FundamentalScore:
    return FundamentalScore(
        id=201,
        run_id=7,
        ticker=ticker,
        fundamental_score=Decimal("88"),
        liquidity_risk_score=Decimal("8"),
    )


def _technical(ticker: str) -> TechnicalScore:
    return TechnicalScore(
        id=301,
        run_id=7,
        ticker=ticker,
        dual_score=Decimal("8.2"),
        setup_score=Decimal("7.8"),
        classification="Breakout",
        stage="PIVOT_READY",
        data_quality_score=Decimal("9.0"),
    )


def _combined(ticker: str) -> CombinedResult:
    return CombinedResult(
        id=401,
        run_id=7,
        ticker=ticker,
        final_score=Decimal("92"),
        combined_decision="Buyable",
        is_complete=True,
    )


def _ranking(ticker: str, profile: str, rank: int, score: int) -> RankingResult:
    return RankingResult(
        id=500 + rank,
        run_id=7,
        ticker=ticker,
        ranking_profile=profile,
        ranking_label=profile.title(),
        profile_rank=rank,
        profile_score=Decimal(score),
        decision_label="Buyable",
    )


def _market_snapshot() -> MarketRegimeSnapshot:
    return MarketRegimeSnapshot(
        id=601,
        run_id=7,
        as_of_date=date(2026, 8, 1),
        calculation_version="v1",
        regime="RISK_ON",
        risk_state="NORMAL",
        score=80.0,
        action_summary="Constructive",
    )


def _sector_snapshot() -> SectorRotationSnapshot:
    return SectorRotationSnapshot(
        id=701,
        run_id=7,
        as_of_date=date(2026, 8, 1),
        calculation_version="v1",
        mode="LIVE",
    )


def _sector_row(sector: str) -> SectorRotationRow:
    return SectorRotationRow(
        id=801,
        snapshot_id=701,
        sector=sector,
        sector_slug=sector.lower(),
        rotation_state="LEADING",
        sector_permission="ALLOW",
        confidence="HIGH",
        current_rank=1,
    )


def _bar(
    ticker: str,
    bar_date: date,
    *,
    close: int,
    what_to_show: str = "TRADES",
) -> PriceBar:
    return PriceBar(
        id=901 + close,
        ticker=ticker,
        bar_date=bar_date,
        timeframe="1 day",
        open=Decimal(close - 1),
        high=Decimal(close + 1),
        low=Decimal(close - 2),
        close=Decimal(close),
        volume=Decimal("1000000"),
        source="IBKR",
        what_to_show=what_to_show,
        data_hash=f"{ticker}-{bar_date.isoformat()}-{close}-{what_to_show}",
    )
