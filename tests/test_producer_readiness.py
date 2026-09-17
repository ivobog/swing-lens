from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.producer_readiness import (
    ConsumerEligibilityStatus,
    NativeReadinessMetrics,
    ProducerReadinessEnvelope,
    ReadinessReason,
    ReadinessStatus,
    UndecidedConsumerEligibilityPolicy,
    legacy_readiness,
    normalize_producer_readiness,
    readiness_from_evidence,
)
from app.services.setup_lifecycle.enums import LifecycleState


def normalize(producer, payload, identity="a" * 64):
    return normalize_producer_readiness(producer, payload, identity_fingerprint=identity)


@pytest.mark.parametrize(
    "producer,payload,status",
    [
        ("FUNDAMENTAL", {"data_coverage_score": Decimal("9.5")}, ReadinessStatus.READY),
        (
            "TECHNICAL",
            {"dual_score": 7.5, "insufficient_data": True, "technical_confidence": "low"},
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
        ),
        (
            "TECHNICAL",
            {"insufficient_data": False, "technical_confidence": "high"},
            ReadinessStatus.READY,
        ),
        (
            "TECHNICAL",
            {"insufficient_data": False, "technical_confidence": "low"},
            ReadinessStatus.DEGRADED,
        ),
        (
            "TECHNICAL",
            {"insufficient_data": True, "technical_confidence": "error"},
            ReadinessStatus.ERROR,
        ),
        ("COMBINED", {"is_complete": True}, ReadinessStatus.READY),
        (
            "COMBINED",
            {"is_complete": False, "final_score": 7.5},
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
        ),
        (
            "RANKING",
            {"is_complete": True, "warning_flags_json": ["low_technical_confidence"]},
            ReadinessStatus.DEGRADED,
        ),
        ("RANKING", {"profile_score": 7.5, "profile_rank": 1}, ReadinessStatus.UNKNOWN),
        ("REGIME", {"confidence": "low", "warnings": ["stale_market_data"]}, ReadinessStatus.STALE),
        (
            "SECTOR",
            {"rows": [{"sector": "Technology", "confidence": "low"}]},
            ReadinessStatus.DEGRADED,
        ),
        (
            "SECTOR",
            {"rows": [{"sector": "Technology", "confidence": "normal"}]},
            ReadinessStatus.READY,
        ),
        (
            "CERI",
            {"data_confidence": "Normal", "opportunity_ledger_json": {"rated": False}},
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
        ),
        (
            "CERI",
            {
                "data_confidence": "Low",
                "confidence_ledger_json": {"freshness": {"status": "STALE", "age_days": 15}},
            },
            ReadinessStatus.STALE,
        ),
        (
            "IBMI",
            {"confidence": "LOW", "coverage_status": "AVAILABLE", "freshness_status": "AVAILABLE"},
            ReadinessStatus.DEGRADED,
        ),
        (
            "IBMI",
            {
                "confidence": "NORMAL",
                "coverage_status": "SUBSCRIPTION_REQUIRED",
                "freshness_status": "UNAVAILABLE",
            },
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
        ),
        (
            "IBMI",
            {"confidence": "HIGH", "coverage_status": "AVAILABLE", "freshness_status": "STALE"},
            ReadinessStatus.STALE,
        ),
        (
            "IBMI",
            {"confidence": "NORMAL", "coverage_status": "AVAILABLE", "freshness_status": "UNKNOWN"},
            ReadinessStatus.UNKNOWN,
        ),
        (
            "SETUP",
            {"confidence_label": "INSUFFICIENT", "state": "READY"},
            ReadinessStatus.INSUFFICIENT_EVIDENCE,
        ),
        (
            "LIFECYCLE",
            {"confidence_label": "NORMAL", "output_state": "FAILED"},
            ReadinessStatus.READY,
        ),
        ("LIFECYCLE", {"output_state": "READY"}, ReadinessStatus.UNKNOWN),
        (
            "WINNER",
            {"eligibility_status": "ELIGIBLE", "technical_data_quality": "high"},
            ReadinessStatus.UNKNOWN,
        ),
    ],
)
def test_producer_signal_contract_preserves_business_payload(producer, payload, status):
    original = deepcopy(payload)
    result = normalize(producer, payload)
    assert result.status is status
    assert payload == original
    assert result.calculation_identity_fingerprint == "a" * 64
    assert ProducerReadinessEnvelope.from_payload(result.canonical_payload()) == result


