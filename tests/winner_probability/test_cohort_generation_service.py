from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.services.winner_probability.cohort_generation_service import (
    COHORT_ALGORITHM_VERSION,
    CohortGenerationStatus,
    EvidenceWatermark,
    WinnerCohortContract,
    canonical_generation_key,
    canonical_watermark_hash,
    validate_generation_transition,
)


def test_cohort_algorithm_version_advances_for_replay_lineage_contract() -> None:
    assert COHORT_ALGORITHM_VERSION == "cohort-v2.1"


def _contract() -> WinnerCohortContract:
    return WinnerCohortContract(
        outcome_definition_id=7,
        feature_schema_version="features-v1",
        calculation_version="calc-v1",
        config_hash="config-hash",
        eligibility_policy_version="eligibility-v1",
        compatibility_policy_version="compatibility-v1",
        cohort_algorithm_version="cohort-v2",
    )


def _watermark(target_stop_revision_id: int = 20) -> EvidenceWatermark:
    return EvidenceWatermark(
        forward_revision_id=10,
        target_stop_revision_id=target_stop_revision_id,
        eligibility_decision_id=30,
        training_replay_id=40,
    )


def test_watermark_hash_is_deterministic_and_material() -> None:
    assert canonical_watermark_hash(_watermark()) == canonical_watermark_hash(
        _watermark()
    )
    assert canonical_watermark_hash(_watermark()) != canonical_watermark_hash(
        _watermark(21)
    )


def test_request_clock_does_not_change_generation_identity() -> None:
    first_requested_at = datetime(2026, 8, 17, 10, 33, 9, 896000, tzinfo=UTC)
    second_requested_at = first_requested_at + timedelta(milliseconds=227)

    first = canonical_generation_key(
        _contract(), _watermark(), requested_at=first_requested_at
    )
    second = canonical_generation_key(
        _contract(), _watermark(), requested_at=second_requested_at
    )

    assert first == second


def test_material_revision_changes_generation_identity() -> None:
    assert canonical_generation_key(
        _contract(), _watermark()
    ) != canonical_generation_key(_contract(), _watermark(21))


def test_generation_lifecycle_rejects_partial_publication() -> None:
    validate_generation_transition(
        CohortGenerationStatus.BUILDING, CohortGenerationStatus.READY
    )
    validate_generation_transition(
        CohortGenerationStatus.READY, CohortGenerationStatus.PUBLISHED
    )

    try:
        validate_generation_transition(
            CohortGenerationStatus.BUILDING, CohortGenerationStatus.PUBLISHED
        )
    except ValueError as exc:
        assert "BUILDING" in str(exc)
    else:
        raise AssertionError("BUILDING generations must not publish directly")
