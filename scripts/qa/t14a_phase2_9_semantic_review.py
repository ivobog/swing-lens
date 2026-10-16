"""Reviewed Phase 2.9 additions to the historical T14 semantic registry.

The T14D JSON remains an immutable historical review artifact.  This module is
the release-local overlay for source surfaces introduced after that review.  It
uses exact source addresses and fails closed when a referenced family or entry
is absent from the current inventory.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def _family(
    family_id: str,
    *,
    owner: str,
    members: tuple[str, ...],
    domains: tuple[str, ...],
    tables: tuple[str, ...],
    status: str,
    disposition: str,
    proof: str,
) -> dict[str, Any]:
    return {
        "writer_family_id": family_id,
        "owner": owner,
        "member_sites": list(members),
        "domains": list(domains),
        "tables": list(tables),
        "status": status,
        "disposition": disposition,
        "proof": proof,
        "semantic_review_complete": True,
        "authority": (
            "Exact source-address review under the native mutation policy; no caller, "
            "table, or domain is inferred from a shared method name."
        ),
        "known_callers": [],
        "known_alternate_families": [],
        "modes": ["CANONICAL_CALCULATION"],
        "phase5_status": "PHASE2_9_REVIEWED",
    }


NEW_WRITER_FAMILIES = {
    "WF_JOB_RECOVERY_REQUEUE": _family(
        "WF_JOB_RECOVERY_REQUEUE",
        owner="app/services/background_job_service.py:requeue_stalled_jobs",
        members=("app/services/background_job_service.py:requeue_stalled_jobs",),
        domains=("PIPELINE",),
        tables=("background_jobs", "pipeline_steps"),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14D",
        proof=(
            "Lease-authorized stalled-job recovery requeues eligible jobs and repairs only the "
            "associated pipeline-step execution state."
        ),
    ),
    "WF_CERI_FEATURE_CERTIFICATION": _family(
        "WF_CERI_FEATURE_CERTIFICATION",
        owner="app/services/ceri/feature_certification_workflow.py:admit_ceri_feature_certification",
        members=(
            "app/services/ceri/feature_certification_workflow.py:admit_ceri_feature_certification",
            "app/services/ceri/feature_certification_workflow.py:execute_ceri_feature_certification_job",
        ),
        domains=("PIPELINE",),
        tables=("pipeline_runs", "pipeline_steps", "upload_runs"),
        status="CURRENT_RULES_WRITER",
        disposition="T14D",
        proof=(
            "Explicit certification admission and its registered durable handler bind the same "
            "upload/pipeline owner before executing current-rule CERI feature work."
        ),
    ),
    "WF_CERI_RUN_SOURCE_MEMBERSHIP": _family(
        "WF_CERI_RUN_SOURCE_MEMBERSHIP",
        owner="app/services/ceri/source_record_service.py:_associate_source_record",
        members=(
            "app/services/ceri/source_record_service.py:_associate_source_record",
            "app/services/ceri/normalization_service.py:_mark_membership",
        ),
        domains=("CERI_SOURCE",),
        tables=("ceri_ingestion_run_source_records",),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14B",
        proof=(
            "Both exact helpers append the run-local membership of an immutable deduplicated "
            "provider fact; neither rewrites the source record."
        ),
    ),
    "WF_CERI_FEATURE_SOURCE_MANIFEST": _family(
        "WF_CERI_FEATURE_SOURCE_MANIFEST",
        owner="app/services/ceri/source_manifest_service.py:freeze_or_verify_feature_source_manifest",
        members=(
            "app/services/ceri/source_manifest_service.py:freeze_or_verify_feature_source_manifest",
        ),
        domains=("CERI_SOURCE",),
        tables=("ceri_feature_source_manifests",),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14B",
        proof=(
            "Feature work freezes exact source identities once and verifies an identical manifest "
            "on retry, preventing a latest-source reselection."
        ),
    ),
    "WF_INSTRUMENT_LIFECYCLE": _family(
        "WF_INSTRUMENT_LIFECYCLE",
        owner="app/services/instrument_lifecycle_service.py:record_instrument_lifecycle",
        members=(
            "app/services/instrument_lifecycle_service.py:record_instrument_lifecycle",
            "app/services/instrument_lifecycle_service.py:_clear_cached_identity",
        ),
        domains=("IBMI_SOURCE",),
        tables=("instrument_lifecycle_records", "ib_contracts"),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14B",
        proof=(
            "A provider-observed lifecycle record and its conditional contract-identity "
            "invalidation "
            "form one IBMI source-readiness operation."
        ),
    ),
    "WF_MARKET_DATA_SESSION_READINESS": _family(
        "WF_MARKET_DATA_SESSION_READINESS",
        owner="app/services/market_data_session_readiness.py:evaluate_and_persist_session_readiness",
        members=(
            "app/services/market_data_session_readiness.py:evaluate_and_persist_session_readiness",
        ),
        domains=("PRICE", "IBMI_SOURCE"),
        tables=(
            "market_data_session_dispositions",
            "instrument_lifecycle_records",
            "ib_contracts",
            "price_bars",
        ),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14B",
        proof=(
            "The native evaluator persists a session disposition from calendar, bar, contract, and "
            "instrument-lifecycle evidence without manufacturing a price observation."
        ),
    ),
    "WF_PIPELINE_DEPENDENCY": _family(
        "WF_PIPELINE_DEPENDENCY",
        owner="app/services/pipeline_dependency_service.py:prepare_ceri_workflow_dependency",
        members=(
            "app/services/pipeline_dependency_service.py:_enqueue_dependency_continuation",
            "app/services/pipeline_dependency_service.py:_reconcile_dependency_job",
            "app/services/pipeline_dependency_service.py:complete_ceri_workflow_dependency",
            "app/services/pipeline_dependency_service.py:enqueue_sec_readiness_dependency",
            "app/services/pipeline_dependency_service.py:fail_ceri_workflow_dependency",
            "app/services/pipeline_dependency_service.py:mark_dependency_job_running",
            "app/services/pipeline_dependency_service.py:prepare_ceri_workflow_dependency",
            "app/services/pipeline_dependency_service.py:prepare_sec_readiness_dependency",
            "app/services/pipeline_dependency_service.py:reconcile_safe_pipeline_invariants",
        ),
        domains=("PIPELINE",),
        tables=("pipeline_dependencies", "pipeline_runs", "background_jobs"),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14D",
        proof=(
            "Dependency preparation, child enqueue, completion/failure, and deterministic "
            "crash-gap "
            "reconciliation retain a single pipeline/root-job ownership chain."
        ),
    ),
    "WF_PIPELINE_STATE_TRANSITION": _family(
        "WF_PIPELINE_STATE_TRANSITION",
        owner="app/services/pipeline_state_machine.py:transition_pipeline",
        members=(
            "app/services/pipeline_state_machine.py:transition_pipeline",
            "app/services/pipeline_state_machine.py:transition_pipeline_step",
        ),
        domains=("PIPELINE",),
        tables=("pipeline_runs", "pipeline_steps"),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14D",
        proof=(
            "Central state-machine functions enforce the reviewed transition graph before mutating "
            "pipeline or pipeline-step status."
        ),
    ),
    "WF_CONSUMER_ELIGIBILITY_DECISION": _family(
        "WF_CONSUMER_ELIGIBILITY_DECISION",
        owner="app/services/consumer_eligibility_ledger.py:persist_consumer_decision",
        members=("app/services/consumer_eligibility_ledger.py:persist_consumer_decision",),
        domains=("PIPELINE",),
        tables=("consumer_eligibility_decisions",),
        status="CANONICAL_WRITER",
        disposition="T14D",
        proof=(
            "The append-only idempotent writer persists the complete orthogonal consumer decision "
            "and canonical fingerprint under an explicit policy version."
        ),
    ),
    "WF_WINNER_ACQUISITION_DECISION": _family(
        "WF_WINNER_ACQUISITION_DECISION",
        owner="app/services/consumer_eligibility_ledger.py:persist_winner_acquisition_decision",
        members=(
            "app/services/consumer_eligibility_ledger.py:persist_winner_acquisition_decision",
        ),
        domains=("WINNER_PREDICTION",),
        tables=("winner_acquisition_decisions",),
        status="CANONICAL_WRITER",
        disposition="T14D",
        proof=(
            "The append-only writer records Winner acquisition outcome, source decisions, exact "
            "source identities, cutoff, and policy version before prediction capture."
        ),
    ),
    "WF_ELIGIBILITY_SHADOW_EVALUATION": _family(
        "WF_ELIGIBILITY_SHADOW_EVALUATION",
        owner="app/services/consumer_eligibility_ledger.py:persist_shadow_evaluation",
        members=("app/services/consumer_eligibility_ledger.py:persist_shadow_evaluation",),
        domains=("PIPELINE",),
        tables=("eligibility_shadow_evaluations",),
        status="DOMAIN_SUPPORTING_STATE",
        disposition="T14D",
        proof=(
            "The idempotent writer retains non-authoritative legacy/proposed comparison evidence; "
            "the data model rejects an authoritative shadow decision."
        ),
    ),
}


EXISTING_FAMILY_SINKS = {
    "app/services/ceri/capture_service.py:CeriRunCaptureService.capture_run": (
        "WF_CERI_SCORE",
        "SEMANTIC_WRITER_MEMBER",
    ),
    "app/services/ceri/identity_resolver.py:reconcile_missing_provider_identity": (
        "WF_CERI_SOURCE_IDENTITY",
        "SEMANTIC_WRITER_MEMBER",
    ),
    "app/services/ceri/pipeline_containment_service.py:_rebuild_ceri_projections": (
        "WF_CORE_CURRENT_PROJECTION",
        "CURRENT_PROJECTION_WRITER",
    ),
    "app/services/ib_contract_resolver.py:_clear_contract_identity_values": (
        "WF_IB_CONTRACT_IDENTITY",
        "DOMAIN_SUPPORTING_STATE",
    ),
    "app/services/ib_contract_resolver.py:invalidate_cached_contract": (
        "WF_IB_CONTRACT_IDENTITY",
        "DOMAIN_SUPPORTING_STATE",
    ),
    "app/services/market_calculation_context_service.py:create_or_get_pipeline_ceri_context": (
        "WF_MARKET_CONTEXT_AUTHORITY",
        "DOMAIN_SUPPORTING_STATE",
    ),
    (
        "app/services/market_calculation_context_service.py:"
        "create_pipeline_ceri_certification_context"
    ): (
        "WF_MARKET_CONTEXT_AUTHORITY",
        "DOMAIN_SUPPORTING_STATE",
    ),
    (
        "app/services/market_calculation_context_service.py:"
        "freeze_or_validate_pipeline_ceri_authority"
    ): (
        "WF_MARKET_CONTEXT_AUTHORITY",
        "DOMAIN_SUPPORTING_STATE",
    ),
    "app/services/pipeline_execution_authority.py:revoke_pipeline_execution_authority": (
        "WF_PIPELINE_CONTROL",
        "DOMAIN_SUPPORTING_STATE",
    ),
    "app/services/pipeline_executor.py:_execute_resumed_pipeline": (
        "WF_PIPELINE_STAGE_STATE",
        "DOMAIN_SUPPORTING_STATE",
    ),
    "app/services/pipeline_service.py:_finalize_ceri_async_timing": (
        "WF_PIPELINE_STAGE_STATE",
        "DOMAIN_SUPPORTING_STATE",
    ),
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.record_evaluation_audit": (
        "WF_SETUP_EVALUATION_RUN_STATE",
        "DOMAIN_SUPPORTING_STATE",
    ),
    "scripts/ops/pipeline_authority_live_canary.py:launch": (
        "WF_UPLOAD_INITIALIZATION",
        "SEMANTIC_WRITER_MEMBER",
    ),
}


NEW_FAMILY_SINK_ROLES = {
    family_id: {
        member: (
            "CANONICAL_SEMANTIC_WRITER"
            if member == family["owner"] and family["status"] == "CANONICAL_WRITER"
            else "CURRENT_RULES_WRITER"
            if family["status"] == "CURRENT_RULES_WRITER"
            else "DOMAIN_SUPPORTING_STATE"
        )
        for member in family["member_sites"]
    }
    for family_id, family in NEW_WRITER_FAMILIES.items()
}


ENTRY_ASSIGNMENTS = {
    "app/services/contextual_consumer_eligibility.py:record_fundamental_admission": (
        "WF_CONSUMER_ELIGIBILITY_DECISION",
        "WF_ELIGIBILITY_SHADOW_EVALUATION",
    ),
    "app/services/consumer_eligibility_ledger.py:persist_consumer_decision": (
        "WF_CONSUMER_ELIGIBILITY_DECISION",
    ),
    "app/services/consumer_eligibility_ledger.py:persist_shadow_evaluation": (
        "WF_ELIGIBILITY_SHADOW_EVALUATION",
    ),
    "app/services/consumer_eligibility_ledger.py:persist_winner_acquisition_decision": (
        "WF_WINNER_ACQUISITION_DECISION",
    ),
    "app/services/eligibility_shadow.py:evaluate_and_record": (
        "WF_ELIGIBILITY_SHADOW_EVALUATION",
    ),
    "app/services/background_job_service.py:mark_job_blocked": (
        "WF_PIPELINE_DEPENDENCY",
        "WF_PIPELINE_STATE_TRANSITION",
        "WF_CORE_CURRENT_PROJECTION",
    ),
    "app/services/background_job_service.py:requeue_stalled_jobs": (
        "WF_JOB_RECOVERY_REQUEUE",
        "WF_PIPELINE_STATE_TRANSITION",
        "WF_CORE_CURRENT_PROJECTION",
    ),
    "app/services/ceri/feature_certification_workflow.py:admit_ceri_feature_certification": (
        "WF_CERI_FEATURE_CERTIFICATION",
        "WF_CERI_FEATURE_SOURCE_MANIFEST",
    ),
    "app/services/ceri/feature_certification_workflow.py:execute_ceri_feature_certification_job": (
        "WF_CERI_FEATURE_CERTIFICATION",
        "WF_CERI_FEATURE_SOURCE_MANIFEST",
    ),
    "app/services/ceri/identity_resolver.py:reconcile_missing_provider_identity": (
        "WF_CERI_SOURCE_IDENTITY",
    ),
    "app/services/ceri/pipeline_containment_service.py:reconcile_unsuccessful_pipeline_ceri": (
        "WF_CORE_CURRENT_PROJECTION",
        "WF_PIPELINE_STATE_TRANSITION",
    ),
    "app/services/ceri/source_manifest_service.py:freeze_or_verify_feature_source_manifest": (
        "WF_CERI_FEATURE_SOURCE_MANIFEST",
    ),
    "app/services/ib_contract_resolver.py:invalidate_cached_contract": ("WF_IB_CONTRACT_IDENTITY",),
    "app/services/instrument_lifecycle_service.py:record_instrument_lifecycle": (
        "WF_INSTRUMENT_LIFECYCLE",
    ),
    "app/services/market_calculation_context_service.py:create_or_get_pipeline_ceri_context": (
        "WF_MARKET_CONTEXT_AUTHORITY",
    ),
    (
        "app/services/market_calculation_context_service.py:"
        "create_pipeline_ceri_certification_context"
    ): (
        "WF_MARKET_CONTEXT_AUTHORITY",
    ),
    (
        "app/services/market_calculation_context_service.py:"
        "freeze_or_validate_pipeline_ceri_authority"
    ): (
        "WF_MARKET_CONTEXT_AUTHORITY",
    ),
    "app/services/market_data_session_readiness.py:evaluate_and_persist_session_readiness": (
        "WF_MARKET_DATA_SESSION_READINESS",
    ),
    "app/services/pipeline_dependency_service.py:complete_ceri_workflow_dependency": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:enqueue_sec_readiness_dependency": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:fail_ceri_workflow_dependency": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:mark_dependency_job_running": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:prepare_ceri_workflow_dependency": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:prepare_sec_readiness_dependency": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:reconcile_pending_dependency_enqueues": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:reconcile_pipeline_job": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_dependency_service.py:reconcile_safe_pipeline_invariants": (
        "WF_PIPELINE_DEPENDENCY",
    ),
    "app/services/pipeline_execution_authority.py:revoke_pipeline_execution_authority": (
        "WF_PIPELINE_CONTROL",
    ),
    "app/services/pipeline_service.py:enqueue_pipeline_after_ceri_completion": (
        "WF_PIPELINE_DEPENDENCY",
        "WF_PIPELINE_CONTROL",
    ),
    "app/services/pipeline_service.py:roll_up_ceri_pipeline_job_failure": (
        "WF_PIPELINE_DEPENDENCY",
        "WF_PIPELINE_STATE_TRANSITION",
        "WF_CORE_CURRENT_PROJECTION",
    ),
    "app/services/pipeline_state_machine.py:transition_pipeline": (
        "WF_PIPELINE_STATE_TRANSITION",
        "WF_CORE_CURRENT_PROJECTION",
    ),
    "app/services/pipeline_state_machine.py:transition_pipeline_step": (
        "WF_PIPELINE_STATE_TRANSITION",
    ),
    "app/services/setup_lifecycle/repository.py:SetupLifecycleRepository.record_evaluation_audit": (
        "WF_SETUP_EVALUATION_RUN_STATE",
    ),
    "scripts/ceri_feature_certification.py:main": (
        "WF_CERI_FEATURE_CERTIFICATION",
        "WF_CERI_FEATURE_SOURCE_MANIFEST",
    ),
    "scripts/instrument_lifecycle_admin.py:main": ("WF_INSTRUMENT_LIFECYCLE",),
    "scripts/ops/pipeline_authority_live_canary.py:main": (
        "WF_UPLOAD_INITIALIZATION",
        "WF_PIPELINE_CONTROL",
        "WF_PIPELINE_DEPENDENCY",
    ),
    "scripts/profile_ceri_writer_manifest.py:main": ("WF_CERI_FEATURE_BUILD",),
}


def apply_phase2_9_review(review: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Return a copy extended by exact, source-reviewed Phase 2.9 records."""

    result = deepcopy(review)
    families = result["writer_families"]
    entries = result["entry_families"]
    sinks = result["sink_reviews"]

    overlap = set(NEW_WRITER_FAMILIES).intersection(families)
    if overlap:
        raise AssertionError(f"Phase 2.9 writer family collision: {sorted(overlap)}")
    families.update(deepcopy(NEW_WRITER_FAMILIES))

    for sink, (family_id, role) in EXISTING_FAMILY_SINKS.items():
        if family_id not in families:
            raise AssertionError(f"Phase 2.9 references unknown writer family {family_id}")
        sinks[sink] = {"family_id": family_id, "role": role}
        members = families[family_id]["member_sites"]
        if sink not in members:
            members.append(sink)

    for family_id, members in NEW_FAMILY_SINK_ROLES.items():
        for sink, role in members.items():
            sinks[sink] = {"family_id": family_id, "role": role}

    current_entries = {row["entrypoint"]: row for row in inventory["entrypoints"]}
    missing = sorted(set(ENTRY_ASSIGNMENTS) - current_entries.keys())
    if missing:
        raise AssertionError(f"Phase 2.9 reviewed entrypoints missing from inventory: {missing}")
    for index, (entrypoint, writer_families) in enumerate(ENTRY_ASSIGNMENTS.items(), start=1):
        if any(family_id not in families for family_id in writer_families):
            raise AssertionError(f"Phase 2.9 entry references unknown family: {entrypoint}")
        entry = current_entries[entrypoint]
        family_id = f"P29_EF_{index:03d}"
        domains = sorted(
            {
                domain
                for writer_family in writer_families
                for domain in families[writer_family]["domains"]
            }
        )
        entries[family_id] = {
            "entry_point_family_id": family_id,
            "entrypoints": [entrypoint],
            "kind": entry["kind"],
            "reachability": (
                "Exact current public service, registered durable handler, or explicit CLI "
                "address retained by the finite source inventory."
            ),
            "domains": domains,
            "discovery_status": "DISCOVERED",
            "disposition": "T14D",
            "adoption_status": "ALREADY_CANONICAL",
            "writer_families": list(writer_families),
            "semantic_review_complete": True,
            "status": "PHASE2_9_REVIEWED",
            "authority_proof": (
                "Exact source-address and reachable reviewed writer-family assignment; native "
                "writer policy remains authoritative."
            ),
            "proof_boundary": (
                "Finite static reachability is retained as evidence and does not establish "
                "runtime dispatch beyond the named source address."
            ),
            "modes": [entry["semantic_mode"]],
        }
    return result