def test_same_numeric_value_cannot_certify_sufficiency():
    ready = normalize(
        "TECHNICAL",
        {
            "dual_score": 7.5,
            "insufficient_data": False,
            "technical_confidence": "normal",
        },
    )
    blocked = normalize(
        "TECHNICAL",
        {
            "dual_score": 7.5,
            "insufficient_data": True,
            "technical_confidence": "normal",
        },
    )
    assert ready.status is ReadinessStatus.READY
    assert blocked.status is ReadinessStatus.INSUFFICIENT_EVIDENCE
    assert ReadinessReason.TECH_INSUFFICIENT_HISTORY in blocked.blocking_reasons
    assert ready.fingerprint() != blocked.fingerprint()


def test_required_history_and_native_error_details_are_retained():
    payload = {
        "insufficient_data": False,
        "technical_confidence": "low",
        "v4_debug_json": {
            "data_readiness": {
                "has_sufficient_history": False,
                "missing_reasons": ["short_history"],
            }
        },
        "missing_data_json": {"missing_benchmark_data": True},
    }
    result = normalize("TECHNICAL", payload)
    assert result.status is ReadinessStatus.INSUFFICIENT_EVIDENCE
    assert (
        result.native_signals.canonical_payload()["technical_data_readiness"]
        == (payload["v4_debug_json"]["data_readiness"])
    )


def test_sparse_fundamental_warning_does_not_invent_coverage_gate():
    result = normalize(
        "FUNDAMENTAL",
        {
            "fundamental_score": 7.5,
            "data_coverage_score": Decimal("1.2"),
            "v2_warning_flags_json": {"flags": ["sparse_fundamental_data"]},
        },
    )
    assert result.status is ReadinessStatus.DEGRADED
    assert not result.blocking_reasons
    assert normalize("FUNDAMENTAL", {"data_coverage_score": Decimal("1.2")}).status is (
        ReadinessStatus.READY
    )


def test_legacy_numeric_evidence_stays_uncertified():
    evidence = SimpleNamespace(
        id=1,
        artifact_kind="TECHNICAL",
        payload_json={"dual_score": 9},
        calculation_identity_fingerprint="a" * 64,
    )
    result = readiness_from_evidence(evidence)
    assert result.status is ReadinessStatus.LEGACY_UNKNOWN
    assert result.evidence_id == 1


def test_deep_immutability_and_deterministic_reasons():
    result = normalize(
        "CERI",
        {
            "data_confidence": "Low",
            "confidence_ledger_json": {
                "score": 4.2,
                "freshness": {"semantic": "PROVIDER_FEED_FRESHNESS", "status": "FRESH"},
            },
        },
    )
    with pytest.raises(FrozenInstanceError):
        result.status = ReadinessStatus.READY
    view = result.native_confidence.canonical_payload()
    view["confidence_ledger_json"]["score"] = 99
    assert result.native_confidence.canonical_payload()["confidence_ledger_json"]["score"] == 4.2
    reordered = replace(result, warning_reasons=tuple(reversed(result.warning_reasons)) * 2)
    assert reordered.fingerprint() == result.fingerprint()
    assert replace(result, evidence_id=42).fingerprint() == result.fingerprint()


def test_identity_version_and_business_anchor_binding():
    payload = {"insufficient_data": False, "technical_confidence": "normal"}
    first = normalize("TECHNICAL", payload)
    second = normalize("TECHNICAL", payload, "b" * 64)
    assert first.status is second.status is ReadinessStatus.READY
    assert first.fingerprint() != second.fingerprint()
    assert replace(first, readiness_policy_version="future-v2").fingerprint() != first.fingerprint()
    anchored = normalize_producer_readiness(
        "TECHNICAL",
        payload,
        identity_fingerprint="a" * 64,
        evaluated_at=datetime(2026, 9, 15, 20, tzinfo=UTC),
        business_anchor=date(2026, 9, 15),
        calculation_versions={"engine": "5.0.0"},
    )
    assert anchored.evaluated_at == "2026-09-15T20:00:00.000000Z"
    assert anchored.business_anchor == "2026-09-15"


