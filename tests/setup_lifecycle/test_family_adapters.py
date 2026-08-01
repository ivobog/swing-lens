from __future__ import annotations

from lifecycle_helpers import snapshot

from app.services.setup_lifecycle.enums import SetupFamily
from app.services.setup_lifecycle.family_adapters import (
    evaluate_family_candidates,
    select_primary_family,
)


def test_family_selection_is_deterministic_when_scores_tie() -> None:
    candidates = evaluate_family_candidates(
        snapshot(
            setup_score=7.8,
            classification="Breakout Pullback VCP Continuation Flag",
            distance_to_pivot_pct=1.0,
            support_distance_atr=0.8,
            reversal_ready=True,
            contraction_count=2,
            volume_percentile_252=30,
            range_percentile_252=30,
        )
    )

    primary = select_primary_family(candidates)

    assert primary is not None
    assert primary.setup_family is SetupFamily.BREAKOUT


def test_generic_family_is_used_when_supported_family_evidence_is_absent() -> None:
    candidates = evaluate_family_candidates(
        snapshot(setup_score=6.2, classification="Constructive Candidate")
    )

    assert len(candidates) == 1
    assert candidates[0].setup_family is SetupFamily.GENERIC
