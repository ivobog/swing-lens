from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from _phase3_helpers import FakeWinnerRepository
from readiness_capture_helpers import ready_identity_context
from test_consumer_readiness import _acquire, _block, _row
from test_winner_calculation_identity_adoption import _service, _session

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.contextual_consumer_eligibility import ContextualConsumerPolicy
from app.services.technical_consumer_eligibility import TechnicalConsumerPolicy
from app.services.winner_probability import consumer_eligibility as permission_module
from app.services.winner_probability.calculation_identity import semantic_artifact_identity
from app.services.winner_probability.consumer_eligibility import (
    WINNER_ELIGIBILITY_KEY,
    WinnerSourceEligibilityError,
)


@pytest.mark.parametrize("source", ["technical", "ranking", "regime", "sector"])
def test_exact_blocked_source_cannot_be_replaced_by_ready_current_candidate(source):
    context, cutoff = ready_identity_context()
    _block(context, cutoff, source, "blocking")
    exact_id = _row(context, source).evidence_id
    current, _ = ready_identity_context()
    newer = _row(current, source)
    newer.id += 1000
    if source == "regime":
        context = replace(
            context,
            market_regime_snapshot=newer,
            market_regime_candidates=(newer, context.market_regime_snapshot),
        )
    elif source == "sector":
        context = replace(
            context,
            sector_rotation_snapshot=newer,
            sector_rotation_candidates=(newer, context.sector_rotation_snapshot),
        )
    elif source == "ranking":
        newer.profile_rank = 2
        context = replace(
            context,
            tickers=(
                replace(
                    context.tickers[0],
                    ranking_results=(
                        *context.tickers[0].ranking_results,
                        newer,
                    ),
                ),
            ),
        )
        payload = deepcopy(context.decision_handoff_manifest.manifest_json)
        payload["artifact_lineage"]["MSFT"]["ranking_results"].append(
            semantic_artifact_identity(newer),
        )
        context.decision_handoff_manifest.manifest_json = payload
        context.decision_handoff_manifest.manifest_fingerprint = Canonical.fingerprint(payload)
    if source in {"technical", "ranking"}:
        with pytest.raises(WinnerSourceEligibilityError) as caught:
            _acquire(context, cutoff)
        decisions = caught.value.decisions.canonical_payload()
    else:
        acquired = _acquire(context, cutoff)
        decisions = acquired.consumer_eligibility.canonical_payload()
    assert decisions[source]["decision"]["producer_evidence_id"] == exact_id
    assert not decisions[source]["included"]
    assert newer.calculation_evidence.payload_json["producer_readiness"]["status"] == "READY"


def _captured():
    context, cutoff = ready_identity_context()
    repository = FakeWinnerRepository(context)
    service = _service(repository)
    assert service.capture_run(_session(), run_id=7, market_cutoff=cutoff).inserted == 1
    return context, cutoff, repository, service, repository.predictions[0]


def test_policy_version_changes_bind_new_acquisition_and_preserve_historical_capture(monkeypatch):
    context, cutoff, repository, service, prediction = _captured()
    original = deepcopy(prediction.feature_json), deepcopy(prediction.lineage_json)
    for name, policy in (
        (
            "FUNDAMENTAL_TO_WINNER",
            ContextualConsumerPolicy("FUNDAMENTAL", "WINNER", "fundamental-to-winner-v2-test"),
        ),
        (
            "COMBINED_TO_WINNER",
            ContextualConsumerPolicy("COMBINED", "WINNER", "combined-to-winner-v2-test"),
        ),
        ("TECHNICAL_TO_WINNER", TechnicalConsumerPolicy("WINNER", "technical-to-winner-v2-test")),
        (
            "RANKING_TO_WINNER",
            ContextualConsumerPolicy("RANKING", "WINNER", "ranking-to-winner-v2-test"),
        ),
        (
            "REGIME_TO_WINNER",
            ContextualConsumerPolicy("REGIME", "WINNER", "regime-to-winner-v2-test"),
        ),
        (
            "SECTOR_TO_WINNER",
            ContextualConsumerPolicy("SECTOR", "WINNER", "sector-to-winner-v2-test"),
        ),
    ):
        monkeypatch.setattr(permission_module, name, policy)
    prospective = _acquire(context, cutoff)
    assert all(
        item["decision"]["policy_version"].endswith("v2-test")
        for item in prospective.consumer_eligibility.canonical_payload().values()
    )
    retry = service.capture_run(_session(), run_id=7, market_cutoff=cutoff)
    assert retry.failed == 1  # Existing active identity cannot be prospectively rewritten.
    assert len(repository.predictions) == 1
    assert (prediction.feature_json, prediction.lineage_json) == original
    future_repository = FakeWinnerRepository(context)
    future = _service(future_repository).capture_run(_session(), run_id=7, market_cutoff=cutoff)
    assert future.inserted == 1
    assert all(
        item["decision"]["policy_version"].endswith("v2-test")
        for item in future_repository.predictions[0].lineage_json[WINNER_ELIGIBILITY_KEY].values()
    )
    assert future_repository.predictions[0].feature_json == prediction.feature_json


