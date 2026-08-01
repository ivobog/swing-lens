from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from app.models.tables import (
    EstimateKind,
    EstimateSource,
    WinnerCohortDefinition,
    WinnerCohortStatistic,
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifest,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_service import EvidenceOutcome
from app.services.winner_probability.probability_estimator import ProbabilityEstimator


def test_estimator_backs_off_to_broader_eligible_cohort_and_persists_membership() -> None:
    config = load_winner_probability_config()
    db = EstimatorFakeDb()
    prediction = _prediction(999, setup_family="Breakout", score_band="8_plus")
    outcome_definition = _definition()
    evidence = tuple(_evidence(index, won=index % 2 == 0) for index in range(20))
    estimator = ProbabilityEstimator(evidence_service=FakeEvidenceService({"L5": evidence}))

    result = estimator.create_decision_time_estimate(
        db,
        prediction=prediction,
        outcome_definition=outcome_definition,
        config=config,
    )

    assert result.status == "estimated"
    assert result.selected_cohort.level == "L5"
    assert result.estimate.source == EstimateSource.COHORT
    assert result.estimate.estimate_kind == EstimateKind.DECISION_TIME
    assert result.estimate.training_cutoff_at == prediction.source_data_cutoff_at
    assert result.estimate.sample_n == 20
    assert result.estimate.point_probability == Decimal("0.500000")
    assert len(db.rows[WinnerEstimateEvidenceMember]) == 20
    assert (
        db.rows[WinnerEvidenceManifest][0].manifest_hash
        == result.estimate.evidence_manifest_hash
    )
    assert (
        db.rows[WinnerCohortStatistic][0].evidence_manifest_hash
        == result.estimate.evidence_manifest_hash
    )


def test_insufficient_estimate_persists_raw_counts_without_fake_probability() -> None:
    config = load_winner_probability_config()
    db = EstimatorFakeDb()
    prediction = _prediction(999)
    outcome_definition = _definition()
    evidence = tuple(_evidence(index, won=True) for index in range(2))
    estimator = ProbabilityEstimator(evidence_service=FakeEvidenceService({"L5": evidence}))

    result = estimator.create_decision_time_estimate(
        db,
        prediction=prediction,
        outcome_definition=outcome_definition,
        config=config,
    )

    assert result.status == "insufficient"
    assert result.estimate.source == EstimateSource.INSUFFICIENT
    assert result.estimate.point_probability is None
    assert result.estimate.sample_n == 2
    assert result.estimate.insufficient_reasons_json == ["no_eligible_cohort"]


def test_latest_rescore_is_stored_separately_from_decision_time() -> None:
    config = load_winner_probability_config()
    db = EstimatorFakeDb()
    prediction = _prediction(999)
    outcome_definition = _definition()
    evidence = tuple(_evidence(index, won=index % 2 == 0) for index in range(20))
    estimator = ProbabilityEstimator(evidence_service=FakeEvidenceService({"L5": evidence}))

    decision_time = estimator.create_decision_time_estimate(
        db,
        prediction=prediction,
        outcome_definition=outcome_definition,
        config=config,
    )
    latest = estimator.create_latest_rescore(
        db,
        prediction=prediction,
        outcome_definition=outcome_definition,
        as_of=prediction.source_data_cutoff_at + timedelta(days=30),
        config=config,
    )

    assert decision_time.estimate.id != latest.estimate.id
    assert decision_time.estimate.estimate_kind == EstimateKind.DECISION_TIME
    assert latest.estimate.estimate_kind == EstimateKind.LATEST_RESCORE


def test_decision_time_estimate_is_immutable_on_duplicate_call() -> None:
    config = load_winner_probability_config()
    db = EstimatorFakeDb()
    prediction = _prediction(999)
    outcome_definition = _definition()
    evidence = tuple(_evidence(index, won=index % 2 == 0) for index in range(20))
    estimator = ProbabilityEstimator(evidence_service=FakeEvidenceService({"L5": evidence}))

    first = estimator.create_decision_time_estimate(
        db,
        prediction=prediction,
        outcome_definition=outcome_definition,
        config=config,
    )
    second = estimator.create_decision_time_estimate(
        db,
        prediction=prediction,
        outcome_definition=outcome_definition,
        config=config,
    )

    assert second.status == "duplicate"
    assert second.estimate is first.estimate
    assert len(db.rows[WinnerProbabilityEstimate]) == 1


class FakeEvidenceService:
    def __init__(self, evidence_by_level: dict[str, tuple[EvidenceOutcome, ...]]) -> None:
        self.evidence_by_level = evidence_by_level

    def load_evidence(self, _db, **kwargs) -> tuple[EvidenceOutcome, ...]:
        return self.evidence_by_level.get(kwargs["cohort_key"].level, ())


class EstimatorFakeDb:
    def __init__(self) -> None:
        self.rows: dict[type, list] = {
            WinnerCohortDefinition: [],
            WinnerCohortStatistic: [],
            WinnerEvidenceManifest: [],
            WinnerEstimateEvidenceMember: [],
            WinnerProbabilityEstimate: [],
        }
        self._next_id = 1

    def scalar(self, statement):
        text = str(statement)
        if "winner_evidence_manifests" in text:
            return None
        return None

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows.setdefault(type(row), []).append(row)

    def flush(self) -> None:
        return None

    def get_existing_probability_estimate(self, **kwargs):
        return next(
            (
                estimate
                for estimate in self.rows[WinnerProbabilityEstimate]
                if estimate.prediction_id == kwargs["prediction_id"]
                and estimate.outcome_definition_id == kwargs["outcome_definition_id"]
                and estimate.estimate_kind == kwargs["estimate_kind"]
                and estimate.source_version == kwargs["source_version"]
                and estimate.training_cutoff_at == kwargs["training_cutoff_at"]
            ),
            None,
        )


def _prediction(
    id: int,
    *,
    setup_family: str = "Breakout",
    score_band: str = "8_plus",
) -> WinnerPredictionSnapshot:
    return WinnerPredictionSnapshot(
        id=id,
        run_id=id,
        ticker=f"T{id}",
        prediction_as_of_date=date(2026, 1, 1),
        source_data_cutoff_at=datetime(2026, 7, 1, 21, 0, tzinfo=UTC),
        entry_schedule_status="RESOLVED",
        entry_data_status="AVAILABLE",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash=f"hash-{id}",
        config_hash="config",
        calculation_version="calc",
        feature_json={
            "setup_family": setup_family,
            "dual_score_band": "8_plus",
            "score_band": score_band,
            "market_risk_state": "Green",
            "sector_state": "Leading",
            "ranking_profile": "momentum_swing",
            "sector_leadership_bucket": "leader",
            "market_regime_family": "Confirmed Uptrend",
        },
    )


def _definition() -> WinnerOutcomeDefinition:
    return WinnerOutcomeDefinition(
        id=1,
        definition_id="T2_5_S2_0_H5_NEXT_OPEN",
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        target_pct=2.5,
        stop_pct=2.0,
        calculation_version="owpe-calc-1.0.0",
        config_hash="config",
        is_primary=True,
        is_active=True,
    )


def _evidence(index: int, *, won: bool) -> EvidenceOutcome:
    prediction = _prediction(index)
    prediction.source_data_cutoff_at = datetime(2026, 1, 1, 21, 0, tzinfo=UTC)
    forward = WinnerForwardOutcome(
        id=index + 1000,
        prediction_id=prediction.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        close_return_pct=Decimal("2.0") if won else Decimal("-1.0"),
        mfe_pct=Decimal("3.0"),
        mae_pct=Decimal("-1.0"),
        matured_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    target = WinnerTargetStopOutcome(
        id=index + 2000,
        prediction_id=prediction.id,
        outcome_definition_id=1,
        forward_outcome_id=forward.id,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="MATURED",
        revision=1,
        is_current_revision=True,
        target_pct=Decimal("2.5"),
        stop_pct=Decimal("2.0"),
        first_event="TARGET_FIRST" if won else "STOP_FIRST",
        primary_winner=won,
        evaluated_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    return EvidenceOutcome(
        prediction=prediction,
        forward_outcome=forward,
        target_stop_outcome=target,
    )
