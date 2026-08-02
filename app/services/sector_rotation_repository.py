from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import SectorRotationRow, SectorRotationSnapshot


@dataclass(frozen=True)
class SectorRotationRowWrite:
    sector: str
    sector_slug: str
    rotation_state: str
    sector_permission: str
    confidence: str
    sector_proxy_ticker: str | None = None
    ticker_count: int = 0
    universe_share: float | None = None
    average_fundamental_score: float | None = None
    average_technical_score: float | None = None
    average_final_score: float | None = None
    average_profile_score: float | None = None
    top_10_count: int = 0
    top_25_count: int = 0
    top_50_count: int = 0
    top_25_share: float | None = None
    buyable_count: int = 0
    watch_count: int = 0
    danger_count: int = 0
    buyable_share: float | None = None
    watch_share: float | None = None
    danger_share: float | None = None
    clean_pullback_count: int = 0
    breakout_count: int = 0
    vcp_count: int = 0
    tight_base_breakout_count: int = 0
    extended_or_overheated_count: int = 0
    missing_fundamental_count: int = 0
    missing_technical_count: int = 0
    universe_leadership_score: float | None = None
    etf_rotation_score: float | None = None
    sector_final_score: float | None = None
    position_size_multiplier: float | None = None
    previous_rank: int | None = None
    current_rank: int | None = None
    rank_change: int | None = None
    score_change: float | None = None
    profile_distribution: dict[str, Any] = field(default_factory=dict)
    setup_distribution: dict[str, Any] = field(default_factory=dict)
    warning_distribution: dict[str, Any] = field(default_factory=dict)
    etf_metrics: dict[str, Any] = field(default_factory=dict)
    component_scores: dict[str, Any] = field(default_factory=dict)
    reason_codes: list[str] = field(default_factory=list)
    warning_flags: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SectorRotationSnapshotWrite:
    run_id: int | None
    as_of_date: date
    calculation_version: str
    mode: str
    config_version: str | None = None
    config_hash: str | None = None
    market_regime_snapshot_id: int | None = None
    default_ranking_profile: str | None = None
    benchmark_ticker: str | None = None
    sector_count: int = 0
    ticker_count: int = 0
    leading_sector: str | None = None
    weakest_sector: str | None = None
    riskiest_sector: str | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    warning_flags: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)
    rows: list[SectorRotationRowWrite] = field(default_factory=list)


