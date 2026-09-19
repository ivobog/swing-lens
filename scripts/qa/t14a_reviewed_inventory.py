"""Phase-5 reviewed inventory layered on an exhaustive syntax census.

Call edges resolve imports and declared default implementations. Ambiguous calls
remain visible in the source census; they are not converted into proven edges.
No business SQL is executed. Handlers are inspected without starting a worker.
"""

from __future__ import annotations

import ast
import csv
import inspect
import json
import re
import textwrap
from collections import Counter, defaultdict, deque

from scripts.qa.t14a_mutation_inventory import ROOT, discover, scoped_nodes
from scripts.qa.t14a_semantic_review import (
    AUTHORITY_FIELDS,
    NON_EDGE_REVIEWS,
    PATH_REVIEWS,
    REVIEWED_DISPATCH,
    TABLE_REVIEWS,
)
from scripts.qa.t14b_semantic_review import NON_EDGE_REVIEWS as ADOPTION_NON_EDGE_REVIEWS

NON_EDGE_REVIEWS = {**NON_EDGE_REVIEWS, **ADOPTION_NON_EDGE_REVIEWS}

DESTINATION = ROOT / "docs/remediation/calculation-lineage"
# These exclusions have source-level database isolation, rather than a QA name.
# Unguarded scripts (including certify_slse_natural and SEC certification) remain
# production-callable. Each proof is retained in the exported census.
ISOLATED_SURFACES = {
    "scripts/forensics/adversarial_temporal_clone_replay.py": (
        "main: assert_disposable_database before create_engine"
    ),
    "scripts/forensics/run153_ceri_lineage_clone_replay.py": (
        "main: assert_disposable_database before create_engine"
    ),
    "scripts/forensics/run153_systemic_remediation_replay.py": (
        "main: assert_disposable_database before create_engine"
    ),
    "scripts/qa/t13e_deployed_probe.py": "main: assert_disposable_database before SessionLocal use",
    "scripts/qa/run_m05_scale.py": "main obtains newly created guarded swinglens_qa_* database",
    "scripts/qa/run_m05_soak.py": "main obtains newly created guarded database from run_m05_scale",
    "scripts/qa/run_m05_restart.py": "main obtains task-created Docker PostgreSQL container",
    "scripts/certify_observability_cross_process.py": (
        "main creates swinglens_obs_cert_<uuid> database; migrations use guarded upgrade"
    ),
}
TEST_VALUE_SURFACES = {
    "scripts/qa/core_configuration_behavior_capture.py": (
        "test_* imports and FakeDb; no live database factory"
    ),
    "scripts/qa/t12b_behavior_probe.py": "test fixture behavior probe; no live database factory",
    "scripts/qa/t12c_behavior_probe.py": "test fixture behavior probe; no live database factory",
    "scripts/qa/t12d_behavior_probe.py": "test fixture behavior probe; no live database factory",
    "scripts/qa/t12e_behavior_probe.py": "test fixture behavior probe; no live database factory",
    "scripts/qa/t13c_behavior_probe.py": "test fixture behavior probe; no live database factory",
    "scripts/qa/t13d_behavior_probe.py": "test fixture behavior probe; no live database factory",
    "scripts/qa/t13e_behavior_probe.py": "test fixture behavior probe; no live database factory",
}
OPERATIONAL_TABLES = frozenset(
    {
        "background_jobs",
        "background_job_enqueue_attempts",
        "background_job_fanout_roots",
        "background_workers",
        "background_supervisors",
        "ib_fetch_runs",
        "ib_fetch_items",
        "ceri_ingestion_runs",
        "ceri_processing_runs",
        "ceri_provider_request_telemetry",
        "ceri_purge_audits",
        "setup_lifecycle_administrative_audit_events",
        "winner_processing_runs",
        "ib_intelligence_runs",
        "ib_intelligence_request_items",
        "ceri_sec_processor_releases",
        "ceri_sec_document_extractions",
        "ceri_sec_sync_states",
    }
)
PROJECTIONS = frozenset(
    {
        "core_calculation_current_projections",
        "setup_signal_snapshot_current_selections",
        "setup_signal_snapshot_selection_events",
        "winner_cohort_refresh_state",
    }
)
# Exact reviewed exceptions to syntactic type inference. Generic repositories are
# mechanisms: their semantic callers, separately inventoried, own domain meaning.
OVERRIDES = {
    "app/services/winner_probability/model_registry.py:ModelRegistry.promote_model": [
        "WinnerModelVersion"
    ],
    "app/services/winner_probability/model_registry.py:ModelRegistry.retire_model": [
        "WinnerModelVersion"
    ],
    (
        "app/services/winner_probability/estimate_publication_service.py:"
        "WinnerEstimatePublicationService.publish"
    ): [
        "WinnerProbabilityEstimate",
        "WinnerEstimatePublicationRequest",
    ],
    (
        "app/services/winner_probability/capture_service.py:"
        "WinnerPredictionCaptureService._capture_ticker"
    ): [
        "WinnerPredictionSnapshot",
        "WinnerTemporalValidityDecision",
    ],
    "app/services/ceri/backfill_service.py:CeriBackfillService.run": ["CeriProcessingRun"],
    "app/services/ceri/feature_rebuild_service.py:_execute_upsert": [
        "CeriRevisionFeature",
        "CeriDerivedFeature",
        "CeriPriceResponseFeature",
        "CeriFeatureBuildState",
    ],
    (
        "app/services/setup_lifecycle/episode_service.py:"
        "SetupLifecycleEpisodeService.refresh_primary_status"
    ): ["SetupLifecycleEpisode"],
    (
        "app/services/setup_lifecycle/episode_service.py:"
        "SetupLifecycleEpisodeService.refresh_primary_statuses"
    ): ["SetupLifecycleEpisode"],
    (
        "app/services/setup_lifecycle/repository.py:"
        "SetupLifecycleRepository.record_snapshot_canonical_decisions"
    ): ["SetupSignalSnapshot"],
    (
        "app/services/winner_probability/cohort_materialization_service.py:"
        "CohortMaterializationService._cancel"
    ): ["WinnerCohortGeneration"],
    "app/services/background_job_service.py:_enqueue_pre_migration_job": ["BackgroundJob"],
    "app/services/ceri/purge_service.py:_apply_purge_lifecycle": [
        "CeriSourceRecord",
        "CeriEstimateSnapshot",
        "CeriEarningsActual",
        "CeriGuidanceEvent",
        "CeriCatalystEventRevision",
        "CeriCatalystSource",
        "CeriRevisionFeature",
        "CeriDerivedFeature",
        "CeriPriceResponseFeature",
        "CeriScoreSnapshot",
        "CeriChangeEvent",
        "CeriAlertEvent",
    ],
    "app/services/ceri/normalization_service.py:CeriNormalizationService._normalize_record": [
        "CeriEstimateSnapshot",
        "CeriEarningsActual",
        "CeriGuidanceEvent",
        "CeriCatalystEvent",
        "CeriCatalystEventRevision",
        "CeriCatalystSource",
        "CeriSourceRecord",
    ],
    "app/services/bar_cache_service.py:cache_bars": ["PriceBar", "PriceBarRevision"],
    "app/services/upload_service.py:create_upload_run": [
        "UploadRun",
        "RawCompanyRow",
        "FundamentalScore",
    ],
    "app/services/ranking_profile_service.py:_persist_rankings": ["RankingResult"],
    "app/services/sector_rotation_repository.py:SectorRotationRepository.save_snapshot": [
        "SectorRotationSnapshot",
        "SectorRotationRow",
    ],
    "app/services/cleanup_service.py:cleanup_rebuildable_artifacts": ["BackgroundJob"],
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add": [
        "SetupLifecycleEvaluationRun",
        "SetupLifecycleEpisode",
        "SetupLifecycleEvent",
        "SignalChangeEvent",
        "SignalAlertEvent",
        "SetupLifecycleAdministrativeAuditEvent",
    ],
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.add_lifecycle_events": [
        "SetupLifecycleEvent"
    ],
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.upsert_snapshots": [
        "SetupSignalSnapshot"
    ],
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository._delete_selected": [
        "SignalAlertEvent",
        "SignalChangeEvent",
        "SetupLifecycleEvent",
        "SetupLifecycleEpisode",
        "SetupSignalSnapshot",
        "SetupLifecycleEvaluationRun",
    ],
    "app/services/winner_probability/repository.py:WinnerProbabilityRepository.add": [
        "WinnerPredictionSnapshot",
        "WinnerPredictionEpisode",
        "WinnerOutcomeDefinition",
        "WinnerForwardOutcome",
        "WinnerTargetStopOutcome",
        "WinnerTrainingEligibilityDecision",
        "WinnerTemporalValidityDecision",
        "WinnerProbabilityEstimate",
    ],
    (
        "app/services/winner_probability/temporal_validation_service.py:"
        "TemporalValidationService.apply_certification"
    ): [
        "WinnerPredictionSnapshot",
        "WinnerTemporalValidityDecision",
    ],
    (
        "app/services/winner_probability/temporal_validation_service.py:"
        "TemporalValidationService.apply_quarantine"
    ): [
        "WinnerPredictionSnapshot",
        "WinnerTemporalValidityDecision",
    ],
    "app/services/ceri/feature_rebuild_service.py:CeriFeatureRebuildService._upsert_derived": [
        "CeriDerivedFeature"
    ],
    "app/services/ceri/controlled_replay_service.py:CeriControlledReplayService.replay": [
        "CeriControlledReplay",
        "CeriRevisionFeature",
        "CeriScoreSnapshot",
    ],
    "scripts/winner_candidate_estimates.py:write": [
        "WinnerProbabilityEstimate",
        "WinnerEstimateEvidenceMember",
    ],
    "scripts/winner_clean_reconstruction.py:build_candidate": ["WinnerCohortGeneration"],
    "scripts/resolve_sec_ciks.py:main": ["CeriCompany"],
}
TRANSIENT = {
    "app/services/ceri/controlled_replay_service.py:_calculate_replay_features",
    (
        "app/services/setup_lifecycle/transition_candidate_service.py:"
        "TransitionCandidateDiscoveryService.assess"
    ),
    "app/services/ceri/change_detection_service.py:CeriChangeDetectionService.detect_score_changes",
    "app/services/ceri/controlled_replay_service.py:_normalize_feature_precision",
    "app/services/ceri/revision_feature_service.py:CeriRevisionFeatureService._feature_from_selection",
    "app/services/ceri/revision_feature_service.py:CeriRevisionFeatureService._feature_from_default",
    "app/services/ceri/revision_feature_service.py:CeriRevisionFeatureService._feature_from_window",
    "app/services/ceri/revision_feature_service.py:CeriRevisionFeatureService.with_derived",
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository._snapshot_from_write",
    "app/services/winner_probability/capture_service.py:WinnerPredictionCaptureService._build_prediction_snapshot",
    "app/services/winner_probability/capture_service.py:_initial_temporal_decision",
}
# Stop traversal at value-only invocations of a shared field-copy helper. The
# helper is still a writer when reached from repository persistence methods.
VALUE_ONLY_BOUNDARIES = {
    "app/services/technical_score_service.py:preview_run_technicals": (
        "Cache mode OFF; finalizer called with persist=False; no ORM flush or derived persistence"
    ),
    "app/services/ceri/guidance_normalizer.py:CeriGuidanceNormalizer.normalize": (
        "New unbound CeriGuidanceEvent; apply_guidance_eligibility edits its constructor "
        "result; no Session or persistence"
    ),
    "scripts/forensics/preflight_selection_readiness_scan.py:main": (
        "PostgreSQL REPEATABLE READ, READ ONLY; prospective context/technical preview; "
        "rejects db.new/dirty/deleted and changed persisted counts; rollback"
    ),
    "app/services/setup_lifecycle/transition_candidate_service.py:_transient_snapshot": (
        "new unbound SetupSignalSnapshot; _apply_snapshot_fields copies DTO only"
    ),
    "app/services/cleanup_service.py:preview_cleanup": "explicit dry_run=True before delete loop",
}
# Principal native owners plus the explicit supporting-artifact review overlay.
DOMAIN_OWNERS = {
    "CERI_ALERT": "app/services/ceri/alert_service.py:CeriAlertService.persist_alert_for_change",
    "WINNER_MODEL": (
        "app/services/winner_probability/model_registry.py:ModelRegistry.register_model"
    ),
    "WINNER_DIAGNOSTICS": (
        "app/services/winner_probability/calibration_service.py:CalibrationService.persist_bins;"
        "app/services/winner_probability/drift_service.py:DriftService.persist_metrics;"
        "app/services/winner_probability/similarity_service.py:SimilarityService.persist_neighbors"
    ),
    "CERI_REVIEW": (
        "app/services/ceri/manual_review_service.py:"
        "CeriManualReviewService.create_catalyst_override"
    ),
    "ALERT_STATUS": "app/services/ceri/alert_service.py:CeriAlertService.acknowledge",
    "IBMI_SOURCE": "app/services/ib_market_intelligence/repository.py:persist_live_snapshot",
    "TRADE_JOURNAL": "app/services/ib_market_intelligence/journal.py:rebuild_trade_episodes",
    "OPERATIONAL": "NOT_APPLICABLE",
    "RAW": "app/services/upload_service.py:create_upload_run",
    "PRICE": "app/services/bar_cache_service.py:cache_bars",
    "CERI_SOURCE": (
        "app/services/ceri/source_record_service.py:CeriSourceRecordService.store_source_record"
    ),
    "CERI_CHANGE": (
        "app/services/ceri/change_detection_service.py:CeriChangeDetectionService._persist_change"
    ),
    "SHARED_LEDGER": "app/services/core_calculation_evidence.py:persist_core_evidence",
    "CURRENT_PROJECTION": "app/services/core_calculation_evidence.py:_advance_current_projection",
    "CONFIGURATION": "app/services/configuration_delivery.py:persist_configuration_anchor",
    "PIPELINE": "app/services/pipeline_service.py:start_pipeline",
    "WINNER_ESTIMATE": (
        "app/services/winner_probability/probability_estimator.py:"
        "ProbabilityEstimator._create_estimate"
    ),
    "FUNDAMENTAL": "app/services/fundamental_score_service.py:recalculate_run_fundamentals",
    "TECHNICAL": "app/services/technical_score_service.py:finalize_technical_scores",
    "COMBINED": "app/services/combined_decision.py:refresh_combined_results",
    "RANKING": "app/services/ranking_profile_service.py:_persist_rankings",
    "REGIME": "app/services/market_regime_repository.py:MarketRegimeRepository.upsert_snapshot",
    "SECTOR": "app/services/sector_rotation_repository.py:SectorRotationRepository.save_snapshot",
    "CERI": "app/services/ceri/snapshot_service.py:CeriSnapshotService.persist_snapshot",
    "IBMI": "app/services/ib_market_intelligence/repository.py:persist_feature",
    "SETUP": "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.upsert_snapshot",
    "LIFECYCLE_EVALUATION": (
        "app/services/setup_lifecycle/decision_evidence.py:persist_lifecycle_evaluation_evidence"
    ),
    "LIFECYCLE_TRANSITION": (
        "app/services/setup_lifecycle/decision_evidence.py:persist_lifecycle_transition_evidence"
    ),
    "ALERT_DECISION": (
        "app/services/setup_lifecycle/decision_evidence.py:persist_alert_decision_evidence"
    ),
    "WINNER_PREDICTION": (
        "app/services/winner_probability/capture_service.py:WinnerPredictionCaptureService._capture_ticker"
    ),
    "WINNER_OUTCOME": (
        "app/services/winner_probability/outcome_revision_service.py:"
        "OutcomeRevisionService.upsert_forward_revision"
    ),
    "WINNER_COHORT": (
        "app/services/winner_probability/cohort_materialization_service.py:"
        "CohortMaterializationService.materialize_slice"
    ),
    "WINNER_GENERATION": (
        "app/services/winner_probability/cohort_generation_service.py:"
        "CohortGenerationService.capture_or_resume"
    ),
    "WINNER_PUBLICATION": (
        "app/services/winner_probability/estimate_publication_service.py:"
        "WinnerEstimatePublicationService.publish"
    ),
}
TABLE_DOMAINS = {
    "upload_runs": "RAW",
    "raw_company_rows": "RAW",
    "price_bars": "PRICE",
    "price_bar_revisions": "PRICE",
    "ceri_source_records": "CERI_SOURCE",
    "ceri_change_events": "CERI_CHANGE",
    "core_calculation_evidence": "SHARED_LEDGER",
    "core_calculation_evidence_sources": "SHARED_LEDGER",
    "core_calculation_current_projections": "CURRENT_PROJECTION",
    "effective_configuration_records": "CONFIGURATION",
    "execution_configuration_anchors": "CONFIGURATION",
    "execution_configuration_bindings": "CONFIGURATION",
    "pipeline_runs": "PIPELINE",
    "pipeline_steps": "PIPELINE",
    "winner_probability_estimates": "WINNER_ESTIMATE",
    "fundamental_scores": "FUNDAMENTAL",
    "technical_scores": "TECHNICAL",
    "combined_results": "COMBINED",
    "ranking_results": "RANKING",
    "market_regime_snapshots": "REGIME",
    "sector_rotation_snapshots": "SECTOR",
    "sector_rotation_rows": "SECTOR",
    "ceri_score_snapshots": "CERI",
    "ib_intelligence_features": "IBMI",
    "setup_signal_snapshots": "SETUP",
    "setup_lifecycle_evaluation_evidence": "LIFECYCLE_EVALUATION",
    "setup_lifecycle_transition_evidence": "LIFECYCLE_TRANSITION",
    "signal_alert_decision_evidence": "ALERT_DECISION",
    "winner_prediction_snapshots": "WINNER_PREDICTION",
    "winner_forward_outcomes": "WINNER_OUTCOME",
    "winner_target_stop_outcomes": "WINNER_OUTCOME",
    "winner_cohort_statistics": "WINNER_COHORT",
    "winner_cohort_generations": "WINNER_GENERATION",
    "winner_estimate_publication_requests": "WINNER_PUBLICATION",
}


