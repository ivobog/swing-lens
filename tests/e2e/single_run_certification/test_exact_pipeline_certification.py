"""Small isolated evidence specimens exercise the same native certificate evaluator."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from single_run_certification.evidence import (
    RUNTIME_DIAGNOSTICS,
    evaluate_pipeline_evidence,
    expected_job_members,
)
from single_run_certification.golden_completion import (
    compare_vector,
    relationship_inventory,
    relationship_key,
)
from single_run_certification.golden_oracle import OracleMismatch, expected_manifest
from single_run_certification.reporting import (
    EXACT_TEST_IDS,
    PINNED_SOURCE,
    REQUIRED_ARTIFACTS,
    CertificationRecorder,
    _result_matrix,
    finalize_pipeline_certificate,
    pipeline_contract,
)


@pytest.fixture(params=(False, True))
def specimen(request):
    mode = request.param
    manifest = expected_manifest(mode)
    identity = {
        "execution_id": "isolated-specimen",
        "runtime_instance_id": "isolated-specimen",
        "invocation_id": "specimen-invocation",
        "test_id": EXACT_TEST_IDS[int(mode)],
        "handoff": mode,
        "source_revision": PINNED_SOURCE,
        "source_fingerprint": "f" * 64,
        "input_sha256": manifest["input_manifest_sha256"],
        "oracle_sha256": manifest["completion"]["manifest_sha256"],
        "configuration_sha256": manifest["configuration_envelope_sha256"],
        "database": {"name": "swinglens_pytest_cert_specimen", "host": "127.0.0.1", "port": 28344},
    }
    contract = pipeline_contract(manifest, identity)
    invocation = {
        "invocation_id": identity["invocation_id"],
        "selected": list(EXACT_TEST_IDS),
        "collected": list(EXACT_TEST_IDS),
        "executed": list(EXACT_TEST_IDS),
        "failed": [],
        "exit_code": 0,
    }
    stamp = "2026-10-27T02:00:00+00:00"
    authority = {"scope_id": "scope", "refresh_cycle_id": "cycle", "acquisition_plan_id": "plan"}
    workflow = "ceri:pipeline:3:specimen-config"
    jobs = []

    def job(job_id, kind, request_key, **extra):
        result = {
            "id": job_id,
            "job_type": kind,
            "request_key": request_key,
            "related_run_id": 3,
            "status": "COMPLETED",
            "parent_job_id": 1,
            "root_job_id": 1,
            "pipeline_dependency_id": None,
            "trigger_source": None,
            "started_at": stamp,
            "completed_at": stamp,
            "workflow_key": workflow,
            "payload": {
                "certification_session_id": identity["execution_id"],
                "certification_authorized": True,
                "pipeline_run_id": 2,
            },
            "result_status": "COMPLETED",
            **authority,
            **extra,
        }
        jobs.append(result)
        return result

    job(
        1,
        "FULL_PIPELINE",
        "root",
        parent_job_id=None,
        root_job_id=None,
        result_status="WAITING_FOR_CERI_COMPLETION",
    )
    for index, (kind, key, payload) in enumerate(expected_job_members(manifest), 2):
        child = job(index, kind, key.format(workflow=workflow))
        child["payload"].update(payload)
    terminal_child = jobs[-1]["id"]
    continuation = job(
        terminal_child + 1,
        "FULL_PIPELINE",
        "continuation",
        parent_job_id=terminal_child,
        pipeline_dependency_id=1,
        trigger_source="CERI_COMPLETION_BARRIER",
    )
    continuation["payload"]["resume_from_step"] = "FREEZING_DECISION_HANDOFF_MANIFEST"
    native = {
        "execution_id": identity["execution_id"],
        "run_id": 3,
        "pipeline_id": 2,
        "fresh_committed_session": True,
        "database": identity["database"],
        "pipeline": {
            "id": 2,
            "upload_run_id": 3,
            "status": "COMPLETED",
            "started_at": stamp,
            "completed_at": stamp,
            **authority,
        },
        "workflow_key": workflow,
        "configuration_anchor": "anchor",
        "market_context_id": 1,
        "ceri_context_id": 2,
        "delivered_settings": manifest["flags"],
        "jobs": jobs,
        "dependencies": [
            {
                "id": 1,
                "dependency_type": "CERI_WORKFLOW",
                "state": "COMPLETED",
                "root_job_id": 1,
                "child_job_id": terminal_child,
                "continuation_job_id": continuation["id"],
                "result_json": {
                    "workflow_key": workflow,
                    "certified_count": len(manifest["completion"]["domains"]["captures"]),
                },
            }
        ],
        "stages": [
            {
                "step_order": row["order"],
                "step_name": row["name"],
                "status": "COMPLETED",
                "started_at": stamp,
                "completed_at": stamp,
                "error_message": None,
            }
            for row in manifest["domains"]["stages"]
        ],
        "gui_comparisons": [{"passed": True}],
        "gui_surfaces": [{"name": "pipeline-progress"}],
        "gui_failures": [],
    }
    full = [jobs[0], continuation]
    entries = [
        {
            "job_id": j["id"],
            "registered_original": True,
            "native_worker_dispatch": True,
            "handler": "app.services.background_worker._execute_full_pipeline_job",
            "certification_session_id": identity["execution_id"],
            "worker_instance_id": "generation",
            "pid": 1234,
            "worker_id": "worker",
            "configuration_anchor": "anchor",
            "execution_token_hash": str(j["id"]),
        }
        for j in full
    ]
    fidelity = {
        "execution_id": identity["execution_id"],
        "pipeline_id": 2,
        "run_id": 3,
        "database_name": identity["database"]["name"],
        "handler_entries": entries,
        "handler_returns": [
            {
                **e,
                "callback_calls": {
                    key: 2 for key in ("lease_guard", "should_cancel", "progress_callback")
                },
            }
            for e in entries
        ],
        "executor_entries": [
            {
                **e,
                "independent_control_factory": True,
                "callbacks": {
                    key: "_execute_full_pipeline_job.<locals>." + key
                    for key in ("lease_guard", "should_cancel", "progress_callback", "memory_probe")
                },
            }
            for e in entries
        ],
        "worker": {
            "instance_id": "generation",
            "process_id": 1234,
            "heartbeat_at": stamp,
            "control_loop_heartbeat_at": stamp,
        },
        "jobs": [
            {
                "id": j["id"],
                "status": j["status"],
                "configuration_anchor": "anchor",
                "progress_sequence": 10,
                "lease_events": [{"event_type": "CLAIMED", "worker_id": "worker"}],
            }
            for j in jobs
        ],
    }
    relationships = relationship_inventory(manifest)
    raw_worker_log = []
    for field, event in (
        ("handler_entries", "native_handler_entered"),
        ("handler_returns", "native_handler_returned"),
        ("executor_entries", "native_executor_entered"),
    ):
        for row in fidelity[field]:
            row["event"] = event
            raw_worker_log.append(row)
    oracle = {
        "expected": manifest["completion"],
        "actual": manifest["completion"]["domains"],
        "relationships": [{"name": n, "passed": True} for n in relationships],
        "declared_relationships": relationships,
        "errors": [],
        "fresh_committed_session": True,
        "execution_id": identity["execution_id"],
        "run_id": 3,
        "pipeline_id": 2,
    }
    config_hash = hashlib.sha256(b"{}").hexdigest()
    configuration = {
        "semantic": {},
        "semantic_hash": config_hash,
        "identity": {"fingerprint": {"digest": config_hash}},
    }
    oracle["component_details"] = {
        "immutable_evidence": [
            {
                "kind": kind,
                "semantic_member": relationship_key(section, row),
                "ownership": {
                    "run_id": {"state": "KNOWN", "value": 3},
                    "pipeline_id": {"state": "UNKNOWN"}
                    if kind == "CERI"
                    else {"state": "KNOWN", "value": 2},
                },
                "expected_context_id": 2 if kind == "CERI" else 1,
                "temporal": {
                    "calculation_cutoff": {
                        "value": manifest["business_time"][
                            "ceri_cutoff" if kind == "CERI" else "market_cutoff"
                        ]
                    },
                    "as_of_session": {"value": manifest["business_time"]["market_cutoff"][:10]},
                },
                "calculation_context": {
                    "market_calculation_context_id": {"value": 2 if kind == "CERI" else 1}
                },
                "effective_configuration": configuration,
                "configuration": {"effective_configuration": {"value": configuration["identity"]}},
                "sources": {},
                "expected_sources": {},
            }
            for section, kind in (
                ("technical", "TECHNICAL"),
                ("combined", "COMBINED"),
                ("ranking", "RANKING"),
                ("market_regime", "REGIME"),
                ("sector", "SECTOR"),
                ("captures", "CERI"),
                ("setup", "SETUP"),
            )
            for row in manifest["completion"]["domains"][section]
        ]
    }
    subset = {
        **manifest["domains"],
        "business_time": manifest["business_time"],
        "operational_ingestion_clock": {
            "initial_claim_at": stamp,
            "continuation_claim_at": stamp,
            "ingestion_started_at": [stamp],
        },
    }
    bundle = {
        "logs/worker-fidelity.jsonl": raw_worker_log,
        "expected-manifest.json": manifest,
        "certificate-contract.json": contract,
        "environment.json": {
            "execution_id": identity["execution_id"],
            "working_tree_fingerprint": identity["source_fingerprint"],
            "database_name": identity["database"]["name"],
        },
        "pipeline-evidence.json": native,
        "db-results/production-worker-fidelity.json": fidelity,
        "db-results/independent-complete-oracle.json": oracle,
        "db-results/independent-golden-subset.json": {"actual": subset},
        "db-results/provider-10-metrics.json": {
            "ceri_max_checkpoint_gap_seconds": 1,
            "full_pipeline_control": {
                "min_progress_sequence": 10,
                "max_recovery_count": 0,
                "stalled_jobs": 0,
            },
        },
        "runtime-invariants.jsonl": [
            {
                "active_job_count": 1,
                "execution_id": identity["execution_id"],
                "pipeline_id": 2,
                "executed_checks": list(RUNTIME_DIAGNOSTICS),
                "failures": [],
                "observed_at": stamp,
            }
        ],
        "checks-reached.json": {
            "execution_id": identity["execution_id"],
            "test_id": identity["test_id"],
            "all_test_checks_reached": True,
        },
        "cleanup.json": {
            "execution_id": identity["execution_id"],
            "database_dropped": True,
            "supervisor_exited": True,
            "owned_processes_alive": [],
        },
    }
    # JSON roundtrip deliberately removes aliases between expected truth and
    # observations; every mutation below changes only an isolated actual copy.
    return json.loads(
        json.dumps(
            {
                "contract": contract,
                "manifest": manifest,
                "bundle": bundle,
                "invocation": invocation,
                "identity": identity,
                "hashes": {path: "a" * 64 for path in REQUIRED_ARTIFACTS},
            }
        )
    )


def evaluate(specimen):
    return evaluate_pipeline_evidence(
        specimen["contract"],
        specimen["manifest"],
        specimen["bundle"],
        invocation=specimen["invocation"],
        expected_identity=specimen["identity"],
        hashes=specimen["hashes"],
    )


def test_exact_positive_permits_all_sealed_absences_and_exclusions(specimen):
    result = evaluate(specimen)
    assert result.verdict == "CERTIFIED", {
        k: v for k, v in result.gate_results.items() if v["status"] != "PASS"
    }
    domains = specimen["manifest"]["completion"]["domains"]
    assert not domains["alerts"] and not domains["changes"] and not domains["setup_changes"]
    assert not domains["setup_alerts"]
    assert len(relationship_inventory(specimen["manifest"])) == 541
    assert sum(map(len, domains.values())) == 402
    # Optional Sector / Unrated / insufficient states are accepted through the
    # exact reviewed literals, not exemptions added to the evaluator.
    assert result.gate_results["section:captures"]["status"] == "PASS"
    assert result.gate_results["section:winner_estimates"]["status"] == "PASS"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_stage",
        "stage_missing_work",
        "skipped_stage",
        "failed_child",
        "partial_child",
        "missing_continuation",
        "duplicate_continuation",
        "foreign_continuation",
        "foreign_ticker",
        "wrong_config",
        "wrong_evidence",
        "wrong_context",
        "wrong_source_graph",
        "wrong_source",
        "changed_value",
        "pass_failed_diagnostics",
        "empty_comparisons",
        "wrong_execution",
        "wrong_input",
        "wrong_expectation",
        "missing_gate",
        "missing_relationship",
        "no_diagnostics",
        "unreached_checks",
        "missing_output",
    ),
)
def test_mutated_evidence_cannot_certify(specimen, mutation):
    native = specimen["bundle"]["pipeline-evidence.json"]
    oracle = specimen["bundle"]["db-results/independent-complete-oracle.json"]
    if mutation == "missing_stage":
        native["stages"].pop()
    elif mutation in {"stage_missing_work", "missing_output"}:
        oracle["actual"]["winner"] = []
    elif mutation == "skipped_stage":
        native["stages"][-1]["status"] = "SKIPPED"
    elif mutation in {"failed_child", "partial_child"}:
        native["jobs"][1]["status"] = "FAILED" if mutation == "failed_child" else "PARTIAL"
    elif mutation == "missing_continuation":
        native["jobs"].pop()
    elif mutation == "duplicate_continuation":
        native["jobs"].append(copy.deepcopy(native["jobs"][-1]))
    elif mutation == "foreign_continuation":
        native["jobs"][-1]["payload"]["certification_session_id"] = "foreign"
    elif mutation == "foreign_ticker":
        oracle["actual"]["technical"][0]["ticker"] = "FOREIGN"
    elif mutation == "wrong_config":
        native["delivered_settings"]["setup_capture_handoff_enabled"] = not specimen["identity"][
            "handoff"
        ]
    elif mutation == "wrong_evidence":
        oracle["relationships"][0]["passed"] = False
    elif mutation == "wrong_context":
        oracle["component_details"]["immutable_evidence"][0]["calculation_context"][
            "market_calculation_context_id"
        ]["value"] = 999
    elif mutation == "wrong_source_graph":
        oracle["component_details"]["immutable_evidence"][0]["sources"] = {"foreign": 999}
    elif mutation == "wrong_source":
        specimen["contract"]["identity"]["source_fingerprint"] = "stale"
    elif mutation == "changed_value":
        oracle["actual"]["technical"][0]["dual_score"] = "-1"
    elif mutation == "pass_failed_diagnostics":
        specimen["bundle"]["runtime-invariants.jsonl"][0].update(
            verdict="PASS", failures=[{"code": "CONTROL_LOOP_STALE"}]
        )
    elif mutation == "empty_comparisons":
        oracle["actual"] = {}
        oracle["relationships"] = []
    elif mutation == "wrong_execution":
        oracle["execution_id"] = "stale"
    elif mutation in {"wrong_input", "wrong_expectation"}:
        key = "input_sha256" if mutation == "wrong_input" else "oracle_sha256"
        specimen["contract"]["identity"][key] = "stale"
    elif mutation == "missing_gate":
        specimen["contract"]["required_gates"].pop("terminal")
    elif mutation == "missing_relationship":
        oracle["relationships"].pop()
    elif mutation == "no_diagnostics":
        specimen["bundle"]["runtime-invariants.jsonl"] = []
    elif mutation == "unreached_checks":
        specimen["bundle"]["checks-reached.json"]["all_test_checks_reached"] = False
    result = evaluate(specimen)
    assert result.verdict == "FAILED", (mutation, result.verdict)


def test_started_pending_continuation_is_incomplete(specimen):
    native = specimen["bundle"]["pipeline-evidence.json"]
    native["pipeline"]["status"] = "WAITING_FOR_CERI_COMPLETION"
    native["jobs"].pop()
    native["dependencies"][0].update(state="WAITING", continuation_job_id=None)
    # A partial observation has not yet written the final fresh-state evidence.
    specimen["bundle"].pop("db-results/independent-complete-oracle.json")
    specimen["bundle"].pop("db-results/production-worker-fidelity.json")
    result = evaluate(specimen)
    assert result.verdict == "INCOMPLETE", result.failures


def test_four_state_precedence_and_omitted_gate():
    recorder = CertificationRecorder("execution", required_gates={"a": "oracle", "b": "native"})
    assert recorder.verdict == "INCOMPLETE"
    recorder.gate("a", "BLOCKED", reason="oracle prerequisite unavailable")
    assert recorder.verdict == "BLOCKED"
    recorder.gate("b", "FAILED", reason="independent observed child violation")
    assert recorder.verdict == "FAILED"
    recorder = CertificationRecorder("execution")
    assert recorder.verdict == "INCOMPLETE"
    recorder.check(True, "one legacy comparison", area="CERI")
    assert recorder.verdict == "PASS"
    matrix = _result_matrix(recorder)
    assert "| DB | GUI |" not in matrix and "not inferred" in matrix


def test_unsupported_conditional_features_are_not_silently_added(specimen):
    # The frozen enabled profile prescribes its own jobs. Features outside that
    # profile do not become new gates just because the application supports them.
    gates = specimen["contract"]["required_gates"]
    assert not any("MATURATION" in key or "v5_shadow" in key for key in gates)


def test_oracle_prerequisite_blocker_cannot_hide_independent_failure(specimen):
    specimen["manifest"]["unresolved"] = [{"domain": "technical"}]
    specimen["bundle"]["pipeline-evidence.json"]["jobs"][1]["status"] = "FAILED"
    result = evaluate(specimen)
    assert result.gate_results["oracle_prerequisite"]["status"] == "BLOCKED"
    assert result.verdict == "FAILED"


def test_unavailable_oracle_is_blocked_before_evaluation(specimen):
    specimen["manifest"]["unresolved"] = [{"domain": "technical"}]
    result = evaluate(specimen)
    assert result.verdict == "BLOCKED"


def test_postgresql_cidr_and_preclaim_admission_samples_are_valid(specimen):
    native = specimen["bundle"]["pipeline-evidence.json"]
    native["database"]["host"] = "127.0.0.1/32"
    samples = specimen["bundle"]["runtime-invariants.jsonl"]
    samples.insert(
        0, {**samples[0], "observed_at": "2026-10-27T01:59:59+00:00", "active_job_count": 0}
    )
    assert evaluate(specimen).verdict == "CERTIFIED"
    native["database"]["host"] = "127.0.0.2/32"
    assert evaluate(specimen).verdict == "FAILED"


def test_stale_observer_and_pending_invocation_are_not_certified(specimen):
    specimen["invocation"].update(exit_code=None, executed=[])
    assert evaluate(specimen).verdict == "INCOMPLETE"
    samples = specimen["bundle"]["runtime-invariants.jsonl"]
    samples[0]["observed_at"] = "2026-10-27T01:59:59+00:00"
    assert evaluate(specimen).verdict == "FAILED"


@pytest.mark.parametrize("timestamp", ("2026-10-26 21:32:00+01:00", "2026-10-26T20:32:00Z"))
def test_json_timestamp_recomparison_uses_exact_aware_instant(timestamp):
    compare_vector(
        [{"ticker": "BHE", "cutoff_at": "2026-10-26T20:32:00+00:00"}],
        [{"ticker": "BHE", "cutoff_at": timestamp}],
        keys=("ticker",),
    )


@pytest.mark.parametrize(
    "timestamp", ("2026-10-26 21:32:01+01:00", "2026-10-26T20:32:00", "2026-10-25T20:32:00+00:00")
)
def test_timestamp_normalization_rejects_wrong_or_unknown_instant(timestamp):
    with pytest.raises(OracleMismatch):
        compare_vector(
            [{"ticker": "BHE", "cutoff_at": "2026-10-26T20:32:00+00:00"}],
            [{"ticker": "BHE", "cutoff_at": timestamp}],
            keys=("ticker",),
        )


def test_file_finalizer_requires_current_complete_artifacts(specimen, tmp_path):
    for path in REQUIRED_ARTIFACTS:
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        value = specimen["bundle"].get(path, {})
        if path.endswith(".jsonl"):
            target.write_text("\n".join(json.dumps(row) for row in value))
        elif path.endswith(".png"):
            target.write_bytes(b"isolated screenshot specimen")
        else:
            target.write_text(json.dumps(value))
    result = finalize_pipeline_certificate(
        tmp_path, invocation=specimen["invocation"], expected_identity=specimen["identity"]
    )
    assert result["verdict"] == "CERTIFIED", result["reasons"]
    (tmp_path / "cleanup.json").unlink()
    result = finalize_pipeline_certificate(
        tmp_path, invocation=specimen["invocation"], expected_identity=specimen["identity"]
    )
    assert result["verdict"] == "INCOMPLETE", result["reasons"]
