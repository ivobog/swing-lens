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
from app.services.sector_taxonomy import (
    SectorNormalizationResult,
    normalize_sector_result,
    sector_slug,
)


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
            config=config,
        )
        for bucket in buckets.values()
    ]
    return sorted(rows, key=lambda row: (row.sector == "Unknown", row.sector))


@dataclass
class _TickerRecord:
    ticker: str
    company_name: str | None
    sector: str
    raw_sector: str | None = None
    sector_taxonomy: str | None = None
    sector_mapping_status: str = "missing"


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
    unmapped_sector_tickers: set[str] = field(default_factory=set)
    raw_sector_distribution: dict[str, int] = field(default_factory=dict)
    sector_mapping_status_counts: dict[str, int] = field(default_factory=dict)
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
        raw_distribution_key = record.raw_sector or "(missing)"
        self.raw_sector_distribution[raw_distribution_key] = (
            self.raw_sector_distribution.get(raw_distribution_key, 0) + 1
        )
        self.sector_mapping_status_counts[record.sector_mapping_status] = (
            self.sector_mapping_status_counts.get(record.sector_mapping_status, 0) + 1
        )
        if record.sector_mapping_status == "missing":
            self.raw_missing_sector_tickers.add(record.ticker)
        elif record.sector_mapping_status == "unmapped":
            self.unmapped_sector_tickers.add(record.ticker)

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
    config: dict[str, Any],
) -> SectorUniverseMetrics:
    ticker_count = len(bucket.tickers)
    warnings = _bucket_warnings(bucket, default_profile)
    top_counts = {
        f"top_{int(cutoff)}": bucket.top_counts.get(f"top_{int(cutoff)}", 0)
        for cutoff in cutoffs
    }
    averages = {
        "fundamental": _average(bucket.fundamental_scores),
        "technical": _average(bucket.technical_scores),
        "final": _average(bucket.final_scores),
        "profile": _average(bucket.default_profile_scores),
    }
    component_result = _universe_score_components(
        bucket=bucket,
        ticker_count=ticker_count,
        total_tickers=total_tickers,
        average_technical_score=averages["technical"],
        average_profile_score=averages["profile"],
        average_final_score=averages["final"],
        top_counts=top_counts,
        config=config,
    )
    confidence = _sector_confidence(
        ticker_count=ticker_count,
        technical_available_count=len(bucket.technical_scores),
        config=config,
    )
    reason_codes = _reason_codes(
        component_scores=component_result.component_scores,
        confidence=confidence,
        danger_share=_share(bucket.danger_count, ticker_count),
    )
    if confidence == "low":
        warnings = _append_unique(warnings, "low_confidence_sector")
    elif confidence == "insufficient":
        warnings = _append_unique(warnings, "insufficient_sector_data")

    return SectorUniverseMetrics(
        sector=bucket.sector,
        sector_slug=sector_slug(bucket.sector),
        raw_sector_distribution=dict(sorted(bucket.raw_sector_distribution.items())),
        sector_mapping_status_counts=dict(
            sorted(bucket.sector_mapping_status_counts.items())
        ),
        ticker_count=ticker_count,
        universe_share=_share(ticker_count, total_tickers),
        average_fundamental_score=averages["fundamental"],
        average_technical_score=averages["technical"],
        average_final_score=averages["final"],
        average_profile_score=averages["profile"],
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
        component_scores=component_result.component_scores,
        universe_leadership_score=component_result.universe_leadership_score,
        confidence=confidence,
        reason_codes=reason_codes,
        warnings=warnings,
        debug={
            "default_ranking_profile": default_profile,
            "raw_sector_distribution": dict(sorted(bucket.raw_sector_distribution.items())),
            "sector_mapping_status_counts": dict(
                sorted(bucket.sector_mapping_status_counts.items())
            ),
            "raw_missing_sector_tickers": sorted(bucket.raw_missing_sector_tickers),
            "unmapped_sector_tickers": sorted(bucket.unmapped_sector_tickers),
            "technical_score_available_count": len(bucket.technical_scores),
            "fundamental_score_available_count": len(bucket.fundamental_scores),
            "technical_availability": _availability(
                available_count=len(bucket.technical_scores),
                ticker_count=ticker_count,
            ),
            "component_debug": component_result.debug,
        },
    )


@dataclass(frozen=True)
class _UniverseComponentResult:
    component_scores: dict[str, float]
    universe_leadership_score: float
    debug: dict[str, Any]


def _universe_score_components(
    bucket: _SectorBucket,
    ticker_count: int,
    total_tickers: int,
    average_technical_score: float | None,
    average_profile_score: float | None,
    average_final_score: float | None,
    top_counts: dict[str, int],
    config: dict[str, Any],
) -> _UniverseComponentResult:
    profile_source = "profile_score"
    profile_component = average_profile_score
    if profile_component is None:
        profile_component = average_final_score
        profile_source = "final_score_fallback" if average_final_score is not None else "missing"

    danger_warning_count = _danger_warning_count(
        warning_distribution=bucket.warning_distribution,
        config=config,
    )
    top_25_share = _share(top_counts.get("top_25", 0), ticker_count) or 0.0
    expected_top_25_share = min(1.0, 25 / total_tickers) if total_tickers > 0 else 0.0
    setup_density = (
        (bucket.buyable_count + bucket.watch_count * 0.5) / ticker_count
        if ticker_count > 0
        else 0.0
    )
    danger_density = bucket.danger_count / ticker_count if ticker_count > 0 else 0.0
    warning_density = danger_warning_count / ticker_count if ticker_count > 0 else 0.0

    component_scores = {
        "average_technical_score": _clamp(average_technical_score or 0.0),
        "average_profile_score": _clamp(profile_component or 0.0),
        "top_candidate_share": _top_candidate_component(
            top_25_share=top_25_share,
            expected_top_25_share=expected_top_25_share,
        ),
        "setup_density": _clamp(setup_density * 10),
        "risk_control": _risk_control_component(
            danger_density=danger_density,
            warning_density=warning_density,
        ),
    }
    weights = config["universe_score"]["weights"]
    score = _clamp(
        sum(
            component_scores[component] * float(weight)
            for component, weight in weights.items()
        )
    )

    return _UniverseComponentResult(
        component_scores=component_scores,
        universe_leadership_score=score,
        debug={
            "average_profile_score_source": profile_source,
            "top_25_share": top_25_share,
            "expected_top_25_share": round(expected_top_25_share, 4),
            "setup_density": round(setup_density, 4),
            "danger_density": round(danger_density, 4),
            "danger_warning_count": danger_warning_count,
            "warning_density": round(warning_density, 4),
        },
    )


