from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from inspect import Parameter, signature
from typing import Any

from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
from app.services.setup_lifecycle.canonicalization import (
    CanonicalizationResult,
    SetupLifecycleCanonicalizer,
)
from app.services.setup_lifecycle.change_detector import (
    SetupLifecycleChangeDetector,
    SignalChangeDetectionResult,
)
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import EvaluationStatus
from app.services.setup_lifecycle.episode_service import (
    SetupLifecycleEpisodeService,
    normalized_snapshot_from_row,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import (
    SetupLifecycleSnapshotCaptureService,
    SnapshotCaptureResult,
)


class SetupLifecycleEvaluationCancelled(Exception):
    pass


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
        self.config = config or load_setup_lifecycle_config()
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

    def evaluate_run(
        self,
        db,
        run_id: int,
        *,
        requester: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
        capture_result: SnapshotCaptureResult | None = None,
        snapshot_ids: tuple[int, ...] | None = None,
    ) -> SetupLifecycleEvaluationResult:
        should_cancel = should_cancel or (lambda: False)
        handoff_snapshot_ids = _handoff_snapshot_ids(capture_result, snapshot_ids)
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
                )
            else:
                self._validate_capture_handoff(
                    db,
                    run_id=run_id,
                    capture_result=capture_result,
                    snapshot_ids=handoff_snapshot_ids,
                )
                capture = capture_result or SnapshotCaptureResult(
                    evaluation_run_id=None,
                    status=EvaluationStatus.COMPLETED.value,
                    captured=len(handoff_snapshot_ids),
                    snapshot_ids=handoff_snapshot_ids,
                )
            self._checkpoint(db, evaluation_run.id, "canonicalize", should_cancel)
            canonical = _canonicalize_run(
                self.canonicalizer,
                db,
                run_id=run_id,
                evaluation_run_id=evaluation_run.id,
                snapshot_ids=handoff_snapshot_ids,
            )
            self._checkpoint(db, evaluation_run.id, "change_detection", should_cancel)
            changes = self.change_detector.detect_and_persist(
                db,
                evaluation_run_id=evaluation_run.id,
                snapshot_ids=canonical.selected_snapshot_ids,
            )
            self._checkpoint(db, evaluation_run.id, "alerts", should_cancel)
            self.alert_service.seed_builtin_rules(db)
            change_alerts = self.alert_service.evaluate_signal_change_events(
                db,
                self.repository.get_signal_change_events_by_ids(db, changes.event_ids),
            )
            self._checkpoint(db, evaluation_run.id, "lifecycle", should_cancel)
            lifecycle_transitions, lifecycle_alerts = self._evaluate_lifecycle_episodes(
                db,
                evaluation_run_id=evaluation_run.id,
                snapshot_ids=canonical.selected_snapshot_ids,
            )
            self._checkpoint(db, evaluation_run.id, "finalize", should_cancel)
        except SetupLifecycleEvaluationCancelled:
            self.repository.complete_evaluation_run(
                db,
                evaluation_run,
                status=EvaluationStatus.CANCELLED.value,
                current_phase="cancelled",
                counts={"failed": 1},
            )
            raise
        except Exception as exc:
            self.repository.complete_evaluation_run(
                db,
                evaluation_run,
                status=EvaluationStatus.FAILED.value,
                current_phase="failed",
                counts={"failed": 1},
                errors={"system": str(exc)},
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
            },
            errors=dict(capture.errors_by_ticker),
            source_snapshot_min_id=min(capture.snapshot_ids) if capture.snapshot_ids else None,
            source_snapshot_max_id=max(capture.snapshot_ids) if capture.snapshot_ids else None,
        )
        return result

    def _validate_capture_handoff(
        self,
        db,
        *,
        run_id: int,
        capture_result: SnapshotCaptureResult | None,
        snapshot_ids: tuple[int, ...],
    ) -> None:
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("Setup capture handoff contains duplicate snapshot IDs.")
        if capture_result is not None and capture_result.captured not in {0, len(snapshot_ids)}:
            raise ValueError("Setup capture handoff count does not match snapshot IDs.")
        snapshots = self.repository.get_snapshots_by_ids(db, snapshot_ids)
        if len(snapshots) != len(snapshot_ids):
            raise ValueError("Setup capture handoff contains missing snapshot IDs.")
        expected_evaluation_run_id = (
            capture_result.evaluation_run_id if capture_result is not None else None
        )
        for snapshot in snapshots:
            if snapshot.run_id != run_id:
                raise ValueError("Setup capture handoff contains a snapshot from another run.")
            if snapshot.config_hash != self.config.config_hash:
                raise ValueError("Setup capture handoff config hash does not match evaluator.")
            if snapshot.engine_version != self.config.engine.version:
                raise ValueError("Setup capture handoff engine version does not match evaluator.")
            if (
                expected_evaluation_run_id is not None
                and snapshot.evaluation_run_id != expected_evaluation_run_id
            ):
                raise ValueError("Setup capture handoff snapshot ownership is inconsistent.")

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
        )

    def _evaluation_version(self, run_id: int) -> str:
        return f"{self.config.engine.version}:run:{run_id}:config:{self.config.config_hash[:12]}"

    def _evaluate_lifecycle_episodes(
        self,
        db,
        *,
        evaluation_run_id: int,
        snapshot_ids: tuple[int, ...],
    ) -> tuple[int, int]:
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
            history_loader(db, cutoffs=cutoffs, limit=window)
            if history_loader is not None
            else {}
        )
        history_by_key = {
            key: [normalized_snapshot_from_row(row) for row in rows]
            for key, rows in prior_rows.items()
        }

        for snapshot in snapshots:
            key = (snapshot.ticker, snapshot.timeframe)
            history = history_by_key.setdefault(key, [])
            result = self.episode_service.apply_snapshot(
                db,
                snapshot,
                evaluation_run_id=evaluation_run_id,
                prior_snapshots=tuple(history[-window:]),
            )
            history.append(normalized_snapshot_from_row(snapshot))
            if result.lifecycle_event is not None and not result.opened:
                transitions += 1
            alerts = self.alert_service.evaluate_episode_result(
                db,
                result,
                evaluation_run_id=evaluation_run_id,
            )
            alert_created += alerts.created
        return transitions, alert_created


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
) -> CanonicalizationResult:
    parameters = signature(canonicalizer.canonicalize_run).parameters.values()
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
    return canonicalizer.canonicalize_run(db, **kwargs)
