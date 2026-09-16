from copy import deepcopy
from dataclasses import replace

import pytest
from _phase3_helpers import FakeWinnerRepository
from readiness_capture_helpers import ready_identity_context, reseal_context
from test_winner_calculation_identity_adoption import _service, _session

from app.services.core_calculation_evidence import EvidenceUnavailableError
from app.services.producer_readiness import (
    ConsumerEligibilityStatus,
    NativeReadinessMetrics,
    ProducerReadinessEnvelope,
    ReadinessReason,
    ReadinessStatus,
)
from app.services.winner_probability.calculation_identity import acquire_winner_sources
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.consumer_eligibility import (
    RANKING_TO_WINNER,
    REGIME_TO_WINNER,
    SECTOR_TO_WINNER,
    TECHNICAL_TO_WINNER,
    WINNER_ELIGIBILITY_KEY,
    WinnerSourceEligibilityError,
)

POLICIES = (TECHNICAL_TO_WINNER, RANKING_TO_WINNER, REGIME_TO_WINNER, SECTOR_TO_WINNER)


@pytest.mark.parametrize("member,value", [("ticker", "OTHER"), ("ranking_profile", "OTHER")])
def test_exact_ranking_evidence_ticker_and_profile_must_match(member, value):
    context, cutoff = ready_identity_context()
    ranking = context.tickers[0].ranking_results[0]
    setattr(ranking.calculation_evidence, member, value)
    with pytest.raises(EvidenceUnavailableError, match="Ranking ticker/profile mismatch"):
        _acquire(context, cutoff)


@pytest.mark.parametrize("policy", POLICIES, ids=lambda item: item.policy_version)
@pytest.mark.parametrize("status", list(ReadinessStatus))
def test_winner_policy_matrix_and_retry_are_typed(policy, status):
    producer = getattr(policy, "producer", "TECHNICAL")
    envelope = ProducerReadinessEnvelope(producer, status, "source-identity", evidence_id=12)
    decision = policy.evaluate(envelope)
    expected = (
        ConsumerEligibilityStatus.ELIGIBLE
        if status is ReadinessStatus.READY
        else ConsumerEligibilityStatus.INELIGIBLE
        if status
        in {
            ReadinessStatus.ERROR,
            ReadinessStatus.STALE,
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
        }
        else ConsumerEligibilityStatus.POLICY_UNDECIDED
    )
    assert decision.status is expected
    assert decision.consumer == "WINNER" and decision.policy_version == policy.policy_version
    assert decision == policy.evaluate(envelope)


@pytest.mark.parametrize("policy", POLICIES)
def test_winner_blocking_reason_prevents_permission_and_overrides_are_rejected(policy):
    producer = getattr(policy, "producer", "TECHNICAL")
    envelope = ProducerReadinessEnvelope(
        producer,
        ReadinessStatus.UNKNOWN,
        "identity",
        blocking_reasons=(ReadinessReason.INSUFFICIENT_NATIVE_CONFIDENCE,),
    )
    assert policy.evaluate(envelope).status is ConsumerEligibilityStatus.INELIGIBLE
    with pytest.raises(ValueError):
        policy.evaluate(envelope, consumer="SETUP")
    with pytest.raises(ValueError):
        policy.evaluate(envelope, config=NativeReadinessMetrics.freeze({"permit_degraded": True}))


def _acquire(context, cutoff):
    return acquire_winner_sources(
        context,
        context.tickers[0],
        run_id=7,
        market_cutoff=cutoff,
        winner_config=load_winner_probability_config(),
    )


def _row(context, source):
    ticker = context.tickers[0]
    return {
        "technical": ticker.technical_score,
        "ranking": ticker.ranking_results[0],
        "regime": context.market_regime_snapshot,
        "sector": context.sector_rotation_snapshot,
    }[source]


def _block(context, cutoff, source, condition):
    row = _row(context, source)
    if source == "technical":
        row.technical_confidence = {"degraded": "low", "error": "error", "unknown": "ok"}.get(
            condition,
            "normal",
        )
        row.insufficient_data = condition == "blocking"
    elif source == "ranking":
        row.is_complete = condition != "blocking"
    elif source == "regime":
        row.confidence = "low" if condition == "degraded" else "normal"
        row.warnings_json = ["severely_stale_market_data"] if condition == "blocking" else []
    elif source == "sector":
        context.tickers[0].sector_row.confidence = "insufficient"
    reseal_context(context, cutoff, source)
    if condition == "legacy":
        evidence = row.calculation_evidence
        evidence.payload_json = {
            key: value
            for key, value in evidence.payload_json.items()
            if key != "producer_readiness"
        }
    elif condition in {"unknown", "error", "degraded"} and source != "technical":
        # Separate stored contract states for producers that do not natively emit
        # all seven states; no new producer threshold/mapping is introduced.
        evidence = row.calculation_evidence
        payload = deepcopy(evidence.payload_json)
        payload["producer_readiness"]["status"] = {
            "unknown": "UNKNOWN",
            "error": "ERROR",
            "degraded": "DEGRADED",
        }[condition]
        payload["producer_readiness"]["blocking_reasons"] = []
        evidence.payload_json = payload


