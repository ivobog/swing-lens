from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    MarketRegimeSnapshot,
    RawCompanyRow,
    SectorRotationRow,
    TechnicalScore,
    UploadRun,
)
from app.services.market_regime_repository import MarketRegimeRepository
from app.services.sector_rotation_config import (
    load_sector_rotation_config,
    sector_rotation_config_hash,
)
from app.services.sector_rotation_dtos import (
    SectorRotationDecision,
    SectorRotationSnapshotDto,
    SectorUniverseMetrics,
)
from app.services.sector_rotation_policy import SectorRotationPolicyService
from app.services.sector_rotation_repository import (
    SectorRotationRepository,
    SectorRotationRowWrite,
    SectorRotationSnapshotWrite,
)
from app.services.sector_universe_service import SectorUniverseService

CALCULATION_VERSION = "sector-rotation-1.0.0"
MODE_UNIVERSE_ONLY = "universe_only"
MODE_COMBINED = "combined"


class SectorRotationService:
    def __init__(
        self,
        universe_service: SectorUniverseService | None = None,
        policy_service: SectorRotationPolicyService | None = None,
        repository: SectorRotationRepository | None = None,
        market_repository: MarketRegimeRepository | None = None,
    ) -> None:
        self.universe_service = universe_service or SectorUniverseService()
        self.policy_service = policy_service or SectorRotationPolicyService()
        self.repository = repository or SectorRotationRepository()
        self.market_repository = market_repository or MarketRegimeRepository()

    def build_sector_rotation_snapshot(
        self,
        db: Session,
        run_id: int | None,
        as_of_date: date | None = None,
        persist: bool = True,
        config: dict[str, Any] | None = None,
    ) -> SectorRotationSnapshotDto:
        return build_sector_rotation_snapshot(
            db=db,
            run_id=run_id,
            as_of_date=as_of_date,
            persist=persist,
            config=config,
            universe_service=self.universe_service,
            policy_service=self.policy_service,
            repository=self.repository,
            market_repository=self.market_repository,
        )


def build_sector_rotation_snapshot(
    db: Session,
    run_id: int | None,
    as_of_date: date | None = None,
    persist: bool = True,
    config: dict[str, Any] | None = None,
    universe_service: SectorUniverseService | None = None,
    policy_service: SectorRotationPolicyService | None = None,
    repository: SectorRotationRepository | None = None,
    market_repository: MarketRegimeRepository | None = None,
) -> SectorRotationSnapshotDto:
    config = config or load_sector_rotation_config()
    config_hash = sector_rotation_config_hash(config)
    default_profile = config["defaults"]["default_ranking_profile"]
    mode = (
        MODE_COMBINED
        if bool(config.get("etf_score", {}).get("enabled", False))
        else MODE_UNIVERSE_ONLY
    )
    as_of_date = as_of_date or _resolve_as_of_date(db, run_id)
    universe_service = universe_service or SectorUniverseService()
    policy_service = policy_service or SectorRotationPolicyService()
    repository = repository or SectorRotationRepository()
    market_repository = market_repository or MarketRegimeRepository()

    market_snapshot = _latest_market_snapshot(market_repository, db, run_id)
    universe_rows = (
        universe_service.build(
            db=db,
            run_id=run_id,
            config=config,
            default_profile=default_profile,
        )
        if run_id is not None
        else []
    )
    previous_snapshot = repository.get_previous_snapshot(
        db,
        as_of_date=as_of_date,
        mode=mode,
        config_hash=config_hash,
        run_id=run_id,
    )
    previous_rows = _previous_rows_by_sector(repository, db, previous_snapshot)

    decisions = [
        policy_service.decide(
            universe=universe,
            etf=None,
            market_regime=market_snapshot,
            previous=previous_rows.get(universe.sector_slug),
            config=config,
        )
        for universe in universe_rows
    ]
    decisions = _rank_decisions(decisions)
    summary = _summary(decisions, universe_rows)
    warnings = _snapshot_warnings(universe_rows, decisions)
    if not universe_rows:
        warnings = _append_unique(warnings, "empty_sector_rotation_universe")

    dto = SectorRotationSnapshotDto(
        run_id=run_id,
        as_of_date=as_of_date.isoformat(),
        mode=mode,
        calculation_version=CALCULATION_VERSION,
        config_version=str(config.get("version") or "") or None,
        config_hash=config_hash,
        default_ranking_profile=default_profile,
        rows=decisions,
        market_regime_snapshot_id=getattr(market_snapshot, "id", None),
        benchmark_ticker=config.get("etf_score", {}).get("benchmark_ticker"),
        universe_rows=universe_rows,
        summary=summary,
        warnings=warnings,
        debug={
            "sector_count": len(decisions),
            "ticker_count": sum(row.ticker_count for row in universe_rows),
            "market_regime": _market_debug(market_snapshot),
            "persist_requested": persist,
        },
    )

    if persist:
        repository.save_snapshot(db, _to_snapshot_write(dto, config))
    return dto


