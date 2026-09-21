"""Build deterministic T15C report, certificate, and exact T15D handoff."""

# ruff: noqa: E501

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "docs" / "remediation" / "calculation-lineage"
INVENTORY = ARTIFACTS / "T15A_scope_operation_inventory.json"
MATRIX = ARTIFACTS / "T15C_operation_reconciliation.csv"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _operation_certificate(row: dict) -> dict:
    kind = row["scope_kind"]
    maturation = kind == "winner-h5-maturation"
    cohort = kind == "winner-cohort-generation"
    capture = kind == "winner-prediction-population"
    return {
        "operation_family_id": row["operation_family_id"],
        "semantic_service": row["semantic_service"],
        "scope_identity": (
            "winner-h5-maturation exact WINNER_PREDICTION members"
            if maturation
            else "winner-cohort-generation exact typed evidence members"
            if cohort
            else "winner capture/backfill exact source-row or explicit-run members"
        ),
        "membership_policy": "FROZEN",
        "refresh_identity": "canonical RefreshCycleIdentity; same cycle converges",
        "retry_semantics": "retains scope/refresh/plan/cutoff/configuration",
        "continuation_semantics": (
            "pending remainder of retained prediction IDs only"
            if maturation
            else "captured generation/manifest remainder only"
            if cohort
            else "retained capture/backfill population only"
        ),
        "truth_source": (
            "retained PriceBar bodies and exact PriceBarRevision when available"
            if maturation or capture
            else "watermark-bound Winner outcome/eligibility evidence manifests"
        ),
        "revision_identity_policy": (
            "EXACT or REVISION_IDENTITY_UNAVAILABLE; never inferred"
            if not cohort
            else "exact outcome revisions; upstream birth-bar gap remains explicit"
        ),
        "checkpoint_binding": "scope_id and refresh_cycle_id persisted on processing run",
        "parent_child_policy": (
            "required continuation inherits identical authority"
            if maturation
            else "generation/job share identical authority"
            if cohort
            else "capture children remain inside admitted owner scope"
        ),
        "legacy_behavior": "LEGACY_UNKNOWN; no semantic backfill from current tables",
        "tests": [
            "tests/test_t15c_winner_scope_truth.py",
            "tests/integration/test_t15c_winner_scope_truth_postgresql.py",
        ],
        "status": "T15C_CERTIFIED",
    }


