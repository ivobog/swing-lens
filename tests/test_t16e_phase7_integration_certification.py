from __future__ import annotations

import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

from app.services.historical_authority_composition import (
    CONTRACT_BY_KEY,
    FORBIDDEN_DEPENDENCY_EDGES,
    HistoricalAuthorityArtifact,
    HistoricalAuthorityDependency,
    validate_historical_authority_composition,
)
from app.services.historical_authority_retention import (
    HistoricalBoundaryClassification,
    HistoricalReconstructionAvailability,
    RetainedAuthority,
    RetentionClass,
    archived_payload,
    classify_historical_availability,
    create_authority_archive,
    restore_authority_archive,
)
from scripts.docs.build_t16e_artifacts import (
    SNAPSHOT_FIELDS,
    finding_snapshot,
    phase7_reconciliation,
    residual_handoff,
)
from scripts.qa.committed_source_identity import (
    classify_legacy_checkout_hash,
    committed_blob_sha256,
    source_freeze,
)

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "docs/remediation/calculation-lineage"
T16D_HEAD = "5a2f92b14f18303c6cb83295d14dc5c2b9dcdb86"

ARTIFACT_GROUPS = {
    "t16a_sha": (
        "T16A_original_context_reconstruction_foundation.md",
        "T16A_reconstruction_feasibility_inventory.json",
        "T16A_historical_authority_matrix.csv",
        "T16A_phase7_handoff.json",
    ),
    "t16b_sha": (
        "T16B_setup_lifecycle_alert_reconstruction.md",
        "T16B_setup_lifecycle_alert_reconstruction_certification.json",
        "T16B_legacy_reconstruction_boundaries.csv",
        "T16B_phase7_handoff.json",
    ),
    "t16c_sha": (
        "T16C_cross_domain_authority_composition.md",
        "T16C_cross_domain_compatibility_matrix.csv",
        "T16C_cross_domain_authority_certification.json",
        "T16C_phase7_handoff.json",
    ),
    "t16d_sha": (
        "T16D_legacy_retention_governance_boundaries.md",
        "T16D_retention_authority_matrix.csv",
        "T16D_legacy_governance_boundaries.csv",
        "T16D_legacy_retention_governance_certification.json",
        "T16D_T16E_exact_handoff.json",
    ),
}

EXPECTED_INPUT_HASHES = {
    "T16A_original_context_reconstruction_foundation.md": (
        "79cfa0057f5c627615f1df6cd058304c07abe8013074b0e715da4c5deeb7de12"
    ),
    "T16A_reconstruction_feasibility_inventory.json": (
        "31c1ea270cb3efb602140d7ff6fff7b276f07108e4a7836b131e7f2acbc9a44a"
    ),
    "T16A_historical_authority_matrix.csv": (
        "c33bbd862f9a268ee19585a68e1ca91d1ec53659306331fedfbd7d016a013ff4"
    ),
    "T16A_phase7_handoff.json": (
        "9f3d291b3e6a1f38bc85fd86f344a9f6e866ba360ad85765e01dfa6dd8f4edd7"
    ),
    "T16B_setup_lifecycle_alert_reconstruction.md": (
        "8fe13a52462e7566dcbde655b95c3cc877e442881549fb353fd42b578a33fd0d"
    ),
    "T16B_setup_lifecycle_alert_reconstruction_certification.json": (
        "43d827ea285150e120c87c03d7137afe37e115effd7115da5a03a3fb3cec34bf"
    ),
    "T16B_legacy_reconstruction_boundaries.csv": (
        "6069a6bc9ad350445794d82b966520472519677590e2b0512a18486db1e6d171"
    ),
    "T16B_phase7_handoff.json": (
        "5ee59c8a14c385f24165bbe7b7de1c0fd21180feaddb528a45b03f96623fc013"
    ),
    "T16C_cross_domain_authority_composition.md": (
        "a451d5a73ac14fc0a5e26b161ec2d7934123d71fbfbe669af845adcca27f90cb"
    ),
    "T16C_cross_domain_compatibility_matrix.csv": (
        "3b843b1f4a4ef3e1bd5df9c8be3e6edb6f25b528d277b415289161ed188c76e5"
    ),
    "T16C_cross_domain_authority_certification.json": (
        "2ffb18cec18d3f9c3799c3f7e406c93ae8234865d07633ba00ec871b3a24e66e"
    ),
    "T16C_phase7_handoff.json": (
        "0b15e29539abdb1fb458a1072c36f6e97c9777ece858ffa4222ddab254c6aa90"
    ),
    "T16D_legacy_retention_governance_boundaries.md": (
        "56c9ffa132f41995e76bfa1847ca4907cc8aae592c14bbe103edbfa8832fba6c"
    ),
    "T16D_retention_authority_matrix.csv": (
        "cd9ed5caf8190b6a54af55b9ef63c8191b6be2a3b2a3b2d6aad0eef76cc3a8df"
    ),
    "T16D_legacy_governance_boundaries.csv": (
        "d8efe20befa44e2066856d53e2ff1f1b46448431b25dac70e04612603fc54eb1"
    ),
    "T16D_legacy_retention_governance_certification.json": (
        "88fe6b5f285048b6d4e7c936237135566c02e9e1019823b30352081237ac487e"
    ),
    "T16D_T16E_exact_handoff.json": (
        "dd638320d307d7fa870f7b78f502e9e8a99d4a7045953098943bb86c9edf87b9"
    ),
    "T15E_phase6_integration_certification.json": (
        "460456f0195433f8e4a3b9c8bb205e7087c6ed1a069d6673689c2ebcac23dac2"
    ),
}


