from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RankingResult,
    RawCompanyRow,
    TechnicalScore,
)
from app.services.market_participation_service import (
    CLEAN_PULLBACK_CLASSIFICATIONS,
    _is_vcp,
)
from app.services.sector_rotation_dtos import SectorUniverseMetrics
from app.services.sector_taxonomy import normalize_sector, sector_slug


class SectorUniverseService:
    def build(
        self,
        db: Session,
        run_id: int,
        config: dict[str, Any],
        default_profile: str | None = None,
    ) -> list[SectorUniverseMetrics]:
        return build_universe_sector_metrics(
            db=db,
            run_id=run_id,
            config=config,
            default_profile=default_profile,
        )


def build_universe_sector_metrics(
    db: Session,
    run_id: int,
    config: dict[str, Any],
    default_profile: str | None = None,
) -> list[SectorUniverseMetrics]:
    default_profile = default_profile or config["defaults"]["default_ranking_profile"]
    raw_rows = _unique_rows(_raw_rows_for_run(db, run_id))
    fundamentals = _by_ticker(_fundamentals_for_run(db, run_id))
    technicals = _by_ticker(_technicals_for_run(db, run_id))
    combined_results = _by_ticker(_combined_results_for_run(db, run_id))
    ranking_results = _ranking_results_for_run(db, run_id)
    rankings_by_ticker = _rankings_by_ticker(ranking_results)

    records = _ticker_records(
        raw_rows=raw_rows,
        fundamentals=fundamentals,
        technicals=technicals,
        combined_results=combined_results,
        rankings_by_ticker=rankings_by_ticker,
        config=config,
    )
    total_tickers = len(records)
    buckets: dict[str, _SectorBucket] = {}

    for record in records.values():
        bucket = buckets.setdefault(record.sector, _SectorBucket(sector=record.sector))
        bucket.add(
            record=record,
            fundamental=fundamentals.get(record.ticker),
            technical=technicals.get(record.ticker),
            combined=combined_results.get(record.ticker),
            rankings=rankings_by_ticker.get(record.ticker, []),
            default_profile=default_profile,
            config=config,
        )

    rows = [
        _to_metrics(
            bucket=bucket,
            total_tickers=total_tickers,
            default_profile=default_profile,
            cutoffs=config["defaults"]["top_candidate_cutoffs"],
        )
        for bucket in buckets.values()
    ]
    return sorted(rows, key=lambda row: (row.sector == "Unknown", row.sector))


@dataclass
class _TickerRecord:
    ticker: str
    company_name: str | None
    sector: str
    raw_sector_missing: bool = False


@dataclass
class _ProfileBucket:
    scores: list[Any] = field(default_factory=list)
    top_10_count: int = 0
    top_25_count: int = 0
    best_rank: int | None = None
    best_ticker: str | None = None

    def add(self, ranking: RankingResult) -> None:
        self.scores.append(ranking.profile_score)
        rank = int(ranking.profile_rank)
        if rank <= 10:
            self.top_10_count += 1
        if rank <= 25:
            self.top_25_count += 1
        if self.best_rank is None or rank < self.best_rank:
            self.best_rank = rank
            self.best_ticker = ranking.ticker.upper()