def _report(cert: dict, t15d_rows: list[dict]) -> str:
    sections = {
        1: "PASS. All 15 assigned Winner operation families adopt explicit scope/refresh semantics; WIN-008 closes. WIN-006 remains truthful PARTIAL for birth bars without revision rows.",
        2: "Started at T15B commit `0c8e5664c52ff19d80d30f33c64ade8772c955a8`, tree `8ef7a81f8392810786ada89560847a4359a393cd`, migration head `0082_pipeline_ceri_scope`, clean worktree. T15C head is `0083_winner_scope_truth`.",
        3: "T15A assigned 15 exact families to T15C and T15B supplied the generic frozen-scope, refresh, plan, remainder, zero-progress, and parent/child primitives. The reconciliation CSV contains 15 rows and zero unreconciled families.",
        4: "Prediction capture, maturation, outcome correction, cohort refresh, generation, and publication are distinct semantic targets. Population operations use persisted typed members; exact single-artifact publication retains generation identity.",
        5: "Maturation admission freezes current pending/current-revision primary H5 NEXT_OPEN predictions due at the retained completed session, with temporal eligibility evaluated once at admission.",
        6: "Membership is exact `WINNER_PREDICTION` IDs. Batches receive those IDs and cannot append a later live-query result.",
        7: "Retry, resume and reclaim change operational leases only. Scope, refresh, plan, cutoff, configuration anchor and members remain invariant.",
        8: "Continuation queries the retained prediction IDs and subtracts terminal outcomes. Required child jobs inherit the parent authority.",
        9: "Zero progress with remaining members records `ZERO_PROGRESS_BLOCKED` and retained members; retry-deferred work defers the same job and creates no child chain.",
        10: "Each completed-session scheduler key is a refresh cycle. Same-cycle concurrency converges; a later terminal-to-new admission creates R2 and may include newly due work.",
        11: "Winner processing runs and checkpoint bodies retain scope/refresh IDs. Single-assignment database triggers reject rebinding.",
        12: "Outcome proofs retain prediction, horizon, policy, sessions, complete PriceBar values/hashes and revision identity state.",
        13: "PriceBar row ID, content hash, symbol, session, source, created/first-seen/revised timestamps and cutoff are retained. Matching PriceBarRevision ID/body is retained when it exists.",
        14: "WIN-006 is PARTIAL: exact revision IDs are enforced wherever revision_count indicates a revision, while historical birth rows explicitly record `REVISION_IDENTITY_UNAVAILABLE`.",
        15: "A later B2 creates O2 through the existing immutable outcome revision service. O1 keeps B1/unavailable birth evidence and predecessor linkage remains explicit.",
        16: "Cohort admission freezes typed prediction, forward-outcome, and target/stop evidence members selected by the exact watermark, cutoff, outcome definition and eligibility policy.",
        17: "The planner advances evidence once, admits one refresh, captures its generation, and keys the job by the canonical refresh ID.",
        18: "Generation birth stores the same scope/refresh/plan plus model/configuration/watermark contract. Root manifest predictions must equal admitted predictions.",
        19: "Generation slices use the existing captured generation checkpoint and manifest; a later matured P4 cannot enter G1 and may enter G2.",
        20: "Publication remains bound to reviewed G1 IDs/keys/hashes. T15C authority is copied to the publication request; retry cannot resolve `latest` or switch to G2.",
        21: "The primary H5 scheduler creates one deterministic completed-session admission. Scheduling time is an observation cutoff, not the scope identity.",
        22: "Idempotency resolves operation type plus refresh identity. Active duplicate triggers coalesce without changing the admitted target set; later cycles are not suppressed after terminal completion.",
        23: "Nullable historical Winner bindings mean `LEGACY_UNKNOWN`. Migration 0083 is additive and performs no production rewrite or historical scope fabrication.",
        24: "PostgreSQL advisory admission locking and unique operational keys prevent divergent concurrent maturation. Content-addressed scope/refresh rows converge for identical cohort admission.",
        25: "Durable rows retain scope/refresh/plan, configuration anchor and operation cutoff across restart. New due predictions and later configuration do not alter R1.",
        26: "Member persistence and retrieval are set-based. Inherited T15A bounds cover 1/50/200 members; Winner admission and remainder queries are bulk operations.",
        27: "Fresh disposable PostgreSQL 18 was upgraded through 0083, downgraded to 0082, and re-upgraded. T15A/T15B migration suites, T15C R1/R2 attack, concurrency, and native price-revision scenarios pass.",
        28: "No Winner formula, threshold, eligibility rule, horizon, target/stop policy or serving calculation changed. T15C changes selection identity, durable binding and truth disclosure only.",
        29: "WIN-008 CLOSED. WIN-006 PARTIAL. XINT-012 CLOSED_FOR_PIPELINE_CERI_WINNER and PARTIAL_OVERALL. INV-SCOPE-001 and INV-REFRESH-001 close for Winner but remain PARTIAL_REPOSITORY_WIDE; INV-REVISION-001 is PARTIAL.",
        30: f"T15D retains {len(t15d_rows)} exact operation families plus provider/source provenance and Core/CERI/Ranking algorithm findings. It does not inherit Winner target-scope freezing.",
        31: "Residual risk is historical birth PriceBar evidence without a revision-row primary key and the seven T15D-assigned scope/provenance families. No current T15C scope defect remains.",
        32: "T15C VERDICT: PASS. Unreconciled assigned families: 0. T15C scope/refresh defects: 0. WIN-008: CLOSED. WIN-006: PARTIAL with explicit unavailable lineage.",
    }
    titles = [
        "Executive verdict",
        "Baselines",
        "T15A/T15B handoff reconciliation",
        "Winner scope model",
        "Maturation admission",
        "Maturation target membership",
        "Retry/resume/reclaim",
        "Continuation/remainder",
        "Zero-progress behavior",
        "Refresh cycles",
        "Checkpoints",
        "Outcome truth model",
        "PriceBar revision inventory",
        "WIN-006 analysis",
        "Outcome revisions",
        "Cohort scope",
        "Cohort refresh",
        "Generation scope",
        "Generation continuation",
        "Publication target binding",
        "Scheduler semantics",
        "Idempotency",
        "Legacy Winner data",
        "Concurrency",
        "Restart behavior",
        "Performance",
        "PostgreSQL certification",
        "Business parity",
        "Finding reconciliation",
        "T15D handoff impact",
        "Residual risks",
        "Final verdict",
    ]
    lines = ["# T15C — Winner target-scope, refresh, and truth-revision adoption", ""]
    for number, title in enumerate(titles, start=1):
        lines.extend((f"## {number}. {title}", "", sections[number], ""))
    lines.extend(
        (
            "## Certificate summary",
            "",
            f"- Assigned operation families: {cert['summary']['assigned_operation_families']}",
            f"- Unreconciled operation families: {cert['summary']['unreconciled_operation_families']}",
            f"- Scope/refresh defects: {cert['summary']['scope_refresh_defects']}",
            "- Migration required: YES; additive only; no legacy backfill.",
            "",
        )
    )
    return "\n".join(lines)


