from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models.tables import (
    ModelStatus,
    WinnerDriftMetric,
    WinnerModelLifecycleEvent,
    WinnerModelVersion,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.model_registry import (
    ModelRegistry,
    ModelRegistryError,
)
from app.services.winner_probability.probability_estimator import ProbabilityEstimator


def test_register_model_starts_in_shadow_and_records_lifecycle_event() -> None:
    db = RegistryFakeDb()

    model = ModelRegistry().register_model(
        db,
        model_key="candidate-1",
        algorithm="cohort",
        outcome_definition_id=1,
        entry_model="NEXT_OPEN",
        training_cutoff_at=_cutoff(),
        artifact_hash="hash",
        artifact_format="json",
        artifact_schema_version="winner-model-artifact-v1",
        feature_schema_version="owpe-features-1.0.0",
        calculation_version="owpe-calc-1.0.0",
        config_hash="config",
        actor="local",
        reason="registered",
        preprocessing={"feature_order": ["setup_family", "ranking_profile"]},
        calibration={"method": "isotonic", "version": "1"},
        dependency_versions={"python": "3.12"},
    )

    assert model.status == ModelStatus.SHADOW
    assert db.rows[WinnerModelLifecycleEvent][0].event_type == "CREATED"
    assert db.rows[WinnerModelLifecycleEvent][0].new_status == ModelStatus.SHADOW


def test_promotion_fails_closed_when_sample_calibration_or_drift_gate_fails() -> None:
    db = RegistryFakeDb()
    model = _model(status=ModelStatus.SHADOW)
    model.metrics_json = {"sample_n": 10, "ece": 0.08, "coverage": 0.9}
    db.add(model)
    drift = WinnerDriftMetric(
        id=7,
        model_version_id=model.id,
        outcome_definition_id=1,
        as_of_date=datetime(2026, 7, 31, tzinfo=UTC).date(),
        metric_name="ece_delta",
        threshold_value=0.04,
        metric_value=0.08,
        breached=True,
        sample_n=50,
        comparison_window="short",
        segment_json={},
        sufficient_sample=True,
    )
    db.add(drift)

    gates = ModelRegistry().evaluate_promotion(db, model=model)

    assert not gates.allowed
    assert "minimum_sample_not_met" in gates.reasons
    assert "calibration_threshold_not_met" in gates.reasons
    assert "critical_drift_breach" in gates.reasons

    with pytest.raises(ModelRegistryError, match="minimum_sample_not_met"):
        ModelRegistry().promote_model(db, model_id=model.id, actor="local", reason="promote")


def test_promotion_activates_candidate_and_retires_previous_active_model() -> None:
    db = RegistryFakeDb()
    active = _model(id=1, status=ModelStatus.ACTIVE, key="active")
    candidate = _model(id=2, status=ModelStatus.SHADOW, key="candidate")
    db.add(active)
    db.add(candidate)

    promoted = ModelRegistry().promote_model(
        db,
        model_id=candidate.id,
        actor="local",
        reason="passes gates",
    )

    assert promoted.status == ModelStatus.ACTIVE
    assert active.status == ModelStatus.RETIRED
    events = db.rows[WinnerModelLifecycleEvent]
    assert any(event.event_type == "PROMOTED" for event in events)
    assert any(event.replacement_model_version_id == candidate.id for event in events)


def test_retirement_is_auditable_and_blocks_only_active_model_without_fallback() -> None:
    db = RegistryFakeDb()
    active = _model(status=ModelStatus.ACTIVE)
    db.add(active)

    with pytest.raises(ModelRegistryError) as exc:
        ModelRegistry().retire_model(
            db,
            model_id=active.id,
            actor="local",
            reason="retire",
        )

    assert exc.value.code == "MODEL_RETIREMENT_BLOCKED"

    inactive = _model(id=2, status=ModelStatus.SHADOW, key="shadow")
    db.add(inactive)
    model, event = ModelRegistry().retire_model(
        db,
        model_id=inactive.id,
        actor="local",
        reason="not needed",
    )

    assert model.status == ModelStatus.RETIRED
    assert event.event_type == "RETIRED"
    assert event.old_status == ModelStatus.SHADOW


def test_failed_candidate_cannot_become_active() -> None:
    db = RegistryFakeDb()
    rejected = _model(status=ModelStatus.REJECTED)
    db.add(rejected)

    with pytest.raises(ModelRegistryError) as exc:
        ModelRegistry().promote_model(
            db,
            model_id=rejected.id,
            actor="local",
            reason="try anyway",
        )

    assert exc.value.code == "MODEL_PROMOTION_BLOCKED"
    assert "model_status_not_promotable" in str(exc.value)


def test_retired_model_cannot_serve_new_latest_rescore() -> None:
    db = RegistryFakeDb()
    retired = _model(status=ModelStatus.RETIRED)
    db.add(retired)

    with pytest.raises(ModelRegistryError, match="retired model"):
        ProbabilityEstimator().create_latest_rescore(
            db,
            prediction=object(),
            outcome_definition=object(),
            as_of=_cutoff(),
            model_version_id=retired.id,
        )


class RegistryFakeDb:
    def __init__(self) -> None:
        self.rows: dict[type, list] = {
            WinnerModelVersion: [],
            WinnerModelLifecycleEvent: [],
            WinnerDriftMetric: [],
        }
        self.flushes = 0
        self._next_id = 1

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows.setdefault(type(row), []).append(row)

    def flush(self) -> None:
        self.flushes += 1

    def get(self, model_type, id):
        return next((row for row in self.rows.get(model_type, []) if row.id == id), None)

    def scalar(self, statement):
        text = str(statement)
        if "winner_drift_metrics" in text:
            return next(
                (
                    row.id
                    for row in self.rows[WinnerDriftMetric]
                    if row.breached and row.sufficient_sample
                ),
                None,
            )
        if "count" in text and "winner_model_versions" in text:
            return sum(
                row.status == ModelStatus.ACTIVE
                for row in self.rows[WinnerModelVersion]
            )
        if "winner_model_versions" in text:
            return next(
                (
                    row.id
                    for row in self.rows[WinnerModelVersion]
                    if row.status == ModelStatus.ACTIVE
                ),
                None,
            )
        return None

    def scalars(self, statement):
        text = str(statement)
        if "winner_model_versions" in text:
            active_rows = [
                row
                for row in self.rows[WinnerModelVersion]
                if row.status == ModelStatus.ACTIVE
            ]
            return iter(active_rows)
        return iter(())


def _model(
    *,
    id: int = 1,
    key: str = "model",
    status: str,
) -> WinnerModelVersion:
    config = load_winner_probability_config()
    return WinnerModelVersion(
        id=id,
        model_key=key,
        algorithm="cohort",
        status=status,
        outcome_definition_id=1,
        entry_model="NEXT_OPEN",
        feature_schema_version=config.feature_schema.version,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        training_cutoff_at=_cutoff(),
        metrics_json={"sample_n": 80, "ece": 0.02, "coverage": 0.9},
        preprocessing_json={"feature_order": ["setup_family", "ranking_profile"]},
        calibration_json={"method": "isotonic", "version": "1"},
        artifact_schema_version="winner-model-artifact-v1",
        artifact_format="json",
        artifact_hash="hash",
        dependency_versions_json={"python": "3.12"},
    )


def _cutoff() -> datetime:
    return datetime(2026, 7, 31, 21, 0, tzinfo=UTC)