@dataclass
class _SectorBucket:
    sector: str
    tickers: set[str] = field(default_factory=set)
    raw_missing_sector_tickers: set[str] = field(default_factory=set)
    fundamental_scores: list[Any] = field(default_factory=list)
    technical_scores: list[Any] = field(default_factory=list)
    final_scores: list[Any] = field(default_factory=list)
    default_profile_scores: list[Any] = field(default_factory=list)
    top_counts: dict[str, int] = field(default_factory=dict)
    setup_distribution: dict[str, int] = field(default_factory=dict)
    warning_distribution: dict[str, int] = field(default_factory=dict)
    buyable_count: int = 0
    watch_count: int = 0
    danger_count: int = 0
    clean_pullback_count: int = 0
    breakout_count: int = 0
    vcp_count: int = 0
    tight_base_breakout_count: int = 0
    extended_or_overheated_count: int = 0
    missing_fundamental_count: int = 0
    missing_technical_count: int = 0
    profile_buckets: dict[str, _ProfileBucket] = field(default_factory=dict)

    def add(
        self,
        record: _TickerRecord,
        fundamental: FundamentalScore | None,
        technical: TechnicalScore | None,
        combined: CombinedResult | None,
        rankings: list[RankingResult],
        default_profile: str,
        config: dict[str, Any],
    ) -> None:
        self.tickers.add(record.ticker)
        if record.raw_sector_missing:
            self.raw_missing_sector_tickers.add(record.ticker)

        if fundamental is None or _float_or_none(fundamental.fundamental_score) is None:
            self.missing_fundamental_count += 1
        else:
            self.fundamental_scores.append(fundamental.fundamental_score)

        if technical is None or _float_or_none(technical.dual_score) is None:
            self.missing_technical_count += 1
        else:
            self.technical_scores.append(technical.dual_score)

        if combined is not None:
            self.final_scores.append(combined.final_score)
            _add_warnings(self.warning_distribution, combined.warning_flags_json)

        if technical is not None:
            self._add_technical(technical, config)

        default_ranking = _default_ranking(rankings, default_profile)
        if default_ranking is not None:
            self.default_profile_scores.append(default_ranking.profile_score)
            for cutoff in config["defaults"]["top_candidate_cutoffs"]:
                if default_ranking.profile_rank <= int(cutoff):
                    key = f"top_{int(cutoff)}"
                    self.top_counts[key] = self.top_counts.get(key, 0) + 1
        elif combined is not None and combined.final_rank is not None:
            for cutoff in config["defaults"]["top_candidate_cutoffs"]:
                if combined.final_rank <= int(cutoff):
                    key = f"top_{int(cutoff)}"
                    self.top_counts[key] = self.top_counts.get(key, 0) + 1

        for ranking in rankings:
            self.profile_buckets.setdefault(ranking.ranking_profile, _ProfileBucket()).add(
                ranking
            )

    def _add_technical(self, technical: TechnicalScore, config: dict[str, Any]) -> None:
        classification = technical.classification or "Other"
        self.setup_distribution[classification] = (
            self.setup_distribution.get(classification, 0) + 1
        )

        setup_labels = config["universe_score"]["setup_labels"]
        if classification in setup_labels["buyable"]:
            self.buyable_count += 1
        elif classification in setup_labels["watch"]:
            self.watch_count += 1
        elif classification in setup_labels["danger"]:
            self.danger_count += 1

        if classification in CLEAN_PULLBACK_CLASSIFICATIONS:
            self.clean_pullback_count += 1
        if classification == "Fresh breakout":
            self.breakout_count += 1
        if classification == "Tight base breakout":
            self.tight_base_breakout_count += 1
        if _is_vcp(technical):
            self.vcp_count += 1
        if classification in {
            "Extended momentum",
            "Overheated momentum",
            "Late-stage extension",
        }:
            self.extended_or_overheated_count += 1

        _add_warnings(self.warning_distribution, technical.warning_flags_json)


def _to_metrics(
    bucket: _SectorBucket,
    total_tickers: int,
    default_profile: str,
    cutoffs: list[int],
) -> SectorUniverseMetrics:
    ticker_count = len(bucket.tickers)
    warnings = _bucket_warnings(bucket, default_profile)
    top_counts = {
        f"top_{int(cutoff)}": bucket.top_counts.get(f"top_{int(cutoff)}", 0)
        for cutoff in cutoffs
    }

    return SectorUniverseMetrics(
        sector=bucket.sector,
        sector_slug=sector_slug(bucket.sector),
        ticker_count=ticker_count,
        universe_share=_share(ticker_count, total_tickers),
        average_fundamental_score=_average(bucket.fundamental_scores),
        average_technical_score=_average(bucket.technical_scores),
        average_final_score=_average(bucket.final_scores),
        average_profile_score=_average(bucket.default_profile_scores),
        top_counts=top_counts,
        setup_distribution=dict(sorted(bucket.setup_distribution.items())),
        warning_distribution=dict(sorted(bucket.warning_distribution.items())),
        buyable_count=bucket.buyable_count,
        watch_count=bucket.watch_count,
        danger_count=bucket.danger_count,
        buyable_share=_share(bucket.buyable_count, ticker_count),
        watch_share=_share(bucket.watch_count, ticker_count),
        danger_share=_share(bucket.danger_count, ticker_count),
        clean_pullback_count=bucket.clean_pullback_count,
        breakout_count=bucket.breakout_count,
        vcp_count=bucket.vcp_count,
        tight_base_breakout_count=bucket.tight_base_breakout_count,
        extended_or_overheated_count=bucket.extended_or_overheated_count,
        missing_fundamental_count=bucket.missing_fundamental_count,
        missing_technical_count=bucket.missing_technical_count,
        profile_distribution=_profile_distribution(bucket.profile_buckets),
        warnings=warnings,
        debug={
            "default_ranking_profile": default_profile,
            "raw_missing_sector_tickers": sorted(bucket.raw_missing_sector_tickers),
            "technical_score_available_count": len(bucket.technical_scores),
            "fundamental_score_available_count": len(bucket.fundamental_scores),
        },
    )


