from __future__ import annotations

from dataclasses import replace

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.confidence_service import (
    SetupLifecycleConfidenceService,
    weighted_confidence_score,
)
from app.services.setup_lifecycle.dtos import FamilyEvidence
from app.services.setup_lifecycle.enums import ConfidenceLabel, SetupFamily


def test_required_feature_component_uses_persisted_coverage_not_optional_signals() -> None:
    normalized = snapshot(
        setup_score=8.0,
        classification="Breakout Base",
        distance_to_pivot_pct=1.0,
        market_regime=None,
    )
    normalized = replace(normalized, required_feature_coverage=1.0)
    evidence = FamilyEvidence(
        setup_family=SetupFamily.BREAKOUT,
        phase_code="PIVOT_READY",
        evidence_score=8.0,
        confidence_score=0,
        trackable=True,
        ready=True,
    )

    result = SetupLifecycleConfidenceService().score(
        normalized,
        evidence,
        persistence_sessions=3,
        context_complete=False,
    )

    assert result.components["required_feature_coverage"] == 1.0
    assert result.components["context_completeness"] == 0.0


def test_exact_sdd_weighted_confidence_example_is_85() -> None:
    components = {
        "required_feature_coverage": 1.0,
        "signal_agreement": 0.8,
        "persistence": 0.5,
        "freshness_and_lineage": 1.0,
        "context_completeness": 1.0,
    }
    weights = {
        "required_feature_coverage": 0.30,
        "signal_agreement": 0.25,
        "persistence": 0.20,
        "freshness_and_lineage": 0.15,
        "context_completeness": 0.10,
    }

    assert sum(weights.values()) == 1.0
    assert weighted_confidence_score(components, weights) == 85


def test_family_adapter_confidence_is_not_applied_as_a_second_stage_blend() -> None:
    normalized = replace(
        snapshot(setup_score=8.0, classification="Breakout Base"),
        required_feature_coverage=1.0,
    )
    base = FamilyEvidence(
        setup_family=SetupFamily.BREAKOUT,
        phase_code="PIVOT_READY",
        evidence_score=8.0,
        confidence_score=20,
        trackable=True,
        ready=True,
    )
    service = SetupLifecycleConfidenceService()

    low_adapter_score = service.score(normalized, base, persistence_sessions=2)
    high_adapter_score = service.score(
        normalized,
        replace(base, confidence_score=95),
        persistence_sessions=2,
    )

    assert low_adapter_score.score == high_adapter_score.score


def test_persistence_component_is_bounded_for_zero_through_three_sessions() -> None:
    normalized = replace(
        snapshot(setup_score=8.0, classification="Breakout Base"),
        required_feature_coverage=1.0,
    )
    evidence = FamilyEvidence(
        setup_family=SetupFamily.BREAKOUT,
        phase_code="PIVOT_READY",
        evidence_score=8.0,
        confidence_score=80,
        trackable=True,
        ready=True,
    )
    service = SetupLifecycleConfidenceService()

    values = [
        service.score(normalized, evidence, persistence_sessions=s).components["persistence"]
        for s in range(4)
    ]

    assert values == [0.0, 1 / 3, 2 / 3, 1.0]


def test_confidence_label_boundaries_are_exact() -> None:
    service = SetupLifecycleConfidenceService()

    assert service.label_for_score(69) is ConfidenceLabel.LOW
    assert service.label_for_score(70) is ConfidenceLabel.NORMAL
    assert service.label_for_score(84) is ConfidenceLabel.NORMAL
    assert service.label_for_score(85) is ConfidenceLabel.HIGH
