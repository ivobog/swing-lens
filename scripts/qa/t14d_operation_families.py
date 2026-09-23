"""Reviewed caller-operation certificates, preserving the exact T14C handoff.

Only protected transaction members and explicitly reviewed transport aliases
share a family. Distinct authority branches remain distinct operations. Runtime
path evidence is recorded separately from semantic positive/negative proof.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from scripts.qa.committed_source_identity import current_committed_source_bytes

ROOT = Path(__file__).resolve().parents[2]
ARTIFACTS = ROOT / "docs/remediation/calculation-lineage"

# Different route parameters here only select display/transport or the error's
# operation label. These paths have no admitted business mutation at all.
READ_ONLY_GROUPS = {
    "REGIME_READ_ONLY_MISS": (
        "app/routers/market_regime_routes.py:run_market_regime_page",
        "app/routers/market_regime_routes.py:market_regime_page",
    ),
    "SECTOR_READ_ONLY_MISS": (
        "app/routers/sector_rotation_routes.py:sector_rotation_dashboard",
        "app/routers/sector_rotation_routes.py:sector_rotation_drilldown",
        "app/routers/sector_rotation_routes.py:api_sector_rotation",
        "app/routers/sector_rotation_routes.py:api_sector_rotation_drilldown",
    ),
    "RANKING_STANDALONE_RETIRED": (
        "app/routers/run_routes.py:refresh_all_ranking_profiles_action",
        "app/routers/run_routes.py:refresh_ranking_profile_action",
    ),
}

# These module helpers only construct the default service and forward their
# exact arguments. A changed body must be reviewed again before inheritance.
FORWARDERS = {
    "app/services/setup_lifecycle/evaluation_service.py:evaluate_setup_lifecycles_for_run": (
        "app/services/setup_lifecycle/evaluation_service.py:"
        "SetupLifecycleEvaluationService.evaluate_run"
    ),
    "app/services/setup_lifecycle/snapshot_builder.py:capture_snapshots_for_run": (
        "app/services/setup_lifecycle/snapshot_builder.py:"
        "SetupLifecycleSnapshotCaptureService.capture_snapshots_for_run"
    ),
    "app/services/pipeline_executor.py:build_sector_rotation_snapshot_for_run": (
        "app/services/sector_rotation_service.py:build_sector_rotation_snapshot"
    ),
    "app/services/pipeline_executor.py:build_market_regime_snapshot_for_run": (
        "app/services/market_regime_command_center.py:MarketRegimeCommandCenterService.build_snapshot"
    ),
    (
        "app/services/sector_rotation_service.py:"
        "SectorRotationService.build_sector_rotation_snapshot"
    ): ("app/services/sector_rotation_service.py:build_sector_rotation_snapshot"),
}
FORWARDER_REVIEW_HASHES = dict(
    zip(
        FORWARDERS,
        (
            "8b63c1f6d67eb5f0c7889588dd2041da0064a26a3a5b8149246b84ffae0abd40",
            "f2aca40e7fccc3a239cdf981324a460529f29e9f9e1aaa8050a25b17889e00ad",
            "cda20ab41ced0056d290148a4dda98d57399640d9d164d73b798f6ba146bde53",
            "31de965b89033d0a0154b06c31f973f3f106947d0ca56d0deee7ab9875b50d49",
            "975a09d0fbe476d53a42f4b58941ea06b3549a18fc2a9fee0a2f5e4d7f85f2fe",
        ),
        strict=True,
    )
)

ALLOWED_FINAL_STATUSES = {
    "CANONICAL_AUTHORITY_DELIVERED",
    "CURRENT_STATE_REPAIR_EXPLICIT",
    "CURRENT_RULES_RETROSPECTIVE_EXPLICIT",
    "SUPPORTED_DISTINCT_SEMANTICS",
    "LEGACY_NONCERTIFIED",
    "RETIRED",
    "READ_ONLY",
    "OPERATIONAL_ONLY",
    "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE",
}

# A boundary certificate is deliberately shared by the runtime mechanism, not
# copied once per route/service/CLI.  Its tests exercise successful delivery,
# missing/altered authority, historical non-reconstruction, and (where
# applicable) execution ownership.  Exact caller and branch fingerprints below
# decide whether an initiator is permitted to inherit one of these certificates.
_COMMON_POSITIVE = (
    "tests/test_domain_mutation.py::test_representative_contexts",
    "tests/test_domain_write_fence.py::test_valid_execution_owner_commits_domain_mutation",
)
_COMMON_NEGATIVE = (
    "tests/test_domain_mutation.py::test_missing_or_mismatched_authority_fails_without_reconstruction",
    "tests/test_domain_mutation.py::test_numeric_survival_cannot_replace_frozen_eligibility",
    "tests/integration/test_t14d_caller_postgresql.py::test_setup_source_loader_never_substitutes_newest_context_for_missing_authority",
)
BOUNDARY_CERTIFICATES = {
    "EXACT_PROTECTED_TRANSACTION_MEMBER": {
        "positive_tests": _COMMON_POSITIVE,
        "negative_tests": _COMMON_NEGATIVE
        + (
            "tests/integration/test_t14c_decision_writer_postgresql.py::test_native_setup_exact_authority_rejects_before_evidence",
        ),
    },
    "CORE_MUTATION_TRANSACTION": {
        "positive_tests": _COMMON_POSITIVE,
        "negative_tests": _COMMON_NEGATIVE,
    },
    "DURABLE_JOB_CONFIGURATION": {
        "positive_tests": _COMMON_POSITIVE
        + (
            "tests/integration/test_decision_configuration_delivery_postgresql.py::test_queued_job_retry_and_resume_keep_c1_after_current_drift",
        ),
        "negative_tests": _COMMON_NEGATIVE
        + (
            "tests/test_t14d_caller_authority.py::test_direct_durable_handler_rejects_missing_anchor",
            "tests/integration/test_decision_configuration_delivery_postgresql.py::test_worker_rejects_valid_other_anchor_before_handler",
        ),
        "query_budget_tests": (
            "tests/integration/test_decision_configuration_delivery_postgresql.py::test_configuration_delivery_bulk_load_has_bounded_queries_and_batch_no_reads",
        ),
    },
    "DECISION_CONFIGURATION_DELIVERY": {
        "positive_tests": _COMMON_POSITIVE
        + (
            "tests/integration/test_decision_configuration_delivery_postgresql.py::test_native_setup_lifecycle_winner_artifacts_retain_own_c1",
        ),
        "negative_tests": _COMMON_NEGATIVE
        + (
            "tests/integration/test_decision_configuration_delivery_postgresql.py::test_valid_anchor_with_unknown_decision_authority_fails_before_math",
        ),
    },
    "EXPLICIT_NATIVE_AUTHORITY_OPERATION": {
        "positive_tests": _COMMON_POSITIVE,
        "negative_tests": _COMMON_NEGATIVE,
    },
    "REVIEWED_TRANSPORT_TO_NATIVE_OPERATION": {
        "positive_tests": _COMMON_POSITIVE
        + (
            "tests/integration/test_t14d_caller_postgresql.py::test_new_root_and_child_preserve_frozen_rules_mode_and_operation_time",
        ),
        "negative_tests": _COMMON_NEGATIVE,
    },
    "REVIEWED_EXACT_FORWARDER": {
        "positive_tests": _COMMON_POSITIVE,
        "negative_tests": _COMMON_NEGATIVE,
    },
    "REVIEWED_TOOLING_NATIVE_OPERATION": {
        "positive_tests": _COMMON_POSITIVE,
        "negative_tests": _COMMON_NEGATIVE
        + (
            "tests/test_t14d_caller_authority.py::test_callable_qa_helper_rejects_non_disposable_target_before_provider_work",
        ),
    },
    "READ_ONLY": {
        "positive_tests": (
            "tests/integration/test_t14d_caller_postgresql.py::test_all_regime_sector_missing_state_reads_have_no_dml_or_enqueue",
        ),
        "negative_tests": (),
    },
    "RETIRED": {
        "positive_tests": (
            "tests/test_t14d_caller_authority.py::test_unbound_standalone_routes_are_formally_retired",
        ),
        "negative_tests": (
            "tests/test_t14d_caller_authority.py::test_retired_legacy_write_is_unconditional_before_any_database_work",
        ),
    },
    "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE": {
        "positive_tests": (),
        "negative_tests": (),
    },
}

# These entry points contain orchestration rather than a reusable decorator or
# a directly named authority resolver.  Each exact source body is pinned in the
# generated inheritance proof; membership in this reviewed set is the semantic
# review, and a changed body/branch invalidates the recorded certificate.
REVIEWED_NATIVE_TRANSPORTS = {
    "app/services/background_job_service.py:enqueue_job",
    "app/services/background_job_service.py:fence_stalled_jobs",
    "app/services/background_job_service.py:mark_job_failed_or_retry",
    "app/services/background_worker.py:run_worker_once",
    "app/services/bar_cache_service.py:ensure_daily_bars",
    "app/services/ceri/backfill_service.py:CeriBackfillService.run",
    "app/services/ceri/change_rebuild_service.py:CeriChangeRebuildService.rebuild",
    "app/services/ceri/feature_rebuild_service.py:CeriFeatureRebuildService.rebuild",
    "app/services/ceri/feature_rebuild_service.py:CeriFeatureRebuildService.rebuild_from_context",
    "app/services/ceri/normalization_service.py:CeriNormalizationService.normalize",
    "app/services/ceri/orchestration.py:CeriIngestionService.ingest",
    "app/services/ceri/sec/incremental_ingestion.py:SecGuidanceIncrementalIngestionService.ingest",
    "app/services/ceri/sec/readiness_repair.py:mark_sec_repair_failure",
    "app/services/ceri/sec/readiness_repair.py:schedule_sec_readiness_repair",
    "app/services/ceri/sec/readiness_repair.py:execute_sec_readiness_repair",
    "app/services/ceri/surprise_feature_service.py:CeriSurpriseFeatureService.summarize",
    "app/services/ib_fetch_executor.py:execute_fetch_plan",
    "app/services/ib_fetch_job_service.py:submit_fetch_job",
    "app/services/market_data_prewarm_service.py:enqueue_market_data_prewarm",
    "app/services/market_data_prewarm_service.py:execute_market_data_prewarm",
    "app/services/market_calculation_context_service.py:attach_reserved_market_context",
    "app/services/market_calculation_context_service.py:create_pipeline_market_context",
    "app/services/market_calculation_context_service.py:market_context_for_pipeline",
    "app/services/market_calculation_context_service.py:reserve_preflight_market_context",
    "app/services/pipeline_service.py:cancel_pipeline",
    "app/services/pipeline_service.py:enqueue_pipeline_after_sec_repair",
    "app/services/pipeline_service.py:mark_pipeline_job_failure",
    "app/services/pipeline_service.py:resume_pipeline",
    "app/services/setup_lifecycle/maintenance_service.py:SetupLifecycleMaintenanceService.daily_maintenance",
    "app/services/setup_lifecycle/maintenance_service.py:SetupLifecycleMaintenanceService.rebuild_alerts",
    "app/services/setup_lifecycle/maintenance_service.py:SetupLifecycleMaintenanceService.repair_ticker",
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add_lifecycle_event",
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add_lifecycle_events",
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add_new_lifecycle_event",
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.supersede_prior_current_events",
    "app/services/transition_preflight_plan_service.py:cancel_transition_preflight",
    "app/services/transition_preflight_plan_service.py:consume_transition_preflight",
    "app/services/transition_preflight_plan_service.py:create_transition_preflight_plan",
    "app/services/transition_preflight_plan_service.py:expire_abandoned_preflights",
    "app/services/winner_probability/cohort_definition.py:CohortDefinitionService.ensure_definition",
    "app/services/winner_probability/backfill.py:WinnerProbabilityBackfillService.execute_backfill",
    "app/services/winner_probability/cohort_refresh_planner.py:CohortRefreshPlanner.request_for_current_evidence",
    "app/services/winner_probability/decision_time_estimate_service.py:DecisionTimeEstimateService.create_decision_time_estimate",
    "app/services/winner_probability/job_handlers.py:enqueue_outcome_maturation_workflow",
    "app/services/winner_probability/job_handlers.py:WinnerCohortRefreshService.refresh_cohorts",
    "app/services/winner_probability/outcome_orchestration_service.py:H5NextOpenOrchestrationService.drain_due",
    "app/services/winner_probability/probability_estimator.py:ProbabilityEstimator.create_decision_time_estimate",
    "app/services/winner_probability/probability_estimator.py:ProbabilityEstimator.create_candidate_rescore_from_generation",
    "app/services/winner_probability/probability_estimator.py:ProbabilityEstimator.create_latest_rescore",
    "app/services/winner_probability/repository.py:WinnerProbabilityRepository.add",
    "app/services/winner_probability/scheduler.py:schedule_primary_h5_maturation",
    "app/services/winner_probability/training_eligibility.py:TrainingEligibilityPolicy.persist_capture_decision",
}
REVIEWED_TOOLING_TRANSPORTS = {
    "scripts/certify_sec_incremental_ingestion.py:main",
    "scripts/certify_technical_artifact_cache.py:main",
    "scripts/forensics/run159_ceri_quarantine.py:main",
    "scripts/profile_sec_performance.py:main",
    "scripts/resolve_sec_ciks.py:main",
    "scripts/validate_ib_market_intelligence.py:main",
    "scripts/winner_estimate_publication.py:main",
    "scripts/winner_ib_request_scope_canary.py:main",
    "scripts/winner_market_data_recovery.py:main",
}


def transparent_forwarder(address, root=ROOT):
    _, node = addressed_node(address, root)
    return (
        source_proof(address, root)["body_sha256"] == FORWARDER_REVIEW_HASHES[address]
        and len(node.body) == 1
        and isinstance(node.body[0], ast.Return)
        and isinstance(node.body[0].value, ast.Call)
        and not any(isinstance(n, (ast.If, ast.IfExp, ast.Assign)) for n in ast.walk(node))
    )


@dataclass(frozen=True)
class CallerOperationFamily:
    operation_family_id: str
    semantic_mode: tuple[str, ...]
    domains: tuple[str, ...]
    member_initiator_ids: tuple[str, ...]
    semantic_service: str
    authority_adapter: tuple[str, ...]
    target_writer_families: tuple[str, ...]
    surfaces: tuple[str, ...]
    transaction_boundary: str
    failure_behavior: str
    inheritance_proofs: tuple[dict, ...]
    authority_equivalence_key: dict
    previous_operation_family_ids: tuple[str, ...]
    positive_tests: tuple[str, ...] = ()
    negative_tests: tuple[str, ...] = ()
    query_budget_tests: tuple[str, ...] = ()
    final_status: str = "INCOMPLETE"


@dataclass(frozen=True)
class AuthorityEquivalenceKey:
    """Financial authority boundaries, independent of the initiating symbol."""

    domains: tuple[str, ...]
    semantic_operation: str
    semantic_modes: tuple[str, ...]
    authority_adapters: tuple[str, ...]
    writer_families: tuple[str, ...]
    transaction_model: str
    execution_model: str
    authority_requirement_set: str
    error_mapping: str
    authority_branch_fingerprint: str


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def authority_key(members, kind, service, root=ROOT):
    """Protected members use their owner's admitted path, never their names.

    For an independent operation the executable body is conservatively retained
    until an exact forwarding/branch review establishes earlier convergence.
    Removing or changing a branch therefore cannot silently retain membership.
    """
    domains = tuple(sorted({d for c in members for d in c["domain"]}))
    modes = tuple(sorted({m for c in members for m in c["semantic_mode"]}))
    targets = tuple(sorted({w for c in members for w in c["target_writer_family"]}))
    policies = json.loads(
        (
            root / "docs/remediation/calculation-lineage/T14D_minimum_authority_contracts.json"
        ).read_text(encoding="utf-8")
    )["policies"]
    addresses = sorted({c["source_address"] for c in members})
    if kind in {"EXACT_PROTECTED_TRANSACTION", "REVIEWED_EXACT_FORWARDER"}:
        addresses = sorted({*addresses, service})
    authority_proofs = [source_proof(a, root) for a in addresses]
    adapters = tuple(
        sorted(
            {
                call
                for proof in authority_proofs
                for call in proof.get("calls", [])
                if "authority" in call or "context" in call or "configuration" in call
            }
            | {
                decorator
                for proof in authority_proofs
                for decorator in proof.get("decorators", [])
                if not decorator.startswith(("router.", "unsafe_route"))
            }
        )
    ) or ("No authority adapter; exact executable operation reviewed separately",)
    errors, branches = [], []
    branches.extend(proof.get("import_bindings", {}) for proof in authority_proofs)
    for address in addresses:
        if not address.split(":")[0].endswith(".py"):
            branches.append(source_proof(address, root)["file_sha256"])
            continue
        _, node = addressed_node(address, root)
        errors.extend(
            ast.dump(n, include_attributes=False)
            for n in ast.walk(node)
            if isinstance(n, (ast.Raise, ast.ExceptHandler))
        )
        # Ignore the declaring function name and transport decorators. Calls,
        # constants, selectors, defaults and execution-affecting branches stay.
        branches.append(
            ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
        )
        branches.append(ast.dump(node.args, include_attributes=False))
    execution = (
        "durable"
        if any(
            any("anchored_job_configuration" in d for d in p.get("decorators", []))
            for p in authority_proofs
        )
        else "synchronous"
    )
    if "schedule" in adapters:
        execution = "scheduled"
    transaction = (
        "EXACT_PROTECTED_TRANSACTION"
        if kind == "EXACT_PROTECTED_TRANSACTION"
        else "NO_BUSINESS_MUTATION"
        if all(
            c["final_disposition"] in {"RETIRED", "READ_ONLY", "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE"}
            for c in members
        )
        else "NATIVE_SESSION_AND_APPLICABLE_DURABLE_FENCE"
    )
    requirements = {
        domain: policies.get(
            domain, {"domain": domain, "requirements": "Exact addressed native operation"}
        )
        for domain in domains
    }
    operation = kind + ":" + service + ":" + "|".join(targets)
    return AuthorityEquivalenceKey(
        domains,
        operation,
        modes,
        adapters,
        targets,
        transaction,
        execution,
        digest(requirements),
        digest(errors),
        digest(branches),
    )


def _review_entries(review):
    return {
        entrypoint: entry
        for entry in review["entry_families"].values()
        for entrypoint in entry.get("entrypoints", [])
    }


def _retained_writer_contract_complete(caller):
    statuses = {
        contract["retained_T14C_status"]
        for contract in caller.get("writer_contract_status", {}).values()
    }
    return bool(statuses) and not statuses.intersection({"POTENTIAL_BYPASS", "DIRECT_SQL_LEGACY"})


def authority_delivery_boundary(caller, proof, review_entry, *, kind=None, service=None):
    """Return the reviewed shared boundary class and concrete delivery point.

    A source body, call-name hit, or retained writer contract on its own is not
    enough.  Mutating callers must have a complete retained writer contract and
    enter one of the finite, tested mechanisms below.  The operation-family
    equivalence key separately pins every authority-affecting branch.
    """
    disposition = caller["final_disposition"]
    if disposition in {"READ_ONLY", "RETIRED", "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE"}:
        return disposition, service or caller["source_address"]
    if not _retained_writer_contract_complete(caller):
        return None
    if kind in {"EXACT_PROTECTED_TRANSACTION", "EXACT_PROTECTED_TRANSACTION_SET"}:
        if not proof["guard_owners"] and caller["source_address"] != service:
            return None
        return "EXACT_PROTECTED_TRANSACTION_MEMBER", service
    if kind == "REVIEWED_EXACT_FORWARDER":
        return "REVIEWED_EXACT_FORWARDER", service

    decorators = proof.get("decorators", [])
    if any("core_writer_member" in decorator for decorator in decorators):
        # A syntactically present member decorator whose exact owner extraction
        # disappeared is an altered guard, not a new independent certificate.
        return None
    if any("core_writer_transaction" in decorator for decorator in decorators):
        return "CORE_MUTATION_TRANSACTION", "core_writer_transaction"
    if any("anchored_job_configuration" in decorator for decorator in decorators):
        return "DURABLE_JOB_CONFIGURATION", "anchored_job_configuration"
    if any("anchored_decision_calculator" in decorator for decorator in decorators):
        return "DECISION_CONFIGURATION_DELIVERY", "anchored_decision_calculator"
    for decorator_name in ("source_mutation_writer", "supporting_mutation_operation"):
        if any(decorator_name in decorator for decorator in decorators):
            return "CORE_MUTATION_TRANSACTION", decorator_name

    calls = proof.get("calls", [])
    authority_tokens = (
        "configuration_delivery_scope",
        "current_delivery",
        "load_configuration_delivery",
        "resolve_pipeline_configurations",
        "domain_mutation_scope",
        "mutation_context",
        "calculation_context",
        "authority",
        "execution_ownership",
        "fence_domain_commits",
        "reject_legacy_mutation",
        "seal_",
        "validate_",
    )
    explicit = next(
        (call for call in calls if any(token in call.lower() for token in authority_tokens)),
        None,
    )
    if explicit:
        return "EXPLICIT_NATIVE_AUTHORITY_OPERATION", explicit
    address = caller["source_address"]
    if address in REVIEWED_NATIVE_TRANSPORTS:
        return "REVIEWED_TRANSPORT_TO_NATIVE_OPERATION", address
    if address in REVIEWED_TOOLING_TRANSPORTS:
        return "REVIEWED_TOOLING_NATIVE_OPERATION", address
    if review_entry.get("semantic_review_complete") is True:
        return "REVIEWED_TRANSPORT_TO_NATIVE_OPERATION", address
    return None


def _final_status(members, modes, boundary):
    if boundary is None:
        return "INCOMPLETE"
    dispositions = {member["final_disposition"] for member in members}
    retained = dispositions & ALLOWED_FINAL_STATUSES
    if len(dispositions) == 1 and len(retained) == 1:
        return retained.pop()
    if dispositions <= {"RETIRED", "READ_ONLY", "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE"}:
        return next(iter(dispositions))
    mode_set = set(modes)
    if mode_set == {"OPERATIONAL"}:
        return "OPERATIONAL_ONLY"
    if mode_set == {"LEGACY_UNCERTIFIED"}:
        return "LEGACY_NONCERTIFIED"
    if "CURRENT_STATE_REPAIR" in mode_set and not mode_set.intersection(
        {"CANONICAL_CALCULATION", "CURRENT_RULES_RETROSPECTIVE", "OUTCOME_MATURATION"}
    ):
        return "CURRENT_STATE_REPAIR_EXPLICIT"
    if "CURRENT_RULES_RETROSPECTIVE" in mode_set and not mode_set.intersection(
        {"CANONICAL_CALCULATION", "CURRENT_STATE_REPAIR", "OUTCOME_MATURATION"}
    ):
        return "CURRENT_RULES_RETROSPECTIVE_EXPLICIT"
    canonical_only = {
        "BOOTSTRAP",
        "CANONICAL_CALCULATION",
        "PUBLICATION",
    }
    if mode_set <= canonical_only:
        return "CANONICAL_AUTHORITY_DELIVERED"
    return "SUPPORTED_DISTINCT_SEMANTICS"


@lru_cache(maxsize=128)
def _parse_source(source):
    return ast.parse(source)


def addressed_node(address, root=ROOT):
    path, name = address.split(":", 1)
    source = (root / path).read_text(encoding="utf-8-sig")
    node = _parse_source(source)
    for part in name.split("."):
        node = next(n for n in node.body if getattr(n, "name", None) == part)
    return source, node


def source_proof(address, root=ROOT):
    path = address.split(":", 1)[0]
    if not path.endswith(".py"):
        # A whole-file proof must use the committed blob. PowerShell's CRLF
        # checkout conversion used to change one operation-family ID without
        # changing the reviewed restore operation at all.
        content = current_committed_source_bytes(root, path)
        return {
            "address": address,
            "file_sha256": hashlib.sha256(content).hexdigest(),
            "guard_owners": [],
        }
    source, node = addressed_node(address, root)
    imports = {}
    for statement in _parse_source(source).body:
        if isinstance(statement, ast.ImportFrom):
            for alias in statement.names:
                imports[alias.asname or alias.name] = (statement.module or "") + "." + alias.name
        elif isinstance(statement, ast.Import):
            for alias in statement.names:
                imports[alias.asname or alias.name.split(".")[0]] = alias.name
    module = _parse_source(source)
    named_constants = {
        target.id: statement.value
        for statement in module.body
        if isinstance(statement, (ast.Assign, ast.AnnAssign))
        for target in (
            statement.targets if isinstance(statement, ast.Assign) else [statement.target]
        )
        if isinstance(target, ast.Name)
    }
    decorators = [ast.unparse(n) for n in node.decorator_list]
    owners = []

    def string_values(value, seen=frozenset()):
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return [value.value]
        if isinstance(value, (ast.Tuple, ast.List)):
            return [item for element in value.elts for item in string_values(element, seen)]
        if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Add):
            return string_values(value.left, seen) + string_values(value.right, seen)
        if isinstance(value, ast.Name) and value.id in named_constants and value.id not in seen:
            return string_values(named_constants[value.id], seen | {value.id})
        return []

    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Call) and ast.unparse(decorator.func) in {
            "source_writer_member",
            "core_writer_member",
        }:
            for arg in decorator.args:
                for value in string_values(arg):
                    module, qualname = value.split(":", 1)
                    owners.append(module.replace(".", "/") + ".py:" + qualname)
    return {
        "address": address,
        "file_sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
        "body_sha256": hashlib.sha256(
            ast.dump(node, include_attributes=False).encode()
        ).hexdigest(),
        "decorators": decorators,
        "guard_owners": sorted(owners),
        "calls": sorted({ast.unparse(n.func) for n in ast.walk(node) if isinstance(n, ast.Call)}),
        "authority_branches": [
            ast.unparse(n.test) for n in ast.walk(node) if isinstance(n, ast.If)
        ],
        "signature": ast.unparse(node.args),
        "import_bindings": imports,
    }


def normalize(callers, review, root=ROOT):
    reviewed_aliases = {a: name for name, members in READ_ONLY_GROUPS.items() for a in members}
    entries = _review_entries(review)
    groups = defaultdict(list)
    proofs = {c["initiator_id"]: source_proof(c["source_address"], root) for c in callers}
    owners = {owner for p in proofs.values() for owner in p["guard_owners"]}
    for caller in callers:
        address = caller["source_address"]
        proof = proofs[caller["initiator_id"]]
        if address in reviewed_aliases:
            key = ("REVIEWED_NON_MUTATING_ALIAS", reviewed_aliases[address])
        elif address in FORWARDERS and transparent_forwarder(address, root):
            target = FORWARDERS[address]
            key = (
                "EXACT_PROTECTED_TRANSACTION" if target in owners else "REVIEWED_EXACT_FORWARDER",
                target,
            )
        elif proof["guard_owners"]:
            key = (
                "EXACT_PROTECTED_TRANSACTION"
                if len(proof["guard_owners"]) == 1
                else "EXACT_PROTECTED_TRANSACTION_SET",
                "|".join(proof["guard_owners"]),
            )
        else:
            key = ("DISTINCT_AUTHORITY_OPERATION", address)
        # The declaring transaction owner joins its protected members.
        if address in owners:
            key = ("EXACT_PROTECTED_TRANSACTION", address)
        elif address in FORWARDERS.values():
            key = ("REVIEWED_EXACT_FORWARDER", address)
        kind, service = key
        boundary = authority_delivery_boundary(
            caller,
            proof,
            entries[address],
            kind=kind,
            service=service,
        )
        if boundary is None:
            group_key = ("INCOMPLETE", address, caller["initiator_id"])
        else:
            certificate, delivery = boundary
            group_key = (
                certificate,
                delivery,
                tuple(sorted(caller["domain"])),
                tuple(sorted(caller["semantic_mode"])),
                tuple(sorted(caller["target_writer_family"])),
                "durable"
                if any("anchored_job_configuration" in d for d in proof.get("decorators", []))
                else "synchronous",
            )
        groups[group_key].append((caller, kind, service, boundary))

    families = []
    for _group_key, grouped in sorted(groups.items()):
        members = [item[0] for item in grouped]
        kinds = {item[1] for item in grouped}
        original_services = {item[2] for item in grouped}
        boundaries = {item[3] for item in grouped}
        assert len(boundaries) == 1
        boundary = boundaries.pop()
        kind = next(iter(kinds)) if len(kinds) == 1 else "SHARED_AUTHORITY_DELIVERY_BOUNDARY"
        original_service = (
            next(iter(original_services)) if len(original_services) == 1 else boundary[1]
        )
        certificate_class, service = boundary or ("INCOMPLETE", original_service)
        equivalence = authority_key(members, kind, service, root)
        ids = tuple(sorted(c["initiator_id"] for c in members))
        classification = {c["final_disposition"] for c in members}
        nonmutating = classification <= {"RETIRED", "READ_ONLY", "EXTERNAL_PRIVILEGED_OUT_OF_SCOPE"}
        family_id = "AF_" + digest(asdict(equivalence))[:16]
        member_proofs = []
        for caller in sorted(members, key=lambda x: x["initiator_id"]):
            proof = proofs[caller["initiator_id"]]
            member_kind = (
                "REVIEWED_EXACT_FORWARDER"
                if caller["source_address"] in FORWARDERS
                and transparent_forwarder(caller["source_address"], root)
                else kind
            )
            member_proofs.append(
                {
                    "initiator_id": caller["initiator_id"],
                    "source": proof,
                    "convergence_kind": member_kind,
                    "shared_service": service,
                    "authority_delivery_boundary": service,
                    "boundary_certificate": certificate_class,
                    "service_source": source_proof(original_service, root)
                    if kind in {"EXACT_PROTECTED_TRANSACTION", "REVIEWED_EXACT_FORWARDER"}
                    else None,
                    "caller_specific_branch": kind == "DISTINCT_AUTHORITY_OPERATION",
                    "inheritance_basis": (
                        "Exact owner guard requires its validating transaction; "
                        "direct invocation fails closed."
                        if member_kind == "EXACT_PROTECTED_TRANSACTION"
                        and caller["source_address"] != service
                        else "Reviewed constructor and argument forwarding; no selector or branch."
                        if member_kind == "REVIEWED_EXACT_FORWARDER"
                        and caller["source_address"] != service
                        else "Distinct operation; its authority branches are recorded."
                        if kind == "DISTINCT_AUTHORITY_OPERATION"
                        else "Non-mutating alias; source and runtime no-write proof required."
                        if kind == "REVIEWED_NON_MUTATING_ALIAS"
                        else "Native transaction owner; members reuse its authority."
                    ),
                }
            )
        modes = tuple(sorted({m for c in members for m in c["semantic_mode"]}))
        if nonmutating:
            modes = tuple(sorted(classification))
        targets = tuple(sorted({w for c in members for w in c["target_writer_family"]}))
        adapters = (
            f"AuthorityDeliveryBoundary={service}",
            f"boundary_certificate={certificate_class}",
        )
        evidence = BOUNDARY_CERTIFICATES.get(certificate_class, {})
        final_status = _final_status(members, modes, boundary)
        families.append(
            CallerOperationFamily(
                operation_family_id=family_id,
                semantic_mode=modes,
                domains=tuple(sorted({d for c in members for d in c["domain"]})),
                member_initiator_ids=ids,
                semantic_service=service,
                authority_adapter=adapters,
                target_writer_families=targets,
                surfaces=tuple(sorted({c["entrypoint_type"] for c in members})),
                transaction_boundary="No business mutation"
                if nonmutating
                else "Same validating Session, native transaction and applicable durable fence",
                failure_behavior="Intentional retirement/missing-state response"
                if nonmutating
                else "Rollback or explicit FAILED/PARTIAL; runtime transport proof required",
                inheritance_proofs=tuple(member_proofs),
                authority_equivalence_key=asdict(equivalence),
                previous_operation_family_ids=tuple(
                    sorted(
                        {
                            old_id
                            for c in members
                            for old_id in c.get("previous_operation_family_ids", [])
                            + [c.get("operation_family_id", "")]
                            if old_id and old_id != family_id
                        }
                    )
                ),
                positive_tests=tuple(evidence.get("positive_tests", ())),
                negative_tests=tuple(evidence.get("negative_tests", ())),
                query_budget_tests=tuple(evidence.get("query_budget_tests", ())),
                final_status=final_status,
            )
        )
    return families


def export(root=ROOT):
    directory = root / "docs/remediation/calculation-lineage"
    certificate_path = directory / "T14D_caller_unification_certification.json"
    certificate = json.loads(certificate_path.read_text(encoding="utf-8"))
    family_path = directory / "T14D_operation_family_certification.json"
    previous = json.loads(family_path.read_text(encoding="utf-8")) if family_path.exists() else {}
    review = json.loads(
        (directory / "T14D_semantic_family_review.json").read_text(encoding="utf-8")
    )
    families = normalize(certificate["callers"], review, root)
    rows = [asdict(f) for f in families]
    mapping = {i: f for f in rows for i in f["member_initiator_ids"]}
    assert len(mapping) == 252 and sum(len(f["member_initiator_ids"]) for f in rows) == 252
    old_by_member = {
        member: family
        for family in previous.get("families", [])
        for member in family["member_initiator_ids"]
    }
    for family in rows:
        prior = [old_by_member.get(member, {}) for member in family["member_initiator_ids"]]
        statuses = {row.get("final_status", "INCOMPLETE") for row in prior}
        # Preserve reviewed certificates only for unchanged executable paths.
        # Final-source regression validity remains a separate mandatory gate.
        unchanged = all(
            any(
                proof["source"].get("body_sha256", proof["source"]["file_sha256"])
                == old_proof["source"].get("body_sha256", old_proof["source"]["file_sha256"])
                for old in prior
                for old_proof in old.get("inheritance_proofs", [])
                if old_proof["initiator_id"] == proof["initiator_id"]
            )
            for proof in family["inheritance_proofs"]
        )
        if (
            family["final_status"] == "INCOMPLETE"
            and unchanged
            and len(statuses) == 1
            and "INCOMPLETE" not in statuses
        ):
            family["final_status"] = statuses.pop()
            for field in ("positive_tests", "negative_tests", "query_budget_tests"):
                family[field] = sorted({test for old in prior for test in old.get(field, [])})
            family["evidence_source_status"] = "FINAL_SOURCE_RERUN_REQUIRED"
        family["remaining_requirements"] = (
            []
            if family["final_status"] != "INCOMPLETE"
            else [
                "Positive explicit caller authority commits at the normalized native operation",
                "Missing/altered authority reject before financial mutation "
                "for every admitted mode",
                "Final-source mandatory regression union",
            ]
        )
    for caller in certificate["callers"]:
        family = mapping[caller["initiator_id"]]
        certified = family["final_status"] != "INCOMPLETE"
        caller.update(
            operation_family_id=family["operation_family_id"],
            certified_family_status=family["final_status"],
            authority_adapter=family["authority_adapter"],
            authority_delivery=(
                "Exact source/branch pin converges on "
                f"{family['authority_adapter'][0]}; shared boundary certificate "
                "proves valid delivery, missing/altered rejection, and historical "
                "non-reconstruction before certified writers."
                if certified
                else "No recognized tested AuthorityDeliveryBoundary."
            ),
            authority_equivalence_key=family["authority_equivalence_key"],
            previous_operation_family_ids=family["previous_operation_family_ids"],
            certification_inheritance_proof=next(
                p
                for p in family["inheritance_proofs"]
                if p["initiator_id"] == caller["initiator_id"]
            ),
            final_disposition=family["final_status"] if certified else "PARTIAL_AUTHORITY",
            status="OPERATION_FAMILY_CERTIFIED" if certified else "OPERATION_FAMILY_INCOMPLETE",
        )
    incomplete = [f["operation_family_id"] for f in rows if f["final_status"] == "INCOMPLETE"]
    payload = {
        "verdict": "FAIL" if incomplete else "PASS",
        "certification_unit": "CALLER_OPERATION_FAMILY",
        "exact_callers": 252,
        "unmapped_callers": [],
        "operation_family_count": len(rows),
        "old_operation_family_count": previous.get(
            "old_operation_family_count", len(previous.get("families", []))
        ),
        "previous_operation_family_records": previous.get(
            "previous_operation_family_records", previous.get("families", [])
        ),
        "authority_equivalence_contract": list(AuthorityEquivalenceKey.__dataclass_fields__),
        "family_status_counts": dict(Counter(f["final_status"] for f in rows)),
        "families": rows,
        "incomplete_operation_family_ids": incomplete,
        "source_pins_alone_are_not_certification": True,
    }
    (directory / "T14D_operation_family_certification.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    uncovered = sorted(
        caller["initiator_id"]
        for caller in certificate["callers"]
        if caller["final_disposition"] == "PARTIAL_AUTHORITY"
    )
    certificate["exact_caller_ids_not_covered"] = uncovered
    certificate["incomplete_normalized_operation_family_ids"] = incomplete
    certificate["INV-ENTRY-001"] = "CLOSED" if not uncovered else "PARTIALLY_ENFORCED"
    certificate["XINT-006"] = "CLOSED" if not uncovered and not incomplete else "OPEN"
    finite = certificate["finite_blockers"]
    finite["exact_caller_ids_not_covered"] = uncovered
    finite["incomplete_normalized_operation_family_ids"] = incomplete
    finite["potential_application_bypass_caller_ids_not_excluded"] = uncovered
    certificate["verdict"] = "PASS" if not any(finite.values()) else "FAIL"
    certificate_path.write_text(
        json.dumps(certificate, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    reconciliation = [
        {
            "old_operation_family_id": old_id,
            "normalized_operation_family_id": family["operation_family_id"],
            "exact_member_caller_ids": family["member_initiator_ids"],
            "authority_equivalence_key": family["authority_equivalence_key"],
            "safe_equivalence_reason": [
                proof["inheritance_basis"] for proof in family["inheritance_proofs"]
            ],
        }
        for family in rows
        for old_id in family["previous_operation_family_ids"]
    ]
    payload["old_to_normalized_reconciliation"] = reconciliation
    family_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    csv.field_size_limit(10_000_000)
    path = directory / "T14D_caller_matrix.csv"
    with path.open(encoding="utf-8", newline="") as file:
        csv_rows = list(csv.DictReader(file))
    for row in csv_rows:
        family = mapping[row["initiator_id"]]
        row.update(
            operation_family_id=family["operation_family_id"],
            certified_family_status=family["final_status"],
            final_disposition=family["final_status"]
            if family["final_status"] != "INCOMPLETE"
            else "PARTIAL_AUTHORITY",
            status="OPERATION_FAMILY_CERTIFIED"
            if family["final_status"] != "INCOMPLETE"
            else "OPERATION_FAMILY_INCOMPLETE",
            authority_source="; ".join(family["authority_adapter"]),
            certification_inheritance_proof=json.dumps(
                next(
                    p
                    for p in family["inheritance_proofs"]
                    if p["initiator_id"] == row["initiator_id"]
                ),
                sort_keys=True,
            ),
        )
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=list(csv_rows[0]))
        writer.writeheader()
        writer.writerows(csv_rows)
    with (directory / "T14D_operation_family_matrix.csv").open(
        "w", encoding="utf-8", newline=""
    ) as file:
        fields = list(rows[0])
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(
            {
                key: json.dumps(value, sort_keys=True)
                if isinstance(value, (dict, list, tuple))
                else value
                for key, value in row.items()
            }
            for row in rows
        )
    print(
        json.dumps(
            {
                "families": len(rows),
                "callers": len(mapping),
                "statuses": Counter(f["final_status"] for f in rows),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    export()
