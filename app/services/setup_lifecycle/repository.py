from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import Select, and_, delete, func, or_, select, text, tuple_, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.tables import (
    SetupLifecycleAdministrativeAuditEvent,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    SetupSignalSnapshotSelectionEvent,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.core_mutation_authority import core_writer_member, core_writer_transaction
from app.services.setup_lifecycle.config import (
    SetupLifecycleConfig,
    load_setup_lifecycle_config,
)


@dataclass(frozen=True)
class SetupSignalSnapshotWrite:
    ticker: str
    timeframe: str
    data_as_of_date: date
    calculated_at: datetime
    origin_type: str
    engine_version: str
    config_version: str
    config_hash: str
    source_data_hash: str
    schema_version: str
    data_quality_label: str
    evaluation_run_id: int | None = None
    run_id: int | None = None
    source_run_id_text: str | None = None
    source_ids: dict[str, int | None] = field(default_factory=dict)
    promoted_fields: dict[str, Any] = field(default_factory=dict)
    signals: dict[str, Any] = field(default_factory=dict)
    feature_flags: dict[str, Any] = field(default_factory=dict)
    warning_flags: list[str] = field(default_factory=list)
    missing_data: dict[str, Any] = field(default_factory=dict)
    source_lineage: dict[str, Any] = field(default_factory=dict)
    diagnostic_high_cross: dict[str, Any] = field(default_factory=dict)
    canonical_decision: dict[str, Any] = field(default_factory=dict)
    debug: dict[str, Any] = field(default_factory=dict)
    effective_configuration: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CanonicalSelectionAdvance:
    selection: SetupSignalSnapshotCurrentSelection
    previous_snapshot: SetupSignalSnapshot | None
    changed: bool
    audit_event: SetupSignalSnapshotSelectionEvent | None


@dataclass(frozen=True)
class PurgeScope:
    before_date: date | None = None
    ticker: str | None = None
    evaluation_run_id: int | None = None


@dataclass(frozen=True)
class PurgePreview:
    scope: PurgeScope
    token: str
    counts: dict[str, int]
    target_ids: dict[str, tuple[int, ...]] = field(default_factory=dict)


def current_canonical_snapshot_predicate(snapshot_entity=SetupSignalSnapshot):
    """Return an EXISTS predicate for the separately modeled current pointer."""
    return (
        select(SetupSignalSnapshotCurrentSelection.id)
        .where(SetupSignalSnapshotCurrentSelection.selected_snapshot_id == snapshot_entity.id)
        .exists()
    )


_LIFECYCLE_OWNERS = (
    "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_snapshot",
    "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_observation_gap",
)
_CANONICAL_OWNER = (
    "app.services.setup_lifecycle.canonicalization:"
    "SetupLifecycleCanonicalizer.canonicalize_snapshots"
)
_LIFECYCLE_EVENT_OWNERS = _LIFECYCLE_OWNERS + (_CANONICAL_OWNER,)


class SetupLifecycleRepository:
    def __init__(self, config: SetupLifecycleConfig | None = None) -> None:
        self.config = config or load_setup_lifecycle_config()

    @core_writer_transaction
    def create_evaluation_run(
        self,
        db: Session,
        *,
        mode: str,
        status: str,
        engine_version: str,
        config_version: str,
        config_hash: str,
        source_run_id: int | None = None,
        source_run_id_text: str | None = None,
        output_evaluation_version: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        ticker_scope: list[str] | None = None,
        requested_config: dict[str, Any] | None = None,
        dry_run: bool = False,
        requester: str | None = None,
    ) -> SetupLifecycleEvaluationRun:
        if mode not in {"LIVE", "REPLAY", "REPAIR", "DRY_RUN"} or status not in {
            "RUNNING",
            "PENDING",
        }:
            raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_MODE_OR_STATUS_INVALID")
        if (
            engine_version != self.config.engine.version
            or config_version != self.config.engine.config_version
            or config_hash != self.config.config_hash
        ):
            raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_CONFIGURATION_MISMATCH")
        if isinstance(db, Session) and source_run_id is not None:
            from app.models.tables import UploadRun

            if db.get(UploadRun, source_run_id) is None:
                raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_SOURCE_MISSING")
        self._evaluation_run_authority(
            db, source_run_id, {"mode": mode, "configuration": config_hash}
        )
        evaluation_run = SetupLifecycleEvaluationRun(
            source_run_id=source_run_id,
            source_run_id_text=source_run_id_text,
            mode=mode,
            status=status,
            engine_version=engine_version,
            config_version=config_version,
            config_hash=config_hash,
            output_evaluation_version=output_evaluation_version,
            date_from=date_from,
            date_to=date_to,
            ticker_scope_json=list(ticker_scope or []),
            requested_config_json=dict(requested_config or {}),
            dry_run=dry_run,
            requester=requester,
            started_at=_utcnow(),
        )
        return self.add(db, evaluation_run)

    @core_writer_transaction
    def complete_evaluation_run(
        self,
        db: Session,
        evaluation_run: SetupLifecycleEvaluationRun,
        *,
        status: str,
        current_phase: str | None = None,
        counts: dict[str, int] | None = None,
        errors: dict[str, Any] | None = None,
        source_snapshot_min_id: int | None = None,
        source_snapshot_max_id: int | None = None,
        completed_at: datetime | None = None,
    ) -> SetupLifecycleEvaluationRun:
        if status not in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}:
            raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_TERMINAL_STATUS_INVALID")
        if evaluation_run.config_hash != self.config.config_hash:
            raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_CONFIGURATION_MISMATCH")
        self._evaluation_run_authority(
            db,
            evaluation_run.source_run_id,
            {"evaluation_run_id": evaluation_run.id, "status": status},
        )
        finished_at = completed_at or _utcnow()
        evaluation_run.status = status
        evaluation_run.current_phase = current_phase
        evaluation_run.completed_at = finished_at
        evaluation_run.heartbeat_at = finished_at
        evaluation_run.last_heartbeat_at = finished_at
        if evaluation_run.started_at is not None:
            evaluation_run.duration_ms = _duration_ms(evaluation_run.started_at, finished_at)
        if source_snapshot_min_id is not None:
            evaluation_run.source_snapshot_min_id = source_snapshot_min_id
        if source_snapshot_max_id is not None:
            evaluation_run.source_snapshot_max_id = source_snapshot_max_id
        if counts is not None:
            self.apply_evaluation_counts(evaluation_run, counts)
        if errors is not None:
            evaluation_run.error_summary_json = dict(errors)
        db.flush()
        return evaluation_run

    def _evaluation_run_authority(self, db, run_id, manifest):
        from app.services.decision_mutation_authority import operational_decision_authority

        operational_decision_authority(
            db,
            writer="setup_evaluation_run_bookkeeping",
            manifest=manifest,
            run_id=run_id,
            job_types=(
                "SETUP_LIFECYCLE_EVALUATE_RUN",
                "SETUP_LIFECYCLE_REPLAY",
                "SETUP_LIFECYCLE_REPAIR_TICKER",
                "SETUP_LIFECYCLE_DAILY_MAINTENANCE",
                "FULL_PIPELINE",
            ),
        )

    @core_writer_transaction
    def heartbeat_evaluation_run(
        self,
        db: Session,
        evaluation_run_id: int,
        *,
        current_phase: str | None = None,
    ) -> None:
        if isinstance(db, Session):
            run = db.get(SetupLifecycleEvaluationRun, evaluation_run_id)
            if run is None:
                raise ValueError("MUTATION_LIFECYCLE_EVALUATION_RUN_MISSING")
            self._evaluation_run_authority(
                db, run.source_run_id, {"evaluation_run_id": run.id, "phase": current_phase}
            )
        now = _utcnow()
        values: dict[str, Any] = {"heartbeat_at": now, "last_heartbeat_at": now}
        if current_phase is not None:
            values["current_phase"] = current_phase
        db.execute(
            update(SetupLifecycleEvaluationRun)
            .where(SetupLifecycleEvaluationRun.id == evaluation_run_id)
            .values(**values)
        )
        db.flush()

    @core_writer_member(
        "app.services.setup_lifecycle.repository:SetupLifecycleRepository.complete_evaluation_run"
    )
    def apply_evaluation_counts(
        self,
        evaluation_run: SetupLifecycleEvaluationRun,
        counts: dict[str, int],
    ) -> None:
        normalized = {key: int(value) for key, value in counts.items()}
        evaluation_run.counts_json = normalized
        for key in (
            "read",
            "captured",
            "canonical",
            "changed",
            "transitioned",
            "alerted",
            "skipped",
            "warning",
            "failed",
        ):
            value = normalized.get(key)
            if value is not None:
                setattr(evaluation_run, f"{key}_count", value)

    @core_writer_transaction
    def upsert_snapshot(
        self,
        db: Session,
        dto: SetupSignalSnapshotWrite,
    ) -> SetupSignalSnapshot:
        self._validate_snapshot_write_scope(db, dto)
        snapshot = self.find_snapshot_by_identity(
            db,
            run_id=dto.run_id,
            ticker=dto.ticker,
            timeframe=dto.timeframe,
            data_as_of_date=dto.data_as_of_date,
            engine_version=dto.engine_version,
            config_hash=dto.config_hash,
            source_data_hash=dto.source_data_hash,
        )
        if snapshot is not None:
            from app.services.decision_mutation_authority import validate_setup_projection

            validate_setup_projection(db, snapshot)
            self._validate_snapshot_retry(db, snapshot, dto)
            return snapshot

        candidate = SetupSignalSnapshot(
            run_id=dto.run_id,
            ticker=self.normalize_ticker(dto.ticker),
            timeframe=dto.timeframe,
            data_as_of_date=dto.data_as_of_date,
            calculated_at=dto.calculated_at,
            origin_type=dto.origin_type,
            engine_version=dto.engine_version,
            config_version=dto.config_version,
            config_hash=dto.config_hash,
            source_data_hash=dto.source_data_hash,
            schema_version=dto.schema_version,
            data_quality_label=dto.data_quality_label,
        )
        self._apply_snapshot_fields(candidate, dto)
        candidate._effective_configuration = dto.effective_configuration
        try:
            with db.begin_nested():
                db.add(candidate)
                db.flush()
            snapshot = candidate
        except IntegrityError:
            snapshot = self.find_snapshot_by_identity(
                db,
                run_id=dto.run_id,
                ticker=dto.ticker,
                timeframe=dto.timeframe,
                data_as_of_date=dto.data_as_of_date,
                engine_version=dto.engine_version,
                config_hash=dto.config_hash,
                source_data_hash=dto.source_data_hash,
            )
            if snapshot is None:
                raise
            from app.services.decision_mutation_authority import validate_setup_projection

            validate_setup_projection(db, snapshot)
            self._validate_snapshot_retry(db, snapshot, dto)
            return snapshot

        from app.services.setup_lifecycle.decision_evidence import persist_setup_evidence

        persist_setup_evidence(db, snapshot)
        db.flush()
        return snapshot

    @core_writer_transaction
    def upsert_snapshots(
        self,
        db: Session,
        dtos: list[SetupSignalSnapshotWrite] | tuple[SetupSignalSnapshotWrite, ...],
        *,
        progress_callback=None,
        should_cancel=None,
    ) -> list[SetupSignalSnapshot]:
        """Idempotently persist one capture batch without a lookup/savepoint per ticker."""
        if not dtos:
            return []
        for dto in dtos:
            self._validate_snapshot_write_scope(db, dto)

        run_ids = {dto.run_id for dto in dtos if dto.run_id is not None}
        includes_standalone = any(dto.run_id is None for dto in dtos)
        run_predicates = []
        if run_ids:
            run_predicates.append(SetupSignalSnapshot.run_id.in_(run_ids))
        if includes_standalone:
            run_predicates.append(SetupSignalSnapshot.run_id.is_(None))
        existing = list(
            db.scalars(
                select(SetupSignalSnapshot).where(
                    or_(*run_predicates),
                    SetupSignalSnapshot.ticker.in_(
                        {self.normalize_ticker(dto.ticker) for dto in dtos}
                    ),
                    SetupSignalSnapshot.timeframe.in_({dto.timeframe for dto in dtos}),
                    SetupSignalSnapshot.data_as_of_date.in_({dto.data_as_of_date for dto in dtos}),
                    SetupSignalSnapshot.engine_version.in_({dto.engine_version for dto in dtos}),
                    SetupSignalSnapshot.config_hash.in_({dto.config_hash for dto in dtos}),
                )
            )
        )
        by_identity = {
            self.snapshot_identity_key(
                run_id=row.run_id,
                ticker=row.ticker,
                timeframe=row.timeframe,
                data_as_of_date=row.data_as_of_date,
                engine_version=row.engine_version,
                config_hash=row.config_hash,
                source_data_hash=row.source_data_hash,
            ): row
            for row in existing
        }
        pending: list[
            tuple[
                tuple[int | None, str, str, str, str, str, str],
                SetupSignalSnapshotWrite,
                SetupSignalSnapshot,
            ]
        ] = []
        for dto in dtos:
            key = self.snapshot_identity_key(
                run_id=dto.run_id,
                ticker=dto.ticker,
                timeframe=dto.timeframe,
                data_as_of_date=dto.data_as_of_date,
                engine_version=dto.engine_version,
                config_hash=dto.config_hash,
                source_data_hash=dto.source_data_hash,
            )
            if key in by_identity:
                self._validate_snapshot_retry(db, by_identity[key], dto)
                continue
            candidate = self._snapshot_from_write(dto)
            by_identity[key] = candidate
            pending.append((key, dto, candidate))

        if pending:
            try:
                with db.begin_nested():
                    db.add_all([candidate for _key, _dto, candidate in pending])
                    db.flush()
            except IntegrityError:
                # A concurrent capture may win one identity after the prefetch.
                # Fall back to the single-row conflict-safe path only then.
                for key, dto, _candidate in pending:
                    by_identity[key] = self.upsert_snapshot(db, dto)

        from app.services.decision_mutation_authority import validate_setup_projection
        from app.services.setup_lifecycle.decision_evidence import persist_setup_evidence

        snapshots = list(by_identity.values())
        for index, snapshot in enumerate(snapshots, start=1):
            if should_cancel is not None and should_cancel():
                from app.services.setup_lifecycle.snapshot_builder import (
                    SetupLifecycleCaptureCancelled,
                )

                raise SetupLifecycleCaptureCancelled("SETUP_CAPTURE_CANCELLED")
            if snapshot.evidence_id is None:
                persist_setup_evidence(db, snapshot)
            else:
                validate_setup_projection(db, snapshot)
            if progress_callback is not None:
                progress_callback(snapshot.ticker, index, len(snapshots), phase="EVIDENCE")

        return [
            by_identity[
                self.snapshot_identity_key(
                    run_id=dto.run_id,
                    ticker=dto.ticker,
                    timeframe=dto.timeframe,
                    data_as_of_date=dto.data_as_of_date,
                    engine_version=dto.engine_version,
                    config_hash=dto.config_hash,
                    source_data_hash=dto.source_data_hash,
                )
            ]
            for dto in dtos
        ]

    def _validate_snapshot_write_scope(self, db, dto):
        if not isinstance(db, Session):
            return
        from app.services.combined_ranking_identity import calculation_identity_from_debug
        from app.services.effective_configuration import EffectiveConfigurationSnapshot

        identity = calculation_identity_from_debug(dto.source_lineage)
        if identity is None:
            raise ValueError("MUTATION_SETUP_IDENTITY_REQUIRED")
        if not isinstance(dto.effective_configuration, EffectiveConfigurationSnapshot):
            raise ValueError("MUTATION_SETUP_CONFIGURATION_REQUIRED")
        temporal = dict(dto.source_lineage.get("temporal_lineage") or {})
        expected_session = (
            date.fromisoformat(str(temporal["input_as_of_session"]))
            if temporal.get("input_as_of_session")
            else dto.data_as_of_date
        )
        if (
            identity.ownership.run_id.value != dto.run_id
            or identity.subject.ticker.value != self.normalize_ticker(dto.ticker)
            or identity.temporal.as_of_session.value != expected_session
        ):
            raise ValueError("MUTATION_SETUP_WRITE_SCOPE_MISMATCH")

    def _snapshot_from_write(self, dto: SetupSignalSnapshotWrite) -> SetupSignalSnapshot:
        snapshot = SetupSignalSnapshot(
            run_id=dto.run_id,
            ticker=self.normalize_ticker(dto.ticker),
            timeframe=dto.timeframe,
            data_as_of_date=dto.data_as_of_date,
            calculated_at=dto.calculated_at,
            origin_type=dto.origin_type,
            engine_version=dto.engine_version,
            config_version=dto.config_version,
            config_hash=dto.config_hash,
            source_data_hash=dto.source_data_hash,
            schema_version=dto.schema_version,
            data_quality_label=dto.data_quality_label,
        )
        self._apply_snapshot_fields(snapshot, dto)
        snapshot._effective_configuration = dto.effective_configuration
        return snapshot

    def _validate_snapshot_retry(self, db, snapshot, dto) -> None:
        from app.services.canonical_evidence import CanonicalEvidenceSerializer
        from app.services.core_calculation_evidence import calculation_evidence_payload
        from app.services.setup_lifecycle.decision_evidence import _SETUP_PROJECTION_FIELDS

        frozen = dto.effective_configuration
        if frozen is None:
            raise ValueError("MUTATION_SETUP_RETRY_CONFIGURATION_REQUIRED")
        if snapshot.evidence_id is not None:
            from app.models.tables import CoreCalculationEvidence

            evidence = db.get(CoreCalculationEvidence, snapshot.evidence_id)
            if frozen.as_dict() != evidence.payload_json.get("effective_configuration_at_creation"):
                raise ValueError("MUTATION_SETUP_RETRY_CONFIGURATION_MISMATCH")

        excluded = _SETUP_PROJECTION_FIELDS | {"calculated_at", "origin_type"}
        candidate = self._snapshot_from_write(dto)
        expected = calculation_evidence_payload(snapshot, excluded_columns=excluded)
        supplied = calculation_evidence_payload(candidate, excluded_columns=excluded)
        if CanonicalEvidenceSerializer.dumps(expected) != CanonicalEvidenceSerializer.dumps(
            supplied
        ):
            raise ValueError("MUTATION_SETUP_ALTERED_RETRY")

    def find_snapshot_by_identity(
        self,
        db: Session,
        *,
        run_id: int | None,
        ticker: str,
        timeframe: str,
        data_as_of_date: date,
        engine_version: str,
        config_hash: str,
        source_data_hash: str,
    ) -> SetupSignalSnapshot | None:
        statement = (
            select(SetupSignalSnapshot)
            .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.timeframe == timeframe)
            .where(SetupSignalSnapshot.data_as_of_date == data_as_of_date)
            .where(SetupSignalSnapshot.engine_version == engine_version)
            .where(SetupSignalSnapshot.config_hash == config_hash)
            .where(SetupSignalSnapshot.source_data_hash == source_data_hash)
        )
        if run_id is None:
            statement = statement.where(SetupSignalSnapshot.run_id.is_(None))
        else:
            statement = statement.where(SetupSignalSnapshot.run_id == run_id)
        return db.scalar(statement.limit(1))

    def lock_canonicalization_keys(
        self,
        db: Session,
        snapshots: tuple[SetupSignalSnapshot, ...] | list[SetupSignalSnapshot],
    ) -> None:
        """Serialize same-key canonicalization before candidates are reloaded.

        PostgreSQL transaction advisory locks close the race where two runs each
        load a candidate set that cannot see the other's uncommitted snapshot.
        Sorted acquisition prevents cross-key deadlocks. SQLite unit tests are
        single-process and intentionally need no equivalent operation.
        """
        bind = db.get_bind()
        if bind.dialect.name != "postgresql":
            return
        keys = sorted(
            {
                self._canonical_selection_lock_key(
                    snapshot.ticker,
                    snapshot.timeframe,
                    snapshot.data_as_of_date,
                )
                for snapshot in snapshots
            }
        )
        if keys:
            db.execute(
                text(
                    "SELECT pg_advisory_xact_lock(hashtextextended(key, 0)) "
                    "FROM unnest(CAST(:keys AS text[])) AS key"
                ),
                {"keys": keys},
            )

    @core_writer_transaction
    def advance_canonical_selection(
        self,
        db: Session,
        snapshot: SetupSignalSnapshot,
        *,
        reason: str,
        decision: dict[str, Any] | None = None,
        evaluation_run_id: int | None = None,
    ) -> CanonicalSelectionAdvance:
        from app.services.decision_mutation_authority import validate_setup_projection

        validate_setup_projection(db, snapshot)
        self.lock_canonicalization_keys(db, [snapshot])
        self._validate_canonical_choice(db, snapshot)
        selection = db.scalar(
            select(SetupSignalSnapshotCurrentSelection)
            .where(SetupSignalSnapshotCurrentSelection.ticker == snapshot.ticker)
            .where(SetupSignalSnapshotCurrentSelection.timeframe == snapshot.timeframe)
            .where(SetupSignalSnapshotCurrentSelection.data_as_of_date == snapshot.data_as_of_date)
            .with_for_update()
        )
        previous = (
            db.get(SetupSignalSnapshot, selection.selected_snapshot_id)
            if selection is not None
            else None
        )
        if (
            previous is not None
            and previous.calculation_cutoff_at is not None
            and (
                snapshot.calculation_cutoff_at is None
                or previous.calculation_cutoff_at > snapshot.calculation_cutoff_at
            )
        ):
            raise ValueError("MUTATION_SETUP_PROJECTION_REGRESSION")
        if selection is not None and selection.selected_snapshot_id == snapshot.id:
            return CanonicalSelectionAdvance(selection, previous, False, None)

        if selection is None:
            latest_other_session = db.scalar(
                select(SetupSignalSnapshot.data_as_of_date)
                .join(
                    SetupSignalSnapshotCurrentSelection,
                    SetupSignalSnapshotCurrentSelection.selected_snapshot_id
                    == SetupSignalSnapshot.id,
                )
                .where(SetupSignalSnapshot.ticker == snapshot.ticker)
                .where(SetupSignalSnapshot.timeframe == snapshot.timeframe)
                .where(SetupSignalSnapshot.data_as_of_date != snapshot.data_as_of_date)
                .order_by(SetupSignalSnapshot.data_as_of_date.desc())
                .limit(1)
            )
            event_semantics = (
                "NEW_SESSION_CANONICAL_INITIALIZATION"
                if latest_other_session is not None
                and snapshot.data_as_of_date > latest_other_session
                else "NEW_KEY_INITIALIZATION"
            )
        else:
            event_semantics = "SAME_SESSION_REPLACEMENT"
        if decision is not None:
            decision["selection_event_type"] = event_semantics

        now = _utcnow()
        if selection is None:
            selection = SetupSignalSnapshotCurrentSelection(
                ticker=snapshot.ticker,
                timeframe=snapshot.timeframe,
                data_as_of_date=snapshot.data_as_of_date,
                selected_snapshot_id=snapshot.id,
                selected_run_id=snapshot.run_id,
                selected_evaluation_run_id=evaluation_run_id,
                revision=1,
                selection_reason=reason,
                selection_decision_json=dict(decision or {}),
                created_at=now,
                updated_at=now,
            )
            db.add(selection)
        else:
            selection.selected_snapshot_id = snapshot.id
            selection.selected_run_id = snapshot.run_id
            selection.selected_evaluation_run_id = evaluation_run_id
            selection.revision += 1
            selection.selection_reason = reason
            selection.selection_decision_json = dict(decision or {})
            selection.updated_at = now
        db.flush()

        event_key = self.stable_key(
            "canonical_selection",
            snapshot.ticker,
            snapshot.timeframe,
            snapshot.data_as_of_date.isoformat(),
            str(selection.revision),
            str(previous.id if previous is not None else ""),
            str(snapshot.id),
        )
        audit_event = SetupSignalSnapshotSelectionEvent(
            ticker=snapshot.ticker,
            timeframe=snapshot.timeframe,
            data_as_of_date=snapshot.data_as_of_date,
            selection_revision=selection.revision,
            previous_snapshot_id=previous.id if previous is not None else None,
            selected_snapshot_id=snapshot.id,
            run_id=snapshot.run_id,
            evaluation_run_id=evaluation_run_id,
            reason=event_semantics,
            decision_json=dict(decision or {}),
            event_key=event_key,
            occurred_at=now,
        )
        db.add(audit_event)
        db.flush()
        return CanonicalSelectionAdvance(selection, previous, True, audit_event)

    @core_writer_transaction
    def advance_canonical_selections(
        self,
        db: Session,
        items: list[tuple[SetupSignalSnapshot, dict[str, Any]]],
        *,
        reason: str,
        evaluation_run_id: int | None = None,
    ) -> list[CanonicalSelectionAdvance]:
        from app.services.decision_mutation_authority import validate_setup_projection

        for snapshot, _decision in items:
            validate_setup_projection(db, snapshot)
        self.lock_canonicalization_keys(db, [snapshot for snapshot, _decision in items])
        for snapshot, _decision in items:
            self._validate_canonical_choice(db, snapshot)
        """Advance an advisory-lock-protected canonicalization batch."""
        if not items:
            return []
        keys = {
            (snapshot.ticker, snapshot.timeframe, snapshot.data_as_of_date)
            for snapshot, _decision in items
        }
        selections = list(
            db.scalars(
                select(SetupSignalSnapshotCurrentSelection)
                .where(
                    tuple_(
                        SetupSignalSnapshotCurrentSelection.ticker,
                        SetupSignalSnapshotCurrentSelection.timeframe,
                        SetupSignalSnapshotCurrentSelection.data_as_of_date,
                    ).in_(keys)
                )
                .with_for_update()
            )
        )
        selection_by_key = {
            (row.ticker, row.timeframe, row.data_as_of_date): row for row in selections
        }
        previous_ids = {
            row.selected_snapshot_id for row in selections if row.selected_snapshot_id is not None
        }
        previous_by_id = (
            {
                row.id: row
                for row in db.scalars(
                    select(SetupSignalSnapshot).where(SetupSignalSnapshot.id.in_(previous_ids))
                )
            }
            if previous_ids
            else {}
        )
        ticker_timeframes = {(ticker, timeframe) for ticker, timeframe, _date in keys}
        canonical_dates: dict[tuple[str, str], list[date]] = {}
        for row in db.scalars(
            select(SetupSignalSnapshot)
            .join(
                SetupSignalSnapshotCurrentSelection,
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(
                tuple_(SetupSignalSnapshot.ticker, SetupSignalSnapshot.timeframe).in_(
                    ticker_timeframes
                )
            )
        ):
            canonical_dates.setdefault((row.ticker, row.timeframe), []).append(row.data_as_of_date)

        advances: list[CanonicalSelectionAdvance] = []
        pending_audits: list[SetupSignalSnapshotSelectionEvent] = []
        for snapshot, decision in items:
            key = (snapshot.ticker, snapshot.timeframe, snapshot.data_as_of_date)
            selection = selection_by_key.get(key)
            previous_target = (
                previous_by_id.get(selection.selected_snapshot_id) if selection else None
            )
            if (
                previous_target is not None
                and previous_target.calculation_cutoff_at is not None
                and (
                    snapshot.calculation_cutoff_at is None
                    or previous_target.calculation_cutoff_at > snapshot.calculation_cutoff_at
                )
            ):
                raise ValueError("MUTATION_SETUP_PROJECTION_REGRESSION")
            previous = (
                previous_by_id.get(selection.selected_snapshot_id)
                if selection is not None
                else None
            )
            if selection is not None and selection.selected_snapshot_id == snapshot.id:
                advances.append(CanonicalSelectionAdvance(selection, previous, False, None))
                continue

            if selection is None:
                other_dates = [
                    value
                    for value in canonical_dates.get((snapshot.ticker, snapshot.timeframe), [])
                    if value != snapshot.data_as_of_date
                ]
                latest_other_session = max(other_dates, default=None)
                event_semantics = (
                    "NEW_SESSION_CANONICAL_INITIALIZATION"
                    if latest_other_session is not None
                    and snapshot.data_as_of_date > latest_other_session
                    else "NEW_KEY_INITIALIZATION"
                )
            else:
                event_semantics = "SAME_SESSION_REPLACEMENT"
            decision["selection_event_type"] = event_semantics
            now = _utcnow()
            if selection is None:
                selection = SetupSignalSnapshotCurrentSelection(
                    ticker=snapshot.ticker,
                    timeframe=snapshot.timeframe,
                    data_as_of_date=snapshot.data_as_of_date,
                    selected_snapshot_id=snapshot.id,
                    selected_run_id=snapshot.run_id,
                    selected_evaluation_run_id=evaluation_run_id,
                    revision=1,
                    selection_reason=reason,
                    selection_decision_json=dict(decision),
                    created_at=now,
                    updated_at=now,
                )
                db.add(selection)
                selection_by_key[key] = selection
            else:
                selection.selected_snapshot_id = snapshot.id
                selection.selected_run_id = snapshot.run_id
                selection.selected_evaluation_run_id = evaluation_run_id
                selection.revision += 1
                selection.selection_reason = reason
                selection.selection_decision_json = dict(decision)
                selection.updated_at = now
            event_key = self.stable_key(
                "canonical_selection",
                snapshot.ticker,
                snapshot.timeframe,
                snapshot.data_as_of_date.isoformat(),
                str(selection.revision),
                str(previous.id if previous is not None else ""),
                str(snapshot.id),
            )
            audit = SetupSignalSnapshotSelectionEvent(
                ticker=snapshot.ticker,
                timeframe=snapshot.timeframe,
                data_as_of_date=snapshot.data_as_of_date,
                selection_revision=selection.revision,
                previous_snapshot_id=previous.id if previous is not None else None,
                selected_snapshot_id=snapshot.id,
                run_id=snapshot.run_id,
                evaluation_run_id=evaluation_run_id,
                reason=event_semantics,
                decision_json=dict(decision),
                event_key=event_key,
                occurred_at=now,
            )
            db.add(audit)
            pending_audits.append(audit)
            advances.append(CanonicalSelectionAdvance(selection, previous, True, audit))
        if pending_audits or selections:
            db.flush()
        return advances

    @core_writer_member(
        "app.services.setup_lifecycle.canonicalization:SetupLifecycleCanonicalizer.canonicalize_snapshots"
    )
    def record_snapshot_canonical_decisions(
        self,
        db: Session,
        items: list[tuple[SetupSignalSnapshot, str, dict[str, Any]]],
    ) -> None:
        changed = False
        for snapshot, reason, decision in items:
            if snapshot.is_canonical:
                continue
            snapshot.is_canonical = True
            snapshot.canonical_reason = reason
            snapshot.canonical_decision_json = dict(decision)
            snapshot.canonicalized_at = _utcnow()
            changed = True
        if changed:
            db.flush()

    @core_writer_member(
        "app.services.setup_lifecycle.canonicalization:SetupLifecycleCanonicalizer.canonicalize_snapshots"
    )
    def record_snapshot_canonical_decision(
        self,
        db: Session,
        snapshot: SetupSignalSnapshot,
        *,
        reason: str,
        decision: dict[str, Any],
    ) -> None:
        """Complete a new run's evidence without revising an earlier decision."""
        if snapshot.is_canonical:
            return
        snapshot.is_canonical = True
        snapshot.canonical_reason = reason
        snapshot.canonical_decision_json = dict(decision)
        snapshot.canonicalized_at = _utcnow()
        db.flush()

    def current_selection_for_key(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        data_as_of_date: date,
    ) -> SetupSignalSnapshotCurrentSelection | None:
        return db.scalar(
            select(SetupSignalSnapshotCurrentSelection)
            .where(SetupSignalSnapshotCurrentSelection.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshotCurrentSelection.timeframe == timeframe)
            .where(SetupSignalSnapshotCurrentSelection.data_as_of_date == data_as_of_date)
        )

    def historical_session_canonical_snapshot(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        data_as_of_date: date,
    ) -> SetupSignalSnapshot | None:
        """Return the canonical snapshot for one immutable session key."""

        return db.scalar(
            select(SetupSignalSnapshot)
            .join(
                SetupSignalSnapshotCurrentSelection,
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.timeframe == timeframe)
            .where(SetupSignalSnapshot.data_as_of_date == data_as_of_date)
        )

    def current_cross_session_snapshot(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str = "1d",
        as_of_date: date | None = None,
    ) -> SetupSignalSnapshot | None:
        """Derive current lifecycle state from the latest session-canonical row."""

        statement = (
            select(SetupSignalSnapshot)
            .join(
                SetupSignalSnapshotCurrentSelection,
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.timeframe == timeframe)
        )
        if as_of_date is not None:
            statement = statement.where(SetupSignalSnapshot.data_as_of_date <= as_of_date)
        return db.scalar(
            statement.order_by(
                SetupSignalSnapshot.data_as_of_date.desc(),
                SetupSignalSnapshot.id.desc(),
            ).limit(1)
        )

    def current_selection_snapshot_ids(
        self,
        db: Session,
        snapshot_ids: tuple[int, ...] | list[int] | set[int],
    ) -> set[int]:
        if not snapshot_ids:
            return set()
        return set(
            db.scalars(
                select(SetupSignalSnapshotCurrentSelection.selected_snapshot_id).where(
                    SetupSignalSnapshotCurrentSelection.selected_snapshot_id.in_(snapshot_ids)
                )
            )
        )

    def latest_canonical_snapshot(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str = "1d",
        as_of_date: date | None = None,
    ) -> SetupSignalSnapshot | None:
        return self.current_cross_session_snapshot(
            db,
            ticker=ticker,
            timeframe=timeframe,
            as_of_date=as_of_date,
        )

    def previous_canonical_snapshot(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        before_date: date,
    ) -> SetupSignalSnapshot | None:
        return db.scalar(
            select(SetupSignalSnapshot)
            .join(
                SetupSignalSnapshotCurrentSelection,
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
            .where(SetupSignalSnapshot.timeframe == timeframe)
            .where(SetupSignalSnapshot.data_as_of_date < before_date)
            .where(SetupSignalSnapshot.evidence_id.is_not(None))
            .order_by(
                SetupSignalSnapshot.data_as_of_date.desc(),
                SetupSignalSnapshot.id.desc(),
            )
            .limit(1)
        )

    def canonical_snapshot_history(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        before_date: date,
        limit: int = 10,
    ) -> list[SetupSignalSnapshot]:
        safe_limit = max(1, min(int(limit), 50))
        return list(
            db.scalars(
                select(SetupSignalSnapshot)
                .join(
                    SetupSignalSnapshotCurrentSelection,
                    SetupSignalSnapshotCurrentSelection.selected_snapshot_id
                    == SetupSignalSnapshot.id,
                )
                .where(SetupSignalSnapshot.ticker == self.normalize_ticker(ticker))
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .where(SetupSignalSnapshot.data_as_of_date < before_date)
                .where(SetupSignalSnapshot.evidence_id.is_not(None))
                .order_by(
                    SetupSignalSnapshot.data_as_of_date.desc(),
                    SetupSignalSnapshot.id.desc(),
                )
                .limit(safe_limit)
            )
        )

    def canonical_snapshot_histories_before(
        self,
        db: Session,
        *,
        cutoffs: dict[tuple[str, str], date],
        limit: int = 10,
    ) -> dict[tuple[str, str], list[SetupSignalSnapshot]]:
        """Load a bounded prior-canonical window for many ticker keys in one query."""
        if not cutoffs:
            return {}
        safe_limit = max(1, min(int(limit), 50))
        normalized_cutoffs = {
            (self.normalize_ticker(ticker), timeframe): cutoff
            for (ticker, timeframe), cutoff in cutoffs.items()
        }
        cutoff_predicates = [
            and_(
                SetupSignalSnapshot.ticker == ticker,
                SetupSignalSnapshot.timeframe == timeframe,
                SetupSignalSnapshot.data_as_of_date < cutoff,
            )
            for (ticker, timeframe), cutoff in normalized_cutoffs.items()
        ]
        history_rank = (
            func.row_number()
            .over(
                partition_by=(SetupSignalSnapshot.ticker, SetupSignalSnapshot.timeframe),
                order_by=(
                    SetupSignalSnapshot.data_as_of_date.desc(),
                    SetupSignalSnapshot.id.desc(),
                ),
            )
            .label("history_rank")
        )
        ranked = (
            select(
                SetupSignalSnapshot.id.label("snapshot_id"),
                history_rank,
            )
            .join(
                SetupSignalSnapshotCurrentSelection,
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == SetupSignalSnapshot.id,
            )
            .where(or_(*cutoff_predicates))
            .where(SetupSignalSnapshot.evidence_id.is_not(None))
            .subquery()
        )
        rows = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .join(ranked, ranked.c.snapshot_id == SetupSignalSnapshot.id)
                .where(ranked.c.history_rank <= safe_limit)
                .order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.timeframe,
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.id,
                )
            )
        )
        result = {key: [] for key in normalized_cutoffs}
        for row in rows:
            result[(row.ticker, row.timeframe)].append(row)
        return result

    def get_snapshots_by_ids(
        self,
        db: Session,
        snapshot_ids: tuple[int, ...] | list[int],
    ) -> list[SetupSignalSnapshot]:
        if not snapshot_ids:
            return []
        return list(
            db.scalars(
                select(SetupSignalSnapshot)
                .where(SetupSignalSnapshot.id.in_(snapshot_ids))
                .order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.timeframe,
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.id,
                )
            )
        )

    def load_snapshots_for_run(
        self,
        db: Session,
        *,
        run_id: int,
        config_hash: str | None = None,
    ) -> list[SetupSignalSnapshot]:
        statement = select(SetupSignalSnapshot).where(SetupSignalSnapshot.run_id == run_id)
        if config_hash is not None:
            statement = statement.where(SetupSignalSnapshot.config_hash == config_hash)
        return list(
            db.scalars(
                statement.order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.timeframe,
                    SetupSignalSnapshot.data_as_of_date,
                    SetupSignalSnapshot.id,
                )
            )
        )

    def _validate_canonical_choice(self, db, snapshot):
        from app.services.decision_mutation_authority import validate_setup_projection
        from app.services.setup_lifecycle.canonicalization import select_canonical_snapshot

        if snapshot.config_hash != self.config.config_hash:
            raise ValueError("MUTATION_SETUP_PROJECTION_CONFIGURATION_MISMATCH")
        candidates = self.load_canonicalization_candidates(db, [snapshot], lock=True)
        compatible = []
        for candidate in candidates:
            if candidate.config_hash == snapshot.config_hash:
                validate_setup_projection(db, candidate)
                compatible.append(candidate)
        if not compatible or select_canonical_snapshot(compatible).id != snapshot.id:
            raise ValueError("MUTATION_SETUP_PROJECTION_NATIVE_CHOICE_MISMATCH")
        from app.models.tables import CoreCalculationEvidence
        from app.services.calculation_identity import CalculationIdentity
        from app.services.decision_effective_configuration import configuration_from_payload
        from app.services.decision_mutation_authority import decision_authority
        from app.services.domain_mutation import MutationDomain, MutationSemanticMode

        evidence = db.get(CoreCalculationEvidence, snapshot.evidence_id)
        configuration = configuration_from_payload(
            evidence.payload_json["effective_configuration_at_creation"]
        )
        decision_authority(
            db,
            domain=MutationDomain.CURRENT_PROJECTION,
            writer="advance_canonical_selection",
            identity=CalculationIdentity.from_canonical_payload(evidence.calculation_identity_json),
            configuration=configuration,
            records={"target_evidence": evidence},
            manifests={
                "projection_scope": {
                    "ticker": snapshot.ticker,
                    "timeframe": snapshot.timeframe,
                    "session": snapshot.data_as_of_date,
                    "snapshot_id": snapshot.id,
                }
            },
            semantic_mode=MutationSemanticMode.CURRENT_PROJECTION_ADVANCE,
        )

    def load_canonicalization_candidates(
        self,
        db: Session,
        affected_snapshots: tuple[SetupSignalSnapshot, ...] | list[SetupSignalSnapshot],
        *,
        lock: bool = False,
    ) -> list[SetupSignalSnapshot]:
        keys = {
            (
                snapshot.ticker,
                snapshot.timeframe,
                snapshot.data_as_of_date,
            )
            for snapshot in affected_snapshots
        }
        if not keys:
            return []
        statement = (
            select(SetupSignalSnapshot)
            .where(
                tuple_(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.timeframe,
                    SetupSignalSnapshot.data_as_of_date,
                ).in_(keys)
            )
            .order_by(
                SetupSignalSnapshot.ticker,
                SetupSignalSnapshot.timeframe,
                SetupSignalSnapshot.data_as_of_date,
                SetupSignalSnapshot.id,
            )
        )
        if lock:
            statement = statement.with_for_update()
        return list(db.scalars(statement))

    def previous_canonical_snapshots(
        self,
        db: Session,
        *,
        tickers: tuple[str, ...],
        timeframe: str,
        before_date: date,
    ) -> dict[str, SetupSignalSnapshot]:
        normalized = tuple(sorted({self.normalize_ticker(ticker) for ticker in tickers}))
        if not normalized:
            return {}
        rows = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .join(
                    SetupSignalSnapshotCurrentSelection,
                    SetupSignalSnapshotCurrentSelection.selected_snapshot_id
                    == SetupSignalSnapshot.id,
                )
                .where(SetupSignalSnapshot.ticker.in_(normalized))
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .where(SetupSignalSnapshot.data_as_of_date < before_date)
                .where(SetupSignalSnapshot.evidence_id.is_not(None))
                .order_by(
                    SetupSignalSnapshot.ticker,
                    SetupSignalSnapshot.data_as_of_date.desc(),
                    SetupSignalSnapshot.id.desc(),
                )
            )
        )
        latest: dict[str, SetupSignalSnapshot] = {}
        for row in rows:
            latest.setdefault(row.ticker, row)
        return latest

    @staticmethod
    def _canonical_selection_lock_key(ticker: str, timeframe: str, as_of_date: date) -> str:
        return f"setup-lifecycle-canonical:{ticker}:{timeframe}:{as_of_date.isoformat()}"

    def count_active_episodes(self, db: Session, *, config_hash: str | None = None) -> int:
        statement = (
            select(func.count())
            .select_from(SetupLifecycleEpisode)
            .where(SetupLifecycleEpisode.status == "ACTIVE")
        )
        if config_hash is not None:
            statement = statement.where(SetupLifecycleEpisode.config_hash == config_hash)
        return int(db.scalar(statement) or 0)

    def active_episode_for_update(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        setup_family: str,
        as_of_date: date,
        lock: bool = True,
    ) -> SetupLifecycleEpisode | None:
        statement = (
            select(SetupLifecycleEpisode)
            .where(SetupLifecycleEpisode.ticker == self.normalize_ticker(ticker))
            .where(SetupLifecycleEpisode.timeframe == timeframe)
            .where(SetupLifecycleEpisode.setup_family == setup_family)
            .where(SetupLifecycleEpisode.status == "ACTIVE")
            .where(SetupLifecycleEpisode.latest_evaluation_evidence_id.is_not(None))
            .where(SetupLifecycleEpisode.latest_transition_evidence_id.is_not(None))
            .where(SetupLifecycleEpisode.opened_on <= as_of_date)
            .where(SetupLifecycleEpisode.current_as_of_date <= as_of_date)
            .where(SetupLifecycleEpisode.last_observed_on <= as_of_date)
            .order_by(
                SetupLifecycleEpisode.current_as_of_date.desc(),
                SetupLifecycleEpisode.opened_on.desc(),
                SetupLifecycleEpisode.id.desc(),
            )
            .limit(1)
        )
        if lock:
            statement = statement.with_for_update()
        eligible = db.scalar(statement)
        if eligible is not None:
            return eligible
        future = db.scalar(
            select(SetupLifecycleEpisode.id)
            .where(SetupLifecycleEpisode.ticker == self.normalize_ticker(ticker))
            .where(SetupLifecycleEpisode.timeframe == timeframe)
            .where(SetupLifecycleEpisode.setup_family == setup_family)
            .where(SetupLifecycleEpisode.status == "ACTIVE")
            .where(
                (SetupLifecycleEpisode.opened_on > as_of_date)
                | (SetupLifecycleEpisode.current_as_of_date > as_of_date)
                | (SetupLifecycleEpisode.last_observed_on > as_of_date)
            )
            .order_by(SetupLifecycleEpisode.id.desc())
            .limit(1)
        )
        if future is not None:
            raise ValueError(
                "historical lifecycle state is unavailable because a newer active episode exists"
            )
        return None

    def latest_closed_episode(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        setup_family: str,
        as_of_date: date,
    ) -> SetupLifecycleEpisode | None:
        return db.scalar(
            select(SetupLifecycleEpisode)
            .where(SetupLifecycleEpisode.ticker == self.normalize_ticker(ticker))
            .where(SetupLifecycleEpisode.timeframe == timeframe)
            .where(SetupLifecycleEpisode.setup_family == setup_family)
            .where(SetupLifecycleEpisode.status == "CLOSED")
            .where(SetupLifecycleEpisode.latest_evaluation_evidence_id.is_not(None))
            .where(SetupLifecycleEpisode.latest_transition_evidence_id.is_not(None))
            .where(SetupLifecycleEpisode.closed_on <= as_of_date)
            .order_by(
                SetupLifecycleEpisode.closed_on.desc().nullslast(),
                SetupLifecycleEpisode.id.desc(),
            )
            .limit(1)
        )

    def active_episodes_for_ticker(
        self,
        db: Session,
        *,
        ticker: str,
        timeframe: str,
    ) -> list[SetupLifecycleEpisode]:
        return list(
            db.scalars(
                select(SetupLifecycleEpisode)
                .where(SetupLifecycleEpisode.ticker == self.normalize_ticker(ticker))
                .where(SetupLifecycleEpisode.timeframe == timeframe)
                .where(SetupLifecycleEpisode.status == "ACTIVE")
                .where(SetupLifecycleEpisode.latest_evaluation_evidence_id.is_not(None))
                .where(SetupLifecycleEpisode.latest_transition_evidence_id.is_not(None))
                .order_by(SetupLifecycleEpisode.id)
            )
        )

    def lifecycle_episodes_for_keys(
        self,
        db: Session,
        keys: set[tuple[str, str]],
    ) -> dict[tuple[str, str], list[SetupLifecycleEpisode]]:
        if not keys:
            return {}
        normalized = {(self.normalize_ticker(ticker), timeframe) for ticker, timeframe in keys}
        rows = list(
            db.scalars(
                select(SetupLifecycleEpisode)
                .where(
                    tuple_(SetupLifecycleEpisode.ticker, SetupLifecycleEpisode.timeframe).in_(
                        normalized
                    )
                )
                .where(SetupLifecycleEpisode.status.in_(("ACTIVE", "CLOSED")))
                .where(SetupLifecycleEpisode.latest_evaluation_evidence_id.is_not(None))
                .where(SetupLifecycleEpisode.latest_transition_evidence_id.is_not(None))
                .order_by(
                    SetupLifecycleEpisode.ticker,
                    SetupLifecycleEpisode.timeframe,
                    SetupLifecycleEpisode.opened_on,
                    SetupLifecycleEpisode.id,
                )
                .with_for_update()
            )
        )
        grouped = {key: [] for key in normalized}
        for row in rows:
            grouped[(row.ticker, row.timeframe)].append(row)
        return grouped

    def active_episodes_for_keys(
        self,
        db: Session,
        keys: set[tuple[str, str]],
    ) -> dict[tuple[str, str], list[SetupLifecycleEpisode]]:
        if not keys:
            return {}
        normalized = {(self.normalize_ticker(ticker), timeframe) for ticker, timeframe in keys}
        rows = list(
            db.scalars(
                select(SetupLifecycleEpisode)
                .where(
                    tuple_(SetupLifecycleEpisode.ticker, SetupLifecycleEpisode.timeframe).in_(
                        normalized
                    )
                )
                .where(SetupLifecycleEpisode.status == "ACTIVE")
                .where(SetupLifecycleEpisode.latest_evaluation_evidence_id.is_not(None))
                .where(SetupLifecycleEpisode.latest_transition_evidence_id.is_not(None))
                .order_by(SetupLifecycleEpisode.id)
            )
        )
        grouped = {key: [] for key in normalized}
        for row in rows:
            grouped[(row.ticker, row.timeframe)].append(row)
        return grouped

    @core_writer_member(_LIFECYCLE_OWNERS)
    def supersede_prior_current_events(
        self,
        db: Session,
        event: SetupLifecycleEvent,
    ) -> None:
        if event.id is None:
            db.flush()
        db.execute(
            update(SetupLifecycleEvent)
            .where(SetupLifecycleEvent.id != event.id)
            .where(SetupLifecycleEvent.is_current_version.is_(True))
            .where(SetupLifecycleEvent.episode_id == event.episode_id)
            .where(SetupLifecycleEvent.effective_date == event.effective_date)
            .where(SetupLifecycleEvent.event_type == event.event_type)
            .values(
                is_current_version=False,
                superseded_by_event_id=event.id,
            )
        )
        db.flush()

    def latest_authoritative_evaluation_version(
        self,
        db: Session,
    ) -> str | None:
        row = db.scalar(
            select(SetupLifecycleEvaluationRun.output_evaluation_version)
            .where(SetupLifecycleEvaluationRun.status == "COMPLETED")
            .where(SetupLifecycleEvaluationRun.dry_run.is_(False))
            .where(SetupLifecycleEvaluationRun.output_evaluation_version.is_not(None))
            .order_by(
                SetupLifecycleEvaluationRun.completed_at.desc().nullslast(),
                SetupLifecycleEvaluationRun.id.desc(),
            )
            .limit(1)
        )
        return row

    @core_writer_member(_LIFECYCLE_EVENT_OWNERS)
    def add_lifecycle_event(
        self,
        db: Session,
        event: SetupLifecycleEvent,
    ) -> SetupLifecycleEvent:
        self._validate_canonical_revision_event(db, event)
        existing = self.get_lifecycle_event(
            db,
            evaluation_run_id=event.evaluation_run_id,
            source_event_key=event.source_event_key,
        )
        if existing is not None:
            return existing
        return self.add(db, event)

    @core_writer_member(_LIFECYCLE_EVENT_OWNERS)
    def add_new_lifecycle_event(
        self,
        db: Session,
        event: SetupLifecycleEvent,
    ) -> SetupLifecycleEvent:
        """Persist an event for a just-created episode, which cannot have duplicates."""
        self._validate_canonical_revision_event(db, event)
        return self.add(db, event)

    @core_writer_member(_LIFECYCLE_EVENT_OWNERS)
    def add_lifecycle_events(
        self,
        db: Session,
        events: list[SetupLifecycleEvent],
    ) -> list[SetupLifecycleEvent]:
        if not events:
            return []
        for event in events:
            self._validate_canonical_revision_event(db, event)
        source_keys = {event.source_event_key for event in events}
        existing = list(
            db.scalars(
                select(SetupLifecycleEvent).where(
                    SetupLifecycleEvent.source_event_key.in_(source_keys)
                )
            )
        )
        by_key = {(row.evaluation_run_id, row.source_event_key): row for row in existing}
        pending: list[SetupLifecycleEvent] = []
        result: list[SetupLifecycleEvent] = []
        for event in events:
            key = (event.evaluation_run_id, event.source_event_key)
            persisted = by_key.get(key)
            if persisted is None:
                persisted = event
                by_key[key] = persisted
                pending.append(persisted)
                db.add(persisted)
            result.append(persisted)
        if pending:
            db.flush()
        return result

    def _validate_canonical_revision_event(self, db, event):
        if not isinstance(db, Session) or event.event_type != "CANONICAL_REVISION":
            return
        from app.services.decision_mutation_authority import validate_setup_projection
        from app.services.setup_lifecycle.canonicalization import _canonical_sort_key, _json_value

        snapshot = db.get(SetupSignalSnapshot, event.snapshot_id)
        if snapshot is None:
            raise ValueError("MUTATION_CANONICAL_AUDIT_TARGET_REQUIRED")
        validate_setup_projection(db, snapshot)
        selection = db.scalar(
            select(SetupSignalSnapshotCurrentSelection).where(
                SetupSignalSnapshotCurrentSelection.selected_snapshot_id == snapshot.id,
                SetupSignalSnapshotCurrentSelection.selected_evaluation_run_id
                == event.evaluation_run_id,
            )
        )
        selected = (event.evidence_json or {}).get("selected_snapshot_id")
        predecessor = (event.evidence_json or {}).get("previous_snapshot_id")
        expected_key = self.stable_key(
            "canonical_revision",
            str(event.evaluation_run_id or ""),
            snapshot.ticker,
            snapshot.timeframe,
            snapshot.data_as_of_date.isoformat(),
            str(predecessor or ""),
            str(snapshot.id),
            snapshot.config_hash,
        )
        if (
            selection is None
            or selected != snapshot.id
            or event.episode_id is not None
            or event.ticker != snapshot.ticker
            or event.timeframe != snapshot.timeframe
            or event.effective_date != snapshot.data_as_of_date
            or event.config_hash != snapshot.config_hash
            or event.source_event_key != expected_key
            or (event.evidence_json or {}).get("canonical_score")
            != _json_value(list(_canonical_sort_key(snapshot)))
        ):
            raise ValueError("MUTATION_CANONICAL_AUDIT_TARGET_MISMATCH")
        audit = db.scalar(
            select(SetupSignalSnapshotSelectionEvent).where(
                SetupSignalSnapshotSelectionEvent.selected_snapshot_id == snapshot.id,
                SetupSignalSnapshotSelectionEvent.selection_revision == selection.revision,
            )
        )
        if audit is None or audit.previous_snapshot_id != predecessor:
            raise ValueError("MUTATION_CANONICAL_AUDIT_PREDECESSOR_MISMATCH")

    def get_lifecycle_event(
        self,
        db: Session,
        *,
        evaluation_run_id: int | None,
        source_event_key: str,
    ) -> SetupLifecycleEvent | None:
        statement = select(SetupLifecycleEvent).where(
            SetupLifecycleEvent.source_event_key == source_event_key
        )
        if evaluation_run_id is None:
            statement = statement.where(SetupLifecycleEvent.evaluation_run_id.is_(None))
        else:
            statement = statement.where(SetupLifecycleEvent.evaluation_run_id == evaluation_run_id)
        return db.scalar(statement.limit(1))

    @core_writer_transaction
    def add_signal_change_event(
        self,
        db: Session,
        event: SignalChangeEvent,
        *,
        effective_configuration=None,
        source_manifest=None,
    ) -> SignalChangeEvent:
        from app.services.decision_mutation_authority import lock_decision_scope
        from app.services.setup_lifecycle.change_authority import (
            signal_change_body,
            validate_signal_change,
        )

        if isinstance(db, Session):
            lock_decision_scope(db, ("setup-signal-change", event.source_event_key))
            proof = validate_signal_change(
                db, event, configuration=effective_configuration, source_manifest=source_manifest
            )
        existing = self.get_signal_change_event(db, event.source_event_key)
        if existing is not None:
            if isinstance(db, Session) and (
                signal_change_body(existing) != signal_change_body(event)
                or CanonicalEvidenceSerializer.dumps(
                    (existing.evidence_json or {}).get("native_change_proof")
                )
                != CanonicalEvidenceSerializer.dumps(proof)
            ):
                raise ValueError("MUTATION_CHANGE_ALTERED_RETRY")
            return existing
        if isinstance(db, Session):
            event.evidence_json = {
                **event.evidence_json,
                "native_change_proof": CanonicalEvidenceSerializer.canonicalize(proof),
            }
        return self.add(db, event)

    def get_signal_change_event(
        self,
        db: Session,
        source_event_key: str,
    ) -> SignalChangeEvent | None:
        return db.scalar(
            select(SignalChangeEvent)
            .where(SignalChangeEvent.source_event_key == source_event_key)
            .limit(1)
        )

    def get_signal_change_events_by_ids(
        self,
        db: Session,
        event_ids: tuple[int, ...] | list[int],
    ) -> list[SignalChangeEvent]:
        if not event_ids:
            return []
        return list(
            db.scalars(
                select(SignalChangeEvent)
                .where(SignalChangeEvent.id.in_(event_ids))
                .order_by(SignalChangeEvent.effective_date, SignalChangeEvent.id)
            )
        )

    @core_writer_member(
        "app.services.setup_lifecycle.alert_service:SetupLifecycleAlertService.seed_builtin_rules"
    )
    def upsert_alert_rule(
        self,
        db: Session,
        *,
        rule_id: str,
        severity: str,
        scope: str,
        config_version: str,
        enabled: bool = True,
        setup_family: str | None = None,
        cooldown_sessions: int = 0,
        minimum_confidence: int = 0,
        condition: dict[str, Any] | None = None,
        market_restrictions: dict[str, Any] | None = None,
        effective_configuration=None,
    ) -> SignalAlertRule:
        if isinstance(db, Session):
            if effective_configuration is None:
                raise ValueError("MUTATION_ALERT_RULE_FULL_CONFIGURATION_REQUIRED")
            effective_configuration.require_family("decision.alerts.setup")
            config = effective_configuration.setup_config()
            native = config.alerts.rules.get(rule_id)
            if native is None or not config.alerts.built_in_rules_enabled:
                raise ValueError("MUTATION_ALERT_RULE_BOOTSTRAP_DISABLED_OR_UNKNOWN")
            expected = {
                "enabled": native.enabled,
                "severity": native.severity.value,
                "scope": native.source,
                "config_version": config.engine.config_version,
                "setup_family": native.filters.get("setup_family"),
                "cooldown_sessions": native.cooldown_sessions,
                "minimum_confidence": native.minimum_confidence,
                "condition": dict(native.filters),
                "market_restrictions": native.filters.get("market_restrictions") or {},
            }
            supplied = {
                "enabled": enabled,
                "severity": severity,
                "scope": scope,
                "config_version": config_version,
                "setup_family": setup_family,
                "cooldown_sessions": cooldown_sessions,
                "minimum_confidence": minimum_confidence,
                "condition": condition or {},
                "market_restrictions": market_restrictions or {},
            }
            if CanonicalEvidenceSerializer.dumps(expected) != CanonicalEvidenceSerializer.dumps(
                supplied
            ):
                raise ValueError("MUTATION_ALERT_RULE_NATIVE_CONFIGURATION_MISMATCH")
        rule = db.scalar(select(SignalAlertRule).where(SignalAlertRule.rule_id == rule_id).limit(1))
        if rule is None:
            rule = SignalAlertRule(rule_id=rule_id)
            db.add(rule)
        rule.enabled = enabled
        rule.severity = severity
        rule.scope = scope
        rule.setup_family = setup_family
        rule.cooldown_sessions = cooldown_sessions
        rule.minimum_confidence = minimum_confidence
        rule.config_version = config_version
        rule.condition_json = dict(condition or {})
        rule.market_restrictions_json = dict(market_restrictions or {})
        db.flush()
        return rule

    def alert_rules(
        self,
        db: Session,
        *,
        enabled_only: bool = True,
    ) -> list[SignalAlertRule]:
        statement = select(SignalAlertRule).order_by(SignalAlertRule.rule_id)
        if enabled_only:
            statement = statement.where(SignalAlertRule.enabled.is_(True))
        return list(db.scalars(statement))

    @core_writer_member(
        "app.services.setup_lifecycle.alert_service:SetupLifecycleAlertService._persist_alert"
    )
    def add_alert_event(
        self,
        db: Session,
        event: SignalAlertEvent,
    ) -> SignalAlertEvent:
        if isinstance(db, Session):
            from app.services.setup_lifecycle.alert_authority import validate_alert_event_projection

            validate_alert_event_projection(db, event)
        existing = db.scalar(
            select(SignalAlertEvent).where(SignalAlertEvent.event_key == event.event_key).limit(1)
        )
        if existing is not None:
            if isinstance(db, Session):
                validate_alert_event_projection(db, existing)
            return existing
        return self.add(db, event)

    def alert_event_by_key(
        self, db: Session, event_key: str, *, through_date: date
    ) -> SignalAlertEvent | None:
        return db.scalar(
            select(SignalAlertEvent)
            .where(SignalAlertEvent.event_key == event_key)
            .where(SignalAlertEvent.effective_date <= through_date)
            .limit(1)
        )

    def recent_alert_events(
        self,
        db: Session,
        *,
        alert_rule_id: int,
        ticker: str,
        timeframe: str,
        since_date: date,
        through_date: date,
        semantic_key: str | None = None,
    ) -> list[SignalAlertEvent]:
        statement = (
            select(SignalAlertEvent)
            .where(SignalAlertEvent.alert_rule_id == alert_rule_id)
            .where(SignalAlertEvent.ticker == self.normalize_ticker(ticker))
            .where(SignalAlertEvent.timeframe == timeframe)
            .where(SignalAlertEvent.effective_date >= since_date)
            .where(SignalAlertEvent.effective_date <= through_date)
            .order_by(SignalAlertEvent.effective_date.desc(), SignalAlertEvent.id.desc())
        )
        rows = list(db.scalars(statement))
        if semantic_key is None:
            return rows
        return [
            row for row in rows if (row.evidence_json or {}).get("semantic_key") == semantic_key
        ]

    def get_alert_event(self, db: Session, alert_id: int) -> SignalAlertEvent | None:
        return db.get(SignalAlertEvent, alert_id)

    @core_writer_transaction
    def acknowledge_alert_event(
        self,
        db: Session,
        alert_id: int,
        *,
        acknowledged_at: datetime | None = None,
    ) -> SignalAlertEvent | None:
        alert = self.get_alert_event(db, alert_id)
        if alert is None:
            return None
        self._validate_alert_status_operation(db, alert, "ACKNOWLEDGED")
        alert.status = "ACKNOWLEDGED"
        alert.acknowledged_at = acknowledged_at or _utcnow()
        db.flush()
        return alert

    @core_writer_transaction
    def dismiss_alert_event(
        self,
        db: Session,
        alert_id: int,
        *,
        dismissed_at: datetime | None = None,
    ) -> SignalAlertEvent | None:
        alert = self.get_alert_event(db, alert_id)
        if alert is None:
            return None
        self._validate_alert_status_operation(db, alert, "DISMISSED")
        alert.status = "DISMISSED"
        alert.dismissed_at = dismissed_at or _utcnow()
        db.flush()
        return alert

    def _validate_alert_status_operation(self, db, alert, status):
        from app.models.tables import SignalAlertDecisionEvidence
        from app.services.decision_mutation_authority import (
            operational_decision_authority,
            validate_retained_decision,
        )

        if not isinstance(db, Session):
            return
        from app.services.setup_lifecycle.alert_authority import validate_alert_event_projection

        validate_alert_event_projection(db, alert, allow_legacy=True)
        decision = (
            db.get(SignalAlertDecisionEvidence, alert.decision_evidence_id)
            if alert.decision_evidence_id is not None
            else None
        )
        if decision is not None:
            validate_retained_decision(db, decision, contract="signal-alert-decision-evidence-v1")
            if (
                decision.ticker != alert.ticker
                or decision.timeframe != alert.timeframe
                or decision.effective_session != alert.effective_date
                or decision.source_event_key != alert.source_event_key
                or decision.decision != "GENERATED"
            ):
                raise ValueError("MUTATION_ALERT_STATUS_TARGET_MISMATCH")
        elif alert.decision_evidence_id is not None:
            raise ValueError("MUTATION_ALERT_STATUS_DECISION_MISSING")
        operational_decision_authority(
            db,
            writer="setup_alert_notification_status",
            manifest={
                "alert_id": alert.id,
                "event_key": alert.event_key,
                "decision_evidence_id": alert.decision_evidence_id,
                "status": status,
                "artifact_role": "NOTIFICATION_STATUS_ONLY",
                "legacy_nonfinancial_status": alert.decision_evidence_id is None,
            },
        )

    @core_writer_transaction
    def write_admin_audit_event(
        self,
        db: Session,
        *,
        event_type: str,
        requester: str,
        evaluation_run_id: int | None = None,
        reason: str | None = None,
        scope: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        affected_counts: dict[str, int] | None = None,
        preview_token: str | None = None,
    ) -> SetupLifecycleAdministrativeAuditEvent:
        if event_type == "PURGE_EXECUTED" and (
            not requester.strip() or not (reason or "").strip() or not preview_token
        ):
            raise ValueError("MUTATION_LIFECYCLE_PURGE_AUDIT_AUTHORITY_REQUIRED")
        audit_event = SetupLifecycleAdministrativeAuditEvent(
            event_type=event_type,
            requester=requester,
            evaluation_run_id=evaluation_run_id,
            reason=reason,
            scope_json=dict(scope or {}),
            before_json=dict(before or {}),
            after_json=dict(after or {}),
            affected_counts_json=dict(affected_counts or {}),
            preview_token_hash=self.hash_token(preview_token) if preview_token else None,
        )
        return self.add(db, audit_event)

    def preview_purge(self, db: Session, scope: PurgeScope) -> PurgePreview:
        counts = {
            "alert_events": self._count(db, self._scoped_alert_events(scope)),
            "signal_change_events": self._count(db, self._scoped_signal_change_events(scope)),
            "lifecycle_events": self._count(db, self._scoped_lifecycle_events(scope)),
            "episodes": self._count(db, self._scoped_episodes(scope)),
            "snapshots": self._count(db, self._scoped_snapshots(scope)),
            "evaluation_runs": self._count(db, self._scoped_evaluation_runs(scope)),
        }
        token = self.stable_hash({"scope": scope.__dict__, "counts": counts})
        target_ids = {}
        if isinstance(db, Session):
            for name, statement in self._purge_statements(scope):
                target_ids[name] = tuple(sorted(row.id for row in db.scalars(statement)))
            token = self.stable_hash(
                {"scope": scope.__dict__, "counts": counts, "target_ids": target_ids}
            )
        return PurgePreview(scope=scope, token=token, counts=counts, target_ids=target_ids)

    def _purge_statements(self, scope):
        return (
            ("alert_events", self._scoped_alert_events(scope)),
            ("signal_change_events", self._scoped_signal_change_events(scope)),
            ("lifecycle_events", self._scoped_lifecycle_events(scope)),
            ("episodes", self._scoped_episodes(scope)),
            ("snapshots", self._scoped_snapshots(scope)),
            ("evaluation_runs", self._scoped_evaluation_runs(scope)),
        )

    @core_writer_transaction
    def execute_purge(self, db: Session, preview: PurgePreview, token: str) -> dict[str, int]:
        if not self.config.retention.purge_enabled:
            raise ValueError("setup lifecycle purge is disabled by retention policy")
        return self._execute_purge_unchecked(db, preview, token)

    @core_writer_member(
        (
            "app.services.setup_lifecycle.repository:SetupLifecycleRepository.execute_purge",
            "app.services.setup_lifecycle.purge_service:SetupLifecyclePurgeService.execute",
        )
    )
    def _execute_purge_unchecked(
        self,
        db: Session,
        preview: PurgePreview,
        token: str,
    ) -> dict[str, int]:
        if token != preview.token:
            raise ValueError("purge preview token does not match")
        if not self.config.retention.purge_enabled:
            raise ValueError("setup lifecycle purge is disabled by retention policy")
        current = self.preview_purge(db, preview.scope)
        if current.token != preview.token:
            raise ValueError("MUTATION_LIFECYCLE_PURGE_STALE_PREVIEW")
        if self.config.retention.purge_audit_required and not db.scalar(
            select(SetupLifecycleAdministrativeAuditEvent.id)
            .where(
                SetupLifecycleAdministrativeAuditEvent.event_type == "PURGE_EXECUTED",
                SetupLifecycleAdministrativeAuditEvent.preview_token_hash == self.hash_token(token),
            )
            .limit(1)
        ):
            raise ValueError("MUTATION_LIFECYCLE_PURGE_AUDIT_AUTHORITY_REQUIRED")
        from app.services.decision_mutation_authority import operational_decision_authority

        operational_decision_authority(
            db,
            writer="setup_derived_purge",
            manifest={"scope": preview.scope.__dict__, "token": token},
        )
        deleted: dict[str, int] = {}
        for name, statement in self._purge_statements(preview.scope):
            if isinstance(db, Session):
                entity = statement.column_descriptions[0]["entity"]
                statement = select(entity).where(entity.id.in_(current.target_ids[name]))
            deleted[name] = self._delete_selected(db, statement)
        db.flush()
        return deleted

    @core_writer_member(
        (
            "app.services.setup_lifecycle.repository:SetupLifecycleRepository.upsert_snapshot",
            "app.services.setup_lifecycle.repository:SetupLifecycleRepository.upsert_snapshots",
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_snapshot",
            "app.services.setup_lifecycle.episode_service:SetupLifecycleEpisodeService.apply_observation_gap",
            "app.services.setup_lifecycle.alert_service:SetupLifecycleAlertService._persist_alert",
            _CANONICAL_OWNER,
        ),
        models=(SetupSignalSnapshot, SetupLifecycleEpisode, SetupLifecycleEvent, SignalAlertEvent),
    )
    def add(self, db: Session, row: Any) -> Any:
        from app.models.tables import (
            SetupLifecycleEvaluationEvidence,
            SetupLifecycleTransitionEvidence,
            SignalAlertDecisionEvidence,
            SignalAlertRuleEvidence,
        )
        from app.services.core_mutation_authority import require_semantic_writer

        native_owners = {
            SignalAlertEvent: (
                "app.services.setup_lifecycle.alert_service:SetupLifecycleAlertService._persist_alert",
            ),
            SignalAlertRule: (
                "app.services.setup_lifecycle.alert_service:SetupLifecycleAlertService.seed_builtin_rules",
            ),
            SignalAlertRuleEvidence: (
                "app.services.setup_lifecycle.decision_evidence:persist_alert_decision_evidence",
            ),
            SignalAlertDecisionEvidence: (
                "app.services.setup_lifecycle.decision_evidence:persist_alert_decision_evidence",
            ),
            SignalChangeEvent: (
                "app.services.setup_lifecycle.repository:SetupLifecycleRepository.add_signal_change_event",
            ),
            SetupLifecycleAdministrativeAuditEvent: (
                "app.services.setup_lifecycle.repository:SetupLifecycleRepository.write_admin_audit_event",
            ),
            SetupSignalSnapshot: tuple(
                "app.services.setup_lifecycle.repository:SetupLifecycleRepository." + name
                for name in ("upsert_snapshot", "upsert_snapshots")
            ),
            SetupLifecycleEpisode: _LIFECYCLE_OWNERS,
            SetupLifecycleEvaluationRun: (
                "app.services.setup_lifecycle.repository:SetupLifecycleRepository.create_evaluation_run",
            ),
            SetupLifecycleEvaluationEvidence: tuple(
                "app.services.setup_lifecycle.decision_evidence:" + name
                for name in (
                    "persist_lifecycle_evaluation_evidence",
                    "persist_observation_gap_evaluation_evidence",
                )
            ),
            SetupLifecycleTransitionEvidence: (
                "app.services.setup_lifecycle.decision_evidence:persist_lifecycle_transition_evidence",
            ),
            SetupSignalSnapshotCurrentSelection: tuple(
                "app.services.setup_lifecycle.repository:SetupLifecycleRepository." + name
                for name in ("advance_canonical_selection", "advance_canonical_selections")
            ),
            SetupSignalSnapshotSelectionEvent: tuple(
                "app.services.setup_lifecycle.repository:SetupLifecycleRepository." + name
                for name in ("advance_canonical_selection", "advance_canonical_selections")
            ),
        }
        if type(row) in native_owners:
            require_semantic_writer(db, native_owners[type(row)])
        if isinstance(row, SetupLifecycleEvent):
            self._validate_canonical_revision_event(db, row)
        if isinstance(db, Session) and isinstance(row, SignalAlertEvent):
            from app.services.setup_lifecycle.alert_authority import validate_alert_event_projection

            validate_alert_event_projection(db, row)
        db.add(row)
        db.flush()
        return row

    @staticmethod
    def normalize_ticker(ticker: str) -> str:
        return ticker.strip().upper()

    @classmethod
    def snapshot_identity_key(
        cls,
        *,
        run_id: int | None,
        ticker: str,
        timeframe: str,
        data_as_of_date: date,
        engine_version: str,
        config_hash: str,
        source_data_hash: str,
    ) -> tuple[int | None, str, str, str, str, str, str]:
        return (
            run_id,
            cls.normalize_ticker(ticker),
            timeframe,
            data_as_of_date.isoformat(),
            engine_version,
            config_hash,
            source_data_hash,
        )

    @classmethod
    def lifecycle_event_key(
        cls,
        *,
        ticker: str,
        timeframe: str,
        setup_family: str,
        effective_date: date,
        from_state: str | None,
        to_state: str,
        engine_version: str,
        config_hash: str,
    ) -> str:
        return cls.stable_key(
            "lifecycle",
            cls.normalize_ticker(ticker),
            timeframe,
            setup_family,
            effective_date.isoformat(),
            from_state or "",
            to_state,
            engine_version,
            config_hash,
        )

    @classmethod
    def signal_change_key(
        cls,
        *,
        ticker: str,
        timeframe: str,
        signal_key: str,
        effective_date: date,
        old_value: Any,
        new_value: Any,
        config_hash: str,
    ) -> str:
        payload = {
            "ticker": cls.normalize_ticker(ticker),
            "timeframe": timeframe,
            "signal_key": signal_key,
            "effective_date": effective_date.isoformat(),
            "old": old_value,
            "new": new_value,
            "config_hash": config_hash,
        }
        return cls.stable_hash(payload)

    @classmethod
    def alert_event_key(
        cls,
        *,
        rule_id: str,
        source_event_key: str,
        ticker: str,
        episode_id: int | None = None,
        effective_date: date | None = None,
        evaluation_run_id: int | None = None,
    ) -> str:
        return cls.stable_key(
            "alert",
            rule_id,
            source_event_key,
            cls.normalize_ticker(ticker),
            str(episode_id or ""),
            effective_date.isoformat() if effective_date else "",
            str(evaluation_run_id or ""),
        )

    @staticmethod
    def stable_key(*parts: str) -> str:
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def stable_hash(payload: Any) -> str:
        return CanonicalEvidenceSerializer.fingerprint(payload)

    @classmethod
    def hash_token(cls, token: str) -> str:
        return cls.stable_key("token", token)

    def _apply_snapshot_fields(
        self,
        snapshot: SetupSignalSnapshot,
        dto: SetupSignalSnapshotWrite,
    ) -> None:
        snapshot._effective_configuration = dto.effective_configuration
        snapshot.evaluation_run_id = dto.evaluation_run_id
        snapshot.run_id = dto.run_id
        snapshot.source_run_id_text = dto.source_run_id_text
        snapshot.ticker = self.normalize_ticker(dto.ticker)
        snapshot.timeframe = dto.timeframe
        snapshot.data_as_of_date = dto.data_as_of_date
        snapshot.calculated_at = dto.calculated_at
        snapshot.origin_type = dto.origin_type
        snapshot.engine_version = dto.engine_version
        snapshot.config_version = dto.config_version
        snapshot.config_hash = dto.config_hash
        snapshot.source_data_hash = dto.source_data_hash
        snapshot.schema_version = dto.schema_version
        snapshot.data_quality_label = dto.data_quality_label
        for field_name, value in dto.source_ids.items():
            if hasattr(snapshot, field_name):
                setattr(snapshot, field_name, value)
        for field_name, value in dto.promoted_fields.items():
            if hasattr(snapshot, field_name):
                setattr(
                    snapshot,
                    field_name,
                    self._coerce_promoted_value(field_name, value),
                )
        snapshot.signals_json = dict(dto.signals)
        snapshot.feature_flags_json = dict(dto.feature_flags)
        snapshot.warning_flags_json = list(dto.warning_flags)
        snapshot.missing_data_json = dict(dto.missing_data)
        snapshot.source_lineage_json = dict(dto.source_lineage)
        temporal = dict(dto.source_lineage.get("temporal_lineage") or {})
        snapshot.calculation_context_id = temporal.get("calculation_context_id")
        snapshot.calculation_cutoff_at = (
            datetime.fromisoformat(str(temporal["calculation_cutoff_at"]))
            if temporal.get("calculation_cutoff_at")
            else None
        )
        snapshot.input_as_of_session = (
            date.fromisoformat(str(temporal["input_as_of_session"]))
            if temporal.get("input_as_of_session")
            else None
        )
        snapshot.calendar_version = temporal.get("calendar_version")
        snapshot.diagnostic_high_cross_json = dict(dto.diagnostic_high_cross)
        snapshot.canonical_decision_json = dict(dto.canonical_decision)
        snapshot.debug_json = dict(dto.debug)

    @staticmethod
    def _coerce_promoted_value(field_name: str, value: Any) -> Any:
        if isinstance(value, float):
            value = Decimal(str(value))
        if isinstance(value, Decimal):
            column = SetupSignalSnapshot.__table__.columns.get(field_name)
            scale = getattr(getattr(column, "type", None), "scale", None)
            if scale is not None:
                # Freeze exactly the value PostgreSQL retains for NUMERIC(p, s)
                # before immutable evidence is derived from this projection.
                return value.quantize(
                    Decimal(1).scaleb(-int(scale)),
                    rounding=ROUND_HALF_UP,
                )
        return value

    def _scoped_snapshots(self, scope: PurgeScope):
        statement = select(SetupSignalSnapshot)
        if scope.ticker is not None:
            statement = statement.where(
                SetupSignalSnapshot.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SetupSignalSnapshot.data_as_of_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SetupSignalSnapshot.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_episodes(self, scope: PurgeScope):
        statement = select(SetupLifecycleEpisode)
        if scope.ticker is not None:
            statement = statement.where(
                SetupLifecycleEpisode.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(
                SetupLifecycleEpisode.current_as_of_date < scope.before_date
            )
        return statement

    def _scoped_lifecycle_events(self, scope: PurgeScope):
        statement = select(SetupLifecycleEvent)
        if scope.ticker is not None:
            statement = statement.where(
                SetupLifecycleEvent.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SetupLifecycleEvent.effective_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SetupLifecycleEvent.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_signal_change_events(self, scope: PurgeScope):
        statement = select(SignalChangeEvent)
        if scope.ticker is not None:
            statement = statement.where(
                SignalChangeEvent.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SignalChangeEvent.effective_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SignalChangeEvent.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_alert_events(self, scope: PurgeScope):
        statement = select(SignalAlertEvent)
        if scope.ticker is not None:
            statement = statement.where(
                SignalAlertEvent.ticker == self.normalize_ticker(scope.ticker)
            )
        if scope.before_date is not None:
            statement = statement.where(SignalAlertEvent.effective_date < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(
                SignalAlertEvent.evaluation_run_id == scope.evaluation_run_id
            )
        return statement

    def _scoped_evaluation_runs(self, scope: PurgeScope):
        statement = select(SetupLifecycleEvaluationRun)
        if scope.before_date is not None:
            statement = statement.where(SetupLifecycleEvaluationRun.date_to < scope.before_date)
        if scope.evaluation_run_id is not None:
            statement = statement.where(SetupLifecycleEvaluationRun.id == scope.evaluation_run_id)
        return statement

    @staticmethod
    def _count(db: Session, statement: Select[tuple[Any]]) -> int:
        return int(db.scalar(select(func.count()).select_from(statement.subquery())) or 0)

    @staticmethod
    def _delete_selected(db: Session, statement: Select[tuple[Any]]) -> int:
        entity = statement.column_descriptions[0]["entity"]
        criteria = (statement.whereclause,) if statement.whereclause is not None else ()
        result = db.execute(delete(entity).where(*criteria))
        return int(result.rowcount or 0)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _duration_ms(started_at: datetime, finished_at: datetime) -> int:
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=UTC)
    if finished_at.tzinfo is None:
        finished_at = finished_at.replace(tzinfo=UTC)
    return max(0, int((finished_at - started_at).total_seconds() * 1000))
