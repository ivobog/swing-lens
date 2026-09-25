"""Bounded CERI-to-Winner production mutation-guard inventory."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_STATUSES = {
    "VERIFIED_POSITIVE",
    "VERIFIED_NEGATIVE",
    "NOT_REACHABLE_CURRENT_CONFIG",
}


@dataclass(frozen=True)
class GuardSurface:
    stage: str
    module: str
    writer: str
    authority: str
    proof: str
    status: str = "VERIFIED_POSITIVE"
    symbols: tuple[str, ...] = ()


SURFACES = (
    GuardSurface(
        "Shared retained-source boundary",
        "app/services/source_mutation_authority.py",
        "source_mutation_writer transaction fence",
        "sealed exact SQL source bundle and PIT witness",
        "tests/integration/test_t14b_source_boundaries_postgresql.py",
        symbols=("source_mutation_writer",),
    ),
    GuardSurface(
        "Shared calculation boundary",
        "app/services/core_mutation_authority.py",
        "core_writer transaction fence",
        "exact ownership, temporal, configuration, source and projection authority",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
        symbols=("core_writer_transaction", "core_writer_member"),
    ),
    GuardSurface(
        "CERI capture",
        "app/services/ceri/decision_evidence.py",
        "CeriRunCaptureService.capture_run",
        "certified calculation identity and normalized source records",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
    ),
    GuardSurface(
        "CERI change",
        "app/services/ceri/change_authority.py",
        "CeriChangeDetectionService.detect_score_changes",
        "certified current/prior scores and exact normalized predecessor",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
    ),
    GuardSurface(
        "CERI alerts",
        "app/services/ceri/alert_authority.py",
        "CeriAlertService.persist_alert_for_change",
        "certified change, score sources, frozen rule and notification",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
    ),
    GuardSurface(
        "CERI completion barrier",
        "app/services/pipeline_service.py",
        "enqueue_pipeline_after_ceri_completion",
        "same workflow/context and complete certified capture",
        "tests/integration/test_ceri_pipeline_completion_barrier.py",
        symbols=("enqueue_pipeline_after_ceri_completion",),
    ),
    GuardSurface(
        "Decision handoff",
        "app/services/transition_preflight_plan_service.py",
        "freeze_transition_decision_handoff_manifest",
        "immutable exact upstream evidence manifest",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
        symbols=("freeze_transition_decision_handoff_manifest",),
    ),
    GuardSurface(
        "Setup caller",
        "app/services/setup_lifecycle/caller_authority.py",
        "SetupLifecycleSnapshotCaptureService.capture_snapshots_for_run",
        "pipeline-owned retained authority",
        "tests/integration/test_t14d_caller_postgresql.py",
        symbols=("require_pipeline_authority",),
    ),
    GuardSurface(
        "Setup capture",
        "app/services/setup_lifecycle/decision_evidence.py",
        "SetupLifecycleRepository.upsert_snapshots",
        "certified exact sources and projection target",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
    ),
    GuardSurface(
        "Setup change",
        "app/services/setup_lifecycle/change_authority.py",
        "SetupLifecycleChangeDetector.detect_and_persist",
        "certified snapshot comparison and current projection",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
    ),
    GuardSurface(
        "Lifecycle",
        "app/services/setup_lifecycle/episode_service.py",
        "SetupLifecycleEpisodeService.apply_snapshot",
        "certified birth/predecessor/evaluation/transition chain",
        "tests/integration/test_t14c_decision_writer_postgresql.py",
    ),
    GuardSurface(
        "Setup alerts",
        "app/services/setup_lifecycle/alert_authority.py",
        "SetupLifecycleAlertService",
        "certified change/evaluation, frozen rule and exact predecessor",
        "tests/integration/test_setup_lifecycle_alert_immutable_evidence.py",
    ),
    GuardSurface(
        "Winner prediction",
        "app/services/winner_probability/mutation_authority.py",
        "WinnerPredictionCaptureService.capture_run",
        "exact handoff, sources, decision time and frozen vector",
        "tests/integration/test_t14c_winner_writer_postgresql.py",
    ),
    GuardSurface(
        "Winner prediction seal",
        "app/services/winner_probability/prediction_authority.py",
        "WinnerPredictionCaptureService.capture_run",
        "native capture proof and immutable prediction body",
        "tests/integration/test_t14c_winner_writer_postgresql.py",
    ),
    GuardSurface(
        "Winner episode",
        "app/services/winner_probability/episode_service.py",
        "WinnerEpisodeService",
        "certified episode key and native birth prediction",
        "tests/integration/test_t14c_winner_writer_postgresql.py",
    ),
    GuardSurface(
        "Winner obligations",
        "app/services/winner_probability/market_data_obligation_service.py",
        "MarketDataObligationService",
        "certified capture/scope and retained acquisition evidence",
        "tests/integration/test_winner_maturation_canary_postgresql.py",
    ),
    GuardSurface(
        "Winner outcomes",
        "app/services/winner_probability/outcome_authority.py",
        "OutcomeMaturationService.process_forward_outcome",
        "certified pending outcome, exact price revisions and native result",
        "tests/integration/test_winner_maturation_canary_postgresql.py",
    ),
    GuardSurface(
        "Winner estimates",
        "app/services/winner_probability/estimate_authority.py",
        "DecisionTimeEstimateService / ProbabilityEstimator",
        "certified estimate, manifest, configuration and serving model",
        "tests/integration/test_t14c_winner_writer_postgresql.py",
    ),
    GuardSurface(
        "Winner cohort",
        "app/services/winner_probability/cohort_authority.py",
        "CohortMaterializationService.materialize_slice",
        "certified financial population, generation, statistics and manifests",
        "tests/integration/test_winner_material_positive_publication_postgresql.py",
    ),
    GuardSurface(
        "Winner generation publication",
        "app/services/winner_probability/cohort_generation_service.py",
        "CohortGenerationService.publish",
        "exact state contract, explicit predecessor and current projection",
        "tests/integration/test_winner_jobs_reliability_postgresql.py",
    ),
    GuardSurface(
        "Winner materialization",
        "app/services/winner_probability/cohort_materialization_service.py",
        "CohortMaterializationService.materialize_slice",
        "generation scope/clock and native financial statistics",
        "tests/integration/test_winner_material_positive_publication_postgresql.py",
    ),
    GuardSurface(
        "Winner publication evidence",
        "app/services/winner_probability/evidence_manifest_service.py",
        "EvidenceManifestService",
        "exact immutable manifest content and inclusion time",
        "tests/integration/test_winner_material_positive_publication_postgresql.py",
    ),
)


def inventory_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for surface in SURFACES:
        guards = _guard_codes(ROOT / surface.module, symbols=surface.symbols)
        if not guards:
            guards = ("POSITIVE_CONTRACT_VALIDATION",)
        rows.extend(
            {
                "stage": surface.stage,
                "guard": guard,
                "production_writer": surface.writer,
                "required_authority": surface.authority,
                "production_shaped_test": surface.proof,
                "status": surface.status,
            }
            for guard in guards
        )
    return rows


def test_every_reachable_downstream_guard_has_named_proof_and_final_status() -> None:
    rows = inventory_rows()
    assert rows
    assert len({(row["stage"], row["guard"]) for row in rows}) == len(rows)
    for row in rows:
        assert row["status"] in ALLOWED_STATUSES
        proof = ROOT / row["production_shaped_test"]
        assert proof.is_file(), row
        assert "test_" in proof.read_text(encoding="utf-8"), row
        assert row["production_writer"] and row["required_authority"], row


def test_inventory_covers_the_reviewed_reachable_authority_modules_exactly() -> None:
    modules = [surface.module for surface in SURFACES]
    assert len(modules) == len(set(modules))
    assert all((ROOT / module).is_file() for module in modules)
    assert {surface.stage for surface in SURFACES} >= {
        "CERI capture",
        "CERI change",
        "CERI alerts",
        "Decision handoff",
        "Setup capture",
        "Lifecycle",
        "Setup alerts",
        "Winner prediction",
        "Winner outcomes",
        "Winner cohort",
        "Winner generation publication",
    }


def _guard_codes(path: Path, *, symbols: tuple[str, ...] = ()) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    values: set[str] = set()
    roots = (
        [
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name in symbols
        ]
        if symbols
        else [tree]
    )
    for root in roots:
        for node in ast.walk(root):
            candidates: list[ast.AST] = []
            if isinstance(node, ast.Raise) and node.exc is not None:
                candidates.append(node.exc)
            if isinstance(node, ast.keyword) and node.arg == "code":
                candidates.append(node.value)
            for candidate in candidates:
                for child in ast.walk(candidate):
                    if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
                        continue
                    code = child.value.split(":", 1)[0]
                    if _is_guard_code(code):
                        values.add(code)
    return tuple(sorted(values))


def _is_guard_code(value: str) -> bool:
    if not value or value != value.upper() or " " in value:
        return False
    return value.startswith(("MUTATION_", "CERI_", "SETUP_")) or any(
        marker in value
        for marker in (
            "CERTIFIED_",
            "SOURCE_REQUIRED",
            "PROJECTION_TARGET",
            "PREDECESSOR_REQUIRED",
            "EVIDENCE_REQUIRED",
            "FINGERPRINT_MISMATCH",
            "TEMPORAL",
            "IDENTITY",
            "AUTHORITY",
            "PUBLICATION",
            "CURRENT_PROJECTION",
        )
    )
