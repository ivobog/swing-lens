from __future__ import annotations

import pytest

from app.models.tables import SetupLifecycleEvaluationRun
from app.services.setup_lifecycle.canonicalization import CanonicalizationResult
from app.services.setup_lifecycle.change_detector import SignalChangeDetectionResult
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.setup_lifecycle.enums import EvaluationStatus
from app.services.setup_lifecycle.evaluation_service import (
    SetupLifecycleEvaluationCancelled,
    SetupLifecycleEvaluationService,
)
from app.services.setup_lifecycle.snapshot_builder import SnapshotCaptureResult


def test_evaluation_service_captures_canonicalizes_and_finalizes_counts() -> None:
    repository = FakeEvaluationRepository()
    service = SetupLifecycleEvaluationService(
        repository=repository,
        capture_service=FakeCaptureService(
            SnapshotCaptureResult(
                evaluation_run_id=1,
                status=EvaluationStatus.COMPLETED.value,
                read=2,
                captured=2,
                warning=1,
                low_confidence=1,
                snapshot_ids=(10, 11),
            )
        ),
        canonicalizer=FakeCanonicalizer(
            CanonicalizationResult(
                selected_snapshot_ids=(11,),
                changed_snapshot_ids=(11,),
                audit_event_ids=(90,),
                groups_evaluated=1,
            )
        ),
        change_detector=FakeChangeDetector(SignalChangeDetectionResult(created_events=4)),
        episode_service=FakeEpisodeService(transitions=1),
        config=load_setup_lifecycle_config(),
    )

    result = service.evaluate_run(db=object(), run_id=7, requester="tester")

    assert result.status == EvaluationStatus.COMPLETED.value
    assert result.snapshots_captured == 2
    assert result.canonical_snapshots == 1
    assert result.canonical_changed == 1
    assert result.change_events == 4
    assert result.lifecycle_transitions == 1
    assert result.low_confidence == 1
    assert result.active_episodes == 3
    assert repository.completed[-1].canonical_count == 1
    assert repository.completed[-1].source_snapshot_min_id == 10
    assert repository.completed[-1].source_snapshot_max_id == 11
    assert repository.heartbeats == [
        "capture",
        "canonicalize",
        "change_detection",
        "lifecycle",
        "finalize",
    ]


def test_evaluation_service_marks_partial_when_capture_has_ticker_failures() -> None:
    repository = FakeEvaluationRepository()
    service = SetupLifecycleEvaluationService(
        repository=repository,
        capture_service=FakeCaptureService(
            SnapshotCaptureResult(
                evaluation_run_id=1,
                status=EvaluationStatus.PARTIAL.value,
                read=2,
                captured=1,
                failed=1,
                snapshot_ids=(10,),
                errors_by_ticker={"BAD": "source failed"},
            )
        ),
        canonicalizer=FakeCanonicalizer(CanonicalizationResult(selected_snapshot_ids=(10,))),
        change_detector=FakeChangeDetector(SignalChangeDetectionResult()),
        episode_service=FakeEpisodeService(),
        config=load_setup_lifecycle_config(),
    )

    result = service.evaluate_run(db=object(), run_id=7)

    assert result.status == EvaluationStatus.PARTIAL.value
    assert result.failed == 1
    assert repository.completed[-1].status == EvaluationStatus.PARTIAL.value
    assert repository.completed[-1].error_summary_json == {"BAD": "source failed"}


def test_evaluation_service_cancellation_finalizes_run_as_cancelled() -> None:
    repository = FakeEvaluationRepository()
    service = SetupLifecycleEvaluationService(
        repository=repository,
        capture_service=FakeCaptureService(SnapshotCaptureResult(evaluation_run_id=1, status="X")),
        canonicalizer=FakeCanonicalizer(CanonicalizationResult()),
        change_detector=FakeChangeDetector(SignalChangeDetectionResult()),
        episode_service=FakeEpisodeService(),
        config=load_setup_lifecycle_config(),
    )
    checks = iter([True])

    with pytest.raises(SetupLifecycleEvaluationCancelled):
        service.evaluate_run(db=object(), run_id=7, should_cancel=lambda: next(checks))

    assert repository.completed[-1].status == EvaluationStatus.CANCELLED.value
    assert repository.completed[-1].current_phase == "cancelled"


class FakeEvaluationRepository:
    def __init__(self) -> None:
        self.created = []
        self.completed: list[SetupLifecycleEvaluationRun] = []
        self.heartbeats: list[str] = []

    def create_evaluation_run(self, _db, **kwargs):
        run = SetupLifecycleEvaluationRun(id=1, **kwargs)
        self.created.append(run)
        return run

    def heartbeat_evaluation_run(self, _db, _evaluation_run_id, *, current_phase=None):
        self.heartbeats.append(current_phase)

    def complete_evaluation_run(
        self,
        _db,
        evaluation_run,
        *,
        status,
        counts=None,
        errors=None,
        **kwargs,
    ):
        evaluation_run.status = status
        evaluation_run.current_phase = kwargs.get("current_phase")
        evaluation_run.source_snapshot_min_id = kwargs.get("source_snapshot_min_id")
        evaluation_run.source_snapshot_max_id = kwargs.get("source_snapshot_max_id")
        evaluation_run.error_summary_json = dict(errors or {})
        if counts:
            for key, value in counts.items():
                if hasattr(evaluation_run, f"{key}_count"):
                    setattr(evaluation_run, f"{key}_count", value)
        self.completed.append(evaluation_run)
        return evaluation_run

    def count_active_episodes(self, _db, *, config_hash=None):
        return 3

    def get_snapshots_by_ids(self, _db, snapshot_ids):
        return [object() for _snapshot_id in snapshot_ids]


class FakeCaptureService:
    def __init__(self, result: SnapshotCaptureResult) -> None:
        self.result = result

    def capture_snapshots_for_run(self, _db, _run_id, **_kwargs) -> SnapshotCaptureResult:
        return self.result


class FakeCanonicalizer:
    def __init__(self, result: CanonicalizationResult) -> None:
        self.result = result

    def canonicalize_run(self, _db, *, run_id, evaluation_run_id=None) -> CanonicalizationResult:
        return self.result


class FakeChangeDetector:
    def __init__(self, result: SignalChangeDetectionResult) -> None:
        self.result = result

    def detect_and_persist(
        self,
        _db,
        *,
        evaluation_run_id,
        snapshot_ids,
    ) -> SignalChangeDetectionResult:
        return self.result


class FakeEpisodeService:
    def __init__(self, transitions: int = 0) -> None:
        self.transitions = transitions
        self.calls = 0

    def apply_snapshot(self, _db, _snapshot, *, evaluation_run_id=None):
        self.calls += 1
        has_transition = self.calls <= self.transitions
        return type(
            "EpisodeResult",
            (),
            {
                "lifecycle_event": object() if has_transition else None,
                "opened": False,
            },
        )()