def _json(name: str):
    return json.loads((ARTIFACTS / name).read_text(encoding="utf-8"))


def _csv(name: str) -> list[dict[str, str]]:
    with (ARTIFACTS / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _artifact_set_sha(names: tuple[str, ...]) -> str:
    payload = b"".join(
        name.encode()
        + b"\0"
        + committed_blob_sha256(
            ROOT, T16D_HEAD, f"docs/remediation/calculation-lineage/{name}"
        ).encode()
        + b"\n"
        for name in sorted(names)
    )
    return hashlib.sha256(payload).hexdigest()


def test_t16a_through_t16d_and_phase6_inputs_are_byte_identical() -> None:
    for name, expected in EXPECTED_INPUT_HASHES.items():
        classify_legacy_checkout_hash(
            ROOT, T16D_HEAD, f"docs/remediation/calculation-lineage/{name}", expected
        )


def test_phase7_reconciliation_conserves_all_twelve_findings() -> None:
    rows = phase7_reconciliation()
    assert len(rows) == len({row["finding_id"] for row in rows}) == 12
    assert {row["pre_phase7_status"] for row in rows} == {"PARTIAL"}
    assert Counter(row["final_status"] for row in rows) == Counter({"PARTIAL": 7, "CLOSED": 5})
    assert {row["current_path_status"] for row in rows} == {"CERTIFIED_SAFE"}
    assert all(row["residual"] and row["machine_boundary"] for row in rows)


def test_fresh_seventy_finding_snapshot_is_generated_not_hand_counted() -> None:
    generated = finding_snapshot()
    recorded = _csv("T16E_finding_status_after_phase7.csv")
    assert tuple(recorded[0]) == SNAPSHOT_FIELDS
    assert recorded == generated
    assert len(recorded) == len({row["finding_id"] for row in recorded}) == 70
    assert Counter(row["status"] for row in recorded) == Counter({"CLOSED": 63, "PARTIAL": 7})
    assert all(row["current_supported_path_safe"] == "YES" for row in recorded)
    assert not [row for row in recorded if row["status"] == "OPEN"]


def test_residual_handoff_contains_only_nonremediable_boundaries() -> None:
    expected = residual_handoff()
    recorded = _json("T16E_post_phase7_residual_handoff.json")
    assert recorded == expected
    assert recorded["active_supported_current_defects"] == 0
    assert recorded["residual_count"] == 7
    assert {item["finding_id"] for item in recorded["residuals"]} == {
        "CORE-005",
        "CORE-009",
        "CERI-008",
        "RANK-007",
        "SETUP-004",
        "SETUP-007",
        "WIN-006",
    }
    assert all(item["current_path_safe"] for item in recorded["residuals"])
    assert all(item["code_action_remaining"] == "none" for item in recorded["residuals"])


def test_current_state_substitution_matrix_accepts_zero_original_authorities() -> None:
    current_substitutions = (
        "current_provider",
        "current_currency_or_security_metadata",
        "current_fx_data",
        "current_earnings_schedule",
        "current_pricebar_revision",
        "current_configuration",
        "current_rules",
        "current_predecessor",
        "current_scope",
    )
    for substitution in current_substitutions:
        result = classify_historical_availability(
            required_authority=("retained_historical_authority", substitution),
            retained_authority=("retained_historical_authority",),
            missing_classification=(HistoricalBoundaryClassification.LEGACY_AUTHORITY_UNAVAILABLE),
            retrospective_available=True,
        )
        assert result.availability is HistoricalReconstructionAvailability.CURRENT_RULES_ONLY
        assert result.exact is False
        assert result.current_state_consulted is False
        assert result.missing_authority == (substitution,)


def test_cross_domain_and_negative_dependency_matrices_are_complete() -> None:
    matrix = _csv("T16C_cross_domain_compatibility_matrix.csv")
    positive = [row for row in matrix if row["dependency_type"] != "FORBIDDEN_ABSENT"]
    negative = [row for row in matrix if row["dependency_type"] == "FORBIDDEN_ABSENT"]
    assert len(CONTRACT_BY_KEY) == len(positive) == 31
    assert len(FORBIDDEN_DEPENDENCY_EDGES) == len(negative) == 7
    assert all("UNKNOWN" not in row["status"] for row in matrix)


def _composition_artifact(domain: str, artifact_id: str) -> HistoricalAuthorityArtifact:
    return HistoricalAuthorityArtifact(
        domain=domain,
        artifact_type="T16E_CERTIFICATION_EVIDENCE",
        artifact_id=artifact_id,
        evidence_fingerprint=f"evidence:{artifact_id}",
        calculation_identity="calculation-1",
        calculation_context="context-1",
        temporal_context="session-1",
        business_session="2026-09-14",
        business_cutoff="2026-09-14T20:00:00+00:00",
        calendar_identity="calendar-1",
        subject_scope="ticker:ACME",
        work_scope_identity="scope-1",
        refresh_cycle_identity="refresh-1",
        effective_configuration="configuration-1",
        readiness="readiness-1",
        source_lineage="source-1",
        source_revision="revision-1",
        predecessor_identity="predecessor-1",
        algorithm_schema="schema-1",
        rule_policy="rule-1",
        provider_revision="provider-1",
        semantic_output="75",
    )


def test_cross_domain_composition_is_set_based_for_1_50_and_200_edges() -> None:
    contract = CONTRACT_BY_KEY[("FUNDAMENTAL", "COMBINED", "behavioral_input")]
    for size in (1, 50, 200):
        dependencies = []
        for index in range(size):
            producer = _composition_artifact("FUNDAMENTAL", f"producer-{index}")
            consumer = _composition_artifact("COMBINED", f"consumer-{index}")
            dependencies.append(
                HistoricalAuthorityDependency(
                    contract,
                    f"fundamental-{index}",
                    consumer,
                    producer,
                    producer,
                )
            )
        result = validate_historical_authority_composition(dependencies)
        assert result.overall_exact is True
        assert len(result.edge_results) == size


def test_archive_round_trip_preserves_every_material_identity_dimension() -> None:
    authority = RetainedAuthority(
        authority_type="PHASE7_CERTIFIED_DECISION",
        semantic_id="decision:1",
        content_fingerprint="sha256:decision-1",
        material_to_reconstruction=True,
        retention_class=RetentionClass.IMMUTABLE_AUTHORITY,
        pinned_by=("manifest:1",),
    )
    payload = {
        "semantic_ids": ["decision:1", "source:S1", "revision:R1"],
        "content_hashes": ["sha256:decision-1", "sha256:source-1"],
        "configuration_identity": "config:K1",
        "scope_identity": "scope:1",
        "refresh_identity": "refresh:1",
        "predecessor_links": ["decision:0"],
        "schema_identity": "phase7-schema-v1",
        "manifest_fingerprint": "manifest-sha256-1",
        "result_fingerprint": "result-sha256-1",
    }
    archive = create_authority_archive(
        archive_id="archive:1",
        source_manifest_fingerprint="manifest-sha256-1",
        authorities=(authority,),
        payload=payload,
    )
    restored = restore_authority_archive(archived_payload(archive))
    assert restored.authorities == archive.authorities
    assert restored.payload == payload
    assert restored.archive_fingerprint == archive.archive_fingerprint


def test_phase6_and_phase5_machine_certificates_remain_closed() -> None:
    phase6 = _json("T15E_phase6_integration_certification.json")
    callers = _json("T14D_caller_unification_certification.json")
    families = _json("T14D_operation_family_certification.json")
    assert phase6["phase6_certified"] is True
    assert phase6["lost_finding_count"] == 0
    assert phase6["active_current_partial_defect_count"] == 0
    assert callers["verdict"] == "PASS"
    assert callers["XINT-006"] == "CLOSED"
    assert callers["INV-ENTRY-001"] == "CLOSED"
    assert not any(callers["finite_blockers"].values())
    assert families["verdict"] == "PASS"
    assert families["exact_callers"] == 252
    assert families["operation_family_count"] == 220
    assert families["incomplete_operation_family_ids"] == []


def test_t16e_certificate_matches_frozen_source_and_artifact_sets() -> None:
    certificate = _json("T16E_phase7_integration_certification.json")
    freeze = source_freeze(ROOT, "d5066e9")
    required = {
        "phase7_certified",
        "source_sha256",
        "test_source_sha256",
        *ARTIFACT_GROUPS,
        "phase7_findings_assigned",
        "phase7_findings_accounted",
        "phase7_findings_lost",
        "active_supported_current_defects",
        "original_context_status",
        "retrospective_status",
        "current_mode_status",
        "reconstruct_invariant_statuses",
        "proof_boundary_status",
        "archive_status",
        "retention_status",
        "purge_status",
        "setup_status",
        "lifecycle_status",
        "alert_status",
        "cross_domain_status",
        "legacy_partial_findings",
        "phase6_regression",
        "phase5_regression",
        "postgresql_status",
        "browser_status",
        "repository_status",
        "static_status",
        "finding_summary_70",
        "final_verdict",
    }
    assert required <= set(certificate)
    assert certificate["phase7_certified"] is True
    assert certificate["frozen_head"] == T16D_HEAD
    assert certificate["source_file_count"] == freeze["implementation_count"]
    assert certificate["test_source_file_count"] == freeze["test_count"]
    for field, names in ARTIFACT_GROUPS.items():
        assert certificate[field] == _artifact_set_sha(names)
    assert certificate["phase7_findings_assigned"] == 12
    assert certificate["phase7_findings_accounted"] == 12
    assert certificate["phase7_findings_lost"] == 0
    assert certificate["active_supported_current_defects"] == 0
    assert certificate["finding_summary_70"] == {
        "CLOSED": 63,
        "PARTIAL": 7,
        "OPEN": 0,
        "TOTAL": 70,
    }
    assert certificate["final_verdict"] == "PASS_PHASE7_OVERALL_CERTIFIED"


def test_t16e_report_has_all_required_numbered_sections() -> None:
    report = (ARTIFACTS / "T16E_phase7_integration_certification.md").read_text(encoding="utf-8")
    titles = [
        "Executive verdict",
        "Baselines",
        "Frozen source",
        "Phase-7 architecture",
        "T16A recertification",
        "T16B recertification",
        "T16C recertification",
        "T16D recertification",
        "Reconstruction modes",
        "Exactness gate",
        "No-silent-downgrade",
        "Setup reconstruction",
        "Lifecycle reconstruction",
        "Alert reconstruction",
        "Historical Technical insufficiency",
        "Cross-domain proof boundaries",
        "Same-value/different-evidence attack",
        "Temporal/config/scope compatibility",
        "Archive semantics",
        "Retention semantics",
        "Purge semantics",
        "Legacy permanent unavailability",
        "Prospective reconstructability",
        "CORE-005",
        "CERI-010",
        "RANK-007",
        "WIN-006",
        "XINT-010",
        "Phase-7 invariants",
        "Phase-6 regression",
        "Phase-5 regression",
        "Negative dependencies",
        "Performance",
        "PostgreSQL",
        "Browser/E2E",
        "Full repository",
        "Static/inventory",
        "Phase-7 finding reconciliation",
        "Original 70 findings after Phase 7",
        "Residual governance risks",
        "Production safety",
        "Final verdict",
    ]
    for number, title in enumerate(titles, 1):
        assert f"## {number}. {title}" in report


def test_t16e_has_no_phase7_implementation_delta() -> None:
    changed = subprocess.check_output(
        [
            "git",
            "diff",
            "--name-only",
            T16D_HEAD,
            "d5066e9",
            "--",
            "app",
            "alembic",
            "config",
        ],
        cwd=ROOT,
        text=True,
    ).splitlines()
    assert changed == []