@pytest.mark.parametrize("source", ["technical", "ranking", "regime", "sector"])
@pytest.mark.parametrize("condition", ["blocking", "degraded", "unknown", "legacy", "error"])
def test_same_values_different_readiness_control_vector_and_capture(source, condition):
    context, cutoff = ready_identity_context()
    ready = _acquire(context, cutoff)
    original_value = (
        ready.ticker_context.technical_score.dual_score,
        ready.ticker_context.ranking_results[0].profile_score,
        ready.run_context.market_regime_snapshot.regime,
        ready.ticker_context.sector_row.current_rank,
    )
    _block(context, cutoff, source, condition)
    selected = _row(context, source)
    if source in {"technical", "ranking"}:
        with pytest.raises(WinnerSourceEligibilityError) as caught:
            _acquire(context, cutoff)
        permission = caught.value.decisions.canonical_payload()[source]
        assert caught.value.sources == (source,)
        repository = FakeWinnerRepository(context)
        result = _service(repository).capture_run(_session(), run_id=7, market_cutoff=cutoff)
        assert result.excluded == 1 and result.failed == 0
        assert repository.predictions == [] and repository.episodes == []
        assert repository.estimates == [] and repository.forward_outcomes == []
        assert result.readiness_rejections[0][WINNER_ELIGIBILITY_KEY][source] == permission
    else:
        acquired = _acquire(context, cutoff)
        permission = acquired.consumer_eligibility.canonical_payload()[source]
        repository = FakeWinnerRepository(context)
        result = _service(repository).capture_run(_session(), run_id=7, market_cutoff=cutoff)
        assert result.inserted == 1
        prediction = repository.predictions[0]
        missing_keys = (
            ("market_regime", "market_regime_family", "market_risk_state")
            if source == "regime"
            else ("sector_state", "sector_rank", "sector_leadership_bucket")
        )
        assert all(prediction.feature_json[key] is None for key in missing_keys)
        assert prediction.lineage_json[WINNER_ELIGIBILITY_KEY][source] == permission
    assert permission["decision"]["status"] != "ELIGIBLE"
    assert not permission["included"]
    assert permission["source_id"] == selected.id
    assert permission["decision"]["producer_evidence_id"] == selected.evidence_id
    assert (
        context.tickers[0].technical_score.dual_score,
        context.tickers[0].ranking_results[0].profile_score,
        context.market_regime_snapshot.regime,
        context.tickers[0].sector_row.current_rank,
    ) == original_value


@pytest.mark.parametrize(
    "sources", [("regime", "sector"), ("ranking", "sector"), ("technical", "ranking")]
)
def test_multiple_source_decisions_remain_independent_and_no_partial_vector(sources):
    context, cutoff = ready_identity_context()
    for source in sources:
        _block(context, cutoff, source, "blocking")
    if "technical" in sources or "ranking" in sources:
        with pytest.raises(WinnerSourceEligibilityError) as caught:
            _acquire(context, cutoff)
        decisions = caught.value.decisions.canonical_payload()
    else:
        acquired = _acquire(context, cutoff)
        decisions = acquired.consumer_eligibility.canonical_payload()
        assert acquired.run_context.market_regime_snapshot is None
        assert acquired.ticker_context.sector_row is None
    assert all(decisions[source]["included"] is (source not in sources) for source in decisions)


def test_rank_zero_no_new_entry_does_not_self_authorize():
    context, cutoff = ready_identity_context()
    ranking = context.tickers[0].ranking_results[0]
    ranking.profile_rank, ranking.decision_label = 0, "No new entry"
    _block(context, cutoff, "ranking", "blocking")
    with pytest.raises(WinnerSourceEligibilityError) as caught:
        _acquire(context, cutoff)
    assert caught.value.sources == ("ranking",)


def test_frozen_values_win_over_mutable_current_numerics():
    context, cutoff = ready_identity_context()
    # Direct policy projections are tested after identity membership. Mutating
    # semantic rows before acquisition must fail the handoff instead of being used.
    acquired = _acquire(context, cutoff)
    original = acquired.ticker_context.technical_score.dual_score
    context.tickers[0].technical_score.dual_score = 0
    assert acquired.ticker_context.technical_score.dual_score == original
    repository = FakeWinnerRepository(context)
    result = _service(repository).capture_run(_session(), run_id=7, market_cutoff=cutoff)
    assert result.failed == 1 and repository.predictions == []


def test_source_clock_audit_and_ready_retry_preserve_full_vector():
    context, cutoff = ready_identity_context()
    repository = FakeWinnerRepository(context)
    service = _service(repository)
    assert service.capture_run(_session(), run_id=7, market_cutoff=cutoff).inserted == 1
    prediction = repository.predictions[0]
    original = deepcopy(prediction.feature_json), prediction.feature_vector_hash
    assert service.capture_run(_session(), run_id=7, market_cutoff=cutoff).duplicate == 1
    assert (prediction.feature_json, prediction.feature_vector_hash) == original
    assert (
        prediction.lineage_json["feature_cutoff_audit"]["technical_score"]["status"] == "available"
    )


def test_no_setup_prefixed_vector_acquisition_or_required_feature_fake_zero():
    context, cutoff = ready_identity_context()
    ticker = replace(
        context.tickers[0], setup_lifecycle_features={"setup_lifecycle_state": "READY"}
    )
    with pytest.raises(ValueError, match="does not accept"):
        acquire_winner_sources(
            context,
            ticker,
            run_id=7,
            market_cutoff=cutoff,
            winner_config=load_winner_probability_config(),
        )
    context.tickers[0].technical_score.dual_score = None
    reseal_context(context, cutoff, "technical")
    repository = FakeWinnerRepository(context)
    result = _service(repository).capture_run(_session(), run_id=7, market_cutoff=cutoff)
    assert result.failed == 1 and repository.predictions == []