def _top_candidate_component(
    top_25_share: float,
    expected_top_25_share: float,
) -> float:
    if expected_top_25_share <= 0:
        return 0.0
    return _clamp(top_25_share / expected_top_25_share * 5)


def _risk_control_component(danger_density: float, warning_density: float) -> float:
    penalty = danger_density * 7 + warning_density * 3
    return _clamp(10 - _clamp(penalty))


def _sector_confidence(
    ticker_count: int,
    technical_available_count: int,
    config: dict[str, Any],
) -> str:
    if ticker_count == 0:
        return "insufficient"

    technical_availability = _availability(
        available_count=technical_available_count,
        ticker_count=ticker_count,
    )
    defaults = config["defaults"]
    if ticker_count < int(defaults["min_tickers_for_normal_confidence"]):
        return "low"
    if technical_availability < 0.50:
        return "low"
    if (
        ticker_count >= int(defaults["min_tickers_for_high_confidence"])
        and technical_availability >= 0.80
    ):
        return "high"
    return "normal"


def _reason_codes(
    component_scores: dict[str, float],
    confidence: str,
    danger_share: float | None,
) -> list[str]:
    reasons: list[str] = []
    if component_scores["average_technical_score"] >= 7.5:
        reasons.append("strong_average_technical_score")
    if component_scores["top_candidate_share"] >= 7.0:
        reasons.append("top_candidate_overrepresentation")
    if component_scores["setup_density"] >= 6.0:
        reasons.append("high_setup_density")
    if component_scores["risk_control"] >= 8.0:
        reasons.append("low_danger_density")
    if (danger_share or 0.0) >= 0.25:
        reasons.append("high_danger_density")
    if confidence in {"low", "insufficient"}:
        reasons.append(f"{confidence}_confidence_sector")
    return reasons


def _danger_warning_count(
    warning_distribution: dict[str, int],
    config: dict[str, Any],
) -> int:
    danger_flags = set(config["universe_score"]["warning_flags"]["danger"])
    return sum(
        count
        for warning, count in warning_distribution.items()
        if warning in danger_flags
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
        normalization = _normalization_for_row(row, config)
        records[ticker] = _TickerRecord(
            ticker=ticker,
            company_name=row.company_name,
            sector=normalization.canonical_sector,
            raw_sector=normalization.raw_sector,
            sector_taxonomy=normalization.taxonomy,
            sector_mapping_status=normalization.status,
        )

    for ticker, combined in combined_results.items():
        normalization = normalize_sector_result(combined.sector, config)
        records.setdefault(
            ticker,
            _TickerRecord(
                ticker=ticker,
                company_name=combined.company_name,
                sector=normalization.canonical_sector,
                raw_sector=normalization.raw_sector,
                sector_taxonomy=normalization.taxonomy,
                sector_mapping_status=normalization.status,
            ),
        )

    for ticker, rankings in rankings_by_ticker.items():
        first = rankings[0]
        normalization = normalize_sector_result(first.sector, config)
        records.setdefault(
            ticker,
            _TickerRecord(
                ticker=ticker,
                company_name=first.company_name,
                sector=normalization.canonical_sector,
                raw_sector=normalization.raw_sector,
                sector_taxonomy=normalization.taxonomy,
                sector_mapping_status=normalization.status,
            ),
        )

    for ticker in sorted(set(fundamentals) | set(technicals)):
        normalization = normalize_sector_result(None, config)
        records.setdefault(
            ticker,
            _TickerRecord(
                ticker=ticker,
                company_name=None,
                sector=normalization.canonical_sector,
                raw_sector=normalization.raw_sector,
                sector_taxonomy=normalization.taxonomy,
                sector_mapping_status=normalization.status,
            ),
        )

    return records


def _normalization_for_row(
    row: RawCompanyRow,
    config: dict[str, Any],
) -> SectorNormalizationResult:
    raw_sector = getattr(row, "sector", None)
    canonical = str(getattr(row, "sector_canonical", None) or "").strip()
    status = str(getattr(row, "sector_mapping_status", None) or "").strip()
    taxonomy = str(getattr(row, "sector_taxonomy", None) or "").strip()
    if canonical and status:
        return SectorNormalizationResult(
            raw_sector=str(raw_sector).strip() if raw_sector is not None else None,
            canonical_sector=canonical,
            taxonomy=taxonomy or str(
                config.get("sector_taxonomy", {}).get("source") or "unknown"
            ),
            status=status,
        )
    return normalize_sector_result(raw_sector, config)


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
    if bucket.unmapped_sector_tickers:
        warnings.append("unmapped_sector")
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


def _availability(available_count: int, ticker_count: int) -> float:
    if ticker_count <= 0:
        return 0.0
    return round(available_count / ticker_count, 4)


def _append_unique(values: list[str], value: str) -> list[str]:
    if value not in values:
        return [*values, value]
    return values


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