def _ticker_records(
    raw_rows: list[RawCompanyRow],
    fundamentals: dict[str, FundamentalScore],
    technicals: dict[str, TechnicalScore],
    combined_results: dict[str, CombinedResult],
    rankings_by_ticker: dict[str, list[RankingResult]],
    config: dict[str, Any],
) -> dict[str, _TickerRecord]:
    records: dict[str, _TickerRecord] = {}
    for row in raw_rows:
        ticker = row.ticker.upper()
        records[ticker] = _TickerRecord(
            ticker=ticker,
            company_name=row.company_name,
            sector=normalize_sector(row.sector, config),
            raw_sector_missing=not str(row.sector or "").strip(),
        )

    for ticker, combined in combined_results.items():
        records.setdefault(
            ticker,
            _TickerRecord(
                ticker=ticker,
                company_name=combined.company_name,
                sector=normalize_sector(combined.sector, config),
            ),
        )

    for ticker, rankings in rankings_by_ticker.items():
        first = rankings[0]
        records.setdefault(
            ticker,
            _TickerRecord(
                ticker=ticker,
                company_name=first.company_name,
                sector=normalize_sector(first.sector, config),
            ),
        )

    for ticker in sorted(set(fundamentals) | set(technicals)):
        records.setdefault(
            ticker,
            _TickerRecord(
                ticker=ticker,
                company_name=None,
                sector=normalize_sector(None, config),
                raw_sector_missing=True,
            ),
        )

    return records


def _profile_distribution(profile_buckets: dict[str, _ProfileBucket]) -> dict[str, Any]:
    distribution: dict[str, Any] = {}
    for profile, bucket in sorted(profile_buckets.items()):
        distribution[profile] = {
            "average_profile_score": _average(bucket.scores),
            "top_10_count": bucket.top_10_count,
            "top_25_count": bucket.top_25_count,
            "best_rank": bucket.best_rank,
            "best_ticker": bucket.best_ticker,
        }
    return distribution


def _bucket_warnings(bucket: _SectorBucket, default_profile: str) -> list[str]:
    warnings: list[str] = []
    if bucket.raw_missing_sector_tickers:
        warnings.append("missing_sector")
    if bucket.missing_technical_count:
        warnings.append("missing_technical_scores")
    if bucket.missing_fundamental_count:
        warnings.append("missing_fundamental_scores")
    if default_profile not in bucket.profile_buckets:
        warnings.append("missing_ranking_profile_results")
    return warnings


def _raw_rows_for_run(db: Session, run_id: int) -> list[RawCompanyRow]:
    return list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )


def _fundamentals_for_run(db: Session, run_id: int) -> list[FundamentalScore]:
    return list(db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id)))


def _technicals_for_run(db: Session, run_id: int) -> list[TechnicalScore]:
    return list(db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id)))


def _combined_results_for_run(db: Session, run_id: int) -> list[CombinedResult]:
    return list(db.scalars(select(CombinedResult).where(CombinedResult.run_id == run_id)))


def _ranking_results_for_run(db: Session, run_id: int) -> list[RankingResult]:
    return list(db.scalars(select(RankingResult).where(RankingResult.run_id == run_id)))


def _unique_rows(rows: list[RawCompanyRow]) -> list[RawCompanyRow]:
    seen: set[str] = set()
    unique: list[RawCompanyRow] = []
    for row in rows:
        ticker = row.ticker.upper()
        if ticker not in seen:
            seen.add(ticker)
            unique.append(row)
    return unique


def _by_ticker(rows: list[Any]) -> dict[str, Any]:
    return {row.ticker.upper(): row for row in rows if row.ticker}


def _rankings_by_ticker(rankings: list[RankingResult]) -> dict[str, list[RankingResult]]:
    grouped: dict[str, list[RankingResult]] = {}
    for ranking in rankings:
        grouped.setdefault(ranking.ticker.upper(), []).append(ranking)
    for ticker in grouped:
        grouped[ticker] = sorted(
            grouped[ticker],
            key=lambda ranking: (ranking.ranking_profile, ranking.profile_rank),
        )
    return grouped


def _default_ranking(
    rankings: list[RankingResult],
    default_profile: str,
) -> RankingResult | None:
    for ranking in rankings:
        if ranking.ranking_profile == default_profile:
            return ranking
    return None


def _add_warnings(distribution: dict[str, int], flags: list[str] | None) -> None:
    for flag in flags or []:
        text = str(flag).strip()
        if text:
            distribution[text] = distribution.get(text, 0) + 1


def _average(values: list[Any]) -> float | None:
    numbers = [_float_or_none(value) for value in values]
    available = [value for value in numbers if value is not None]
    if not available:
        return None
    return round(sum(available) / len(available), 4)


def _share(count: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(count / denominator, 4)


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
