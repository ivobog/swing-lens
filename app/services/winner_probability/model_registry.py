from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    LifecycleEventType,
    ModelStatus,
    WinnerDriftMetric,
    WinnerModelLifecycleEvent,
    WinnerModelVersion,
)
from app.services.winner_probability.config import (
    WinnerProbabilityConfig,
    load_winner_probability_config,
)
from app.services.winner_probability.model_artifact_service import (
    ModelArtifactService,
)


class ModelRegistryError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PromotionGateResult:
    allowed: bool
    reasons: tuple[str, ...]
    metrics: dict[str, Any]


class ModelRegistry:
    def __init__(self, *, artifact_service: ModelArtifactService | None = None) -> None:
        self.artifact_service = artifact_service or ModelArtifactService()

    def register_model(
        self,
        db: Session,
        *,
        model_key: str,
        algorithm: str,
        outcome_definition_id: int,
        entry_model: str,
        training_cutoff_at: datetime,
        artifact_hash: str,
        artifact_format: str,
        artifact_schema_version: str,
        feature_schema_version: str,
        calculation_version: str,
        config_hash: str,
        actor: str,
        reason: str,
        status: str = ModelStatus.SHADOW,
        training_window_start: date | None = None,
        hyperparameters: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        preprocessing: dict[str, Any] | None = None,
        calibration: dict[str, Any] | None = None,
        dependency_versions: dict[str, Any] | None = None,
    ) -> WinnerModelVersion:
        if status == ModelStatus.ACTIVE:
            raise ModelRegistryError(
                "MODEL_PROMOTION_BLOCKED",
                "New model versions must start in SHADOW or REJECTED.",
            )
        if status not in {ModelStatus.CANDIDATE, ModelStatus.SHADOW, ModelStatus.REJECTED}:
            raise ModelRegistryError("INVALID_MODEL_STATUS", f"Invalid initial status: {status}")
        model = WinnerModelVersion(
            model_key=model_key,
            algorithm=algorithm,
            status=status,
            outcome_definition_id=outcome_definition_id,
            entry_model=entry_model,
            feature_schema_version=feature_schema_version,
            calculation_version=calculation_version,
            config_hash=config_hash,
            training_window_start=training_window_start,
            training_cutoff_at=training_cutoff_at,
            hyperparameters_json=hyperparameters or {},
            metrics_json=metrics or {},
            preprocessing_json=preprocessing or {},
            calibration_json=calibration or {},
            artifact_schema_version=artifact_schema_version,
            artifact_format=artifact_format,
            artifact_hash=artifact_hash,
            dependency_versions_json=dependency_versions or {},
        )
        db.add(model)
        db.flush()
        self._record_event(
            db,
            model=model,
            event_type=LifecycleEventType.CREATED,
            actor=actor,
            reason=reason,
            old_status=None,
            new_status=status,
        )
        return model

    def evaluate_promotion(
        self,
        db: Session,
        *,
        model: WinnerModelVersion,
        config: WinnerProbabilityConfig | None = None,
        minimum_sample: int | None = None,
    ) -> PromotionGateResult:
        config = config or load_winner_probability_config()
        metrics = model.metrics_json or {}
        minimum_sample = minimum_sample or int(config.drift.thresholds["min_sample"])
        reasons: list[str] = []
        if model.status not in {ModelStatus.CANDIDATE, ModelStatus.SHADOW}:
            reasons.append("model_status_not_promotable")
        try:
            self.artifact_service.validate_model_version(model, config=config)
        except ValueError as exc:
            reasons.append(f"artifact_invalid:{exc}")
        if int(metrics.get("sample_n") or 0) < minimum_sample:
            reasons.append("minimum_sample_not_met")
        ece_threshold = float(config.drift.thresholds["ece_delta"])
        if metrics.get("ece") is None or float(metrics["ece"]) > ece_threshold:
            reasons.append("calibration_threshold_not_met")
        minimum_coverage = float(config.cohort.min_coverage)
        if metrics.get("coverage") is None or float(metrics["coverage"]) < minimum_coverage:
            reasons.append("coverage_threshold_not_met")
        if self._has_critical_drift(db, model.id):
            reasons.append("critical_drift_breach")
        return PromotionGateResult(
            allowed=not reasons,
            reasons=tuple(reasons),
            metrics={
                "minimum_sample": minimum_sample,
                "sample_n": metrics.get("sample_n"),
                "ece": metrics.get("ece"),
                "coverage": metrics.get("coverage"),
            },
        )

    def promote_model(
        self,
        db: Session,
        *,
        model_id: int,
        actor: str,
        reason: str,
        config: WinnerProbabilityConfig | None = None,
    ) -> WinnerModelVersion:
        model = self._require_model(db, model_id)
        gates = self.evaluate_promotion(db, model=model, config=config)
        if not gates.allowed:
            raise ModelRegistryError(
                "MODEL_PROMOTION_BLOCKED",
                ", ".join(gates.reasons),
            )
        old_status = model.status
        self._retire_active_replaced_models(db, model)
        model.status = ModelStatus.ACTIVE
        model.activated_at = _utcnow()
        self._record_event(
            db,
            model=model,
            event_type=LifecycleEventType.PROMOTED,
            actor=actor,
            reason=reason,
            old_status=old_status,
            new_status=ModelStatus.ACTIVE,
            metadata={"gates": gates.metrics},
        )
        db.flush()
        return model

    def retire_model(
        self,
        db: Session,
        *,
        model_id: int,
        actor: str,
        reason: str,
        replacement_model_version_id: int | None = None,
        allow_without_active_fallback: bool = False,
    ) -> tuple[WinnerModelVersion, WinnerModelLifecycleEvent]:
        model = self._require_model(db, model_id)
        old_status = model.status
        if old_status == ModelStatus.ACTIVE and not allow_without_active_fallback:
            active_count = self._active_count(db, model.outcome_definition_id)
            replacement_active = (
                replacement_model_version_id is not None
                and self._is_active(db, replacement_model_version_id)
            )
            if active_count <= 1 and not replacement_active:
                raise ModelRegistryError(
                    "MODEL_RETIREMENT_BLOCKED",
                    "Cannot retire the only active model without an active fallback.",
                )
        model.status = ModelStatus.RETIRED
        model.retired_at = _utcnow()
        event = self._record_event(
            db,
            model=model,
            event_type=LifecycleEventType.RETIRED,
            actor=actor,
            reason=reason,
            old_status=old_status,
            new_status=ModelStatus.RETIRED,
            replacement_model_version_id=replacement_model_version_id,
        )
        db.flush()
        return model, event

    def ensure_can_serve_latest_rescore(
        self,
        db: Session,
        *,
        model_version_id: int | None,
    ) -> None:
        if model_version_id is None:
            return
        model = self._require_model(db, model_version_id)
        if model.status == ModelStatus.RETIRED:
            raise ModelRegistryError(
                "MODEL_RETIRED",
                "A retired model cannot serve new latest re-scores.",
            )

    def _require_model(self, db: Session, model_id: int) -> WinnerModelVersion:
        model = db.get(WinnerModelVersion, model_id)
        if model is None:
            raise ModelRegistryError("MODEL_NOT_FOUND", f"Model {model_id} was not found.")
        return model

    def _has_critical_drift(self, db: Session, model_id: int) -> bool:
        return bool(
            db.scalar(
                select(WinnerDriftMetric.id)
                .where(WinnerDriftMetric.model_version_id == model_id)
                .where(WinnerDriftMetric.breached.is_(True))
                .where(WinnerDriftMetric.sufficient_sample.is_(True))
                .limit(1)
            )
        )

    def _active_count(self, db: Session, outcome_definition_id: int) -> int:
        return int(
            db.scalar(
                select(func.count(WinnerModelVersion.id))
                .where(WinnerModelVersion.outcome_definition_id == outcome_definition_id)
                .where(WinnerModelVersion.status == ModelStatus.ACTIVE)
            )
            or 0
        )

    def _is_active(self, db: Session, model_id: int) -> bool:
        return bool(
            db.scalar(
                select(WinnerModelVersion.id)
                .where(WinnerModelVersion.id == model_id)
                .where(WinnerModelVersion.status == ModelStatus.ACTIVE)
            )
        )

    def _retire_active_replaced_models(
        self,
        db: Session,
        replacement: WinnerModelVersion,
    ) -> None:
        active_models = list(
            db.scalars(
                select(WinnerModelVersion)
                .where(
                    WinnerModelVersion.outcome_definition_id
                    == replacement.outcome_definition_id
                )
                .where(WinnerModelVersion.status == ModelStatus.ACTIVE)
            )
        )
        for active_model in active_models:
            if active_model.id == replacement.id:
                continue
            old_status = active_model.status
            active_model.status = ModelStatus.RETIRED
            active_model.retired_at = _utcnow()
            self._record_event(
                db,
                model=active_model,
                event_type=LifecycleEventType.RETIRED,
                actor="system",
                reason=f"Replaced by model {replacement.id}.",
                old_status=old_status,
                new_status=ModelStatus.RETIRED,
                replacement_model_version_id=replacement.id,
            )

    def _record_event(
        self,
        db: Session,
        *,
        model: WinnerModelVersion,
        event_type: str,
        actor: str,
        reason: str,
        old_status: str | None,
        new_status: str | None,
        replacement_model_version_id: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> WinnerModelLifecycleEvent:
        event = WinnerModelLifecycleEvent(
            model_version_id=model.id,
            event_type=event_type,
            actor=actor,
            reason=reason.strip() or event_type,
            old_status=old_status,
            new_status=new_status,
            replacement_model_version_id=replacement_model_version_id,
            metadata_json=metadata or {},
        )
        db.add(event)
        db.flush()
        return event


def _utcnow() -> datetime:
    return datetime.now(UTC)
