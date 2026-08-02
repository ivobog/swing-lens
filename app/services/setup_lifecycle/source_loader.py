from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import select
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
        price_bars = tuple(
            db.scalars(
                select(PriceBar)
                .where(PriceBar.ticker.in_(tickers))
                .where(PriceBar.timeframe.in_(DAILY_PRICE_TIMEFRAMES))
                .where(PriceBar.what_to_show.in_(PRICE_BAR_SOURCE_ORDER))
                .order_by(PriceBar.ticker, PriceBar.bar_date)
            )
        ) if tickers else ()
        technical_scores = tuple(
            db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id))
        )
        context_cutoff = _run_context_cutoff_date(
            upload_run=upload_run,
            raw_rows=raw_rows,
            technical_scores=technical_scores,
            price_bars=price_bars,
        )
        market_snapshot = self._latest_market_snapshot(db, run_id, context_cutoff)
        sector_snapshot = self._latest_sector_snapshot(db, run_id, context_cutoff)
        sector_rows = tuple(
            db.scalars(
                select(SectorRotationRow).where(
                    SectorRotationRow.snapshot_id == sector_snapshot.id
                )
            )
        ) if sector_snapshot is not None else ()

        return build_run_source_context(
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
            sector_rotation_rows=sector_rows,
            price_bars=price_bars,
        )

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
    sector_rotation_rows: tuple[SectorRotationRow, ...] = (),
    price_bars: tuple[PriceBar, ...] = (),
) -> RunSourceContext:
    fundamentals = _by_ticker(fundamental_scores)
    technicals = _by_ticker(technical_scores)
    combined = _by_ticker(combined_results)
    rankings = _rankings_by_ticker(ranking_results)
    sector_rows = _sector_rows_by_name(sector_rotation_rows)
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
                market_regime_snapshot=market_regime_snapshot,
                sector_rotation_snapshot=sector_rotation_snapshot,
                sector_rotation_row=sector_rows.get(
                    _sector_key(row.sector_canonical or row.sector)
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


def _latest_context_statement(model, cutoff: date):
    return select(model).where(model.as_of_date <= cutoff)


def _run_context_cutoff_date(
    *,
    upload_run: UploadRun,
    raw_rows: tuple[RawCompanyRow, ...],
    technical_scores: tuple[TechnicalScore, ...],
    price_bars: tuple[PriceBar, ...],
) -> date:
    ticker_cutoffs = [
        cutoff
        for row in raw_rows
        if row.ticker and (cutoff := _ticker_context_cutoff_date(row, technical_scores, price_bars))
        is not None
    ]
    if ticker_cutoffs:
        return min(ticker_cutoffs)
    timestamp = upload_run.processed_at or upload_run.uploaded_at
    if timestamp is not None:
        return timestamp.date()
    return date.today()


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


def _sector_rows_by_name(rows: tuple[SectorRotationRow, ...]) -> dict[str, SectorRotationRow]:
    return {_sector_key(row.sector): row for row in rows}


def _price_bars_by_ticker(rows: tuple[PriceBar, ...]) -> dict[str, list[PriceBar]]:
    grouped: dict[str, list[PriceBar]] = defaultdict(list)
    for row in rows:
        grouped[normalize_ticker(row.ticker)].append(row)
    for ticker in grouped:
        grouped[ticker].sort(key=lambda row: (row.bar_date, row.what_to_show))
    return grouped


def _sector_key(value: str | None) -> str:
    return str(value or "").strip().casefold()
