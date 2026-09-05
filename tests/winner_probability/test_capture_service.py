from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from _phase3_helpers import FakeWinnerRepository, build_run_context

from app.models.tables import EntryDataStatus, PredictionEligibility, WinnerProbabilityEstimate
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.feature_extractor import WinnerFeatureExtractor


def test_completed_run_creates_snapshot_pending_outcomes_and_decision_estimate() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context())
    service = _capture_service(repository)

    result = service.capture_run(
        object(),
        run_id=7,
        config=config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
        decision_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
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
    assert prediction.lineage_json["feature_cutoff_audit_hash"]
    assert prediction.lineage_json["feature_cutoff_audit"]["combined_score"] == {
        "status": "available",
        "source_available_at": "2026-07-31T15:00:00+00:00",
    }
    assert prediction.decision_at == datetime(2026, 7, 31, 21, 30, tzinfo=UTC)
    assert prediction.captured_at == datetime(2026, 7, 31, 21, 30, tzinfo=UTC)
    assert prediction.lineage_json["point_in_time_validation"] == {
        "source_cutoff": "VALID",
        "entry_timing": "VALID",
        "semantic_input_time": "VALID",
    }
    assert len(repository.temporal_decisions) == 1
    assert repository.temporal_decisions[0].evidence_eligible is True
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


def test_historical_source_mutation_does_not_change_existing_snapshot_hash(caplog) -> None:
    config = load_winner_probability_config()
    context = build_run_context()
    repository = FakeWinnerRepository(context)
    service = _capture_service(repository)

    service.capture_run(object(), run_id=7, config=config)
    original_hash = repository.predictions[0].feature_vector_hash
    context.tickers[0].combined_result.final_score = Decimal("9.9")
    second = service.capture_run(object(), run_id=7, config=config)

    assert second.failed == 1
    assert "winner_prediction.capture_failed" in caplog.text
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
        decision_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.planned_entry_session.isoformat() == "2026-08-03"
    assert prediction.entry_data_status == EntryDataStatus.NOT_DUE
    assert prediction.exclusion_reason is None


def test_run_104_cutoff_uses_completed_us_session_not_zurich_calendar_date() -> None:
    config = load_winner_probability_config()
    context = build_run_context(as_of_date=datetime(2026, 8, 14).date())
    cutoff = datetime(2026, 8, 14, 4, 34, 21, tzinfo=ZoneInfo("Europe/Zurich"))
    context.upload_run.uploaded_at = cutoff
    context.upload_run.processed_at = cutoff
    ticker = context.tickers[0]
    for row in (
        ticker.raw_row,
        ticker.fundamental_score,
        ticker.technical_score,
        ticker.combined_result,
        *ticker.ranking_results,
        context.market_regime_snapshot,
        context.sector_rotation_snapshot,
    ):
        row.created_at = cutoff
    repository = FakeWinnerRepository(context)

    result = _capture_service(repository).capture_run(
        object(), run_id=7, config=config, captured_at=cutoff, decision_at=cutoff
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.prediction_as_of_date.isoformat() == "2026-08-13"
    assert prediction.planned_entry_session.isoformat() == "2026-08-14"
    assert prediction.entry_data_status == EntryDataStatus.NOT_DUE
    assert "sector_rotation_date_after_completed_signal_session" in prediction.warning_flags_json


def test_run_120_128_intraday_decision_cannot_use_already_passed_open() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context(as_of_date=datetime(2026, 8, 19).date()))
    decision = datetime(2026, 8, 20, 11, 28, tzinfo=ZoneInfo("America/New_York"))

    result = _capture_service(repository).capture_run(
        object(),
        run_id=7,
        config=config,
        decision_at=decision,
        captured_at=decision.astimezone(UTC),
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.prediction_as_of_date.isoformat() == "2026-08-19"
    assert prediction.planned_entry_session.isoformat() == "2026-08-21"
    assert prediction.decision_at == decision


def test_finalization_rebinds_entry_when_feature_extraction_crosses_market_open() -> None:
    config = load_winner_probability_config()
    context = build_run_context(as_of_date=datetime(2026, 8, 19).date())
    extractor = WinnerFeatureExtractor()
    before_open = datetime(2026, 8, 20, 9, 29, 59, tzinfo=ZoneInfo("America/New_York"))
    after_open = datetime(2026, 8, 20, 9, 30, tzinfo=ZoneInfo("America/New_York"))

    provisional = extractor.extract(
        context,
        context.tickers[0],
        config,
        decision_at=before_open,
    )
    finalized = extractor.finalize_decision_timing(provisional, decision_at=after_open)

    assert provisional.planned_entry_session == date(2026, 8, 20)
    assert finalized.planned_entry_session == date(2026, 8, 21)
    assert finalized.lineage_json["technical_point_in_time_boundary"]["feature_as_of_at"]
    assert finalized.lineage_json["technical_point_in_time_boundary"]["decision_at"].startswith(
        "2026-08-20T09:30:00"
    )


def test_calculation_1_0_stale_source_anchor_cannot_force_august_3_open() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context(as_of_date=datetime(2026, 7, 31).date()))
    decision = datetime(2026, 8, 3, 20, 0, tzinfo=ZoneInfo("America/New_York"))

    result = _capture_service(repository).capture_run(
        object(),
        run_id=7,
        config=config,
        decision_at=decision,
        captured_at=decision.astimezone(UTC),
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.planned_entry_session.isoformat() == "2026-08-04"
    assert prediction.planned_entry_session.isoformat() != "2026-08-03"


def test_future_dated_optional_context_is_nulled_and_warned_without_snapshot() -> None:
    config = load_winner_probability_config()
    repository = FakeWinnerRepository(build_run_context(as_of_date=datetime(2026, 8, 3).date()))
    service = _capture_service(repository)

    result = service.capture_run(
        object(),
        run_id=7,
        config=config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
        decision_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
    )

    assert result.inserted == 1
    prediction = repository.predictions[0]
    assert prediction.market_regime is None
    assert prediction.sector_state is None
    assert "future_market_regime_snapshot_omitted" in prediction.warning_flags_json
    assert "future_sector_rotation_context_omitted" in prediction.warning_flags_json
    assert prediction.lineage_json["feature_cutoff_audit"]["market_regime"]["status"] == "missing"


def test_future_dated_required_feature_source_fails_capture() -> None:
    config = load_winner_probability_config()
    context = build_run_context()
    context.tickers[0].combined_result.created_at = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)
    repository = FakeWinnerRepository(context)
    service = _capture_service(repository)

    result = service.capture_run(
        object(),
        run_id=7,
        config=config,
        captured_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
        decision_at=datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
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
