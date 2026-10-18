from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import Parameter, signature
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.observability.logging import log_event
from app.services.configuration_delivery import anchored_decision_calculator
from app.services.core_mutation_authority import core_writer_transaction
from app.services.domain_write_fence import bounded_domain_session
from app.services.market_calculation_context_service import (
    assert_pipeline_calculation_context,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.canonicalization import (
    CanonicalizationResult,
    SetupLifecycleCanonicalizer,
)
from app.services.setup_lifecycle.change_detector import (
    SetupLifecycleChangeDetector,
    SignalChangeDetectionResult,
)
from app.services.setup_lifecycle.config import SetupLifecycleConfig
from app.services.setup_lifecycle.decision_evidence import persist_setup_evidence
from app.services.setup_lifecycle.enums import EvaluationStatus
from app.services.setup_lifecycle.episode_service import (
    SetupLifecycleEpisodeService,
    normalized_snapshot_from_row,
)
from app.services.setup_lifecycle.errors import SetupCaptureHandoffError
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleCaptureCancelled,
    SetupLifecycleSnapshotCaptureService,
    SnapshotCaptureResult,
)


class SetupLifecycleEvaluationCancelled(Exception):
    pass


_LIFECYCLE_PROGRESS_BATCH_SIZE = 5
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SetupLifecycleEvaluationResult:
    evaluation_run_id: int | None
    status: str
    snapshots_captured: int = 0
    canonical_snapshots: int = 0
    canonical_changed: int = 0
    change_events: int = 0
    lifecycle_transitions: int = 0
    alerts: int = 0
    active_episodes: int = 0
    low_confidence: int = 0
    failed: int = 0
    warnings: int = 0
    captured_snapshot_ids: tuple[int, ...] = ()
    canonical_snapshot_ids: tuple[int, ...] = ()
    errors_by_ticker: dict[str, str] = field(default_factory=dict)
    reconciled_episodes: int = 0
    reconciliation_plans: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "evaluation_run_id": self.evaluation_run_id,
            "status": self.status,
            "snapshots_captured": self.snapshots_captured,
            "canonical_snapshots": self.canonical_snapshots,
            "canonical_changed": self.canonical_changed,
            "change_events": self.change_events,
            "changed": self.change_events,
            "lifecycle_transitions": self.lifecycle_transitions,
            "transitioned": self.lifecycle_transitions,
            "alerts": self.alerts,
            "alerted": self.alerts,
            "active_episodes": self.active_episodes,
            "low_confidence": self.low_confidence,
            "failed": self.failed,
            "warning": self.warnings,
            "captured": self.snapshots_captured,
            "canonical": self.canonical_snapshots,
            "reconciled_episodes": self.reconciled_episodes,
            "reconciliation_plans": list(self.reconciliation_plans),
        }