class SectorRotationRepository:
    def save_snapshot(
        self,
        db: Session,
        dto: SectorRotationSnapshotWrite,
    ) -> SectorRotationSnapshot:
        snapshot = self._matching_snapshot(db, dto)
        if snapshot is None:
            snapshot = SectorRotationSnapshot(
                run_id=dto.run_id,
                as_of_date=dto.as_of_date,
                calculation_version=dto.calculation_version,
                config_hash=dto.config_hash,
                mode=dto.mode,
            )
            db.add(snapshot)
            db.flush()
        else:
            db.execute(
                delete(SectorRotationRow).where(
                    SectorRotationRow.snapshot_id == snapshot.id
                )
            )

        self._apply_snapshot_fields(snapshot, dto)
        db.flush()

        rows = [_to_row_model(snapshot_id=snapshot.id, dto=row) for row in dto.rows]
        if rows:
            db.add_all(rows)
        db.flush()
        return snapshot

    def latest_for_run(
        self,
        db: Session,
        run_id: int,
    ) -> SectorRotationSnapshot | None:
        return db.scalar(
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.run_id == run_id)
            .order_by(
                SectorRotationSnapshot.as_of_date.desc(),
                SectorRotationSnapshot.created_at.desc(),
                SectorRotationSnapshot.id.desc(),
            )
            .limit(1)
        )

    def latest_for_run_as_of_or_before(
        self,
        db: Session,
        run_id: int,
        as_of_date: date,
    ) -> SectorRotationSnapshot | None:
        return db.scalar(
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.run_id == run_id)
            .where(SectorRotationSnapshot.as_of_date <= as_of_date)
            .order_by(
                SectorRotationSnapshot.as_of_date.desc(),
                SectorRotationSnapshot.created_at.desc(),
                SectorRotationSnapshot.id.desc(),
            )
            .limit(1)
        )

    def latest_global_as_of_or_before(
        self,
        db: Session,
        as_of_date: date,
    ) -> SectorRotationSnapshot | None:
        return db.scalar(
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.run_id.is_(None))
            .where(SectorRotationSnapshot.as_of_date <= as_of_date)
            .order_by(
                SectorRotationSnapshot.as_of_date.desc(),
                SectorRotationSnapshot.created_at.desc(),
                SectorRotationSnapshot.id.desc(),
            )
            .limit(1)
        )

    def get_previous_snapshot(
        self,
        db: Session,
        as_of_date: date,
        mode: str,
        config_hash: str | None = None,
        run_id: int | None = None,
    ) -> SectorRotationSnapshot | None:
        statement = (
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.as_of_date < as_of_date)
            .where(SectorRotationSnapshot.mode == mode)
        )
        if config_hash is not None:
            statement = statement.where(SectorRotationSnapshot.config_hash == config_hash)
        if run_id is not None:
            statement = statement.where(SectorRotationSnapshot.run_id == run_id)

        return db.scalar(
            statement.order_by(
                SectorRotationSnapshot.as_of_date.desc(),
                SectorRotationSnapshot.created_at.desc(),
                SectorRotationSnapshot.id.desc(),
            ).limit(1)
        )

    def get_snapshot_rows(
        self,
        db: Session,
        snapshot_id: int,
    ) -> list[SectorRotationRow]:
        return list(
            db.scalars(
                select(SectorRotationRow)
                .where(SectorRotationRow.snapshot_id == snapshot_id)
                .order_by(SectorRotationRow.current_rank, SectorRotationRow.sector)
            )
        )

    def get_sector_row(
        self,
        db: Session,
        snapshot_id: int,
        sector_slug: str,
    ) -> SectorRotationRow | None:
        return db.scalar(
            select(SectorRotationRow)
            .where(SectorRotationRow.snapshot_id == snapshot_id)
            .where(SectorRotationRow.sector_slug == sector_slug)
            .limit(1)
        )

    def history(
        self,
        db: Session,
        limit: int = 30,
        run_id: int | None = None,
    ) -> list[SectorRotationSnapshot]:
        safe_limit = max(1, min(int(limit), 500))
        statement = select(SectorRotationSnapshot)
        if run_id is not None:
            statement = statement.where(SectorRotationSnapshot.run_id == run_id)
        return list(
            db.scalars(
                statement.order_by(
                    SectorRotationSnapshot.as_of_date.desc(),
                    SectorRotationSnapshot.created_at.desc(),
                    SectorRotationSnapshot.id.desc(),
                ).limit(safe_limit)
            )
        )

    def _matching_snapshot(
        self,
        db: Session,
        dto: SectorRotationSnapshotWrite,
    ) -> SectorRotationSnapshot | None:
        statement = (
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.as_of_date == dto.as_of_date)
            .where(SectorRotationSnapshot.calculation_version == dto.calculation_version)
            .where(SectorRotationSnapshot.mode == dto.mode)
        )
        if dto.run_id is None:
            statement = statement.where(SectorRotationSnapshot.run_id.is_(None))
        else:
            statement = statement.where(SectorRotationSnapshot.run_id == dto.run_id)
        if dto.config_hash is None:
            statement = statement.where(SectorRotationSnapshot.config_hash.is_(None))
        else:
            statement = statement.where(SectorRotationSnapshot.config_hash == dto.config_hash)
        return db.scalar(statement.limit(1))

    def _apply_snapshot_fields(
        self,
        snapshot: SectorRotationSnapshot,
        dto: SectorRotationSnapshotWrite,
    ) -> None:
        snapshot.run_id = dto.run_id
        snapshot.market_regime_snapshot_id = dto.market_regime_snapshot_id
        snapshot.as_of_date = dto.as_of_date
        snapshot.calculation_version = dto.calculation_version
        snapshot.config_version = dto.config_version
        snapshot.config_hash = dto.config_hash
        snapshot.mode = dto.mode
        snapshot.default_ranking_profile = dto.default_ranking_profile
        snapshot.benchmark_ticker = dto.benchmark_ticker
        snapshot.sector_count = dto.sector_count
        snapshot.ticker_count = dto.ticker_count
        snapshot.leading_sector = dto.leading_sector
        snapshot.weakest_sector = dto.weakest_sector
        snapshot.riskiest_sector = dto.riskiest_sector
        snapshot.summary_json = dict(dto.summary)
        snapshot.warning_flags_json = list(dto.warning_flags)
        snapshot.debug_json = dict(dto.debug)


def _to_row_model(
    snapshot_id: int,
    dto: SectorRotationRowWrite,
) -> SectorRotationRow:
    return SectorRotationRow(
        snapshot_id=snapshot_id,
        sector=dto.sector,
        sector_slug=dto.sector_slug,
        sector_proxy_ticker=dto.sector_proxy_ticker,
        ticker_count=dto.ticker_count,
        universe_share=dto.universe_share,
        average_fundamental_score=dto.average_fundamental_score,
        average_technical_score=dto.average_technical_score,
        average_final_score=dto.average_final_score,
        average_profile_score=dto.average_profile_score,
        top_10_count=dto.top_10_count,
        top_25_count=dto.top_25_count,
        top_50_count=dto.top_50_count,
        top_25_share=dto.top_25_share,
        buyable_count=dto.buyable_count,
        watch_count=dto.watch_count,
        danger_count=dto.danger_count,
        buyable_share=dto.buyable_share,
        watch_share=dto.watch_share,
        danger_share=dto.danger_share,
        clean_pullback_count=dto.clean_pullback_count,
        breakout_count=dto.breakout_count,
        vcp_count=dto.vcp_count,
        tight_base_breakout_count=dto.tight_base_breakout_count,
        extended_or_overheated_count=dto.extended_or_overheated_count,
        missing_fundamental_count=dto.missing_fundamental_count,
        missing_technical_count=dto.missing_technical_count,
        universe_leadership_score=dto.universe_leadership_score,
        etf_rotation_score=dto.etf_rotation_score,
        sector_final_score=dto.sector_final_score,
        rotation_state=dto.rotation_state,
        sector_permission=dto.sector_permission,
        position_size_multiplier=dto.position_size_multiplier,
        confidence=dto.confidence,
        previous_rank=dto.previous_rank,
        current_rank=dto.current_rank,
        rank_change=dto.rank_change,
        score_change=dto.score_change,
        profile_distribution_json=dict(dto.profile_distribution),
        setup_distribution_json=dict(dto.setup_distribution),
        warning_distribution_json=dict(dto.warning_distribution),
        etf_metrics_json=dict(dto.etf_metrics),
        component_scores_json=dict(dto.component_scores),
        reason_codes_json=list(dto.reason_codes),
        warning_flags_json=list(dto.warning_flags),
        debug_json=dict(dto.debug),
    )
