"""Prepared native evidence reaches estimation without accepting injected financial readers."""

from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.orm import Session

from app.models.tables import WinnerPredictionSnapshot
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.decision_time_estimate_service import (
    DecisionTimeEstimateService,
)
from app.services.winner_probability.evidence_service import RunEvidenceCandidateUniverse
from app.services.winner_probability.probability_estimator import ProbabilityEstimator


def test_native_estimation_reuses_prepared_candidates_and_clears_them(monkeypatch):
    class ForbiddenInjectedService:
        def __getattr__(self, name):
            raise AssertionError("native financial selection used injected service: " + name)

    injected = ForbiddenInjectedService()
    estimator = ProbabilityEstimator(evidence_service=injected)
    service = DecisionTimeEstimateService(probability_estimator=estimator)
    config = load_winner_probability_config()
    definition = SimpleNamespace(
        id=7, is_active=True, definition_id=config.primary_outcome_definition.id
    )
    cutoff = datetime(2026, 7, 1, 20, tzinfo=UTC)
    prediction = WinnerPredictionSnapshot(
        id=101,
        ticker="NATIVE",
        prediction_as_of_date=date(2026, 6, 30),
        source_data_cutoff_at=cutoff,
        feature_schema_version=config.feature_schema.version,
        calculation_version=config.engine.calculation_version,
        config_hash=config.config_hash,
        feature_json={},
    )
    native = estimator._native_evidence_service

    def prepare(*args, **kwargs):
        native._run_candidate_universe = RunEvidenceCandidateUniverse(
            outcome_definition_id=7,
            config_hash=config.config_hash,
            feature_schema_version=config.feature_schema.version,
            calculation_version=config.engine.calculation_version,
            native_rows=(),
            compatibility_candidates=(),
            temporal_decisions={},
            lineage_price_bars={},
            lineage_price_bar_revisions={},
            load_seconds=0,
        )
        return native._run_candidate_universe

    def reread(*args, **kwargs):
        raise AssertionError("estimation reread the native candidate corpus")

    monkeypatch.setattr(native, "prepare_run_candidate_universe", prepare)
    monkeypatch.setattr(estimator, "_materialize_cohort_statistic", lambda *a, **k: None)
    with Session() as db:
        monkeypatch.setattr(db, "execute", reread)
        monkeypatch.setattr(db, "scalar", reread)
        service.prepare_capture_run(db, outcome_definition=definition, config=config)
        assert estimator.evidence_service_for(
            db, outcome_definition=SimpleNamespace(id=8), config=config
        ) is not native
        assert (
            estimator._select_cohort(
                db,
                prediction=prediction,
                outcome_definition=definition,
                training_cutoff_at=cutoff,
                config=config,
            )
            is None
        )
        metrics = service.capture_run_evidence_metrics()
        assert metrics["cache_reuse_count"] == metrics["evidence_evaluation_count"] == 1
        service.clear_capture_run()
        assert service.capture_run_evidence_metrics() == {}
        assert estimator.evidence_service_for(
            db, outcome_definition=definition, config=config
        ) is not estimator.evidence_service_for(
            db, outcome_definition=definition, config=config
        )
        with pytest.raises(AssertionError, match="reread the native candidate corpus"):
            estimator._select_cohort(
                db,
                prediction=prediction,
                outcome_definition=definition,
                training_cutoff_at=cutoff,
                config=config,
            )
