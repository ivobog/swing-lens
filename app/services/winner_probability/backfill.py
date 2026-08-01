from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import UploadRun
from app.services.winner_probability.capture_service import (
    WinnerPredictionCaptureCancelled,
    WinnerPredictionCaptureResult,
    WinnerPredictionCaptureService,
)
from app.services.winner_probability.config import WinnerProbabilityConfig
from app.settings import Settings

DEFAULT_RECONSTRUCTION_METHOD = "HISTORICAL_AS_OF_REPLAY"
TRUST_MARKERS = ("point_in_time_trustworthy", "owpe_trusted_source")


class WinnerBackfillCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class RolloutReadinessCheck:
    name: str
    passed: bool
    message: str


@dataclass(frozen=True)
class RolloutReadinessReport:
    checks: tuple[RolloutReadinessCheck, ...]
    safe_defaults: bool
    ready_for_manual_capture: bool
    ready_for_pipeline_capture: bool
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class BackfillRequest:
    run_ids: tuple[int, ...]
    trusted_run_ids: tuple[int, ...] = ()
    reconstruction_method: str = DEFAULT_RECONSTRUCTION_METHOD
    limit: int = 100
    allow_reconstructed_training: bool = False


@dataclass(frozen=True)
class BackfillPlanItem:
    run_id: int
    status: str
    reason: str | None
    source_cutoff_at: datetime | None
    reconstruction_method: str
    source_quality_flags: tuple[str, ...]
    production_training_allowed: bool


@dataclass(frozen=True)
class BackfillPlan:
    items: tuple[BackfillPlanItem, ...]

    @property
    def ready_count(self) -> int:
        return sum(item.status == "READY" for item in self.items)

    @property
    def skipped_count(self) -> int:
        return sum(item.status == "SKIPPED" for item in self.items)


@dataclass(frozen=True)
class BackfillExecutionResult:
    plan: BackfillPlan
    captured_runs: int
    skipped_runs: int
    counts: dict[str, int]


class WinnerProbabilityRolloutService:
    def readiness_report(
        self,
        *,
        settings: Settings,
        migrations_applied: bool,
        lease_hardened: bool,
        decision_time_estimator_available: bool,
        selected_runs_validated: bool = False,
        reproduction_validated: bool = False,
        cohort_baseline_stable: bool = False,
    ) -> RolloutReadinessReport:
        checks = (
            RolloutReadinessCheck(
                "feature_flags_disabled_by_default",
                not settings.winner_probability_enabled
                and not settings.winner_probability_capture_in_pipeline
                and not settings.winner_probability_admin_enabled,
                "OWPE starts with capture, pipeline capture, and admin mutation disabled.",
            ),
            RolloutReadinessCheck(
                "migrations_applied",
                migrations_applied,
                "OWPE schema migrations are applied before rollout actions.",
            ),
            RolloutReadinessCheck(
                "lease_heartbeat_and_fencing",
                lease_hardened,
                "Long-running jobs have heartbeat and fencing protection.",
            ),
            RolloutReadinessCheck(
                "decision_time_estimator",
                decision_time_estimator_available,
                "Manual capture requires the real decision-time estimator.",
            ),
            RolloutReadinessCheck(
                "selected_capture_validation",
                selected_runs_validated,
                "Selected completed runs must validate snapshots, pending rows, and estimates.",
            ),
            RolloutReadinessCheck(
                "reproduction_validation",
                reproduction_validated,
                "Selected estimates must reproduce from immutable evidence manifests.",
            ),
            RolloutReadinessCheck(
                "cohort_baseline_stable",
                cohort_baseline_stable,
                (
                    "Similarity and shadow models stay supporting-only until "
                    "baseline stability exists."
                ),
            ),
        )
        blockers = tuple(check.name for check in checks if not check.passed)
        ready_for_manual_capture = all(
            _check(checks, name)
            for name in (
                "migrations_applied",
                "lease_heartbeat_and_fencing",
                "decision_time_estimator",
            )
        )
        ready_for_pipeline_capture = ready_for_manual_capture and all(
            _check(checks, name)
            for name in ("selected_capture_validation", "reproduction_validation")
        )
        return RolloutReadinessReport(
            checks=checks,
            safe_defaults=_check(checks, "feature_flags_disabled_by_default"),
            ready_for_manual_capture=ready_for_manual_capture,
            ready_for_pipeline_capture=ready_for_pipeline_capture,
            blockers=blockers,
        )


