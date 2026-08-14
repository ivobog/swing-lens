from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from time import perf_counter

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

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
from app.services.operational_metrics import operational_metrics
from app.settings import get_settings

DAILY_PRICE_TIMEFRAMES = ("1 day", "1d")
PRICE_BAR_SOURCE_ORDER = ("TRADES", "ADJUSTED_LAST")


@dataclass(frozen=True)
class TickerSourceContext:
    raw_row: RawCompanyRow
    fundamental_score: FundamentalScore | None = None
    technical_score: TechnicalScore | None = None
    combined_result: CombinedResult | None = None
    ranking_results: tuple[RankingResult, ...] = ()
    ranking_results_by_profile: dict[str, RankingResult] = field(default_factory=dict)
    market_regime_snapshot: MarketRegimeSnapshot | None = None
    sector_rotation_snapshot: SectorRotationSnapshot | None = None
    sector_rotation_row: SectorRotationRow | None = None
    price_bars: tuple[PriceBar, ...] = ()

    @property
    def ticker(self) -> str:
        return normalize_ticker(self.raw_row.ticker)

    @property
    def latest_completed_bar(self) -> PriceBar | None:
        return latest_completed_bar(self.price_bars)


@dataclass(frozen=True)
class RunSourceContext:
    upload_run: UploadRun
    market_regime_snapshot: MarketRegimeSnapshot | None
    sector_rotation_snapshot: SectorRotationSnapshot | None
    tickers: tuple[TickerSourceContext, ...]