def table_domain(table):
    if table in TABLE_REVIEWS:
        return TABLE_REVIEWS[table].domain
    return (
        "OPERATIONAL"
        if table in OPERATIONAL_TABLES
        else TABLE_DOMAINS.get(table, "AUXILIARY_UNREVIEWED")
    )


def table_owner(table):
    if table in TABLE_REVIEWS:
        return TABLE_REVIEWS[table].owner
    return (
        "NOT_APPLICABLE"
        if table in OPERATIONAL_TABLES
        else DOMAIN_OWNERS.get(TABLE_DOMAINS.get(table), "UNREVIEWED")
    )


READ_ONLY_MODELS = {
    "EngineParameters": "Legacy model; no production writer/caller found; no repair/backfill."
}


def module_name(path):
    return path.removesuffix(".py").replace("/", ".")


def symbol_id(module, symbol):
    return module.replace(".", "/") + ".py:" + symbol


def handler_registry():
    from app.services.background_worker import default_job_handlers

    return {
        key: symbol_id(inspect.unwrap(handler).__module__, inspect.unwrap(handler).__qualname__)
        for key, handler in sorted(default_job_handlers().items())
    }


def route_registry():
    """Inspect mounted routes without entering ASGI lifespan or connecting to DB."""
    from fastapi.routing import APIRoute

    from app.main import create_app

    return sorted(
        (
            {
                "entrypoint": symbol_id(
                    inspect.unwrap(route.endpoint).__module__,
                    inspect.unwrap(route.endpoint).__qualname__,
                ),
                "path": route.path,
                "methods": sorted(route.methods),
            }
            for route in create_app().routes
            if isinstance(route, APIRoute)
        ),
        key=lambda row: (row["path"], row["methods"], row["entrypoint"]),
    )