class WinnerProbabilityBackfillService:
    def plan_backfill(
        self,
        db: Session,
        request: BackfillRequest,
    ) -> BackfillPlan:
        if not request.run_ids:
            raise ValueError("backfill requires at least one run_id")
        if request.limit <= 0:
            raise ValueError("backfill limit must be positive")
        runs = _load_runs(db, request.run_ids, request.limit)
        by_id = {run.id: run for run in runs}
        items: list[BackfillPlanItem] = []
        for run_id in request.run_ids[: request.limit]:
            run = by_id.get(run_id)
            if run is None:
                items.append(_skipped_item(run_id, request, "run_not_found"))
                continue
            items.append(_plan_run(run, request))
        return BackfillPlan(items=tuple(items))

    def execute_backfill(
        self,
        db: Session,
        request: BackfillRequest,
        *,
        config: WinnerProbabilityConfig,
        capture_service: WinnerPredictionCaptureService | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> BackfillExecutionResult:
        plan = self.plan_backfill(db, request)
        capture_service = capture_service or WinnerPredictionCaptureService()
        counts: dict[str, int] = {
            "inserted": 0,
            "duplicate": 0,
            "excluded": 0,
            "failed": 0,
            "warnings": 0,
            "pending_outcomes": 0,
            "target_stop_outcomes": 0,
            "decision_time_estimates": 0,
            "insufficient_estimates": 0,
        }
        captured_runs = 0
        for item in plan.items:
            if item.status != "READY":
                continue
            if should_cancel is not None and should_cancel():
                raise WinnerBackfillCancelled("winner probability backfill was cancelled")
            try:
                result = capture_service.capture_run(
                    db,
                    run_id=item.run_id,
                    config=config,
                    captured_at=item.source_cutoff_at,
                    reconstruction_method=item.reconstruction_method,
                    source_quality_flags=item.source_quality_flags,
                    production_training_allowed=item.production_training_allowed,
                    should_cancel=should_cancel,
                )
            except WinnerPredictionCaptureCancelled as exc:
                raise WinnerBackfillCancelled(str(exc)) from exc
            _merge_counts(counts, result)
            captured_runs += 1
        return BackfillExecutionResult(
            plan=plan,
            captured_runs=captured_runs,
            skipped_runs=plan.skipped_count,
            counts=counts,
        )


def _load_runs(db: Session, run_ids: tuple[int, ...], limit: int) -> tuple[UploadRun, ...]:
    rows = db.scalars(
        select(UploadRun)
        .where(UploadRun.id.in_(run_ids[:limit]))
        .order_by(UploadRun.uploaded_at.asc(), UploadRun.id.asc())
    )
    all_method = getattr(rows, "all", None)
    return tuple(all_method() if callable(all_method) else rows)


def _plan_run(run: UploadRun, request: BackfillRequest) -> BackfillPlanItem:
    flags = ["reconstructed_history", "exclude_from_production_training"]
    if run.status != "COMPLETED":
        return _skipped_item(run.id, request, "run_not_completed")
    source_cutoff_at = run.processed_at or run.uploaded_at
    if source_cutoff_at is None:
        return _skipped_item(run.id, request, "missing_source_cutoff")
    if not _is_trustworthy(run, request.trusted_run_ids):
        return _skipped_item(run.id, request, "untrusted_point_in_time_source")
    if run.id not in request.trusted_run_ids:
        flags.append("trusted_by_run_notes")
    return BackfillPlanItem(
        run_id=run.id,
        status="READY",
        reason=None,
        source_cutoff_at=source_cutoff_at,
        reconstruction_method=request.reconstruction_method,
        source_quality_flags=tuple(flags),
        production_training_allowed=request.allow_reconstructed_training,
    )


def _skipped_item(
    run_id: int,
    request: BackfillRequest,
    reason: str,
) -> BackfillPlanItem:
    return BackfillPlanItem(
        run_id=run_id,
        status="SKIPPED",
        reason=reason,
        source_cutoff_at=None,
        reconstruction_method=request.reconstruction_method,
        source_quality_flags=("reconstructed_history", reason),
        production_training_allowed=False,
    )


def _is_trustworthy(run: UploadRun, trusted_run_ids: tuple[int, ...]) -> bool:
    if run.id in trusted_run_ids:
        return True
    notes = (run.notes or "").casefold()
    return any(marker in notes for marker in TRUST_MARKERS)


def _merge_counts(counts: dict[str, int], result: WinnerPredictionCaptureResult) -> None:
    for key, value in result.as_dict().items():
        counts[key] = counts.get(key, 0) + int(value)


def _check(checks: tuple[RolloutReadinessCheck, ...], name: str) -> bool:
    return next(check.passed for check in checks if check.name == name)