class SetupLifecycleSourceLoader:
    def __init__(
        self,
        *,
        latest_bar_projection_enabled: bool | None = None,
        shadow_compare_enabled: bool | None = None,
    ) -> None:
        settings = get_settings()
        self.latest_bar_projection_enabled = (
            settings.setup_latest_bar_projection_enabled
            if latest_bar_projection_enabled is None
            else latest_bar_projection_enabled
        )
        self.shadow_compare_enabled = (
            settings.setup_latest_bar_projection_shadow_compare_enabled
            if shadow_compare_enabled is None
            else shadow_compare_enabled
        )
        self.last_metrics: dict[str, float] = {}

    def load_run_context(self, db: Session, run_id: int) -> RunSourceContext:
        upload_run = db.get(UploadRun, run_id)
        if upload_run is None:
            raise ValueError(f"Upload run {run_id} was not found.")
        if str(upload_run.status).upper() != "COMPLETED":
            raise ValueError(f"Upload run {run_id} is not completed.")

        raw_rows = tuple(
            db.scalars(
                select(RawCompanyRow)
                .where(RawCompanyRow.run_id == run_id)
                .order_by(RawCompanyRow.row_number)
            )
        )
        tickers = tuple(row.ticker.upper() for row in raw_rows if row.ticker)
        source_cutoff = _upload_run_cutoff_date(upload_run)
        price_bars = self._load_price_bars(db, tickers, cutoff=source_cutoff)
        operational_metrics.increment(
            "swinglens_setup_price_rows_materialized_total",
            len(price_bars),
            mode="latest_projection" if self.latest_bar_projection_enabled else "legacy",
        )
        technical_scores = tuple(
            db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id))
        )
        context_cutoff = _run_context_cutoff_date(
            upload_run=upload_run,
            raw_rows=raw_rows,
            technical_scores=technical_scores,
            price_bars=price_bars,
        )
        context_started_at = perf_counter()
        ticker_cutoffs = {
            normalize_ticker(row.ticker): (
                _ticker_context_cutoff_date(row, technical_scores, price_bars) or context_cutoff
            )
            for row in raw_rows
            if row.ticker and row.ticker.strip()
        }
        latest_cutoff = max(ticker_cutoffs.values(), default=context_cutoff)
        market_candidates = tuple(
            db.scalars(
                _latest_context_statement(MarketRegimeSnapshot, latest_cutoff)
                .where(
                    or_(
                        MarketRegimeSnapshot.run_id == run_id,
                        MarketRegimeSnapshot.run_id.is_(None),
                    )
                )
                .where(MarketRegimeSnapshot.is_current_revision.is_(True))
            )
        )
        sector_candidates = tuple(
            db.scalars(
                _latest_context_statement(SectorRotationSnapshot, latest_cutoff)
                .where(
                    or_(
                        SectorRotationSnapshot.run_id == run_id,
                        SectorRotationSnapshot.run_id.is_(None),
                    )
                )
                .where(SectorRotationSnapshot.is_current_revision.is_(True))
            )
        )
        market_by_ticker = {
            ticker: _select_context_candidate(market_candidates, cutoff, run_id)
            for ticker, cutoff in ticker_cutoffs.items()
        }
        sector_by_ticker = {
            ticker: _select_context_candidate(sector_candidates, cutoff, run_id)
            for ticker, cutoff in ticker_cutoffs.items()
        }
        market_snapshot = _select_context_candidate(market_candidates, latest_cutoff, run_id)
        sector_snapshot = _select_context_candidate(sector_candidates, latest_cutoff, run_id)
        sector_snapshot_ids = tuple(
            snapshot.id for snapshot in sector_candidates if snapshot.id is not None
        )
        sector_rows = (
            tuple(
                db.scalars(
                    select(SectorRotationRow).where(
                        SectorRotationRow.snapshot_id.in_(sector_snapshot_ids)
                    )
                )
            )
            if sector_snapshot_ids
            else ()
        )

        context = build_run_source_context(
            upload_run=upload_run,
            raw_rows=raw_rows,
            fundamental_scores=tuple(
                db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id))
            ),
            technical_scores=technical_scores,
            combined_results=tuple(
                db.scalars(select(CombinedResult).where(CombinedResult.run_id == run_id))
            ),
            ranking_results=tuple(
                db.scalars(select(RankingResult).where(RankingResult.run_id == run_id))
            ),
            market_regime_snapshot=market_snapshot,
            sector_rotation_snapshot=sector_snapshot,
            market_regime_snapshots_by_ticker=market_by_ticker,
            sector_rotation_snapshots_by_ticker=sector_by_ticker,
            sector_rotation_rows=sector_rows,
            price_bars=price_bars,
        )
        self.last_metrics["setup_context_build_ms"] = round(
            (perf_counter() - context_started_at) * 1000, 3
        )
        return context

    def _load_price_bars(
        self,
        db: Session,
        tickers: tuple[str, ...],
        *,
        cutoff: date,
    ) -> tuple[PriceBar, ...]:
        if not tickers:
            self.last_metrics["setup_latest_bar_query_ms"] = 0.0
            return ()

        started_at = perf_counter()
        legacy_price_bars: tuple[PriceBar, ...] | None = None
        projected_price_bars: tuple[PriceBar, ...] | None = None

        if self.latest_bar_projection_enabled or self.shadow_compare_enabled:
            projected_price_bars = tuple(
                db.scalars(
                    _latest_price_bar_history_statement(
                        tickers,
                        cutoff=cutoff,
                        session_count=2,
                    )
                )
            )
        if not self.latest_bar_projection_enabled or self.shadow_compare_enabled:
            legacy_price_bars = tuple(
                db.scalars(_legacy_price_bars_statement(tickers, cutoff=cutoff))
            )

        if self.shadow_compare_enabled:
            assert legacy_price_bars is not None
            assert projected_price_bars is not None
            mismatches = compare_latest_bar_selection(legacy_price_bars, projected_price_bars)
            operational_metrics.increment(
                "swinglens_setup_latest_bar_projection_shadow_comparisons_total"
            )
            if mismatches:
                operational_metrics.increment(
                    "swinglens_setup_latest_bar_projection_shadow_mismatches_total",
                    len(mismatches),
                )
                operational_metrics.increment(
                    "swinglens_setup_latest_bar_query_ms_total",
                    (perf_counter() - started_at) * 1000,
                    mode="shadow_fallback",
                )
                self.last_metrics["setup_latest_bar_query_ms"] = round(
                    (perf_counter() - started_at) * 1000, 3
                )
                return legacy_price_bars

        query_ms = (perf_counter() - started_at) * 1000
        operational_metrics.increment(
            "swinglens_setup_latest_bar_query_ms_total",
            query_ms,
            mode="latest_projection" if self.latest_bar_projection_enabled else "legacy",
        )
        self.last_metrics["setup_latest_bar_query_ms"] = round(query_ms, 3)
        if self.latest_bar_projection_enabled:
            assert projected_price_bars is not None
            return projected_price_bars
        assert legacy_price_bars is not None
        return legacy_price_bars

    def _latest_market_snapshot(
        self,
        db: Session,
        run_id: int,
        cutoff: date,
    ) -> MarketRegimeSnapshot | None:
        run_snapshot = db.scalar(
            _latest_context_statement(MarketRegimeSnapshot, cutoff)
            .where(MarketRegimeSnapshot.run_id == run_id)
            .order_by(
                MarketRegimeSnapshot.as_of_date.desc(),
                MarketRegimeSnapshot.created_at.desc(),
                MarketRegimeSnapshot.id.desc(),
            )
            .limit(1)
        )
        if run_snapshot is not None:
            return run_snapshot
        return db.scalar(
            _latest_context_statement(MarketRegimeSnapshot, cutoff)
            .where(MarketRegimeSnapshot.run_id.is_(None))
            .order_by(
                MarketRegimeSnapshot.as_of_date.desc(),
                MarketRegimeSnapshot.created_at.desc(),
                MarketRegimeSnapshot.id.desc(),
            )
            .limit(1)
        )

    def _latest_sector_snapshot(
        self,
        db: Session,
        run_id: int,
        cutoff: date,
    ) -> SectorRotationSnapshot | None:
        run_snapshot = db.scalar(
            _latest_context_statement(SectorRotationSnapshot, cutoff)
            .where(SectorRotationSnapshot.run_id == run_id)
            .order_by(
                SectorRotationSnapshot.as_of_date.desc(),
                SectorRotationSnapshot.created_at.desc(),
                SectorRotationSnapshot.id.desc(),
            )
            .limit(1)
        )
        if run_snapshot is not None:
            return run_snapshot
        return db.scalar(
            _latest_context_statement(SectorRotationSnapshot, cutoff)
            .where(SectorRotationSnapshot.run_id.is_(None))
            .order_by(
                SectorRotationSnapshot.as_of_date.desc(),
                SectorRotationSnapshot.created_at.desc(),
                SectorRotationSnapshot.id.desc(),
            )
            .limit(1)
        )


