from __future__ import annotations

import csv
from dataclasses import replace
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command
from app.services.historical_authority_composition import (
    CONTRACT_BY_KEY,
    EDGE_CONTRACTS,
    FORBIDDEN_DEPENDENCY_EDGES,
    CompositionDimension,
    HistoricalAuthorityArtifact,
    HistoricalAuthorityDependency,
    HistoricalCompatibilityStatus,
    reject_undeclared_dependency,
    validate_historical_authority_composition,
)
from app.services.original_context_reconstruction import (
    AuthorityAvailability,
    AuthorityDimension,
    AuthorityReference,
    AuthorityReferenceKind,
    AuthorityResolution,
    OriginalContextReconstructionManifest,
    ReconstructionMode,
    ReconstructionStatus,
)

ROOT = Path(__file__).resolve().parents[1]
MATRIX = ROOT / "docs/remediation/calculation-lineage/T16C_cross_domain_compatibility_matrix.csv"


def _artifact(domain: str, artifact_id: str, **changes) -> HistoricalAuthorityArtifact:
    base = HistoricalAuthorityArtifact(
        domain=domain,
        artifact_type="TestEvidence",
        artifact_id=artifact_id,
        evidence_fingerprint=f"evidence:{artifact_id}",
        calculation_identity=f"calculation:{artifact_id}",
        calculation_context="calculation-context-1",
        temporal_context="temporal-context-1",
        business_session="2026-09-14",
        business_cutoff="2026-09-14T20:00:00+00:00",
        calendar_identity="calendar-1",
        subject_scope="ticker:ABC",
        work_scope_identity="work-scope-1",
        refresh_cycle_identity="refresh-cycle-1",
        effective_configuration="config-1",
        readiness="readiness-1",
        source_lineage="lineage-1",
        source_revision="revision-1",
        predecessor_identity="predecessor-1",
        algorithm_schema="algorithm-1",
        rule_policy="rule-1",
        provider_revision="provider-1",
        semantic_output="75",
    )
    return replace(base, **changes)


def _dependency(
    key=("FUNDAMENTAL", "COMBINED", "behavioral_input"),
    *,
    expected_changes=None,
    resolved_changes=None,
    consumer_changes=None,
):
    contract = CONTRACT_BY_KEY[key]
    consumer = _artifact(contract.consumer_domain, "consumer", **(consumer_changes or {}))
    expected = _artifact(contract.producer_domain, "producer-1", **(expected_changes or {}))
    resolved = replace(expected, **(resolved_changes or {}))
    return HistoricalAuthorityDependency(contract, "source", consumer, expected, resolved)


def test_exact_composition_is_typed_content_addressed_and_deterministic() -> None:
    dependency = _dependency()
    first = validate_historical_authority_composition((dependency,))
    second = validate_historical_authority_composition((dependency,))

    assert first.overall_exact is True
    assert first.edge_results[0].status is HistoricalCompatibilityStatus.COMPATIBLE
    assert first.proof_boundary_fingerprint == second.proof_boundary_fingerprint
    assert first.failed_edge is None
    assert first.missing_authority == ()