def test_native_metrics_keep_distinct_domain_semantics():
    tech = normalize(
        "TECHNICAL",
        {
            "technical_confidence": "low",
            "insufficient_data": False,
            "data_quality_score": Decimal("7"),
        },
    )
    ceri = normalize(
        "CERI",
        {
            "data_confidence": "Low",
            "coverage_pct": 37.5,
            "confidence_ledger_json": {
                "score": 4.2,
                "freshness": {"semantic": "PROVIDER_FEED_FRESHNESS", "age_days": 5},
            },
        },
    )
    assert tech.native_confidence.canonical_payload()["technical_confidence"] == "low"
    assert tech.native_confidence.canonical_payload()["data_quality_score"] == "7"
    assert ceri.native_coverage.canonical_payload()["coverage_pct"] == 37.5
    assert ceri.native_freshness.canonical_payload()["provider_feed_freshness"]["age_days"] == 5


def test_lifecycle_state_is_not_evidence_readiness():
    assert LifecycleState.READY != ReadinessStatus.READY
    with pytest.raises(TypeError, match="never LifecycleState"):
        ProducerReadinessEnvelope("LIFECYCLE", LifecycleState.READY, "a" * 64)
    assert normalize("LIFECYCLE", {"output_state": "READY"}).status is ReadinessStatus.UNKNOWN


@pytest.mark.parametrize("status", list(ReadinessStatus))
def test_placeholder_policy_never_silently_allows(status):
    readiness = replace(
        legacy_readiness("TECHNICAL", identity_fingerprint="a" * 64),
        status=status,
    )
    decision = UndecidedConsumerEligibilityPolicy().evaluate(
        readiness,
        consumer="COMBINED",
        config=NativeReadinessMetrics.freeze({"future": True}),
    )
    assert decision.status is ConsumerEligibilityStatus.POLICY_UNDECIDED
    assert decision.to_dto()["producer_readiness_fingerprint"] == readiness.fingerprint()


def test_mismatched_readiness_identity_fails_closed():
    result = normalize("TECHNICAL", {"insufficient_data": False, "technical_confidence": "high"})
    with pytest.raises(ValueError, match="identity mismatch"):
        readiness_from_evidence(
            SimpleNamespace(
                id=1,
                artifact_kind="TECHNICAL",
                calculation_identity_fingerprint="b" * 64,
                payload_json={"producer_readiness": result.canonical_payload()},
            )
        )


def test_setup_preserves_own_confidence_and_native_freshness():
    payload = {
        "confidence_label": "NORMAL",
        "technical_confidence": "LOW",
        "freshness_status": "FRESH",
    }
    assert normalize("SETUP", payload).status is ReadinessStatus.READY
    assert normalize("SETUP", {**payload, "freshness_status": "STALE"}).status is (
        ReadinessStatus.STALE
    )
    assert normalize("SETUP", {**payload, "freshness_status": "NEAR_STALE"}).status is (
        ReadinessStatus.DEGRADED
    )


def test_winner_readiness_guard_allows_operational_lineage_only():
    from sqlalchemy.orm.attributes import set_committed_value

    from app.models.tables import WinnerPredictionSnapshot, _protect_winner_readiness_update

    stored = {"producer_readiness": legacy_readiness("WINNER").canonical_payload()}
    prediction = WinnerPredictionSnapshot(id=1, lineage_json=deepcopy(stored))
    set_committed_value(prediction, "lineage_json", deepcopy(stored))
    connection = SimpleNamespace(
        execute=lambda _statement: SimpleNamespace(scalar_one=lambda: stored)
    )
    prediction.lineage_json = {**stored, "dependent_episode": True}
    _protect_winner_readiness_update(None, connection, prediction)
    prediction.lineage_json = {"producer_readiness": {"status": "READY"}}
    with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
        _protect_winner_readiness_update(None, connection, prediction)


