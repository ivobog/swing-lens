from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models.tables import EstimateKind, WinnerProbabilityEstimate
from app.services.winner_probability.api_service import _sort_value
from app.services.winner_probability.estimate_lifecycle import estimate_is_serving
from app.services.winner_probability.estimate_publication_service import (
    DecisionReconstructionCategory,
    PublicationInvariantViolation,
    classify_decision_reconstruction,
    validate_clean_insufficient_replacement,
)


def test_decision_reconstruction_taxonomy_is_evidence_precise() -> None:
    assert (
        classify_decision_reconstruction(
            member_count=10,
            invalid_member_count=1,
            unverifiable_member_count=2,
        )
        == DecisionReconstructionCategory.DIRECTLY_CONTAMINATED
    )
    assert (
        classify_decision_reconstruction(
            member_count=10,
            invalid_member_count=0,
            unverifiable_member_count=2,
        )
        == DecisionReconstructionCategory.LEGACY_EVIDENCE_UNVERIFIABLE
    )
    assert (
        classify_decision_reconstruction(
            member_count=0,
            invalid_member_count=0,
            unverifiable_member_count=0,
        )
        == DecisionReconstructionCategory.NO_ORIGINAL_EVIDENCE
    )
    assert (
        classify_decision_reconstruction(
            member_count=10,
            invalid_member_count=0,
            unverifiable_member_count=0,
        )
        == DecisionReconstructionCategory.OTHER_POINT_IN_TIME_UNRECONSTRUCTABLE
    )


def test_clean_historical_insufficient_never_fabricates_a_probability() -> None:
    valid = SimpleNamespace(
        estimate_kind=EstimateKind.DECISION_TIME,
        point_probability=None,
        lower_bound=None,
        upper_bound=None,
        evidence_grade="Insufficient",
        insufficient_reasons_json=["no_clean_evidence_at_original_decision_cutoff"],
    )
    validate_clean_insufficient_replacement(valid)

    with pytest.raises(PublicationInvariantViolation, match="numeric probability"):
        validate_clean_insufficient_replacement(
            SimpleNamespace(**{**vars(valid), "point_probability": 0})
        )
    with pytest.raises(PublicationInvariantViolation, match="reason"):
        validate_clean_insufficient_replacement(
            SimpleNamespace(**{**vars(valid), "insufficient_reasons_json": []})
        )


def test_research_reconstruction_is_structurally_non_serving() -> None:
    assert EstimateKind.RESEARCH_RECONSTRUCTION == "RESEARCH_RECONSTRUCTION"
    statement = (
        select(WinnerProbabilityEstimate.id)
        .where(estimate_is_serving())
        .where(WinnerProbabilityEstimate.estimate_kind == EstimateKind.RESEARCH_RECONSTRUCTION)
    )
    compiled = str(
        statement.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert "RESEARCH_RECONSTRUCTION" in compiled
    assert "estimate_kind IN" in compiled


def test_insufficient_probability_sorts_after_numeric_values_in_both_directions() -> None:
    assert _sort_value(None, False) > _sort_value(0, False)
    assert _sort_value(None, True) > _sort_value(0, True)