class SetupLifecycleEvaluationService:
    def __init__(
        self,
        *,
        repository: SetupLifecycleRepository | None = None,
        capture_service: SetupLifecycleSnapshotCaptureService | None = None,
        canonicalizer: SetupLifecycleCanonicalizer | None = None,
        change_detector: SetupLifecycleChangeDetector | None = None,
        episode_service: SetupLifecycleEpisodeService | None = None,
        alert_service: SetupLifecycleAlertService | None = None,
        config: SetupLifecycleConfig | None = None,
    ) -> None:
        from app.services.decision_effective_configuration import resolve_setup_configuration

        self.effective_configuration = resolve_setup_configuration(config)
        self.config = self.effective_configuration.setup_config()
        self.repository = repository or SetupLifecycleRepository()
        self.capture_service = capture_service or SetupLifecycleSnapshotCaptureService(
            repository=self.repository,
            config=self.config,
        )
        self.canonicalizer = canonicalizer or SetupLifecycleCanonicalizer(
            repository=self.repository,
            config=self.config,
        )
        self.change_detector = change_detector or SetupLifecycleChangeDetector(
            repository=self.repository,
            config=self.config,
        )
        self.episode_service = episode_service or SetupLifecycleEpisodeService(
            repository=self.repository,
            config=self.config,
        )
        self.alert_service = alert_service or SetupLifecycleAlertService(
            repository=self.repository,
            config=self.config,
        )

    @anchored_decision_calculator
    def evaluate_run(
        self,
        db,
        run_id: int,
        *,
        requester: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        capture_result: SnapshotCaptureResult | None = None,
        snapshot_ids: tuple[int, ...] | None = None,
        market_cutoff: MarketCalculationCutoff | None = None,
        pipeline_run_id: int | None = None,
        progress_callback: Callable[..., None] | None = None,
    ) -> SetupLifecycleEvaluationResult:
        try:
            # Capture prepares exact sources before opening the lifecycle
            # publication transaction. Retain its attempt membership separately
            # from the creators of any idempotently reused snapshots.
            if isinstance(db, Session) and capture_result is None and snapshot_ids is None:
                if pipeline_run_id is not None:
                    market_cutoff = assert_pipeline_calculation_context(
                        db,
                        pipeline_run_id=pipeline_run_id,
                        upload_run_id=run_id,
                        supplied_context=market_cutoff,
                    )

                def capture_progress(ticker, processed, total, *, phase):
                    _report_lifecycle_progress(
                        progress_callback,
                        phase="RECAPTURE_" + phase,
                        processed=processed,
                        total=total,
                        current_item=ticker,
                        last_completed_item=ticker if processed else None,
                    )

                log_event(
                    logger,
                    "setup.lifecycle.capture_started",
                    run_id=run_id,
                    pipeline_run_id=pipeline_run_id,
                    capture_path="INTERNAL_RECAPTURE",
                )
                try:
                    capture_result = self.capture_service.capture_snapshots_for_run(
                        db,
                        run_id,
                        requester=requester,
                        market_cutoff=market_cutoff,
                        should_cancel=should_cancel,
                        progress_callback=capture_progress,
                    )
                except SetupLifecycleCaptureCancelled as exc:
                    raise SetupLifecycleEvaluationCancelled(str(exc)) from exc
            log_event(
                logger,
                "setup.lifecycle.capture_resolved",
                run_id=run_id,
                pipeline_run_id=pipeline_run_id,
                capture_attempt_id=capture_result.evaluation_run_id if capture_result else None,
                snapshot_count=len(capture_result.snapshot_ids)
                if capture_result
                else len(snapshot_ids or ()),
            )
            return self._evaluate_run(
                db,
                run_id,
                requester=requester,
                should_cancel=should_cancel,
                capture_result=capture_result,
                snapshot_ids=snapshot_ids,
                market_cutoff=market_cutoff,
                pipeline_run_id=pipeline_run_id,
                progress_callback=progress_callback,
            )
        except Exception as exc:
            from app.models.tables import UploadRun
            from app.services.background_job_service import JobLeaseLost
            from app.services.pipeline_executor import PipelineCancelled

            if not isinstance(db, Session) or isinstance(exc, JobLeaseLost):
                raise
            db.rollback()
            # The semantic writer has rolled back the complete business unit.
            # Only an audit row is committed here, in a separately fenced
            # transaction; the caller still owns a successful publication.
            with bounded_domain_session(db) as audit_db:
                if audit_db.get(UploadRun, run_id) is not None:
                    failed = self.repository.create_evaluation_run(
                        audit_db,
                        mode="LIVE",
                        status=EvaluationStatus.RUNNING.value,
                        engine_version=self.config.engine.version,
                        config_version=self.config.engine.config_version,
                        config_hash=self.config.config_hash,
                        source_run_id=run_id,
                        source_run_id_text=str(run_id),
                        output_evaluation_version=self._evaluation_version(run_id),
                        requester=requester,
                    )
                    self.repository.complete_evaluation_run(
                        audit_db,
                        failed,
                        status=(
                            EvaluationStatus.CANCELLED.value
                            if isinstance(
                                exc,
                                (
                                    SetupLifecycleEvaluationCancelled,
                                    SetupLifecycleCaptureCancelled,
                                    PipelineCancelled,
                                ),
                            )
                            else EvaluationStatus.FAILED.value
                        ),
                        current_phase="failed",
                        counts={"failed": 1},
                        errors={
                            "system": exc.as_dict()
                            if isinstance(exc, SetupCaptureHandoffError)
                            else str(exc)
                        },
                    )
                    audit_db.commit()
            raise

    @core_writer_transaction
    def _evaluate_run(
        self,
        db,
        run_id: int,
        *,
        requester: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        capture_result: SnapshotCaptureResult | None = None,
        snapshot_ids: tuple[int, ...] | None = None,
        market_cutoff: MarketCalculationCutoff | None = None,
        pipeline_run_id: int | None = None,
        progress_callback: Callable[..., None] | None = None,
    ) -> SetupLifecycleEvaluationResult:
        from sqlalchemy.orm import Session

        from app.services.entrypoint_authority import EntryPointAuthorityError

        adopt_alert_delivery = getattr(self.alert_service, "adopt_delivered_configuration", None)
        if callable(adopt_alert_delivery):
            adopt_alert_delivery()

        if (
            isinstance(db, Session)
            and market_cutoff is None
            and capture_result is None
            and snapshot_ids is None
        ):
            raise EntryPointAuthorityError(
                "SETUP_EXPLICIT_CALCULATION_AUTHORITY_REQUIRED",
                "Setup evaluation requires exact captured snapshot IDs "
                "or an explicit frozen cutoff.",
            )
        should_cancel = should_cancel or (lambda: False)
        if pipeline_run_id is not None:
            market_cutoff = assert_pipeline_calculation_context(
                db,
                pipeline_run_id=pipeline_run_id,
                upload_run_id=run_id,
                supplied_context=market_cutoff,
            )
        handoff_snapshot_ids = _handoff_snapshot_ids(capture_result, snapshot_ids)
        if isinstance(db, Session) and handoff_snapshot_ids is not None:
            completed = self._completed_publication(
                db,
                run_id,
                handoff_snapshot_ids,
                capture_result,
                market_cutoff,
                should_cancel=should_cancel,
                progress_callback=progress_callback,
            )
            if completed is not None:
                return completed
        evaluation_run = self.repository.create_evaluation_run(
            db,
            mode="LIVE",
            status=EvaluationStatus.RUNNING.value,
            engine_version=self.config.engine.version,
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            source_run_id=run_id,
            source_run_id_text=str(run_id),
            output_evaluation_version=self._evaluation_version(run_id),
            requester=requester,
        )

        try:
            self._checkpoint(db, evaluation_run.id, "capture", should_cancel)
            if handoff_snapshot_ids is None:
                capture = self.capture_service.capture_snapshots_for_run(
                    db,
                    run_id,
                    evaluation_run=evaluation_run,
                    requester=requester,
                    finalize_evaluation_run=False,
                    market_cutoff=market_cutoff,
                )
            else:
                self._validate_capture_handoff(
                    db,
                    run_id=run_id,
                    capture_result=capture_result,
                    snapshot_ids=handoff_snapshot_ids,
                    market_cutoff=market_cutoff,
                    pipeline_run_id=pipeline_run_id,
                )
                capture = capture_result or SnapshotCaptureResult(
                    evaluation_run_id=None,
                    status=EvaluationStatus.COMPLETED.value,
                    captured=len(handoff_snapshot_ids),
                    snapshot_ids=handoff_snapshot_ids,
                )
            capture_total = len(capture.snapshot_ids)
            _report_lifecycle_progress(
                progress_callback,
                phase="INPUTS_LOADED",
                processed=capture_total,
                total=capture_total,
                current_item="captured-setup-snapshots",
                last_completed_item="captured-setup-snapshots",
            )
            self._checkpoint(db, evaluation_run.id, "canonicalize", should_cancel)
            canonical = _canonicalize_run(
                self.canonicalizer,
                db,
                run_id=run_id,
                evaluation_run_id=evaluation_run.id,
                snapshot_ids=handoff_snapshot_ids,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )
            selected_snapshot_ids = tuple(canonical.selected_snapshot_ids)
            selected_total = len(selected_snapshot_ids)
            _report_lifecycle_progress(
                progress_callback,
                phase="CANDIDATES_SELECTED",
                processed=selected_total,
                total=capture_total,
                current_item="canonical-setup-candidates",
                last_completed_item="canonical-setup-candidates",
            )
            selected_snapshots = self.repository.get_snapshots_by_ids(db, selected_snapshot_ids)
            for index, snapshot in enumerate(selected_snapshots, start=1):
                if snapshot.evidence_id is None:
                    persist_setup_evidence(db, snapshot)
                else:
                    from app.services.decision_mutation_authority import (
                        validate_setup_projection,
                    )

                    validate_setup_projection(db, snapshot)
                if index % _LIFECYCLE_PROGRESS_BATCH_SIZE == 0 or index == selected_total:
                    _report_lifecycle_progress(
                        progress_callback,
                        phase="EVIDENCE_VALIDATION_BATCH_COMPLETED",
                        processed=index,
                        total=selected_total,
                        current_item=snapshot.ticker,
                        last_completed_item=snapshot.ticker,
                    )
            _report_lifecycle_progress(
                progress_callback,
                phase="EVIDENCE_AUTHORITY_VALIDATION_COMPLETED",
                processed=selected_total,
                total=selected_total,
                current_item="canonical-setup-candidates",
                last_completed_item="canonical-setup-candidates",
            )
            self._checkpoint(db, evaluation_run.id, "change_detection", should_cancel)
            changes = self.change_detector.detect_and_persist(
                db,
                evaluation_run_id=evaluation_run.id,
                snapshot_ids=selected_snapshot_ids,
                progress_callback=progress_callback,
                should_cancel=should_cancel,
            )
            _report_lifecycle_progress(
                progress_callback,
                phase="CHANGE_DETECTION_COMPLETED",
                processed=selected_total,
                total=selected_total,
                current_item="canonical-setup-candidates",
                last_completed_item="canonical-setup-candidates",
            )
            self._checkpoint(db, evaluation_run.id, "alerts", should_cancel)
            self.alert_service.seed_builtin_rules(db)
            change_alerts = self.alert_service.evaluate_signal_change_events(
                db,
                self.repository.get_signal_change_events_by_ids(db, changes.event_ids),
            )
            _report_lifecycle_progress(
                progress_callback,
                phase="CHANGE_ALERT_EVALUATION_COMPLETED",
                processed=len(changes.event_ids),
                total=len(changes.event_ids),
                current_item="signal-change-events",
                last_completed_item="signal-change-events",
            )
            self._checkpoint(db, evaluation_run.id, "lifecycle", should_cancel)
            lifecycle_transitions, lifecycle_alerts, reconciliation_plans = (
                self._evaluate_lifecycle_episodes(
                    db,
                    evaluation_run_id=evaluation_run.id,
                    snapshot_ids=selected_snapshot_ids,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                )
            )
            self._checkpoint(db, evaluation_run.id, "finalize", should_cancel)
        except SetupLifecycleEvaluationCancelled:
            if isinstance(db, Session):
                raise
            self.repository.complete_evaluation_run(
                db,
                evaluation_run,
                status=EvaluationStatus.CANCELLED.value,
                current_phase="cancelled",
                counts={"failed": 1},
            )
            raise
        except Exception as exc:
            if isinstance(db, Session):
                raise
            from app.services.setup_lifecycle.errors import SetupLifecycleReconciliationError

            self.repository.complete_evaluation_run(
                db,
                evaluation_run,
                status=EvaluationStatus.FAILED.value,
                current_phase="failed",
                counts={"failed": 1},
                errors={
                    "system": (
                        exc.as_dict()
                        if isinstance(exc, SetupLifecycleReconciliationError)
                        else str(exc)
                    )
                },
            )
            raise

        result = self._result(
            db,
            evaluation_run.id,
            capture,
            canonical,
            changes,
            lifecycle_transitions=lifecycle_transitions,
            alerts=change_alerts.created + lifecycle_alerts,
            reconciliation_plans=reconciliation_plans,
        )
        _report_lifecycle_progress(
            progress_callback,
            phase="PERSISTENCE_STARTED",
            processed=0,
            total=1,
            current_item="evaluation-result",
        )
        if isinstance(db, Session):
            self.repository.record_evaluation_audit(
                db,
                evaluation_run.id,
                {
                    **(evaluation_run.audit_json or {}),
                    "published_snapshots": {str(key): "published" for key in selected_snapshot_ids},
                    "published_change_events": {
                        str(key): "published" for key in changes.event_ids if key is not None
                    },
                    "published_alert_events": {
                        str(key): "published" for key in change_alerts.event_ids if key is not None
                    },
                },
            )
        self.repository.complete_evaluation_run(
            db,
            evaluation_run,
            status=result.status,
            current_phase="completed",
            counts={
                "read": capture.read,
                "captured": result.snapshots_captured,
                "canonical": result.canonical_snapshots,
                "changed": result.change_events,
                "transitioned": result.lifecycle_transitions,
                "alerted": result.alerts,
                "skipped": capture.skipped,
                "warning": result.warnings,
                "failed": result.failed,
                "reconciled": result.reconciled_episodes,
            },
            errors=dict(capture.errors_by_ticker),
            source_snapshot_min_id=min(capture.snapshot_ids) if capture.snapshot_ids else None,
            source_snapshot_max_id=max(capture.snapshot_ids) if capture.snapshot_ids else None,
        )
        _report_lifecycle_progress(
            progress_callback,
            phase="PERSISTENCE_STAGED",
            processed=1,
            total=1,
            current_item="evaluation-result",
            last_completed_item="evaluation-result",
        )
        return result

    def _completed_publication(
        self,
        db,
        run_id,
        snapshot_ids,
        capture,
        market_cutoff,
        *,
        should_cancel,
        progress_callback,
    ):
        from app.models.tables import (
            SetupLifecycleEpisode,
            SetupLifecycleEvaluationRun,
            SetupSignalSnapshotSelectionEvent,
            SignalAlertEvent,
        )
        from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
        from app.services.decision_mutation_authority import (
            validate_episode_projection,
        )
        from app.services.setup_lifecycle.alert_authority import validate_alert_event_projection
        from app.services.setup_lifecycle.change_authority import (
            _projection_pin,
            validate_signal_change,
        )

        for published in db.scalars(
            select(SetupLifecycleEvaluationRun)
            .where(
                SetupLifecycleEvaluationRun.source_run_id == run_id,
                SetupLifecycleEvaluationRun.config_hash == self.config.config_hash,
                SetupLifecycleEvaluationRun.status.in_(("COMPLETED", "PARTIAL")),
            )
            .order_by(SetupLifecycleEvaluationRun.id.desc())
        ):
            audit = published.audit_json or {}
            if set(audit.get("published_snapshots", {})) != {str(key) for key in snapshot_ids}:
                continue
            self._validate_capture_handoff(
                db,
                run_id=run_id,
                capture_result=capture,
                snapshot_ids=snapshot_ids,
                market_cutoff=market_cutoff,
            )

            def checkpoint(processed, total, item):
                if should_cancel():
                    raise SetupLifecycleEvaluationCancelled("Setup lifecycle replay cancelled.")
                _report_lifecycle_progress(
                    progress_callback,
                    phase="PUBLICATION_REPLAY_VALIDATION",
                    processed=processed,
                    total=total,
                    current_item=item,
                    last_completed_item=item if processed else None,
                )

            checkpoint(0, len(snapshot_ids), "snapshots")
            for index, snapshot in enumerate(
                self.repository.get_snapshots_by_ids(db, snapshot_ids), 1
            ):
                revision = db.scalar(
                    select(SetupSignalSnapshotSelectionEvent)
                    .where(
                        SetupSignalSnapshotSelectionEvent.selected_snapshot_id == snapshot.id,
                        SetupSignalSnapshotSelectionEvent.occurred_at <= published.completed_at,
                    )
                    .order_by(SetupSignalSnapshotSelectionEvent.id.desc())
                    .limit(1)
                )
                if revision is None:
                    raise ValueError("MUTATION_CHANGE_PUBLISHED_SELECTION_MISSING")
                _projection_pin(db, snapshot, discover=True, selection_event_id=revision.id)
                checkpoint(index, len(snapshot_ids), f"snapshot:{snapshot.id}")
            change_ids = tuple(int(key) for key in audit.get("published_change_events", {}))
            changes = self.repository.get_signal_change_events_by_ids(db, change_ids)
            if len(changes) != len(change_ids):
                raise ValueError("MUTATION_CHANGE_PUBLISHED_EVENT_MISSING")
            for index, change in enumerate(changes, 1):
                if should_cancel():
                    raise SetupLifecycleEvaluationCancelled("Setup lifecycle replay cancelled.")
                proof = (change.evidence_json or {}).get("native_change_proof") or {}
                verified = validate_signal_change(
                    db,
                    change,
                    configuration=self.effective_configuration,
                    source_manifest=proof.get("source_manifest"),
                    source_validation=True,
                )
                if Canonical.dumps(proof) != Canonical.dumps(verified):
                    raise ValueError("MUTATION_CHANGE_ALTERED_RETRY")
                if index % 10 == 0 or index == len(changes):
                    checkpoint(index, len(changes), f"change:{change.id}")
            episodes = list(
                db.scalars(
                    select(SetupLifecycleEpisode).where(
                        SetupLifecycleEpisode.current_snapshot_id.in_(snapshot_ids)
                    )
                )
            )
            for index, episode in enumerate(episodes, 1):
                validate_episode_projection(db, episode)
                checkpoint(index, len(episodes), f"episode:{episode.id}")
            alerts = list(
                db.scalars(
                    select(SignalAlertEvent).where(
                        (SignalAlertEvent.evaluation_run_id == published.id)
                        | SignalAlertEvent.id.in_(
                            tuple(int(key) for key in audit.get("published_alert_events", {}))
                        )
                    )
                )
            )
            for index, alert in enumerate(alerts, 1):
                validate_alert_event_projection(db, alert)
                checkpoint(index, len(alerts), f"alert:{alert.id}")
            return self._result(
                db,
                published.id,
                capture
                or SnapshotCaptureResult(
                    evaluation_run_id=None,
                    status=published.status,
                    snapshot_ids=snapshot_ids,
                    captured=len(snapshot_ids),
                ),
                CanonicalizationResult(selected_snapshot_ids=snapshot_ids),
                SignalChangeDetectionResult(event_ids=change_ids),
                lifecycle_transitions=0,
                alerts=0,
            )
        return None

    def _validate_capture_handoff(
        self,
        db,
        *,
        run_id: int,
        capture_result: SnapshotCaptureResult | None,
        snapshot_ids: tuple[int, ...],
        market_cutoff: MarketCalculationCutoff | None,
        pipeline_run_id: int | None = None,
    ) -> None:
        context = {
            "run_id": run_id,
            "pipeline_run_id": pipeline_run_id,
            "capture_attempt_id": capture_result.evaluation_run_id if capture_result else None,
            "calculation_context_id": market_cutoff.context_id if market_cutoff else None,
        }

        def reject(reason, message, **details):
            raise SetupCaptureHandoffError(reason, message, **context, **details)

        if len(snapshot_ids) != len(set(snapshot_ids)):
            reject("DUPLICATE_IDS", "Setup capture handoff contains duplicate snapshot IDs.")
        if capture_result is not None and capture_result.captured not in {0, len(snapshot_ids)}:
            reject("COUNT_MISMATCH", "Setup capture handoff count does not match snapshot IDs.")
        snapshots = self.repository.get_snapshots_by_ids(db, snapshot_ids)
        if len(snapshots) != len(snapshot_ids):
            reject("MISSING_IDS", "Setup capture handoff contains missing snapshot IDs.")
        expected_evaluation_run_id = (
            capture_result.evaluation_run_id if capture_result is not None else None
        )
        members = None
        if isinstance(db, Session) and expected_evaluation_run_id is not None:
            from app.services.setup_lifecycle.capture_provenance import validate_capture_membership

            members = validate_capture_membership(
                db,
                capture_attempt_id=expected_evaluation_run_id,
                run_id=run_id,
                config=self.config,
                snapshots=snapshots,
                pipeline_run_id=pipeline_run_id,
            )
        for snapshot in snapshots:
            if snapshot.run_id != run_id:
                reject(
                    "RUN_MISMATCH",
                    "Setup capture handoff contains a snapshot from another run.",
                    snapshot_id=snapshot.id,
                    ticker=snapshot.ticker,
                )
            if snapshot.config_hash != self.config.config_hash:
                reject(
                    "CONFIG_MISMATCH",
                    "Setup capture handoff config hash does not match evaluator.",
                    snapshot_id=snapshot.id,
                    ticker=snapshot.ticker,
                )
            if snapshot.engine_version != self.config.engine.version:
                reject(
                    "ENGINE_MISMATCH",
                    "Setup capture handoff engine version does not match evaluator.",
                    snapshot_id=snapshot.id,
                    ticker=snapshot.ticker,
                )
            expected_creator = (
                members[str(snapshot.id)]["creator_evaluation_run_id"]
                if members is not None
                else expected_evaluation_run_id
            )
            if (
                expected_evaluation_run_id is not None
                and snapshot.evaluation_run_id != expected_creator
            ):
                reject(
                    "OWNER_MISMATCH",
                    "Setup capture handoff snapshot ownership is inconsistent.",
                    snapshot_id=snapshot.id,
                    ticker=snapshot.ticker,
                    expected_creator_id=expected_creator,
                    actual_creator_id=snapshot.evaluation_run_id,
                )
            if market_cutoff is not None and (
                snapshot.calculation_context_id != market_cutoff.context_id
                or snapshot.calculation_cutoff_at != market_cutoff.cutoff_at
                or snapshot.input_as_of_session != market_cutoff.latest_completed_session
                or snapshot.calendar_version != market_cutoff.calendar_version
            ):
                reject(
                    "CONTEXT_MISMATCH",
                    "Setup capture handoff does not match the pipeline market context.",
                    snapshot_id=snapshot.id,
                    ticker=snapshot.ticker,
                )

    def _checkpoint(
        self,
        db,
        evaluation_run_id: int,
        phase: str,
        should_cancel: Callable[[], bool],
    ) -> None:
        self.repository.heartbeat_evaluation_run(db, evaluation_run_id, current_phase=phase)
        if should_cancel():
            raise SetupLifecycleEvaluationCancelled("Setup lifecycle evaluation cancelled.")

    def _result(
        self,
        db,
        evaluation_run_id: int,
        capture: SnapshotCaptureResult,
        canonical: CanonicalizationResult,
        changes: SignalChangeDetectionResult,
        *,
        lifecycle_transitions: int,
        alerts: int,
        reconciliation_plans: tuple[dict[str, Any], ...] = (),
    ) -> SetupLifecycleEvaluationResult:
        failed = capture.failed
        status = EvaluationStatus.PARTIAL.value if failed else EvaluationStatus.COMPLETED.value
        return SetupLifecycleEvaluationResult(
            evaluation_run_id=evaluation_run_id,
            status=status,
            snapshots_captured=capture.captured,
            canonical_snapshots=canonical.selected_count,
            canonical_changed=canonical.changed_count,
            change_events=changes.created_events,
            lifecycle_transitions=lifecycle_transitions,
            alerts=alerts,
            active_episodes=self.repository.count_active_episodes(
                db,
                config_hash=self.config.config_hash,
            ),
            low_confidence=capture.low_confidence,
            failed=failed,
            warnings=capture.warning + len(canonical.warnings),
            captured_snapshot_ids=capture.snapshot_ids,
            canonical_snapshot_ids=canonical.selected_snapshot_ids,
            errors_by_ticker=dict(capture.errors_by_ticker),
            reconciled_episodes=sum(
                len(plan.get("displaced_episode_ids", ())) for plan in reconciliation_plans
            ),
            reconciliation_plans=reconciliation_plans,
        )

    def _evaluation_version(self, run_id: int) -> str:
        return f"{self.config.engine.version}:run:{run_id}:config:{self.config.config_hash[:12]}"

    def _evaluate_lifecycle_episodes(
        self,
        db,
        *,
        evaluation_run_id: int,
        snapshot_ids: tuple[int, ...],
        progress_callback: Callable[..., None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> tuple[int, int, tuple[dict[str, Any], ...]]:
        transitions = 0
        alert_created = 0
        snapshots = self.repository.get_snapshots_by_ids(db, snapshot_ids)
        window = self.config.episodes.history_window_sessions
        cutoffs: dict[tuple[str, str], Any] = {}
        for snapshot in snapshots:
            key = (snapshot.ticker, snapshot.timeframe)
            cutoffs[key] = min(cutoffs.get(key, snapshot.data_as_of_date), snapshot.data_as_of_date)
        history_loader = getattr(
            self.repository,
            "canonical_snapshot_histories_before",
            None,
        )
        prior_rows = (
            history_loader(db, cutoffs=cutoffs, limit=window) if history_loader is not None else {}
        )
        history_by_key = {
            key: [normalized_snapshot_from_row(row) for row in rows]
            for key, rows in prior_rows.items()
        }

        episode_loader = getattr(self.repository, "lifecycle_episodes_for_keys", None)
        apply_parameters = signature(self.episode_service.apply_snapshot).parameters
        use_preloaded_episodes = (
            episode_loader is not None and "preloaded_episodes" in apply_parameters
        )
        episode_keys = {(snapshot.ticker, snapshot.timeframe) for snapshot in snapshots}
        episodes_by_key = episode_loader(db, episode_keys) if use_preloaded_episodes else {}
        alert_parameters = signature(self.alert_service.evaluate_episode_result).parameters
        preload_alert_rules = "rules" in alert_parameters
        alert_rules = self.alert_service.rules_for_evaluation(db) if preload_alert_rules else None
        total = len(snapshots)
        _report_lifecycle_progress(
            progress_callback,
            phase="LIFECYCLE_INPUTS_LOADED",
            processed=total,
            total=total,
            current_item="history-episodes-alert-rules",
            last_completed_item="history-episodes-alert-rules",
        )

        planner = getattr(self.episode_service, "plan_reconciliation", None)
        plans_by_snapshot: dict[int, Any] = {}
        if callable(planner) and use_preloaded_episodes:
            for index, snapshot in enumerate(snapshots, start=1):
                if should_cancel is not None and should_cancel():
                    raise SetupLifecycleEvaluationCancelled("Setup reconciliation cancelled.")
                key = (snapshot.ticker, snapshot.timeframe)
                plan = planner(
                    db,
                    snapshot,
                    prior_snapshots=tuple(history_by_key.get(key, ())[-window:]),
                    preloaded_episodes=tuple(episodes_by_key.get(key, ())),
                )
                plans_by_snapshot[snapshot.id] = plan
                if index % _LIFECYCLE_PROGRESS_BATCH_SIZE == 0 or index == total:
                    _report_lifecycle_progress(
                        progress_callback,
                        phase="RECONCILIATION_BATCH_COMPLETED",
                        processed=index,
                        total=total,
                        current_item=snapshot.ticker,
                        last_completed_item=snapshot.ticker,
                    )
            audit = {
                "contract": "setup-lifecycle-reconciliation-preflight-v1",
                "plans": [plans_by_snapshot[key].as_dict() for key in sorted(plans_by_snapshot)],
            }
            recorder = getattr(self.repository, "record_evaluation_audit", None)
            if callable(recorder):
                recorder(db, evaluation_run_id, audit)

        _report_lifecycle_progress(
            progress_callback,
            phase="LIFECYCLE_EVALUATION_STARTED",
            processed=0,
            total=total,
            current_item="setup-candidates",
        )
        for index, snapshot in enumerate(snapshots, start=1):
            if should_cancel is not None and should_cancel():
                raise SetupLifecycleEvaluationCancelled("Setup lifecycle evaluation cancelled.")
            key = (snapshot.ticker, snapshot.timeframe)
            history = history_by_key.setdefault(key, [])
            episode_kwargs = (
                {
                    "preloaded_episodes": tuple(episodes_by_key.get(key, ())),
                    "refresh_primary": False,
                    **(
                        {"reconciliation_plan": plans_by_snapshot[snapshot.id]}
                        if snapshot.id in plans_by_snapshot
                        and "reconciliation_plan" in apply_parameters
                        else {}
                    ),
                }
                if use_preloaded_episodes
                else {}
            )
            result = self.episode_service.apply_snapshot(
                db,
                snapshot,
                evaluation_run_id=evaluation_run_id,
                prior_snapshots=tuple(history[-window:]),
                **episode_kwargs,
            )
            if use_preloaded_episodes and result.episode is not None:
                cached = episodes_by_key.setdefault(key, [])
                if result.episode not in cached:
                    cached.append(result.episode)
            history.append(normalized_snapshot_from_row(snapshot))
            if result.lifecycle_event is not None and not result.opened:
                transitions += 1
            reconciliation_results = tuple(getattr(result, "reconciliation_results", ()))
            all_results = (*reconciliation_results, result)
            transitions += sum(
                1 for reconciled in reconciliation_results if reconciled.lifecycle_event is not None
            )
            for evaluated in all_results:
                alerts = self.alert_service.evaluate_episode_result(
                    db,
                    evaluated,
                    evaluation_run_id=evaluation_run_id,
                    **({"rules": alert_rules} if preload_alert_rules else {}),
                )
                alert_created += alerts.created
            if index % _LIFECYCLE_PROGRESS_BATCH_SIZE == 0 or index == total:
                _report_lifecycle_progress(
                    progress_callback,
                    phase="LIFECYCLE_EVALUATION_BATCH_COMPLETED",
                    processed=index,
                    total=total,
                    current_item=snapshot.ticker,
                    last_completed_item=snapshot.ticker,
                )
        if use_preloaded_episodes:
            snapshots_by_key = {
                (snapshot.ticker, snapshot.timeframe): snapshot for snapshot in snapshots
            }
            ordered_keys = sorted(episode_keys)
            primary_total = len(ordered_keys)
            for offset in range(0, primary_total, _LIFECYCLE_PROGRESS_BATCH_SIZE):
                batch_keys = ordered_keys[offset : offset + _LIFECYCLE_PROGRESS_BATCH_SIZE]
                self.episode_service.refresh_primary_statuses(
                    db,
                    keys=batch_keys,
                    market_cutoffs={
                        key: self.episode_service._snapshot_cutoff(db, snapshots_by_key[key])
                        for key in batch_keys
                    },
                )
                processed = offset + len(batch_keys)
                _report_lifecycle_progress(
                    progress_callback,
                    phase="PRIMARY_STATUS_REFRESH_BATCH_COMPLETED",
                    processed=processed,
                    total=primary_total,
                    current_item=batch_keys[-1][0],
                    last_completed_item=batch_keys[-1][0],
                )
        _report_lifecycle_progress(
            progress_callback,
            phase="STATE_TRANSITION_EVALUATION_COMPLETED",
            processed=total,
            total=total,
            current_item="setup-candidates",
            last_completed_item="setup-candidates",
        )
        return (
            transitions,
            alert_created,
            tuple(plans_by_snapshot[key].as_dict() for key in sorted(plans_by_snapshot)),
        )


def evaluate_setup_lifecycles_for_run(
    db,
    run_id: int,
    *,
    capture_result: SnapshotCaptureResult | None = None,
    snapshot_ids: tuple[int, ...] | None = None,
) -> SetupLifecycleEvaluationResult:
    return SetupLifecycleEvaluationService().evaluate_run(
        db,
        run_id,
        capture_result=capture_result,
        snapshot_ids=snapshot_ids,
    )


def _report_lifecycle_progress(
    progress_callback: Callable[..., None] | None,
    *,
    phase: str,
    processed: int,
    total: int,
    current_item: str | None = None,
    last_completed_item: str | None = None,
) -> None:
    if progress_callback is None:
        return
    progress_callback(
        phase=phase,
        processed=processed,
        total=total,
        current_item=current_item,
        last_completed_item=last_completed_item,
    )


def _handoff_snapshot_ids(
    capture_result: SnapshotCaptureResult | None,
    snapshot_ids: tuple[int, ...] | None,
) -> tuple[int, ...] | None:
    capture_ids = tuple(capture_result.snapshot_ids) if capture_result is not None else None
    explicit_ids = tuple(snapshot_ids) if snapshot_ids is not None else None
    if capture_ids is not None and explicit_ids is not None and capture_ids != explicit_ids:
        raise ValueError("Setup capture handoff snapshot IDs disagree.")
    return explicit_ids if explicit_ids is not None else capture_ids


def _canonicalize_run(
    canonicalizer,
    db,
    *,
    run_id: int,
    evaluation_run_id: int,
    snapshot_ids: tuple[int, ...] | None,
    progress_callback=None,
    should_cancel=None,
) -> CanonicalizationResult:
    parameters = tuple(signature(canonicalizer.canonicalize_run).parameters.values())
    accepts_snapshot_ids = any(
        parameter.name == "snapshot_ids" or parameter.kind == Parameter.VAR_KEYWORD
        for parameter in parameters
    )
    kwargs: dict[str, Any] = {
        "run_id": run_id,
        "evaluation_run_id": evaluation_run_id,
    }
    if snapshot_ids is not None and accepts_snapshot_ids:
        kwargs["snapshot_ids"] = snapshot_ids
    for name, value in (("progress_callback", progress_callback), ("should_cancel", should_cancel)):
        if any(p.name == name or p.kind == Parameter.VAR_KEYWORD for p in parameters):
            kwargs[name] = value
    return canonicalizer.canonicalize_run(db, **kwargs)
