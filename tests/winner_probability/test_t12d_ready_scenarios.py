from dataclasses import replace

import pytest
from _phase3_helpers import FakeWinnerRepository
from readiness_capture_helpers import ready_identity_context
from test_probability_estimator import EstimatorFakeDb, FakeEvidenceService, _definition, _evidence
from test_winner_calculation_identity_adoption import _service, _session

from app.services.winner_probability.probability_estimator import ProbabilityEstimator


@pytest.mark.parametrize(
    "mode", ["canonical", "cross_run", "no_regime", "no_sector", "no_context", "historical"]
)
def test_exact_ready_winner_vector_pins_and_rated_probability(mode):
    context, cutoff = ready_identity_context(newer_incompatible_regime=mode == "cross_run")
    if mode in {"no_regime", "no_context"}:
        context = replace(context, market_regime_snapshot=None, market_regime_candidates=())
    if mode in {"no_sector", "no_context"}:
        context = replace(
            context,
            sector_rotation_snapshot=None,
            sector_rotation_candidates=(),
            tickers=(replace(context.tickers[0], sector_row=None),),
        )
    repository = FakeWinnerRepository(context)
    result = _service(repository).capture_run(
        _session(),
        run_id=7,
        market_cutoff=cutoff,
        reconstruction_method="HISTORICAL_AS_OF_REPLAY" if mode == "historical" else None,
    )
    assert result.inserted == 1
    prediction = repository.predictions[0]
    estimator = ProbabilityEstimator(
        evidence_service=FakeEvidenceService(
            {
                "L5": tuple(_evidence(index, won=index % 2 == 0) for index in range(20)),
            }
        )
    )
    estimate = estimator.create_decision_time_estimate(
        EstimatorFakeDb(),
        prediction=prediction,
        outcome_definition=_definition(),
    )
    assert estimate.status == "estimated"
    assert estimate.estimate.point_probability == 0.5
    assert (
        prediction.source_ids_json["technical_evidence_id"]
        == context.tickers[0].technical_score.evidence_id
    )