@pytest.mark.parametrize(
    ("resolved_changes", "expected_status", "dimension"),
    [
        (
            {"artifact_id": "producer-2", "evidence_fingerprint": "evidence:producer-2"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_SOURCE_LINEAGE,
            CompositionDimension.PRODUCER_EVIDENCE,
        ),
        (
            {"effective_configuration": "config-2"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_CONFIGURATION,
            CompositionDimension.EFFECTIVE_CONFIGURATION,
        ),
        (
            {"readiness": "readiness-2"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_READINESS,
            CompositionDimension.READINESS,
        ),
        (
            {"source_lineage": "lineage-2"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_SOURCE_LINEAGE,
            CompositionDimension.SOURCE_LINEAGE,
        ),
        (
            {"algorithm_schema": "algorithm-2"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_SCHEMA_ALGORITHM,
            CompositionDimension.ALGORITHM_SCHEMA,
        ),
    ],
)
def test_same_value_different_material_evidence_is_rejected(
    resolved_changes, expected_status, dimension
) -> None:
    result = validate_historical_authority_composition(
        (_dependency(resolved_changes=resolved_changes),)
    )

    assert result.overall_exact is False
    assert result.failed_edge.status is expected_status
    assert result.failed_edge.failure_dimension is dimension
    assert result.dependencies[0].resolved_producer.semantic_output == "75"


@pytest.mark.parametrize(
    ("consumer_changes", "expected_status", "dimension"),
    [
        (
            {"calculation_context": "same-run-but-other-semantic-context"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_CALCULATION_CONTEXT,
            CompositionDimension.CALCULATION_CONTEXT,
        ),
        (
            {"temporal_context": "session-2"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_TEMPORAL_CONTEXT,
            CompositionDimension.TEMPORAL_CONTEXT,
        ),
        (
            {"subject_scope": "ticker:XYZ"},
            HistoricalCompatibilityStatus.INCOMPATIBLE_SCOPE,
            CompositionDimension.SUBJECT_SCOPE,
        ),
    ],
)
def test_operational_colocation_does_not_override_semantic_mismatch(
    consumer_changes, expected_status, dimension
) -> None:
    result = validate_historical_authority_composition(
        (_dependency(consumer_changes=consumer_changes),)
    )
    assert result.failed_edge.status is expected_status
    assert result.failed_edge.failure_dimension is dimension


def test_temporal_ranking_attack_on_setup_is_rejected() -> None:
    dependency = _dependency(
        ("RANKING", "SETUP", "score_decision_profile_metadata"),
        consumer_changes={"temporal_context": "S1"},
        expected_changes={"temporal_context": "S1"},
        resolved_changes={
            "artifact_id": "ranking-R2",
            "evidence_fingerprint": "ranking-R2-proof",
            "temporal_context": "S2",
        },
    )
    result = validate_historical_authority_composition((dependency,))
    assert result.failed_edge.status is HistoricalCompatibilityStatus.INCOMPATIBLE_TEMPORAL_CONTEXT


def test_cross_chain_predecessor_is_rejected() -> None:
    result = validate_historical_authority_composition(
        (
            _dependency(
                (
                    "LIFECYCLE_EVALUATION",
                    "LIFECYCLE_TRANSITION",
                    "evaluation_predecessor",
                ),
                resolved_changes={"predecessor_identity": "chain-B"},
            ),
        )
    )
    assert result.failed_edge.status is HistoricalCompatibilityStatus.INCOMPATIBLE_PREDECESSOR


def test_current_or_missing_producer_authority_fails_closed() -> None:
    current = _dependency(resolved_changes={"historical_authority": False})
    missing = replace(current, resolved_producer=None)

    for dependency in (current, missing):
        result = validate_historical_authority_composition((dependency,))
        assert result.overall_exact is False
        assert result.failed_edge.status is HistoricalCompatibilityStatus.MISSING_AUTHORITY
        assert CompositionDimension.PRODUCER_EVIDENCE in result.missing_authority


def test_manifest_fingerprint_binds_composition_and_authorization() -> None:
    reference = AuthorityReference(
        "TestEvidence",
        "consumer",
        AuthorityReferenceKind.RETAINED_HISTORICAL,
    )
    authority = (
        AuthorityResolution(
            AuthorityDimension.CALCULATION_IDENTITY,
            AuthorityAvailability.EXACT,
            True,
            (reference,),
        ),
    )
    exact = validate_historical_authority_composition((_dependency(),))
    substituted = validate_historical_authority_composition(
        (
            _dependency(
                resolved_changes={
                    "artifact_id": "producer-2",
                    "evidence_fingerprint": "evidence:producer-2",
                }
            ),
        )
    )
    base = OriginalContextReconstructionManifest(
        "TestDecision",
        "decision-1",
        "historical-1",
        ReconstructionMode.ORIGINAL_CONTEXT,
        authority,
        composition_required=True,
        composition=exact,
    )
    attacked = replace(base, composition=substituted)

    assert base.status is ReconstructionStatus.EXACT
    assert attacked.status is ReconstructionStatus.INSUFFICIENT_EVIDENCE
    assert base.fingerprint() != attacked.fingerprint()


def test_winner_contracts_have_exactly_seven_independent_sources() -> None:
    sources = {
        contract.producer_domain
        for contract in EDGE_CONTRACTS
        if contract.consumer_domain == "WINNER"
    }
    assert sources == {
        "RAW",
        "FUNDAMENTAL",
        "TECHNICAL",
        "COMBINED",
        "RANKING",
        "REGIME",
        "SECTOR",
    }
    assert "IBMI" not in sources
    assert "SETUP" not in sources
    assert "LIFECYCLE" not in sources
    assert "CERI" not in sources


def test_winner_seven_source_composition_rejects_newer_same_value_ranking() -> None:
    dependencies = []
    for producer in (
        "RAW",
        "FUNDAMENTAL",
        "TECHNICAL",
        "COMBINED",
        "RANKING",
        "REGIME",
        "SECTOR",
    ):
        contract = CONTRACT_BY_KEY[(producer, "WINNER", "independent_prediction_input")]
        consumer = _artifact("WINNER", "prediction-1")
        expected = _artifact(producer, f"{producer.lower()}-1")
        resolved = expected
        if producer == "RANKING":
            resolved = replace(
                expected,
                artifact_id="ranking-newer",
                evidence_fingerprint="ranking-newer-proof",
                semantic_output=expected.semantic_output,
            )
        dependencies.append(
            HistoricalAuthorityDependency(contract, producer.lower(), consumer, expected, resolved)
        )

    result = validate_historical_authority_composition(dependencies)
    assert result.overall_exact is False
    assert result.failed_edge.producer_domain == "RANKING"
    assert result.failed_edge.status is HistoricalCompatibilityStatus.INCOMPATIBLE_SOURCE_LINEAGE


def test_negative_dependency_edges_are_explicitly_rejected() -> None:
    expected = {
        ("CERI", "RANKING"),
        ("CERI", "SETUP"),
        ("CERI", "WINNER"),
        ("SETUP", "WINNER"),
        ("LIFECYCLE", "WINNER"),
        ("IBMI", "WINNER"),
        ("SECTOR", "RANKING"),
    }
    assert FORBIDDEN_DEPENDENCY_EDGES == expected
    for producer, consumer in expected:
        with pytest.raises(ValueError, match="UNDECLARED_DEPENDENCY"):
            reject_undeclared_dependency(producer, consumer)


def test_matrix_matches_machine_contract_registry_without_unknown_edges() -> None:
    with MATRIX.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))

    positive = {
        (row["producer_domain"], row["consumer_domain"], row["dependency_type"])
        for row in rows
        if row["dependency_type"] != "FORBIDDEN_ABSENT"
    }
    negative = {
        (row["producer_domain"], row["consumer_domain"])
        for row in rows
        if row["dependency_type"] == "FORBIDDEN_ABSENT"
    }
    assert positive == set(CONTRACT_BY_KEY)
    assert negative == FORBIDDEN_DEPENDENCY_EDGES
    assert len(positive) == 31
    assert len(negative) == 7
    assert all("UNKNOWN" not in row["status"] for row in rows)
    for row in rows:
        if row["dependency_type"] == "FORBIDDEN_ABSENT":
            continue
        contract = CONTRACT_BY_KEY[
            (row["producer_domain"], row["consumer_domain"], row["dependency_type"])
        ]
        assert {item.value for item in contract.required_exact_dimensions} == set(
            row["required_exact_dimensions"].split("|")
        )
        assert {item.value for item in contract.required_compatible_dimensions} == set(
            row["required_compatible_dimensions"].split("|")
        )
        assert contract.allowed_differences == (row["allowed_differences"],)


@pytest.mark.integration
@pytest.mark.destructive
def test_postgresql_attack_matrix_is_fail_closed(
    disposable_postgres_database: str,
) -> None:
    alembic = Config("alembic.ini")
    alembic.attributes["database_url"] = disposable_postgres_database
    command.upgrade(alembic, "head")
    engine = create_engine(disposable_postgres_database)
    with engine.begin() as connection:
        assert connection.scalar(text("select current_database()"))

    exact = validate_historical_authority_composition((_dependency(),))
    attacks = (
        _dependency(
            resolved_changes={
                "artifact_id": "same-value-newer-evidence",
                "evidence_fingerprint": "same-value-newer-proof",
            }
        ),
        _dependency(consumer_changes={"temporal_context": "other-session"}),
        _dependency(resolved_changes={"effective_configuration": "config-2"}),
        _dependency(consumer_changes={"subject_scope": "ticker:XYZ"}),
        _dependency(resolved_changes={"source_lineage": "lineage-2"}),
        _dependency(resolved_changes={"historical_authority": False}),
        replace(_dependency(), resolved_producer=None),
    )
    expected = (
        HistoricalCompatibilityStatus.INCOMPATIBLE_SOURCE_LINEAGE,
        HistoricalCompatibilityStatus.INCOMPATIBLE_TEMPORAL_CONTEXT,
        HistoricalCompatibilityStatus.INCOMPATIBLE_CONFIGURATION,
        HistoricalCompatibilityStatus.INCOMPATIBLE_SCOPE,
        HistoricalCompatibilityStatus.INCOMPATIBLE_SOURCE_LINEAGE,
        HistoricalCompatibilityStatus.MISSING_AUTHORITY,
        HistoricalCompatibilityStatus.MISSING_AUTHORITY,
    )

    assert exact.overall_exact is True
    assert [
        validate_historical_authority_composition((attack,)).failed_edge.status
        for attack in attacks
    ] == list(expected)
    engine.dispose()
