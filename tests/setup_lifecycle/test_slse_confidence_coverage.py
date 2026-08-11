from __future__ import annotations

from dataclasses import replace

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.confidence_service import SetupLifecycleConfidenceService
from app.services.setup_lifecycle.dtos import FamilyEvidence
from app.services.setup_lifecycle.enums import SetupFamily


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
