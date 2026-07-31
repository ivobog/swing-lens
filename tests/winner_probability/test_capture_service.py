from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from _phase3_helpers import FakeWinnerRepository, build_run_context

from app.models.tables import EntryDataStatus, PredictionEligibility, WinnerProbabilityEstimate
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config


def test_completed_run_creates_snapshot_pending_outcomes_and_decision_estimate() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context())
    service = _capture_service(repository)

    result = service.capture_run(
        object(),
        run_id=7,
        config=config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
    )

    assert result.inserted == 1
    assert result.pending_outcomes == 10
    assert result.target_stop_outcomes == 2
    assert result.decision_time_estimates == 1
    assert result.insufficient_estimates == 1
    prediction = repository.predictions[0]
    assert prediction.eligibility_status == PredictionEligibility.ELIGIBLE
    assert prediction.ticker == "MSFT"
    assert prediction.feature_json["combined_score"] == "8.5"
    assert len(prediction.feature_vector_hash) == 64
    assert repository.estimates[0].insufficient_reasons_json == ["no_eligible_cohort"]


def test_repeated_capture_returns_duplicate_without_new_children() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context())
    service = _capture_service(repository)

    service.capture_run(object(), run_id=7, config=config)
    second = service.capture_run(object(), run_id=7, config=config)

    assert second.duplicate == 1
    assert second.pending_outcomes == 0
    assert second.target_stop_outcomes == 0
    assert second.decision_time_estimates == 0
    assert len(repository.predictions) == 1
    assert len(repository.forward_outcomes) == 10
    assert len(repository.target_stop_outcomes) == 2
    assert len(repository.estimates) == 1


def test_historical_source_mutation_does_not_change_existing_snapshot_hash() -> None:
    config = load_winner_probability_config()
    context = build_run_context()
    repository = FakeWinnerRepository(context)
    service = _capture_service(repository)

    service.capture_run(object(), run_id=7, config=config)
    original_hash = repository.predictions[0].feature_vector_hash
    context.tickers[0].combined_result.final_score = Decimal("9.9")
    second = service.capture_run(object(), run_id=7, config=config)

    assert second.failed == 1
    assert repository.predictions[0].feature_vector_hash == original_hash
    assert repository.predictions[0].feature_json["combined_score"] == "8.5"


def test_missing_optional_regime_and_sector_context_is_captured_as_warnings() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context(include_market=False, include_sector=False))
    service = _capture_service(repository)

    result = service.capture_run(object(), run_id=7, config=config)

    assert result.inserted == 1
    assert result.warnings == 2
    prediction = repository.predictions[0]
    assert prediction.market_regime is None
    assert prediction.sector_state is None
    assert "missing_market_regime_snapshot" in prediction.warning_flags_json
    assert "missing_sector_rotation_context" in prediction.warning_flags_json


def test_future_next_open_entry_is_not_a_capture_exclusion() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context())
    service = _capture_service(repository)

    result = service.capture_run(
        object(),
        run_id=7,
        config=config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.planned_entry_session.isoformat() == "2026-08-03"
    assert prediction.entry_data_status == EntryDataStatus.NOT_DUE
    assert prediction.exclusion_reason is None


def test_future_dated_source_context_fails_without_snapshot() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context(as_of_date=datetime(2026, 8, 3).date()))
    service = _capture_service(repository)

    result = service.capture_run(
        object(),
        run_id=7,
        config=config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
    )

    assert result.failed == 1
    assert repository.predictions == []


def _capture_service(repository: FakeWinnerRepository) -> WinnerPredictionCaptureService:
    return WinnerPredictionCaptureService(
        repository=repository,
        decision_time_estimate_service=FakeDecisionTimeEstimateService(repository),
    )


class FakeDecisionTimeEstimateService:
    def __init__(self, repository: FakeWinnerRepository) -> None:
        self.repository = repository

    def create_decision_time_estimate(self, db, *, prediction, outcome_definition, config):
        existing = self.repository.get_decision_time_estimate(
            db,
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            source_version="cohort_baseline_v1",
            training_cutoff_at=prediction.source_data_cutoff_at,
        )
        if existing is not None:
            return type("Result", (), {"estimate": existing, "status": "duplicate"})()
        estimate = WinnerProbabilityEstimate(
            prediction_id=prediction.id,
            outcome_definition_id=outcome_definition.id,
            estimate_kind="DECISION_TIME",
            source="INSUFFICIENT",
            source_version="cohort_baseline_v1",
            training_cutoff_at=prediction.source_data_cutoff_at,
            evidence_grade="Insufficient",
            insufficient_reasons_json=["no_eligible_cohort"],
            config_hash=config.config_hash,
            feature_schema_version=config.feature_schema.version,
        )
        self.repository.add(db, estimate)
        return type("Result", (), {"estimate": estimate, "status": "insufficient"})()