def graph(discovery, root=ROOT):
    functions = {f["id"]: f for f in discovery["functions"]}
    imports, defaults, constants = {}, {}, {}
    declared_returns, module_types, bases = {}, {}, {}
    returned_calls = defaultdict(list)
    source_by_id = {}
    by_path = defaultdict(list)
    for f in discovery["functions"]:
        by_path[f["path"]].append(f)
    for file in discovery["files"]:
        path = file["path"]
        module = module_name(path)
        source = (root / path).read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        bindings = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                for alias in node.names:
                    bindings[alias.asname or alias.name] = node.module + "." + alias.name
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imported = alias.name if alias.asname else alias.name.split(".")[0]
                    # A script's explicit fallback import uses the same sibling
                    # file when executed with scripts/ on sys.path.
                    if (
                        "." not in imported
                        and not (root / (imported + ".py")).exists()
                        and (root / "scripts" / (imported + ".py")).exists()
                    ):
                        imported = "scripts." + imported
                    bindings[alias.asname or alias.name.split(".")[0]] = imported
            elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                        constants[module + "." + target.id] = node.value.value
        imports[module] = bindings

        def full_name(name, bindings=bindings, module=module):
            return bindings.get(name, module + "." + name)

        module_types[module] = {}
        for declaration in tree.body:
            if isinstance(declaration, (ast.Assign, ast.AnnAssign)):
                value = declaration.value
                if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
                    targets = (
                        declaration.targets
                        if isinstance(declaration, ast.Assign)
                        else [declaration.target]
                    )
                    for target in targets:
                        if isinstance(target, ast.Name):
                            module_types[module][target.id] = [full_name(value.func.id)]
        for f in by_path[path]:
            snippet = textwrap.dedent("\n".join(source.splitlines()[f["line"] - 1 : f["end_line"]]))
            try:
                declaration = ast.parse(snippet).body[0]
            except SyntaxError:
                continue
            if (
                isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef))
                and declaration.returns
            ):
                declared_returns[f["id"]] = [
                    full_name(n.id)
                    for n in ast.walk(declaration.returns)
                    if isinstance(n, ast.Name)
                ]
            if isinstance(declaration, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for returned in scoped_nodes(declaration):
                    if (
                        isinstance(returned, ast.Return)
                        and isinstance(returned.value, ast.Call)
                        and isinstance(returned.value.func, ast.Name)
                    ):
                        returned_calls[f["id"]].append(full_name(returned.value.func.id))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases[full_name(node.name)] = [
                    full_name(n.id) for n in node.bases if isinstance(n, ast.Name)
                ]
                fields = {}
                argument_types = {}
                for method in node.body:
                    if isinstance(method, ast.FunctionDef) and method.name == "__init__":
                        for argument in method.args.args + method.args.kwonlyargs:
                            if argument.annotation:
                                argument_types[argument.arg] = [
                                    n.id
                                    for n in ast.walk(argument.annotation)
                                    if isinstance(n, ast.Name)
                                ]
                declaration_nodes = [
                    item
                    for item in node.body
                    if isinstance(item, (ast.Assign, ast.AnnAssign))
                    or isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == "__init__"
                ]
                for assignment in (
                    item for declaration in declaration_nodes for item in ast.walk(declaration)
                ):
                    if isinstance(assignment, (ast.Assign, ast.AnnAssign)):
                        targets = (
                            assignment.targets
                            if isinstance(assignment, ast.Assign)
                            else [assignment.target]
                        )
                        value = assignment.value
                        if value is None:
                            continue
                        names = [
                            n.func.id
                            for n in ast.walk(value)
                            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                        ]
                        if isinstance(value, ast.Name):
                            names.append(value.id)
                            names.extend(argument_types.get(value.id, []))
                        for target in targets:
                            field = (
                                target.attr
                                if isinstance(target, ast.Attribute)
                                and ast.unparse(target.value) == "self"
                                else target.id
                                if isinstance(target, ast.Name)
                                else None
                            )
                            if field and names:
                                fields[field] = [
                                    bindings.get(name, module + "." + name) for name in names
                                ]
                defaults[(module, node.name)] = fields
        lines = source.splitlines()
        for f in by_path[path]:
            source_by_id[f["id"]] = "\n".join(lines[f["line"] - 1 : f["end_line"]])
    # Resolve only a function's explicit returned constructor/callee, rather
    # than arbitrary nested calls or a method with a globally shared name.
    changed = True
    while changed:
        changed = False
        for key, returned in returned_calls.items():
            previous = set(declared_returns.get(key, []))
            resolved = set(previous)
            for full in returned:
                owner, symbol = full.rsplit(".", 1)
                if full in bases:
                    resolved.add(full)
                else:
                    resolved.update(declared_returns.get(symbol_id(owner, symbol), []))
            if resolved != previous:
                declared_returns[key] = sorted(resolved)
                changed = True

    def returned_types(full):
        owner, symbol = full.rsplit(".", 1)
        return declared_returns.get(symbol_id(owner, symbol), [full])

    for module_fields in module_types.values():
        for name, types in module_fields.items():
            module_fields[name] = [value for full in types for value in returned_types(full)]
    for class_fields in defaults.values():
        for name, types in class_fields.items():
            # A Callable field retains the function itself; a factory-assigned
            # service field additionally exposes its explicit returned class.
            class_fields[name] = sorted(
                set(types) | {value for full in types for value in returned_types(full)}
            )
    methods = defaultdict(list)
    for key, f in functions.items():
        methods[f["symbol"].rsplit(".", 1)[-1]].append(key)
    handlers = handler_registry()

    def method_targets(full, method):
        if "." not in full:
            return []
        owner, symbol = full.rsplit(".", 1)
        candidate = symbol_id(owner, symbol + "." + method)
        if candidate in functions:
            return [candidate]
        return [target for base in bases.get(full, []) for target in method_targets(base, method)]

    def typed_receivers(expression, module, local_types, class_name):
        if isinstance(expression, ast.Name):
            if expression.id in {"self", "cls"} and class_name:
                return [module + "." + class_name]
            if module + "." + expression.id in bases:
                return [module + "." + expression.id]
            imported = imports[module].get(expression.id)
            if imported and "." in imported:
                owner, symbol = imported.rsplit(".", 1)
                retained = module_types.get(owner, {}).get(symbol)
                if retained:
                    return retained
            return local_types.get(
                expression.id, module_types.get(module, {}).get(expression.id, [])
            )
        if isinstance(expression, ast.BoolOp):
            return [
                value
                for operand in expression.values
                for value in typed_receivers(operand, module, local_types, class_name)
            ]
        if isinstance(expression, ast.Subscript):
            # Declared dict/list element annotations are already retained in
            # local_types. No type is inferred merely from the subscript key.
            return typed_receivers(expression.value, module, local_types, class_name)
        if isinstance(expression, ast.Call):
            if isinstance(expression.func, ast.Name):
                full = imports[module].get(expression.func.id, module + "." + expression.func.id)
                owner, symbol = full.rsplit(".", 1)
                return declared_returns.get(symbol_id(owner, symbol), [full])
            if isinstance(expression.func, ast.Attribute):
                owners = typed_receivers(expression.func.value, module, local_types, class_name)
                return [
                    value
                    for owner in owners
                    for target in method_targets(owner, expression.func.attr)
                    for value in declared_returns.get(target, [])
                ]
        if isinstance(expression, ast.Attribute):
            result = []
            for full in typed_receivers(expression.value, module, local_types, class_name):
                if "." not in full:
                    continue
                owner, symbol = full.rsplit(".", 1)
                result.extend(defaults.get((owner, symbol), {}).get(expression.attr, []))
            return result
        return []

    job_names = defaultdict(dict)
    for module, bindings in imports.items():
        for name, full in bindings.items():
            value = constants.get(full)
            if value in handlers:
                job_names[module][name] = value
    for full, value in constants.items():
        if value in handlers:
            module, name = full.rsplit(".", 1)
            job_names[module][name] = value
    edges, unresolved = {}, []
    for key, f in functions.items():
        module = module_name(f["path"])
        bindings = imports[module]
        targets = {}
        class_name = f["symbol"].split(".")[0] if "." in f["symbol"] else None
        local_types = {}
        try:
            local_tree = ast.parse(textwrap.dedent(source_by_id[key]))
        except SyntaxError:
            local_tree = ast.Module(body=[], type_ignores=[])
        local_nodes = list(scoped_nodes(local_tree.body[0])) if local_tree.body else []
        if local_tree.body and isinstance(
            local_tree.body[0], (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            for argument in local_tree.body[0].args.args + local_tree.body[0].args.kwonlyargs:
                if argument.annotation:
                    local_types[argument.arg] = [
                        bindings.get(node.id, module + "." + node.id)
                        for node in ast.walk(argument.annotation)
                        if isinstance(node, ast.Name)
                    ]
        referenced_names = {n.id for n in local_nodes if isinstance(n, ast.Name)}
        referenced_strings = {
            n.value for n in local_nodes if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }
        for assignment in local_nodes:
            if isinstance(assignment, (ast.Assign, ast.AnnAssign)):
                value = assignment.value
                if value is None:
                    continue
                targets_ = (
                    assignment.targets
                    if isinstance(assignment, ast.Assign)
                    else [assignment.target]
                )
                constructors = [
                    n.func.id
                    for n in ast.walk(value)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                ]
                for target in targets_:
                    if isinstance(target, ast.Name):
                        declared = typed_receivers(value, module, local_types, class_name)
                        declared.extend(
                            bindings.get(name, module + "." + name) for name in constructors
                        )
                        if isinstance(assignment, ast.AnnAssign):
                            declared.extend(
                                bindings.get(n.id, module + "." + n.id)
                                for n in ast.walk(assignment.annotation)
                                if isinstance(n, ast.Name)
                            )
                        local_types.setdefault(target.id, []).extend(declared)
        # This wrapper invokes its first argument, preserving only supported
        # context kwargs. Passing a declared dependency into it is a concrete
        # callback edge, although the dependency is not itself an ast.Call.
        if module == "app.services.pipeline_executor":
            for call_node in local_nodes:
                if (
                    isinstance(call_node, ast.Call)
                    and isinstance(call_node.func, ast.Name)
                    and call_node.func.id == "_call_market_sensitive"
                    and call_node.args
                ):
                    callback = call_node.args[0]
                    if (
                        isinstance(callback, ast.Attribute)
                        and isinstance(callback.value, ast.Name)
                        and callback.value.id == "dependencies"
                    ):
                        for full in defaults.get((module, "PipelineExecutionDependencies"), {}).get(
                            callback.attr, []
                        ):
                            owner, symbol = full.rsplit(".", 1)
                            target = symbol_id(owner, symbol)
                            if target in functions:
                                targets[target] = "MARKET_SENSITIVE_CALLBACK_DEFAULT"
        for call in f["calls"]:
            parts = call.split(".")
            candidates = []
            try:
                expression = ast.parse(call, mode="eval").body
            except SyntaxError:
                expression = None
            if isinstance(expression, ast.Attribute):
                for full in typed_receivers(expression.value, module, local_types, class_name):
                    candidates.extend(
                        (target, "DECLARED_RECEIVER_OR_RETURN")
                        for target in method_targets(full, expression.attr)
                    )
            if (
                isinstance(expression, ast.Attribute)
                and isinstance(expression.value, ast.Call)
                and isinstance(expression.value.func, ast.Name)
            ):
                constructor = expression.value.func.id
                full = bindings.get(constructor, module + "." + constructor)
                owner, symbol = full.rsplit(".", 1)
                candidates.append(
                    (symbol_id(owner, symbol + "." + expression.attr), "CONSTRUCTED_SERVICE_METHOD")
                )
            optional_receiver = expression.value if isinstance(expression, ast.Attribute) else None
            if isinstance(optional_receiver, ast.Call):
                optional_receiver = optional_receiver.func
            if isinstance(optional_receiver, ast.BoolOp):
                for default in ast.walk(optional_receiver):
                    if isinstance(default, ast.Call) and isinstance(default.func, ast.Name):
                        full = bindings.get(default.func.id, module + "." + default.func.id)
                        if "." in full:
                            owner, symbol = full.rsplit(".", 1)
                            candidates.append(
                                (
                                    symbol_id(owner, symbol + "." + expression.attr),
                                    "EXPLICIT_OR_DEFAULT",
                                )
                            )
            if len(parts) == 1:
                candidates.append((symbol_id(module, call), "LOCAL_CALL"))
                if call in bindings:
                    full = bindings[call]
                    owner, symbol = full.rsplit(".", 1)
                    candidates.append((symbol_id(owner, symbol), "IMPORTED_CALL"))
            elif parts[0] in {"self", "cls"}:
                candidates.append((symbol_id(module, class_name + "." + parts[-1]), "LOCAL_METHOD"))
                if len(parts) == 3:
                    for full in defaults.get((module, class_name), {}).get(parts[1], []):
                        owner, symbol = full.rsplit(".", 1)
                        candidates.append(
                            (symbol_id(owner, symbol + "." + parts[-1]), "DEFAULT_IMPLEMENTATION")
                        )
            elif parts[0] in bindings:
                full = bindings[parts[0]] + "." + ".".join(parts[1:])
                for split in range(1, len(full.split("."))):
                    components = full.split(".")
                    candidates.append(
                        (
                            symbol_id(".".join(components[:split]), ".".join(components[split:])),
                            "IMPORTED_METHOD",
                        )
                    )
            elif parts[0] == "dependencies":
                for full in defaults.get((module, "PipelineExecutionDependencies"), {}).get(
                    parts[-1], []
                ):
                    owner, symbol = full.rsplit(".", 1)
                    candidates.append((symbol_id(owner, symbol), "DEFAULT_PIPELINE_DEPENDENCY"))
            elif parts[0] in local_types:
                for full in local_types[parts[0]]:
                    if "." not in full:
                        continue
                    owner, symbol = full.rsplit(".", 1)
                    candidates.append(
                        (symbol_id(owner, symbol + "." + parts[-1]), "LOCAL_DEFAULT_IMPLEMENTATION")
                    )
            resolved = [
                (candidate, kind) for candidate, kind in candidates if candidate in functions
            ]
            reviewed_dispatch = REVIEWED_DISPATCH.get((key, call))
            if reviewed_dispatch:
                resolved.append((reviewed_dispatch[0], "SOURCE_REVIEWED_DISPATCH"))
            if (
                not resolved
                and len(parts) > 1
                and parts[-1]
                not in {"add", "delete", "execute", "commit", "flush", "update", "get"}
            ):
                same_package = [
                    m
                    for m in methods.get(parts[-1], [])
                    if m.split(":")[0].startswith(f["path"].rsplit("/", 1)[0] + "/")
                ]
                options = same_package or methods.get(parts[-1], [])
                if options:
                    row = {"caller": key, "call": call, "candidates": sorted(options)}
                    review = NON_EDGE_REVIEWS.get((key, call))
                    if review:
                        row["review_status"] = "FALSE_POSITIVE"
                        row["review_proof"] = review
                    unresolved.append(row)
            for candidate, kind in resolved:
                if candidate != key:
                    targets[candidate] = kind
        # Enqueue boundaries: concrete job constants/literals referenced by this
        # function. Parameterized enqueue helpers also remain visible as helpers.
        if any("enqueue" in call or "schedule" in call for call in f["calls"]):
            referenced_jobs = referenced_strings.intersection(handlers)
            referenced_jobs.update(
                value for name, value in job_names[module].items() if name in referenced_names
            )
            for job_type in referenced_jobs:
                handler = handlers[job_type]
                if handler in functions and handler != key:
                    targets[handler] = "ENQUEUE_TO_REGISTERED_HANDLER"
        edges[key] = targets
        if key in VALUE_ONLY_BOUNDARIES:
            edges[key] = {}
    return edges, sorted(unresolved, key=lambda r: (r["caller"], r["call"])), handlers, source_by_id


def sql_tables(f, models):
    tables = set()
    for statement in f["sql_mutation_strings"]:
        for match in re.finditer(
            r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM|TRUNCATE(?:\s+TABLE)?)\s+([a-z_][a-z_0-9]*)",
            statement,
            re.I,
        ):
            name = match.group(1).lower()
            if name in models.values():
                tables.add(name)
    return tables


def semantic_mode(f):
    name = f["symbol"].lower()
    if "replay" in name or "reconstruction" in f["path"] or "historical_backfill" in name:
        return "CURRENT_RULES_RETROSPECTIVE"
    if any(
        word in name for word in ("repair", "quarantine", "certification", "backfill", "rescore")
    ):
        return "CURRENT_STATE_REPAIR"
    if "publish" in name or "publication" in f["path"]:
        return "PUBLICATION"
    if "bootstrap" in name or "seed_builtin" in name:
        return "BOOTSTRAP"
    if "maintenance" in name or "purge" in name or "cleanup" in name:
        return "MAINTENANCE"
    if "matur" in name or "outcome_service" in f["path"]:
        return "OUTCOME_MATURATION"
    if "legacy" in name:
        return "LEGACY_UNCERTIFIED"
    return "CANONICAL_CALCULATION"


def build_inventory(root=ROOT):
    discovery = discover(root)
    models = discovery["models"]
    functions = {f["id"]: f for f in discovery["functions"]}
    for f in functions.values():
        proof = ISOLATED_SURFACES.get(f["path"]) or TEST_VALUE_SURFACES.get(f["path"])
        if proof:
            f["scope"] = "TEST_ONLY"
            f["scope_proof"] = proof
    edges, unresolved, handlers, sources = graph(discovery, root)
    writers, source_census, unknown = {}, [], []
    for key, f in functions.items():
        direct_sql = sql_tables(f, models)
        mapped = set(OVERRIDES.get(key, f["write_models"]))
        tables = {models[m] for m in mapped} | direct_sql
        # Generic persistence delegated by this semantic method is also a writer.
        delegates = any(
            call.endswith((".add", ".add_all")) and "db." not in call for call in f["calls"]
        )
        material = bool(
            f["orm_sites"]
            or f["field_sites"]
            or direct_sql
            or (delegates and f["created_models"])
            or key in OVERRIDES
        )
        scope = f["scope"]
        if not material and not (
            f["sites"] or f["campaign"] or f["routes"] or f["attribute_sites"]
        ):
            continue
        classification = "DISCOVERY_ONLY"
        if scope != "PRODUCTION":
            classification = scope
        elif key in TRANSIENT or (
            f["field_sites"]
            and not f["orm_sites"]
            and not direct_sql
            and f["created_models"]
            and not delegates
        ):
            classification = "TRANSIENT_BUILDER"
        elif material:
            if not tables:
                classification = "UNKNOWN_WRITER"
                unknown.append(key)
            elif tables <= OPERATIONAL_TABLES:
                classification = "OPERATIONAL_WRITER"
            elif direct_sql:
                classification = "DIRECT_SQL"
            elif tables <= PROJECTIONS:
                classification = "CURRENT_PROJECTION_WRITER"
            elif semantic_mode(f) in {"CURRENT_STATE_REPAIR", "CURRENT_RULES_RETROSPECTIVE"}:
                classification = "CURRENT_RULES_WRITER"
            elif semantic_mode(f) == "LEGACY_UNCERTIFIED":
                classification = "LEGACY_WRITER"
            else:
                classification = "SHARED_DOMAIN_WRITER"
        row = {
            "writer": key,
            "line": f["line"],
            "classification": classification,
            "semantic_mode": "OPERATIONAL"
            if classification == "OPERATIONAL_WRITER"
            else semantic_mode(f),
            "models": sorted(mapped),
            "tables": sorted(tables),
            "mechanisms": sorted({s["mechanism"] for s in f["orm_sites"]}),
            "field_sites": f["field_sites"],
            "campaign": f["campaign"],
            "scope": scope,
            "attribute_sites": f["attribute_sites"],
            "scope_proof": f.get("scope_proof", "production callable unless proven isolated"),
            "mode_proof": "NAME_CANDIDATE_REQUIRES_SEMANTIC_REVIEW",
        }
        source_census.append(row)
        if key == "scripts/verify_owpe_pre11_activation.py:main":
            row["classification"] = "ROLLBACK_ONLY_SQL_PROBE"
            row["scope_proof"] = "_mutation_is_rejected rolls back on success and exception"
        if material and row["classification"] not in {
            "TRANSIENT_BUILDER",
            "TEST_ONLY",
            "MIGRATION_ONLY",
            "ROLLBACK_ONLY_SQL_PROBE",
        }:
            writers[key] = row

    predecessors = defaultdict(set)
    for caller, children in edges.items():
        for child in children:
            predecessors[child].add(caller)
    writer_ancestors = set(writers)
    queue = deque(writers)
    while queue:
        for caller in predecessors.get(queue.popleft(), ()):
            if caller not in writer_ancestors:
                writer_ancestors.add(caller)
                queue.append(caller)

    def reachable(start):
        if start not in writer_ancestors:
            return {}
        queue = deque([(start, (start,))])
        seen, paths = set(), {}
        while queue:
            node, path = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            if node in writers:
                paths[node] = path
            for child in sorted(set(edges.get(node, {})).intersection(writer_ancestors)):
                queue.append((child, (*path, child)))
        return paths

    entrypoints, route_census = [], []
    handler_by_function = defaultdict(list)
    for job_type, handler in handlers.items():
        handler_by_function[handler].append(job_type)
    for key, f in functions.items():
        if f["scope"] != "PRODUCTION":
            continue
        paths = reachable(key)
        has_business = any(writers[w]["classification"] != "OPERATIONAL_WRITER" for w in paths)
        if f["routes"]:
            for route in f["routes"]:
                unsafe = bool(re.search(r"\.(post|put|patch|delete|api_route)\(", route))
                route_census.append(
                    {
                        "entrypoint": key,
                        "route": route,
                        "classification": "BUSINESS_MUTATING"
                        if has_business
                        else "OPERATIONAL_ONLY"
                        if paths
                        else "NO_PERSISTED_WRITER_FOUND",
                        "unsafe_method": unsafe,
                    }
                )
        public_service = (
            f.get("public_addressable", True)
            and f["path"].startswith("app/services/")
            and not f["symbol"].rsplit(".", 1)[-1].startswith("_")
        )
        startup = key in {
            "app/main.py:lifespan",
            "app/services/background_worker.py:run_worker",
            "app/worker.py:main",
            "app/worker_supervisor.py:main",
        }
        if startup:
            # Startup registration is separate from the worker's subsequent loop.
            paths = {
                writer: path
                for writer, path in paths.items()
                if writers[writer]["classification"] == "OPERATIONAL_WRITER"
                and not any("run_worker_once" in step or "scheduler.py:" in step for step in path)
            }
            has_business = False
        scheduler = "scheduler.py" in f["path"] and f["symbol"].startswith("schedule_")
        cli = f["path"].startswith("scripts/") and f["symbol"] == "main"
        if (
            f["routes"]
            or key in handler_by_function
            or public_service
            or startup
            or scheduler
            or cli
        ) and paths:
            kind = (
                "DURABLE_JOB"
                if key in handler_by_function
                else "STARTUP"
                if startup
                else "SCHEDULER"
                if scheduler
                else "CLI"
                if cli
                else "HTTP"
                if f["routes"]
                else "SERVICE_PUBLIC"
            )
            entrypoints.append(
                {
                    "entrypoint": key,
                    "line": f["line"],
                    "kind": kind,
                    "semantic_mode": semantic_mode(f),
                    "business_mutating": has_business,
                    "registered_jobs": handler_by_function.get(key, []),
                    "paths": {writer: list(path) for writer, path in sorted(paths.items())},
                }
            )
    reverse = {}
    for model, table in models.items():
        matching = [key for key, writer in writers.items() if table in writer["tables"]]
        reverse[table] = {
            "model": model,
            "writers": sorted(matching),
            "status": "KNOWN_WRITERS"
            if matching
            else "READ_ONLY_LEGACY"
            if model in READ_ONLY_MODELS
            else "UNKNOWN_TABLE_WRITERS",
            "domain": table_domain(table),
            "canonical_writer": table_owner(table),
            "artifact_classification": TABLE_REVIEWS[table].classification
            if table in TABLE_REVIEWS
            else "OPERATIONAL"
            if table in OPERATIONAL_TABLES
            else "PRINCIPAL_DOMAIN_ARTIFACT",
            "ownership_proof": TABLE_REVIEWS[table].proof
            if table in TABLE_REVIEWS
            else "Principal native owner retained in DOMAIN_OWNERS; "
            "alternate persistence sites retained",
            "disposition": TABLE_REVIEWS[table].disposition
            if table in TABLE_REVIEWS
            else "OPERATIONAL_NO_ACTION"
            if table in OPERATIONAL_TABLES
            else "T14A_PATH_REVIEW_REQUIRED",
            "entrypoints": sorted(
                entry["entrypoint"]
                for entry in entrypoints
                if any(writer in entry["paths"] for writer in matching)
            ),
        }
    mounted_routes = []
    entries = {entry["entrypoint"]: entry for entry in entrypoints}
    for route in route_registry():
        entry = entries.get(route["entrypoint"])
        route["classification"] = (
            "BUSINESS_MUTATING"
            if entry and entry["business_mutating"]
            else "OPERATIONAL_ONLY"
            if entry
            else "NO_PERSISTED_WRITER_FOUND"
        )
        route["unsafe_method"] = bool(set(route["methods"]) - {"GET", "HEAD", "OPTIONS"})
        mounted_routes.append(route)
    result = {
        "writers": list(writers.values()),
        "entrypoints": entrypoints,
        "routes": mounted_routes,
        "route_decorator_candidates": route_census,
        "handlers": handlers,
        "reverse_index": reverse,
        "source_census": source_census,
        "unresolved_static_calls": unresolved,
        "unknown_writers": unknown,
        "files": discovery["files"],
        "call_edges": {key: value for key, value in sorted(edges.items()) if value},
        "proof_boundary": (
            "Static candidates plus declared defaults, not complete Python runtime reachability. "
            "The raw 1667 UNIQUE_SERVICE_SYMBOL edges are preserved separately and never become "
            "proven graph edges by name alone. Only explicit branch reviews replace candidate "
            "mode/authority labels; other paths remain uncertified."
        ),
        "value_only_boundaries": VALUE_ONLY_BOUNDARIES,
        "domain_owners": DOMAIN_OWNERS,
        "module_sites": discovery["module_sites"],
        "certification": {
            "verdict": "FAIL",
            "reason": (
                "Writer ownership and initiator discovery/disposition are the foundation gate. "
                "Caller authority adoption belongs to T14B/C/D. "
                "Raw inferred edges and unreviewed path permutations are discovery evidence, "
                "not independent PASS obligations."
            ),
            "business_entrypoint_candidates": sum(e["business_mutating"] for e in entrypoints),
            "business_writer_sites": sum(
                w["classification"] != "OPERATIONAL_WRITER" for w in writers.values()
            ),
            "actual_semantic_writer_count": None,
            "actual_production_entrypoint_count": None,
        },
        "material_unresolved_calls": [
            row
            for row in unresolved
            if functions[row["caller"]]["scope"] == "PRODUCTION"
            and row.get("review_status") != "FALSE_POSITIVE"
            and any(candidate in writer_ancestors for candidate in row["candidates"])
        ],
    }
    reviewed_pairs = {(review.entrypoint, review.writer) for review in PATH_REVIEWS}
    result["semantic_review_progress"] = {
        "reviewed_path_variants": len(PATH_REVIEWS),
        "reviewed_entrypoint_writer_pairs": len(reviewed_pairs),
        "reviewed_table_ownership_records": len(TABLE_REVIEWS),
        "unreviewed_auxiliary_owners": [
            table for table, row in reverse.items() if row["canonical_writer"] == "UNREVIEWED"
        ],
        "unreviewed_business_entrypoints": [
            entry["entrypoint"]
            for entry in entrypoints
            if entry["business_mutating"]
            and any(
                writers[writer]["classification"] != "OPERATIONAL_WRITER"
                and (entry["entrypoint"], writer) not in reviewed_pairs
                for writer in entry["paths"]
            )
        ],
        "reviewed_false_positive_call_sites": sum(
            row.get("review_status") == "FALSE_POSITIVE" for row in unresolved
        ),
        "unresolved_material_call_sites": len(result["material_unresolved_calls"]),
    }
    from scripts.qa.t14a_semantic_completeness import semantic_completeness

    result["semantic_completeness"] = semantic_completeness(result)
    result["certification"].update(
        {
            key: value
            for key, value in result["semantic_completeness"].items()
            if key
            in {
                "verdict",
                "actual_semantic_writer_count",
                "actual_production_entrypoint_count",
            }
        }
    )
    return result


def write_csv(path, fields, rows):
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def inferred_edge_review(inventory):
    raw_path = DESTINATION / "T14A_raw_discovery.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    reviewed = []
    for caller, targets in sorted(raw["call_edges"].items()):
        for target, kind in sorted(targets.items()):
            if kind != "UNIQUE_SERVICE_SYMBOL":
                continue
            current = inventory["call_edges"].get(caller, {}).get(target)
            if current:
                status = "CONFIRMED_CALL_GRAPH"
                proof = "Exact declared/imported/default source resolution: " + current
            else:
                status = "UNREVIEWED"
                proof = (
                    "Global method-name inference removed; receiver family review still required"
                )
            reviewed.append({"caller": caller, "target": target, "status": status, "proof": proof})
    return reviewed


def export_inventory(destination=DESTINATION):
    inventory = build_inventory()
    inventory["inferred_edge_reviews"] = inferred_edge_review(inventory)
    inventory["raw_inferred_edge_count"] = len(inventory["inferred_edge_reviews"])
    from scripts.qa.t14a_semantic_completeness import semantic_completeness

    inventory["semantic_completeness"] = semantic_completeness(inventory)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "T14A_mutation_source_census.json").write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    write_csv(
        destination / "T14A_inferred_edge_review.csv",
        ["caller", "target", "status", "proof"],
        inventory["inferred_edge_reviews"],
    )
    raw = json.loads((DESTINATION / "T14A_raw_discovery.json").read_text(encoding="utf-8"))
    current_entries = {row["entrypoint"]: row for row in inventory["entrypoints"]}
    current_writers = {row["writer"]: row for row in inventory["writers"]}
    trace = []
    for category, field in (("ENTRYPOINT", "entrypoint"), ("WRITER_SITE", "writer")):
        current = current_entries if field == "entrypoint" else current_writers
        for candidate in raw["entrypoints" if field == "entrypoint" else "writers"]:
            address = candidate[field]
            proof = VALUE_ONLY_BOUNDARIES.get(address)
            if proof:
                status = "READ_ONLY_NO_ACTION"
            elif address in current:
                status = "RETAINED_CANDIDATE"
                proof = (
                    "Exact source address retained; ownership links do not certify path semantics"
                )
            elif address.count(".") > 2 and ".evidence_progress_guard" in address:
                status = "NESTED_CALLBACK_MECHANISM"
                proof = (
                    "Lexically nested callback; public_addressable=False; enclosing owner retained"
                )
            else:
                status = "UNREVIEWED_DISCOVERY_DIFFERENCE"
                proof = "Old inferred reachability removed; explicit disposition remains required"
            trace.append(
                {"category": category, "raw_address": address, "status": status, "proof": proof}
            )
    write_csv(
        destination / "T14A_raw_candidate_trace.csv",
        ["category", "raw_address", "status", "proof"],
        trace,
    )
    rows = []
    by_writer = {writer["writer"]: writer for writer in inventory["writers"]}
    for entry in inventory["entrypoints"]:
        for writer, path in entry["paths"].items():
            site = by_writer[writer]
            domains = sorted({table_domain(table) for table in site["tables"]})
            proof = sorted(
                {inventory["call_edges"][a][b] for a, b in zip(path, path[1:], strict=False)}
            )
            operational = site["classification"] == "OPERATIONAL_WRITER"
            status = "NOT_APPLICABLE" if operational else "UNREVIEWED"
            rows.append(
                {
                    "entrypoint": entry["entrypoint"],
                    "line": entry["line"],
                    "kind": entry["kind"],
                    "semantic_mode": entry["semantic_mode"],
                    "business_mutating": entry["business_mutating"],
                    "writer": writer,
                    "path": " -> ".join(path),
                    "proof": ";".join(proof) or "DIRECT_SITE",
                    "domain": ";".join(domains),
                    "canonical_writer": ";".join(
                        sorted({table_owner(table) for table in site["tables"]})
                    ),
                    "tables": ";".join(site["tables"]),
                    "calculation_identity": status,
                    "temporal": status,
                    "evidence": status,
                    "readiness": status,
                    "configuration": status,
                    "execution_fencing": status,
                    "pipeline_run": status,
                    "current_latest_fallback": status,
                    "classification": "OPERATIONAL_ONLY" if operational else "UNKNOWN_ENTRY_POINT",
                    "target_task": "T14A_REVIEW_THEN_T14B_C_D",
                }
            )
            variants = [
                review
                for review in PATH_REVIEWS
                if (review.entrypoint, review.writer) == (entry["entrypoint"], writer)
            ]
            if variants:
                base = rows.pop()
                for review in variants:
                    rows.append(
                        {
                            **base,
                            **dict(zip(AUTHORITY_FIELDS, review.authorities, strict=True)),
                            "semantic_mode": review.mode,
                            "classification": review.classification,
                            "target_task": review.disposition,
                            "proof": base["proof"] + ";" + review.variant + ": " + review.proof,
                        }
                    )
    write_csv(
        destination / "T14A_entrypoint_writer_inventory.csv",
        [
            "entrypoint",
            "line",
            "kind",
            "semantic_mode",
            "business_mutating",
            "writer",
            "path",
            "proof",
            "domain",
            "canonical_writer",
            "tables",
            "calculation_identity",
            "temporal",
            "evidence",
            "readiness",
            "configuration",
            "execution_fencing",
            "pipeline_run",
            "current_latest_fallback",
            "classification",
            "target_task",
        ],
        rows,
    )
    rows = []
    for writer in inventory["writers"]:
        for table in writer["tables"] or [""]:
            rows.append(
                {
                    "writer": writer["writer"],
                    "line": writer["line"],
                    "classification": writer["classification"],
                    "semantic_mode": writer["semantic_mode"],
                    "table": table,
                    "mechanisms": ";".join(writer["mechanisms"]),
                    "domain": table_domain(table),
                    "canonical_writer": table_owner(table),
                    "transaction_boundary": "CALLER_SESSION; REVIEW_COMMIT_PATH",
                    "execution_fence": "DURABLE_ONLY; ENTRY_PATH_REVIEW_REQUIRED",
                    "callers": ";".join(
                        sorted(
                            entry["entrypoint"]
                            for entry in inventory["entrypoints"]
                            if writer["writer"] in entry["paths"]
                        )
                    ),
                }
            )
    write_csv(
        destination / "T14A_writer_table_inventory.csv",
        [
            "writer",
            "line",
            "classification",
            "semantic_mode",
            "table",
            "mechanisms",
            "domain",
            "canonical_writer",
            "transaction_boundary",
            "execution_fence",
            "callers",
        ],
        rows,
    )
    from app.services.domain_mutation import MUTATION_AUTHORITY_POLICIES

    rows = [
        {
            "domain": p.domain.value,
            "identity": p.identity,
            "temporal": p.temporal,
            "configuration": p.configuration_namespace or "NOT_REQUIRED",
            "required_evidence": ";".join(p.evidence_roles),
            "optional_evidence": ";".join(p.optional_evidence_roles),
            "eligibility": ";".join(p.eligibility_roles),
            "mandatory_consumption": ";".join(p.mandatory_consumption),
            "run": p.run,
            "pipeline": "EXACT_IF_BOUND" if p.pipeline_if_bound else "NOT_REQUIRED",
            "fence": "DURABLE_ONLY" if p.fence_if_durable else "NOT_REQUIRED",
            "modes": ";".join(sorted(mode.value for mode in p.modes)),
            "eligibility_if_pinned": ";".join(p.eligibility_if_pinned),
            "canonical_writer": DOMAIN_OWNERS.get(p.domain.value, "UNREVIEWED"),
            "artifact": ";".join(
                sorted(
                    table
                    for table in inventory["reverse_index"]
                    if table_domain(table) == p.domain.value
                )
            )
            or "DOMAIN_DECLARATION",
            "projection_rules": "REUSE_NATIVE_SCOPE_COMPATIBILITY_AND_MONOTONIC_ORDER",
            "legacy_behavior": "DECLARATION_POLICY_REJECTS_LEGACY; EXISTING_ADOPTION_DEFERRED",
            "authority_review_status": "DECLARED_POLICY; EXISTING_ADOPTION_DEFERRED",
        }
        for p in MUTATION_AUTHORITY_POLICIES.values()
    ]
    declared = {p.domain.value for p in MUTATION_AUTHORITY_POLICIES.values()}
    for domain in sorted({table_domain(table) for table in inventory["reverse_index"]} - declared):
        tables = sorted(
            table for table in inventory["reverse_index"] if table_domain(table) == domain
        )
        read_only = all(
            inventory["reverse_index"][table]["status"] == "READ_ONLY_LEGACY" for table in tables
        )
        status = (
            "NOT_REQUIRED_READ_ONLY"
            if read_only
            else "REUSE_CALLER_DOMAIN_NATIVE_CORE_EVIDENCE_POLICY"
            if domain == "SHARED_LEDGER"
            else "DECLARATION_GAP_REQUIRES_T14A_REVIEW"
        )
        extra = dict.fromkeys(rows[0], status)
        extra.update(
            domain=domain,
            canonical_writer=";".join(sorted({table_owner(table) for table in tables})),
            artifact=";".join(tables),
            authority_review_status=status,
        )
        rows.append(extra)
    write_csv(destination / "T14A_domain_authority_matrix.csv", list(rows[0]), rows)
    write_csv(
        destination / "T14A_table_writer_reverse_index.csv",
        [
            "table",
            "model",
            "domain",
            "canonical_writer",
            "writers",
            "entrypoints",
            "status",
            "artifact_classification",
            "ownership_proof",
            "disposition",
        ],
        [
            {
                "table": table,
                **{
                    key: ";".join(value) if isinstance(value, list) else value
                    for key, value in row.items()
                },
            }
            for table, row in sorted(inventory["reverse_index"].items())
        ],
    )
    from scripts.qa.t14a_semantic_families import link_exports

    link_exports(destination, inventory)
    return inventory


if __name__ == "__main__":
    result = export_inventory()
    print(
        json.dumps(
            {
                "writers": len(result["writers"]),
                "entrypoints": len(result["entrypoints"]),
                "routes": len(result["routes"]),
                "handlers": len(result["handlers"]),
                "writer_classifications": dict(
                    Counter(w["classification"] for w in result["writers"])
                ),
                "unknown_writers": result["unknown_writers"],
                "unknown_tables": {
                    table: row
                    for table, row in result["reverse_index"].items()
                    if row["status"] == "UNKNOWN_TABLE_WRITERS"
                },
            },
            indent=2,
        )
    )