def test_envelope_rejects_mutable_metrics_and_unbound_certification():
    with pytest.raises(TypeError, match="immutable NativeReadinessMetrics"):
        ProducerReadinessEnvelope(
            "TECHNICAL", ReadinessStatus.UNKNOWN, None, native_confidence={"confidence": []}
        )
    with pytest.raises(ValueError, match="bind to Calculation Identity"):
        ProducerReadinessEnvelope("TECHNICAL", ReadinessStatus.READY, None)


def test_empty_fundamental_warning_container_is_not_a_warning():
    result = normalize(
        "FUNDAMENTAL", {"data_coverage_score": 10, "v2_warning_flags_json": {"flags": []}}
    )
    assert result.status is ReadinessStatus.READY
    assert not result.warning_reasons


def test_sector_readiness_canonicalizes_native_row_order():
    rows = [
        {"sector": "Energy", "confidence": "normal", "warnings": []},
        {"sector": "Technology", "confidence": "low", "warnings": ["sparse_prior"]},
    ]
    first = normalize("SECTOR", {"rows": rows})
    second = normalize("SECTOR", {"rows": list(reversed(rows))})
    assert first.fingerprint() == second.fingerprint()


def test_setup_intrinsic_quality_is_distinct_from_trading_state_and_confidence():
    result = normalize(
        "SETUP",
        {
            "confidence_label": "HIGH",
            "data_quality_label": "INSUFFICIENT",
            "state": "READY",
        },
    )
    assert result.status is ReadinessStatus.INSUFFICIENT_EVIDENCE
    assert ReadinessReason.INSUFFICIENT_NATIVE_DATA_QUALITY in result.blocking_reasons


def test_lifecycle_gap_evidence_and_current_pointer_never_certify_trading_ready():
    from app.models.tables import SetupLifecycleEpisode, SetupLifecycleEvaluationEvidence
    from app.services.setup_lifecycle.decision_evidence import (
        get_lifecycle_readiness_for_episode,
        persist_observation_gap_evaluation_evidence,
    )

    prior = SimpleNamespace(
        id=10,
        decision_session=date(2026, 9, 10),
        counters_json={},
        output_state="READY",
        setup_evidence_id=1,
        calculation_cutoff_at=datetime(2026, 9, 10, 20, tzinfo=UTC),
        calendar_version="swinglens-us-equities-v1",
        calculation_identity_fingerprint="a" * 64,
    )
    rows = {10: prior}

    def add(row):
        row.id = 11
        rows[11] = row

    db = SimpleNamespace(
        get=lambda model, row_id: (
            rows.get(row_id) if model is SetupLifecycleEvaluationEvidence else None
        ),
        scalar=lambda _statement: None,
        add=add,
        flush=lambda: None,
    )
    episode = SetupLifecycleEpisode(
        ticker="ACME",
        timeframe="1D",
        setup_family="BREAKOUT",
        current_state="READY",
        current_phase="READY",
        latest_evaluation_evidence_id=10,
        latest_transition_evidence_id=None,
        last_observed_on=date(2026, 9, 10),
        engine_version="test-v1",
        config_version="test-v1",
        config_hash="c" * 64,
    )
    result = persist_observation_gap_evaluation_evidence(
        db,
        episode=episode,
        observed_on=date(2026, 9, 11),
        missing_observation_sessions=1,
        threshold=3,
        evaluation_run_id=None,
    )
    episode.latest_evaluation_evidence_id = result.id
    readiness = get_lifecycle_readiness_for_episode(db, episode)
    assert result.output_state == "READY"
    assert readiness.status is ReadinessStatus.UNKNOWN
    assert ReadinessReason.LIFECYCLE_OBSERVATION_GAP in readiness.warning_reasons
    assert readiness.evidence_id == 11
    assert readiness.native_signals.canonical_payload()["missing_observation_sessions"] == 1