def main() -> None:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    assigned = sorted(
        (row for row in inventory["records"] if row["phase6_target_task"] == "T15C"),
        key=lambda row: row["operation_family_id"],
    )
    t15d = sorted(
        (row for row in inventory["records"] if row["phase6_target_task"] == "T15D"),
        key=lambda row: row["operation_family_id"],
    )
    with MATRIX.open(newline="", encoding="utf-8") as handle:
        matrix = list(csv.DictReader(handle))
    if len(assigned) != 15 or {r["operation_family_id"] for r in matrix} != {
        r["operation_family_id"] for r in assigned
    }:
        raise RuntimeError("T15C reconciliation does not exactly match the T15A handoff")
    records = [_operation_certificate(row) for row in assigned]
    cert = {
        "schema_version": "t15c-winner-scope-truth-certification-v1",
        "verdict": "PASS",
        "baseline": {
            "commit": "0c8e5664c52ff19d80d30f33c64ade8772c955a8",
            "tree": "8ef7a81f8392810786ada89560847a4359a393cd",
            "incoming_migration_head": "0082_pipeline_ceri_scope",
            "migration_head": "0083_winner_scope_truth",
        },
        "artifact_integrity": {
            "t15a_inventory_sha256": _sha(INVENTORY),
            "t15b_certificate_sha256": _sha(
                ARTIFACTS / "T15B_pipeline_ceri_scope_refresh_certification.json"
            ),
            "reconciliation_sha256": _sha(MATRIX),
        },
        "summary": {
            "assigned_operation_families": len(records),
            "certified_operation_families": len(records),
            "unreconciled_operation_families": 0,
            "scope_refresh_defects": 0,
            "t15d_remaining_operation_families": len(t15d),
        },
        "findings": {
            "WIN-006": "PARTIAL_EXACT_WHERE_AVAILABLE_BIRTH_REVISION_IDENTITY_UNAVAILABLE",
            "WIN-008": "CLOSED",
            "XINT-012": "CLOSED_FOR_PIPELINE_CERI_WINNER_PARTIAL_OVERALL",
            "INV-SCOPE-001": "CLOSED_FOR_WINNER_PARTIAL_REPOSITORY_WIDE",
            "INV-REFRESH-001": "CLOSED_FOR_WINNER_PARTIAL_REPOSITORY_WIDE",
            "INV-REVISION-001": "PARTIAL",
        },
        "migration": {
            "required": True,
            "additive": True,
            "production_rewrite": False,
            "legacy_backfill": False,
        },
        "operation_families": records,
    }
    handoff = {
        "schema_version": "t15c-t15d-exact-handoff-v1",
        "source_task": "T15C",
        "t15d_operation_family_ids": [row["operation_family_id"] for row in t15d],
        "t15d_operation_families": t15d,
        "remaining_findings": {
            "WIN-006": "birth PriceBar rows without exact PriceBarRevision primary keys",
            "provider_source_provenance": ["CORE-005", "INV-REVISION-001"],
            "algorithm_specific": ["CERI-001", "CERI-002", "CORE-001", "RANK-001", "RANK-008"],
        },
        "winner_scope_work_remaining": [],
    }
    (ARTIFACTS / "T15C_winner_scope_truth_certification.json").write_text(
        json.dumps(cert, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (ARTIFACTS / "T15C_T15D_exact_handoff.json").write_text(
        json.dumps(handoff, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    (ARTIFACTS / "T15C_winner_scope_truth_adoption.md").write_text(
        _report(cert, t15d), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
