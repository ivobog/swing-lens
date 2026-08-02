from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import MarketRegimeSnapshot


@dataclass(frozen=True)
class MarketRegimeSnapshotWrite:
    as_of_date: date
    calculation_version: str
    config_version: str | None
    regime: str
    risk_state: str
    score: float
    risk_off: bool
    gate_ok: bool
    confidence: str
    action_summary: str
    position_size_multiplier: float
    preferred_profiles: list[str] = field(default_factory=list)
    allowed_profiles: list[str] = field(default_factory=list)
    reduced_profiles: list[str] = field(default_factory=list)
    blocked_profiles: list[str] = field(default_factory=list)
    allowed_setups: list[str] = field(default_factory=list)
    blocked_setups: list[str] = field(default_factory=list)
    input_symbols: dict[str, Any] = field(default_factory=dict)
    index_health: dict[str, Any] = field(default_factory=dict)
    universe_participation: dict[str, Any] = field(default_factory=dict)
    sector_leadership: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict[str, Any] = field(default_factory=dict)


class MarketRegimeRepository:
    def upsert_snapshot(
        self,
        db: Session,
        dto: MarketRegimeSnapshotWrite,
        run_id: int | None = None,
    ) -> MarketRegimeSnapshot:
        evidence_hash = self.snapshot_evidence_hash(dto)
        snapshot = self._matching_snapshot(db, dto, run_id, evidence_hash)
        if snapshot is not None:
            return snapshot

        previous = self._latest_logical_snapshot(db, dto, run_id)
        revision = (previous.revision if previous is not None else 0) + 1
        snapshot = MarketRegimeSnapshot(
            run_id=run_id,
            as_of_date=dto.as_of_date,
            calculation_version=dto.calculation_version,
            config_version=dto.config_version,
            evidence_hash=evidence_hash,
            revision=revision,
            is_current_revision=True,
        )
        db.add(snapshot)

        self._apply_snapshot_fields(snapshot, dto, run_id)
        db.flush()
        if previous is not None:
            self._supersede_previous_revision(db, previous, snapshot)
        return snapshot

    def latest(self, db: Session) -> MarketRegimeSnapshot | None:
        return db.scalar(
            select(MarketRegimeSnapshot)
            .where(MarketRegimeSnapshot.is_current_revision.is_(True))
            .order_by(
                MarketRegimeSnapshot.as_of_date.desc(),
                MarketRegimeSnapshot.created_at.desc(),
                MarketRegimeSnapshot.id.desc(),
            )
            .limit(1)
        )

    def latest_for_run(self, db: Session, run_id: int) -> MarketRegimeSnapshot | None:
        return db.scalar(
            select(MarketRegimeSnapshot)
            .where(MarketRegimeSnapshot.run_id == run_id)
            .where(MarketRegimeSnapshot.is_current_revision.is_(True))
            .order_by(
                MarketRegimeSnapshot.as_of_date.desc(),
                MarketRegimeSnapshot.created_at.desc(),
                MarketRegimeSnapshot.id.desc(),
            )
            .limit(1)
        )

    def latest_for_run_as_of_or_before(
        self,
        db: Session,
        run_id: int,
        as_of_date: date,
    ) -> MarketRegimeSnapshot | None:
        return db.scalar(
            select(MarketRegimeSnapshot)
            .where(MarketRegimeSnapshot.run_id == run_id)
            .where(MarketRegimeSnapshot.as_of_date <= as_of_date)
            .where(MarketRegimeSnapshot.is_current_revision.is_(True))
            .order_by(
                MarketRegimeSnapshot.as_of_date.desc(),
                MarketRegimeSnapshot.created_at.desc(),
                MarketRegimeSnapshot.id.desc(),
            )
            .limit(1)
        )

    def latest_global_as_of_or_before(
        self,
        db: Session,
        as_of_date: date,
    ) -> MarketRegimeSnapshot | None:
        return db.scalar(
            select(MarketRegimeSnapshot)
            .where(MarketRegimeSnapshot.run_id.is_(None))
            .where(MarketRegimeSnapshot.as_of_date <= as_of_date)
            .where(MarketRegimeSnapshot.is_current_revision.is_(True))
            .order_by(
                MarketRegimeSnapshot.as_of_date.desc(),
                MarketRegimeSnapshot.created_at.desc(),
                MarketRegimeSnapshot.id.desc(),
            )
            .limit(1)
        )

    def history(self, db: Session, limit: int = 30) -> list[MarketRegimeSnapshot]:
        safe_limit = max(1, min(int(limit), 500))
        return list(
            db.scalars(
                select(MarketRegimeSnapshot)
                .order_by(
                    MarketRegimeSnapshot.as_of_date.desc(),
                    MarketRegimeSnapshot.created_at.desc(),
                    MarketRegimeSnapshot.id.desc(),
                )
                .limit(safe_limit)
            )
        )

    def delete_for_run(self, db: Session, run_id: int) -> None:
        db.execute(
            delete(MarketRegimeSnapshot).where(MarketRegimeSnapshot.run_id == run_id)
        )
        db.flush()

    def _matching_snapshot(
        self,
        db: Session,
        dto: MarketRegimeSnapshotWrite,
        run_id: int | None,
        evidence_hash: str,
    ) -> MarketRegimeSnapshot | None:
        statement = self._logical_snapshot_statement(dto, run_id).where(
            MarketRegimeSnapshot.evidence_hash == evidence_hash
        )
        return db.scalar(statement.limit(1))

    def _latest_logical_snapshot(
        self,
        db: Session,
        dto: MarketRegimeSnapshotWrite,
        run_id: int | None,
    ) -> MarketRegimeSnapshot | None:
        return db.scalar(
            self._logical_snapshot_statement(dto, run_id)
            .order_by(
                MarketRegimeSnapshot.revision.desc(),
                MarketRegimeSnapshot.id.desc(),
            )
            .limit(1)
        )

    def _logical_snapshot_statement(
        self,
        dto: MarketRegimeSnapshotWrite,
        run_id: int | None,
    ):
        statement = (
            select(MarketRegimeSnapshot)
            .where(MarketRegimeSnapshot.as_of_date == dto.as_of_date)
            .where(MarketRegimeSnapshot.calculation_version == dto.calculation_version)
        )
        if run_id is None:
            statement = statement.where(MarketRegimeSnapshot.run_id.is_(None))
        else:
            statement = statement.where(MarketRegimeSnapshot.run_id == run_id)

        if dto.config_version is None:
            statement = statement.where(MarketRegimeSnapshot.config_version.is_(None))
        else:
            statement = statement.where(
                MarketRegimeSnapshot.config_version == dto.config_version
            )

        return statement

    def _supersede_previous_revision(
        self,
        db: Session,
        previous: MarketRegimeSnapshot,
        current: MarketRegimeSnapshot,
    ) -> None:
        now = datetime.now(UTC)
        previous.is_current_revision = False
        previous.superseded_by_snapshot_id = current.id
        previous.superseded_at = now
        db.flush()

    def _apply_snapshot_fields(
        self,
        snapshot: MarketRegimeSnapshot,
        dto: MarketRegimeSnapshotWrite,
        run_id: int | None,
    ) -> None:
        snapshot.run_id = run_id
        snapshot.as_of_date = dto.as_of_date
        snapshot.calculation_version = dto.calculation_version
        snapshot.config_version = dto.config_version
        snapshot.regime = dto.regime
        snapshot.risk_state = dto.risk_state
        snapshot.score = dto.score
        snapshot.risk_off = dto.risk_off
        snapshot.gate_ok = dto.gate_ok
        snapshot.confidence = dto.confidence
        snapshot.action_summary = dto.action_summary
        snapshot.position_size_multiplier = dto.position_size_multiplier
        snapshot.preferred_profiles_json = list(dto.preferred_profiles)
        snapshot.allowed_profiles_json = list(dto.allowed_profiles)
        snapshot.reduced_profiles_json = list(dto.reduced_profiles)
        snapshot.blocked_profiles_json = list(dto.blocked_profiles)
        snapshot.allowed_setups_json = list(dto.allowed_setups)
        snapshot.blocked_setups_json = list(dto.blocked_setups)
        snapshot.input_symbols_json = dict(dto.input_symbols)
        snapshot.index_health_json = dict(dto.index_health)
        snapshot.universe_participation_json = dict(dto.universe_participation)
        snapshot.sector_leadership_json = list(dto.sector_leadership)
        snapshot.reasons_json = list(dto.reasons)
        snapshot.warnings_json = list(dto.warnings)
        snapshot.debug_json = dict(dto.debug)
        snapshot.evidence_hash = self.snapshot_evidence_hash(dto)

    @staticmethod
    def snapshot_evidence_hash(dto: MarketRegimeSnapshotWrite) -> str:
        payload = asdict(dto)
        data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