def _no_current_policy(monkeypatch):
    from app.services.winner_probability import calculation_identity, capture_service

    def forbidden(*args, **kwargs):
        raise AssertionError("Frozen operation reacquired current source permission")

    monkeypatch.setattr(calculation_identity, "acquire_winner_sources", forbidden)
    monkeypatch.setattr(capture_service, "acquire_winner_sources", forbidden)
    monkeypatch.setattr(TechnicalConsumerPolicy, "evaluate", forbidden)
    monkeypatch.setattr(ContextualConsumerPolicy, "evaluate", forbidden)


def test_actual_rescore_uses_captured_vector_after_sources_and_policies_change(monkeypatch):
    from test_probability_estimator import (
        EstimatorFakeDb,
        FakeEvidenceService,
        _definition,
        _evidence,
    )

    from app.services.winner_probability.probability_estimator import ProbabilityEstimator

    context, _, _, _, prediction = _captured()
    original = deepcopy(prediction.feature_json), deepcopy(prediction.lineage_json)
    context.tickers[0].technical_score.insufficient_data = True
    context.tickers[0].ranking_results[0].is_complete = False
    context.market_regime_snapshot.warnings_json = ["severely_stale_market_data"]
    context.tickers[0].sector_row.confidence = "insufficient"
    from contextual_readiness_helpers import seal_contextual
    from readiness_capture_helpers import seal_winner_source

    seal_winner_source(context.tickers[0].technical_score, "TECHNICAL")
    seal_winner_source(context.tickers[0].ranking_results[0], "RANKING")
    seal_contextual(context.market_regime_snapshot, evidence_id=400001)
    seal_contextual(
        context.sector_rotation_snapshot,
        rows=(context.tickers[0].sector_row,),
        evidence_id=400002,
    )
    _no_current_policy(monkeypatch)
    estimator = ProbabilityEstimator(
        evidence_service=FakeEvidenceService(
            {
                "L5": tuple(_evidence(index, won=index % 2 == 0) for index in range(20)),
            }
        )
    )
    result = estimator.create_latest_rescore(
        EstimatorFakeDb(),
        prediction=prediction,
        outcome_definition=_definition(),
        as_of=prediction.source_data_cutoff_at + timedelta(days=30),
    )
    assert result.status == "estimated" and result.estimate.point_probability is not None
    assert (prediction.feature_json, prediction.lineage_json) == original


def test_actual_maturation_uses_outcome_truth_after_current_readiness_changes(monkeypatch):
    from test_outcome_service import (
        FakeOutcomeDb,
        FakeOutcomeRepository,
        _bars,
        _forward,
        _target_stop,
    )

    from app.services.winner_probability.outcome_service import OutcomeMaturationService

    context, _, _, _, prediction = _captured()
    original = deepcopy(prediction.feature_json), deepcopy(prediction.lineage_json)
    context.tickers[0].technical_score.technical_confidence = "error"
    context.tickers[0].ranking_results[0].is_complete = False
    _no_current_policy(monkeypatch)
    forward, target = _forward(), _target_stop()
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[forward],
        target_stop_outcomes=[target],
        bars={
            "MSFT": _bars([100, 101, 102, 102, 103], highs=[101, 102, 103, 103, 104]),
            "SPY": _bars([200, 200, 201, 201, 202], ticker="SPY"),
            "XLK": _bars([50, 50, 50.5, 51, 51], ticker="XLK"),
        },
    )
    result = OutcomeMaturationService(repository=repository).process_due_outcomes(
        FakeOutcomeDb(repository),
        now=datetime(2026, 8, 10, 21, tzinfo=UTC),
    )
    assert result.matured == 1 and result.target_stop_matured == 1
    assert forward.close_return_pct == 3 and target.primary_winner is True
    assert (prediction.feature_json, prediction.lineage_json) == original
    assert prediction.lineage_json[WINNER_ELIGIBILITY_KEY]["technical"]["included"]
