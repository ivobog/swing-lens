from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.services.setup_lifecycle.canonicalization import (
    CanonicalizationResult,
    SetupLifecycleCanonicalizer,
)
from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import EvaluationStatus
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

    def evaluate_run(
        self,
        db,
        run_id: int,
        *,
        requester: str | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> SetupLifecycleEvaluationResult:
        should_cancel = should_cancel or (lambda: False)
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
            capture = self.capture_service.capture_snapshots_for_run(
                db,
                run_id,
                evaluation_run=evaluation_run,
                requester=requester,
                finalize_evaluation_run=False,
            )
            self._checkpoint(db, evaluation_run.id, "canonicalize", should_cancel)
            canonical = self.canonicalizer.canonicalize_run(
                db,
                run_id=run_id,
                evaluation_run_id=evaluation_run.id,
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

        result = self._result(db, evaluation_run.id, capture, canonical)
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
    ) -> SetupLifecycleEvaluationResult:
        failed = capture.failed
        status = EvaluationStatus.PARTIAL.value if failed else EvaluationStatus.COMPLETED.value
        return SetupLifecycleEvaluationResult(
            evaluation_run_id=evaluation_run_id,
            status=status,
            snapshots_captured=capture.captured,
            canonical_snapshots=canonical.selected_count,
            canonical_changed=canonical.changed_count,
            change_events=0,
            lifecycle_transitions=0,
            alerts=0,
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


def evaluate_setup_lifecycles_for_run(
    db,
    run_id: int,
) -> SetupLifecycleEvaluationResult:
    return SetupLifecycleEvaluationService().evaluate_run(db, run_id)
