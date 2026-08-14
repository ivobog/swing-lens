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


def test_pivot_metadata_does_not_shadow_an_explicit_pullback_family() -> None:
    candidates = evaluate_family_candidates(
        snapshot(
            setup_score=7.8,
            classification="Pullback Uptrend",
            distance_to_pivot_pct=1.0,
            held_near_support=True,
        )
    )

    primary = select_primary_family(candidates)

    assert primary is not None
    assert primary.setup_family is SetupFamily.PULLBACK


def test_incidental_declining_volume_does_not_shadow_explicit_vcp_family() -> None:
    candidates = evaluate_family_candidates(
        snapshot(
            setup_score=7.8,
            classification="VCP",
            contraction_count=2,
            volume_percentile_252=30,
        )
    )

    primary = select_primary_family(candidates)

    assert primary is not None
    assert primary.setup_family is SetupFamily.VCP


def test_family_adapters_preserve_trigger_distance_evidence_without_sentinels() -> None:
    candidates = evaluate_family_candidates(
        snapshot(
            setup_score=7.8,
            classification="VCP Pullback Continuation",
            trigger_price=100.0,
            distance_to_pivot_pct=2.0,
            contraction_count=2,
            volume_percentile_252=30,
            held_near_support=True,
            range_percentile_252=30,
        )
    )

    by_family = {candidate.setup_family: candidate for candidate in candidates}
    for family in (SetupFamily.VCP, SetupFamily.PULLBACK, SetupFamily.CONTINUATION):
        evidence = by_family[family].evidence
        assert evidence["trigger_price"] == 100.0
        assert evidence["trigger_distance_pct"] == 2.0
        assert evidence["trigger_distance_missing_reason"] is None
        assert 999.0 not in evidence.values()