def build_run_source_context(
    *,
    upload_run: UploadRun,
    raw_rows: tuple[RawCompanyRow, ...],
    fundamental_scores: tuple[FundamentalScore, ...] = (),
    technical_scores: tuple[TechnicalScore, ...] = (),
    combined_results: tuple[CombinedResult, ...] = (),
    ranking_results: tuple[RankingResult, ...] = (),
    market_regime_snapshot: MarketRegimeSnapshot | None = None,
    sector_rotation_snapshot: SectorRotationSnapshot | None = None,
    market_regime_snapshots_by_ticker: dict[str, MarketRegimeSnapshot | None] | None = None,
    sector_rotation_snapshots_by_ticker: dict[str, SectorRotationSnapshot | None] | None = None,
    sector_rotation_rows: tuple[SectorRotationRow, ...] = (),
    price_bars: tuple[PriceBar, ...] = (),
) -> RunSourceContext:
    fundamentals = _by_ticker(fundamental_scores)
    technicals = _by_ticker(technical_scores)
    combined = _by_ticker(combined_results)
    rankings = _rankings_by_ticker(ranking_results)
    sector_rows = _sector_rows_by_snapshot_and_name(sector_rotation_rows)
    market_by_ticker = market_regime_snapshots_by_ticker or {}
    rotation_by_ticker = sector_rotation_snapshots_by_ticker or {}
    bars = _price_bars_by_ticker(price_bars)

    return RunSourceContext(
        upload_run=upload_run,
        market_regime_snapshot=market_regime_snapshot,
        sector_rotation_snapshot=sector_rotation_snapshot,
        tickers=tuple(
            TickerSourceContext(
                raw_row=row,
                fundamental_score=fundamentals.get(normalize_ticker(row.ticker)),
                technical_score=technicals.get(normalize_ticker(row.ticker)),
                combined_result=combined.get(normalize_ticker(row.ticker)),
                ranking_results=tuple(rankings.get(normalize_ticker(row.ticker), ())),
                ranking_results_by_profile={
                    ranking.ranking_profile: ranking
                    for ranking in rankings.get(normalize_ticker(row.ticker), ())
                },
                market_regime_snapshot=market_by_ticker.get(
                    normalize_ticker(row.ticker), market_regime_snapshot
                ),
                sector_rotation_snapshot=rotation_by_ticker.get(
                    normalize_ticker(row.ticker), sector_rotation_snapshot
                ),
                sector_rotation_row=sector_rows.get(
                    (
                        getattr(
                            rotation_by_ticker.get(
                                normalize_ticker(row.ticker), sector_rotation_snapshot
                            ),
                            "id",
                            None,
                        ),
                        _sector_key(row.sector_canonical or row.sector),
                    )
                ),
                price_bars=tuple(bars.get(normalize_ticker(row.ticker), ())),
            )
            for row in raw_rows
            if row.ticker and row.ticker.strip()
        ),
    )