def _rank_decisions(decisions: list[SectorRotationDecision]) -> list[SectorRotationDecision]:
    ranked = sorted(
        decisions,
        key=lambda decision: (
            -1 * (decision.final_score if decision.final_score is not None else -1),
            _confidence_sort(decision.confidence),
            (
                decision.debug.get("danger_share")
                if decision.debug.get("danger_share") is not None
                else 1
            ),
            decision.sector,
        ),
    )
    result: list[SectorRotationDecision] = []
    for rank, decision in enumerate(ranked, start=1):
        rank_change = (
            decision.previous_rank - rank
            if decision.previous_rank is not None
            else None
        )
        result.append(replace(decision, rank=rank, rank_change=rank_change))
    return result


def _summary(
    decisions: list[SectorRotationDecision],
    universe_rows: list[SectorUniverseMetrics],
) -> dict[str, Any]:
    by_slug = {row.sector_slug: row for row in universe_rows}
    scored = [decision for decision in decisions if decision.final_score is not None]
    riskiest = max(
        universe_rows,
        key=lambda row: (row.danger_share or 0.0, row.danger_count, row.sector),
        default=None,
    )
    most_represented_top25 = max(
        universe_rows,
        key=lambda row: (row.top_counts.get("top_25", 0), row.sector),
        default=None,
    )
    improving = max(
        [decision for decision in decisions if decision.score_change is not None],
        key=lambda decision: decision.score_change or 0.0,
        default=None,
    )
    leading = scored[0] if scored else None
    weakest = scored[-1] if scored else None
    return {
        "leading_sector": leading.sector if leading else None,
        "weakest_sector": weakest.sector if weakest else None,
        "riskiest_sector": riskiest.sector if riskiest else None,
        "most_represented_top25_sector": (
            most_represented_top25.sector if most_represented_top25 else None
        ),
        "fastest_improving_sector": improving.sector if improving else None,
        "sector_count": len(decisions),
        "ticker_count": sum(row.ticker_count for row in universe_rows),
        "leading_sector_ticker_count": (
            by_slug[leading.sector_slug].ticker_count
            if leading and leading.sector_slug in by_slug
            else None
        ),
    }


def _snapshot_warnings(
    universe_rows: list[SectorUniverseMetrics],
    decisions: list[SectorRotationDecision],
) -> list[str]:
    warnings: list[str] = []
    for row in universe_rows:
        for warning in row.warnings:
            warnings = _append_unique(warnings, warning)
    for decision in decisions:
        for warning in decision.warnings:
            warnings = _append_unique(warnings, warning)
    return warnings


def _to_snapshot_write(
    dto: SectorRotationSnapshotDto,
    config: dict[str, Any],
) -> SectorRotationSnapshotWrite:
    universe_by_slug = {row.sector_slug: row for row in dto.universe_rows}
    return SectorRotationSnapshotWrite(
        run_id=dto.run_id,
        market_regime_snapshot_id=dto.market_regime_snapshot_id,
        as_of_date=date.fromisoformat(dto.as_of_date),
        calculation_version=dto.calculation_version,
        config_version=dto.config_version,
        config_hash=dto.config_hash,
        mode=dto.mode,
        default_ranking_profile=dto.default_ranking_profile,
        benchmark_ticker=dto.benchmark_ticker,
        sector_count=dto.summary.get("sector_count", 0),
        ticker_count=dto.summary.get("ticker_count", 0),
        leading_sector=dto.summary.get("leading_sector"),
        weakest_sector=dto.summary.get("weakest_sector"),
        riskiest_sector=dto.summary.get("riskiest_sector"),
        summary=dict(dto.summary),
        warning_flags=list(dto.warnings),
        debug=dict(dto.debug),
        rows=[
            _to_row_write(decision, universe_by_slug[decision.sector_slug], config)
            for decision in dto.rows
            if decision.sector_slug in universe_by_slug
        ],
    )


