from __future__ import annotations

from app.services.historical_authority_retention import (
    FINDING_BOUNDARIES,
    HistoricalBoundaryClassification,
    HistoricalReconstructionAvailability,
    PurgeDisposition,
    RetainedAuthority,
    RetentionClass,
    archived_payload,
    assess_authority_purge,
    classify_historical_availability,
    create_authority_archive,
    restore_authority_archive,
)


def _authority(
    semantic_id: str,
    *,
    material: bool = True,
    pinned: bool = True,
) -> RetainedAuthority:
    return RetainedAuthority(
        authority_type="TEST_AUTHORITY",
        semantic_id=semantic_id,
        content_fingerprint=f"sha256:{semantic_id}",
        material_to_reconstruction=material,
        retention_class=RetentionClass.IMMUTABLE_AUTHORITY,
        pinned_by=("decision:1",) if pinned else (),
    )


def test_core_005_equal_current_value_cannot_supply_legacy_provenance() -> None:
    result = classify_historical_availability(
        required_authority=(
            "value",
            "provider",
            "revision",
            "published_at",
            "currency",
            "fx_identity",
        ),
        retained_authority=("value",),
        missing_classification=(HistoricalBoundaryClassification.LEGACY_AUTHORITY_UNAVAILABLE),
    )

    assert result.availability is HistoricalReconstructionAvailability.PERMANENTLY_UNAVAILABLE
    assert result.current_state_consulted is False
    assert set(result.missing_authority) == {
        "provider",
        "revision",
        "published_at",
        "currency",
        "fx_identity",
    }


def test_rank_007_current_schedule_cannot_replace_missing_historical_schedule() -> None:
    result = classify_historical_availability(
        required_authority=(
            "earnings_date",
            "provider",
            "publication_time",
            "retrieval_time",
            "source_revision",
            "business_cutoff",
            "risk_policy",
        ),
        retained_authority=("earnings_date", "business_cutoff", "risk_policy"),
        missing_classification=(HistoricalBoundaryClassification.EXTERNAL_PROVIDER_NOT_RETAINABLE),
        external_unavailable=True,
        retrospective_available=True,
    )
    assert (
        result.availability is HistoricalReconstructionAvailability.EXTERNAL_AUTHORITY_UNAVAILABLE
    )
    assert result.current_state_consulted is False


def test_win_006_matching_current_ohlcv_does_not_create_revision_identity() -> None:
    result = classify_historical_availability(
        required_authority=("ohlcv_body", "price_bar_revision_id", "observation_cutoff"),
        retained_authority=("ohlcv_body", "observation_cutoff"),
        missing_classification=(HistoricalBoundaryClassification.LEGACY_REVISION_UNAVAILABLE),
    )
    assert result.availability is HistoricalReconstructionAvailability.PERMANENTLY_UNAVAILABLE
    assert result.missing_authority == ("price_bar_revision_id",)


def test_xint_010_current_k2_cannot_replace_missing_historical_k1() -> None:
    result = classify_historical_availability(
        required_authority=("effective_configuration", "calculation_identity"),
        retained_authority=("calculation_identity",),
        missing_classification=(
            HistoricalBoundaryClassification.CONFIGURATION_AUTHORITY_UNAVAILABLE
        ),
        retrospective_available=True,
    )
    assert result.availability is HistoricalReconstructionAvailability.CURRENT_RULES_ONLY
    assert result.missing_authority == ("effective_configuration",)
    assert result.current_state_consulted is False


def test_purge_blocks_pinned_material_authority() -> None:
    result = assess_authority_purge((_authority("source:S1"),))
    assert result.disposition is PurgeDisposition.BLOCK_MATERIAL_AUTHORITY
    assert result.allowed is False
    assert result.resulting_availability is HistoricalReconstructionAvailability.EXACT


def test_purge_requires_visible_downgrade_for_unpinned_material_authority() -> None:
    source = _authority("legacy-source:S1", pinned=False)
    blocked = assess_authority_purge((source,))
    downgraded = assess_authority_purge((source,), explicit_reconstruction_downgrade=True)

    assert blocked.disposition is PurgeDisposition.BLOCK_MATERIAL_AUTHORITY
    assert downgraded.disposition is PurgeDisposition.ALLOW_WITH_EXPLICIT_DOWNGRADE
    assert (
        downgraded.resulting_availability
        is HistoricalReconstructionAvailability.PERMANENTLY_UNAVAILABLE
    )


def test_operational_cache_purge_does_not_change_reconstruction_availability() -> None:
    result = assess_authority_purge((_authority("cache:1", material=False, pinned=False),))
    assert result.disposition is PurgeDisposition.ALLOW_OPERATIONAL_PURGE
    assert result.resulting_availability is HistoricalReconstructionAvailability.EXACT


def test_archive_round_trip_preserves_semantic_ids_hashes_manifest_and_result() -> None:
    authority = _authority("source:S1")
    archive = create_authority_archive(
        archive_id="archive:decision-1",
        source_manifest_fingerprint="manifest-sha256-1",
        authorities=(authority,),
        payload={
            "semantic_id": "decision-1",
            "manifest_fingerprint": "manifest-sha256-1",
            "result_fingerprint": "result-sha256-1",
        },
    )
    restored = restore_authority_archive(archived_payload(archive))

    assert restored.archive_fingerprint == archive.archive_fingerprint
    assert restored.source_manifest_fingerprint == archive.source_manifest_fingerprint
    assert restored.authorities == archive.authorities
    assert restored.payload == archive.payload


def test_retention_validation_is_set_based_for_1_50_and_200_authorities() -> None:
    for size in (1, 50, 200):
        authorities = tuple(_authority(f"source:{index}") for index in range(size))
        result = assess_authority_purge(authorities)
        assert result.disposition is PurgeDisposition.BLOCK_MATERIAL_AUTHORITY
        assert len(result.blocked_authority_ids) == size


def test_five_finding_registry_has_no_current_supported_defect() -> None:
    assert set(FINDING_BOUNDARIES) == {
        "CORE-005",
        "CERI-010",
        "RANK-007",
        "WIN-006",
        "XINT-010",
    }
    assert all(row["current_path_certified"] for row in FINDING_BOUNDARIES.values())
    assert FINDING_BOUNDARIES["CERI-010"]["status"] == "CLOSED"
    assert FINDING_BOUNDARIES["XINT-010"]["status"] == "CLOSED"