def latest_completed_bar(price_bars: tuple[PriceBar, ...]) -> PriceBar | None:
    eligible = [
        row
        for row in price_bars
        if row.bar_date is not None
        and row.close is not None
        and row.what_to_show in PRICE_BAR_SOURCE_ORDER
    ]
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda row: (
            row.bar_date,
            -PRICE_BAR_SOURCE_ORDER.index(row.what_to_show),
            row.id or 0,
        ),
    )


def _legacy_price_bars_statement(tickers: tuple[str, ...], *, cutoff: date):
    return (
        select(PriceBar)
        .where(PriceBar.ticker.in_(tickers))
        .where(PriceBar.timeframe.in_(DAILY_PRICE_TIMEFRAMES))
        .where(PriceBar.what_to_show.in_(PRICE_BAR_SOURCE_ORDER))
        .where(PriceBar.bar_date <= cutoff)
        .order_by(PriceBar.ticker, PriceBar.bar_date)
    )


def _latest_price_bars_statement(tickers: tuple[str, ...], *, cutoff: date):
    return (
        select(PriceBar)
        .where(PriceBar.ticker.in_(tickers))
        .where(PriceBar.timeframe.in_(DAILY_PRICE_TIMEFRAMES))
        .where(PriceBar.what_to_show.in_(PRICE_BAR_SOURCE_ORDER))
        .where(PriceBar.bar_date <= cutoff)
        .where(PriceBar.close.is_not(None))
        .distinct(PriceBar.ticker)
        .order_by(
            PriceBar.ticker,
            PriceBar.bar_date.desc(),
            case(
                (PriceBar.what_to_show == "TRADES", 0),
                (PriceBar.what_to_show == "ADJUSTED_LAST", 1),
                else_=2,
            ),
            PriceBar.id.desc(),
        )
    )


def compare_latest_bar_selection(
    legacy_price_bars: tuple[PriceBar, ...],
    projected_price_bars: tuple[PriceBar, ...],
) -> tuple[str, ...]:
    legacy_by_ticker = _latest_bars_by_ticker(legacy_price_bars)
    projected_by_ticker = _latest_bars_by_ticker(projected_price_bars)
    mismatches: list[str] = []
    for ticker in sorted(set(legacy_by_ticker) | set(projected_by_ticker)):
        legacy = legacy_by_ticker.get(ticker)
        projected = projected_by_ticker.get(ticker)
        legacy_identity = _bar_identity(legacy)
        projected_identity = _bar_identity(projected)
        if legacy_identity != projected_identity:
            mismatches.append(
                f"{ticker}: legacy={legacy_identity!r}, projected={projected_identity!r}"
            )
    return tuple(mismatches)


def _bar_identity(bar: PriceBar | None) -> tuple[object, ...] | None:
    if bar is None:
        return None
    return (bar.id, bar.bar_date, bar.what_to_show)


def _latest_context_statement(model, cutoff: date):
    return select(model).where(model.as_of_date <= cutoff)


def _run_context_cutoff_date(
    *,
    upload_run: UploadRun,
    raw_rows: tuple[RawCompanyRow, ...],
    technical_scores: tuple[TechnicalScore, ...],
    price_bars: tuple[PriceBar, ...],
) -> date:
    latest_bars = _latest_bars_by_ticker(price_bars)
    technical_by_ticker = _by_ticker(technical_scores)
    ticker_cutoffs = [
        latest_bar.bar_date if latest_bar is not None else technical.created_at.date()
        for row in raw_rows
        if row.ticker
        and (
            (latest_bar := latest_bars.get(normalize_ticker(row.ticker))) is not None
            or (
                (technical := technical_by_ticker.get(normalize_ticker(row.ticker))) is not None
                and technical.created_at is not None
            )
        )
    ]
    if ticker_cutoffs:
        return min(ticker_cutoffs)
    timestamp = upload_run.processed_at or upload_run.uploaded_at
    if timestamp is not None:
        return timestamp.date()
    return date.today()


def _upload_run_cutoff_date(upload_run: UploadRun) -> date:
    timestamp = upload_run.processed_at or upload_run.uploaded_at
    if timestamp is None:
        raise ValueError("completed upload run requires a processed or uploaded timestamp")
    return timestamp.date()