def _to_row_write(
    decision: SectorRotationDecision,
    universe: SectorUniverseMetrics,
    config: dict[str, Any],
) -> SectorRotationRowWrite:
    proxy = config.get("sector_etf_proxies", {}).get(universe.sector)
    return SectorRotationRowWrite(
        sector=universe.sector,
        sector_slug=universe.sector_slug,
        sector_proxy_ticker=proxy,
        ticker_count=universe.ticker_count,
        universe_share=universe.universe_share,
        average_fundamental_score=universe.average_fundamental_score,
        average_technical_score=universe.average_technical_score,
        average_final_score=universe.average_final_score,
        average_profile_score=universe.average_profile_score,
        top_10_count=universe.top_counts.get("top_10", 0),
        top_25_count=universe.top_counts.get("top_25", 0),
        top_50_count=universe.top_counts.get("top_50", 0),
        top_25_share=_share(universe.top_counts.get("top_25", 0), universe.ticker_count),
        buyable_count=universe.buyable_count,
        watch_count=universe.watch_count,
        danger_count=universe.danger_count,
        buyable_share=universe.buyable_share,
        watch_share=universe.watch_share,
        danger_share=universe.danger_share,
        clean_pullback_count=universe.clean_pullback_count,
        breakout_count=universe.breakout_count,
        vcp_count=universe.vcp_count,
        tight_base_breakout_count=universe.tight_base_breakout_count,
        extended_or_overheated_count=universe.extended_or_overheated_count,
        missing_fundamental_count=universe.missing_fundamental_count,
        missing_technical_count=universe.missing_technical_count,
        universe_leadership_score=decision.universe_score,
        etf_rotation_score=decision.etf_score,
        sector_final_score=decision.final_score,
        rotation_state=decision.rotation_state,
        sector_permission=decision.permission,
        position_size_multiplier=decision.position_size_multiplier,
        confidence=decision.confidence,
        previous_rank=decision.previous_rank,
        current_rank=decision.rank,
        rank_change=decision.rank_change,
        score_change=decision.score_change,
        profile_distribution=universe.profile_distribution,
        setup_distribution=universe.setup_distribution,
        warning_distribution=universe.warning_distribution,
        component_scores=universe.component_scores,
        reason_codes=decision.reasons,
        warning_flags=decision.warnings,
        debug={**universe.debug, **decision.debug},
    )


def _previous_rows_by_sector(
    repository: SectorRotationRepository,
    db: Session,
    previous_snapshot: Any | None,
) -> dict[str, SectorRotationRow]:
    if previous_snapshot is None or getattr(previous_snapshot, "id", None) is None:
        return {}
    rows = repository.get_snapshot_rows(db, previous_snapshot.id)
    return {row.sector_slug: row for row in rows}


def _latest_market_snapshot(
    market_repository: MarketRegimeRepository,
    db: Session,
    run_id: int | None,
) -> MarketRegimeSnapshot | None:
    if run_id is not None:
        snapshot = market_repository.latest_for_run(db, run_id)
        if snapshot is not None:
            return snapshot
    return market_repository.latest(db)


def _resolve_as_of_date(db: Session, run_id: int | None) -> date:
    if run_id is not None:
        latest_technical = db.scalar(
            select(func.max(TechnicalScore.created_at)).where(TechnicalScore.run_id == run_id)
        )
        if latest_technical is not None:
            return latest_technical.date()

        uploaded_at = db.scalar(select(UploadRun.uploaded_at).where(UploadRun.id == run_id))
        if uploaded_at is not None:
            return uploaded_at.date()

        raw_exists = db.scalar(
            select(func.count(RawCompanyRow.id)).where(RawCompanyRow.run_id == run_id)
        )
        if raw_exists:
            return date.today()
    return date.today()


def _market_debug(snapshot: MarketRegimeSnapshot | None) -> dict[str, Any]:
    if snapshot is None:
        return {"available": False}
    return {
        "available": True,
        "id": snapshot.id,
        "regime": snapshot.regime,
        "risk_state": snapshot.risk_state,
        "risk_off": snapshot.risk_off,
    }


def _confidence_sort(confidence: str) -> int:
    return {"high": 0, "normal": 1, "low": 2, "insufficient": 3}.get(confidence, 4)


def _share(count: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(count / denominator, 4)


def _append_unique(values: list[str], value: str) -> list[str]:
    if value and value not in values:
        return [*values, value]
    return values