def _ticker_context_cutoff_date(
    raw_row: RawCompanyRow,
    technical_scores: tuple[TechnicalScore, ...],
    price_bars: tuple[PriceBar, ...],
) -> date | None:
    ticker = normalize_ticker(raw_row.ticker)
    latest_bar = latest_completed_bar(
        tuple(row for row in price_bars if normalize_ticker(row.ticker) == ticker)
    )
    if latest_bar is not None:
        return latest_bar.bar_date
    technical = next(
        (row for row in technical_scores if normalize_ticker(row.ticker) == ticker),
        None,
    )
    if technical is not None and technical.created_at is not None:
        return technical.created_at.date()
    return None


def _latest_bars_by_ticker(
    price_bars: tuple[PriceBar, ...],
) -> dict[str, PriceBar]:
    grouped: dict[str, list[PriceBar]] = defaultdict(list)
    for row in price_bars:
        grouped[normalize_ticker(row.ticker)].append(row)
    return {
        ticker: latest
        for ticker, rows in grouped.items()
        if (latest := latest_completed_bar(tuple(rows))) is not None
    }


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def _by_ticker(rows) -> dict[str, object]:
    return {normalize_ticker(row.ticker): row for row in rows if row.ticker}


def _rankings_by_ticker(rows: tuple[RankingResult, ...]) -> dict[str, list[RankingResult]]:
    grouped: dict[str, list[RankingResult]] = defaultdict(list)
    for row in rows:
        grouped[normalize_ticker(row.ticker)].append(row)
    for ticker in grouped:
        grouped[ticker].sort(key=lambda row: (row.profile_rank, row.ranking_profile))
    return grouped


def _sector_rows_by_snapshot_and_name(
    rows: tuple[SectorRotationRow, ...],
) -> dict[tuple[int | None, str], SectorRotationRow]:
    return {(row.snapshot_id, _sector_key(row.sector)): row for row in rows}


def _select_context_candidate(rows, cutoff: date, run_id: int):
    eligible = [row for row in rows if row.as_of_date <= cutoff]
    if not eligible:
        return None
    run_rows = [row for row in eligible if row.run_id == run_id]
    pool = run_rows or [row for row in eligible if row.run_id is None]
    if not pool:
        return None
    return max(
        pool,
        key=lambda row: (
            row.as_of_date,
            row.created_at.timestamp() if row.created_at is not None else 0,
            row.id or 0,
        ),
    )


def _latest_price_bar_history_statement(
    tickers: tuple[str, ...],
    *,
    cutoff: date,
    session_count: int = 2,
):
    """Return one authoritative price source for each of the latest sessions per ticker."""
    safe_count = max(1, min(int(session_count), 10))
    source_priority = case(
        (PriceBar.what_to_show == "TRADES", 0),
        (PriceBar.what_to_show == "ADJUSTED_LAST", 1),
        else_=2,
    )
    ranked = (
        select(
            PriceBar.id.label("price_bar_id"),
            func.dense_rank()
            .over(
                partition_by=PriceBar.ticker,
                order_by=PriceBar.bar_date.desc(),
            )
            .label("date_rank"),
            func.row_number()
            .over(
                partition_by=(PriceBar.ticker, PriceBar.bar_date),
                order_by=(source_priority, PriceBar.id.desc()),
            )
            .label("source_rank"),
        )
        .where(PriceBar.ticker.in_(tickers))
        .where(PriceBar.timeframe.in_(DAILY_PRICE_TIMEFRAMES))
        .where(PriceBar.what_to_show.in_(PRICE_BAR_SOURCE_ORDER))
        .where(PriceBar.bar_date <= cutoff)
        .where(PriceBar.close.is_not(None))
        .subquery()
    )
    return (
        select(PriceBar)
        .join(ranked, ranked.c.price_bar_id == PriceBar.id)
        .where(ranked.c.date_rank <= safe_count)
        .where(ranked.c.source_rank == 1)
        .order_by(PriceBar.ticker, PriceBar.bar_date, PriceBar.id)
    )


def _price_bars_by_ticker(rows: tuple[PriceBar, ...]) -> dict[str, list[PriceBar]]:
    grouped: dict[str, list[PriceBar]] = defaultdict(list)
    for row in rows:
        grouped[normalize_ticker(row.ticker)].append(row)
    for ticker in grouped:
        grouped[ticker].sort(key=lambda row: (row.bar_date, row.what_to_show))
    return grouped


def _sector_key(value: str | None) -> str:
    return str(value or "").strip().casefold()
