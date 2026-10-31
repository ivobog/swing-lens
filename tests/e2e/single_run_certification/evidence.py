from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, inspect, text

RUN_COLUMN_NAMES = ("run_id", "source_run_id", "upload_run_id", "related_run_id")

# These queries run on every successful production observer call. Memory sampling
# and fault injection are intentionally not claimed by this happy-path profile.
RUNTIME_DIAGNOSTICS = (
    "EXPIRED_ACTIVE_JOB_LEASE",
    "PROGRESS_FROZEN",
    "CONTROL_LOOP_STALE",
    "ACTIVE_JOB_OWNER_MISMATCH",
    "DUPLICATE_ACTIVE_JOB",
    "ORPHAN_CHILD_JOB",
    "DUPLICATE_CONTINUATION",
    "DUPLICATE_CHILD_JOB",
    "CROSS_RUN_JOB_LINEAGE",
    "UNEXPECTED_RECOVERY_LOOP",
    "PARENT_COMPLETE_WITH_MANDATORY_CHILD_ACTIVE",
    "MANDATORY_DEPENDENCY_MISSING",
    "PARENT_WAITING_WITHOUT_RECOVERABLE_CHILD",
    "PARENT_COMPLETE_WITH_OPEN_DEPENDENCY",
    "POSTGRES_LOCK_WAIT_EXCEEDED",
    "POSTGRES_TRANSACTION_STALLED",
    "POSTGRES_IDLE_IN_TRANSACTION",
)


def capture_pipeline_evidence(engine: Engine, env, recorder, run_id: int, pipeline_id: int) -> None:
    """Fresh committed durable facts; no terminal-status helper or output selector."""
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models.tables import BackgroundJob, PipelineDependency, PipelineRun, PipelineStep
    from app.services.configuration_delivery import binding_reference, load_configuration_delivery

    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        anchor = binding_reference(db, pipeline_run_id=pipeline_id)
        delivery = load_configuration_delivery(db, anchor)
        jobs = list(db.scalars(select(BackgroundJob).where(BackgroundJob.related_run_id == run_id)))
        payload_fields = (
            "pipeline_run_id",
            "certification_session_id",
            "certification_authorized",
            "resume_from_step",
            "provider",
            "dataset",
            "tickers",
            "batch_index",
        )
        value = {
            "execution_id": env.execution_id,
            "run_id": run_id,
            "pipeline_id": pipeline_id,
            "fresh_committed_session": True,
            "database": query_rows(
                engine,
                "select current_database() as name, inet_server_addr()::text as host, "
                "inet_server_port() as port",
            )[0],
            "pipeline": _project_fields(
                pipeline,
                (
                    "id",
                    "upload_run_id",
                    "status",
                    "scope_id",
                    "refresh_cycle_id",
                    "acquisition_plan_id",
                    "started_at",
                    "completed_at",
                ),
            ),
            "workflow_key": pipeline.result_json["ceri_provider_workflow_key"],
            "configuration_anchor": anchor["fingerprint"],
            "market_context_id": pipeline.market_calculation_context.id,
            "ceri_context_id": pipeline.result_json["ceri_calculation_context_id"],
            "delivered_settings": dict(delivery.configurations["execution.settings"].values),
            "jobs": [
                {
                    **_project_fields(
                        job,
                        (
                            "id",
                            "job_type",
                            "status",
                            "related_run_id",
                            "parent_job_id",
                            "root_job_id",
                            "workflow_key",
                            "request_key",
                            "trigger_source",
                            "pipeline_dependency_id",
                            "scope_id",
                            "refresh_cycle_id",
                            "acquisition_plan_id",
                            "started_at",
                            "completed_at",
                        ),
                    ),
                    "payload": {key: job.payload_json.get(key) for key in payload_fields},
                    "result_status": (job.result_json or {}).get("status"),
                }
                for job in jobs
            ],
            "dependencies": [
                _project_fields(
                    dep,
                    (
                        "id",
                        "dependency_type",
                        "state",
                        "root_job_id",
                        "child_job_id",
                        "continuation_job_id",
                        "result_json",
                    ),
                )
                for dep in db.scalars(
                    select(PipelineDependency).where(
                        PipelineDependency.pipeline_run_id == pipeline_id
                    )
                )
            ],
            "stages": [
                _project_fields(
                    step,
                    (
                        "step_order",
                        "step_name",
                        "status",
                        "started_at",
                        "completed_at",
                        "error_message",
                        "result_json",
                    ),
                )
                for step in db.scalars(
                    select(PipelineStep)
                    .where(PipelineStep.pipeline_run_id == pipeline_id)
                    .order_by(PipelineStep.step_order)
                )
            ],
            "gui_comparisons": recorder.comparisons,
            "gui_surfaces": recorder.surfaces,
            "gui_failures": recorder.failures,
        }
    (env.artifact_dir / "pipeline-evidence.json").write_text(
        json.dumps(value, indent=2, sort_keys=True, default=_json_default), encoding="utf-8"
    )


def _project_fields(row, fields):
    return {name: getattr(row, name) for name in fields}


def expected_job_members(manifest: dict) -> list[tuple[str, str, dict]]:
    """Expand the sealed batch plan and enabled frozen tail, independent of jobs."""
    result = []
    for batch in manifest["conditional_obligations"]["ceri_batches"]:
        job_type = (
            "CERI_PROVIDER_INGEST_BATCH" if batch["role"] == "provider" else "CERI_NORMALIZE_BATCH"
        )
        result.append(
            (
                job_type,
                batch["request_key_template"],
                {
                    "provider": batch["provider"],
                    "dataset": batch["dataset"],
                    "batch_index": batch["batch"],
                    "tickers": batch["tickers"],
                },
            )
        )
    tickers = sorted(row["ticker"] for row in manifest["domains"]["raw"])
    size = manifest["flags"]["ceri_feature_batch_size"]
    for start in range(0, len(tickers), size):
        index = start // size + 1
        result.append(
            (
                "CERI_FEATURE_BATCH",
                "{workflow}:feature:" + f"{index:04d}",
                {
                    "tickers": tickers[start : start + size],
                    "batch_index": index,
                },
            )
        )
    result += [
        (kind, "{workflow}:" + suffix, {})
        for kind, suffix in (
            ("CERI_RUN_FINALIZE", "finalize"),
            ("CERI_CAPTURE_RUN", "capture"),
            ("CERI_CHANGE_DETECTION", "change"),
        )
    ]
    if manifest["flags"]["ceri_alerts_enabled"]:
        result.append(("CERI_ALERT_REBUILD", "{workflow}:alert", {}))
    return result


def evaluate_pipeline_evidence(
    contract: dict,
    manifest: dict,
    bundle: dict,
    *,
    invocation: dict,
    expected_identity: dict,
    hashes: dict,
):
    """The authoritative four-state reconciliation, also used by mutation controls."""
    from single_run_certification.golden_completion import (
        KEYS,
        compare_vector,
        relationship_inventory,
        relationship_key,
    )
    from single_run_certification.golden_oracle import (
        canonical_hash,
        compare_records,
        require_complete,
    )
    from single_run_certification.reporting import (
        EXACT_TEST_IDS,
        PINNED_SOURCE,
        REQUIRED_ARTIFACTS,
        CertificationRecorder,
        pipeline_gate_inventory,
    )

    identity = contract["identity"]
    required = pipeline_gate_inventory(manifest)
    recorder = CertificationRecorder(identity["execution_id"], required_gates=required)
    native = bundle.get("pipeline-evidence.json", {})
    terminal = native.get("pipeline", {}).get("status") == "COMPLETED"
    missing_status = "FAILED" if terminal else "INCOMPLETE"

    def check(name, predicate, *, evidence=None, missing=missing_status):
        try:
            passed = predicate()
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            recorder.gate(
                name, missing, reason="missing/invalid evidence: " + str(exc), evidence=evidence
            )
        except AssertionError as exc:
            pending_output = not terminal and (
                name.startswith(("section:", "subset:", "relationship:"))
                or name in {"fresh_committed", "comparison_inventory"}
            )
            recorder.gate(
                name,
                "INCOMPLETE" if pending_output else "FAILED",
                reason=str(exc),
                evidence=evidence,
            )
        else:
            recorder.gate(
                name,
                "PASS" if passed is not False else "FAILED",
                evidence=evidence,
                reason="" if passed is not False else "required contract violated",
            )

    def same(left, right):
        assert left == right, "expected=" + repr(right) + "; actual=" + repr(left)

    try:
        require_complete(manifest)
    except Exception as exc:
        oracle_available = False
        recorder.gate("oracle_prerequisite", "BLOCKED", reason=repr(exc))
    else:
        oracle_available = True
        recorder.gate("oracle_prerequisite", "PASS", evidence="sealed a05b-r3 literals")
    check(
        "identity",
        lambda: (
            same(contract["required_gates"], required),
            same(contract["manifest_sha256"], canonical_hash(manifest))
            if oracle_available
            else None,
            same(identity, expected_identity),
            same(identity["test_id"], EXACT_TEST_IDS[int(identity["handoff"])]),
            same(identity["runtime_instance_id"], identity["execution_id"]),
            same(bool(identity["invocation_id"]), True),
            same(len(identity["source_fingerprint"]), 64),
            same(identity["source_revision"], PINNED_SOURCE),
            same(identity["input_sha256"], manifest["input_manifest_sha256"]),
            same(identity["oracle_sha256"], manifest["completion"]["manifest_sha256"]),
            same(identity["configuration_sha256"], manifest["configuration_envelope_sha256"]),
            same(bundle["expected-manifest.json"], manifest) if oracle_available else None,
            same(bundle["environment.json"]["execution_id"], identity["execution_id"]),
            same(
                bundle["environment.json"]["working_tree_fingerprint"],
                identity["source_fingerprint"],
            ),
            same(native["execution_id"], identity["execution_id"]),
        ),
        evidence="certificate-contract.json; environment.json; expected-manifest.json",
    )
    check(
        "configuration",
        lambda: (
            all(
                same(value, manifest["flags"][key]) is None
                for key, value in native["delivered_settings"].items()
                if key in manifest["flags"]
            )
            and bool(native["delivered_settings"])
            and bool(native["configuration_anchor"])
        ),
        evidence="pipeline-evidence.json#delivered_settings",
    )
    check(
        "disposable_postgresql",
        lambda: (
            same(
                {
                    **native["database"],
                    "host": str(ipaddress.ip_interface(native["database"]["host"]).ip),
                },
                identity["database"],
            ),
            same(bundle["environment.json"]["database_name"], identity["database"]["name"]),
            same(identity["database"]["host"], "127.0.0.1"),
            same(identity["database"]["name"].startswith("swinglens_pytest_cert_"), True),
        ),
        evidence="pipeline-evidence.json#database",
    )
    jobs = native.get("jobs", [])
    full = sorted((j for j in jobs if j["job_type"] == "FULL_PIPELINE"), key=lambda j: j["id"])
    fidelity = bundle.get("db-results/production-worker-fidelity.json", {})

    def handler():
        same(fidelity["execution_id"], identity["execution_id"])
        same(fidelity["pipeline_id"], native["pipeline_id"])
        same(fidelity["run_id"], native["run_id"])
        same(fidelity["database_name"], identity["database"]["name"])
        entries, returns, executors = (
            fidelity[key] for key in ("handler_entries", "handler_returns", "executor_entries")
        )
        raw = bundle["logs/worker-fidelity.jsonl"]
        for event, observed in (
            ("native_handler_entered", entries),
            ("native_handler_returned", returns),
            ("native_executor_entered", executors),
        ):
            same([row for row in raw if row.get("event") == event], observed)
        same(len(full), 2)
        for population in (entries, returns, executors):
            same(sorted(row["job_id"] for row in population), [job["id"] for job in full])
        worker = fidelity["worker"]
        assert worker["heartbeat_at"] and worker["control_loop_heartbeat_at"]
        assert len({row["execution_token_hash"] for row in entries}) == len(entries)
        for entry in entries:
            same(entry["registered_original"], True)
            same(entry["native_worker_dispatch"], True)
            same(entry["handler"], "app.services.background_worker._execute_full_pipeline_job")
            same(entry["certification_session_id"], identity["execution_id"])
            same(entry["worker_instance_id"], worker["instance_id"])
            same(entry["pid"], worker["process_id"])
            same(entry["configuration_anchor"], native["configuration_anchor"])
            executor = next(row for row in executors if row["job_id"] == entry["job_id"])
            returned = next(row for row in returns if row["job_id"] == entry["job_id"])
            same(executor["execution_token_hash"], entry["execution_token_hash"])
            assert executor["independent_control_factory"]
            same(
                executor["callbacks"],
                {
                    key: "_execute_full_pipeline_job.<locals>." + key
                    for key in ("lease_guard", "should_cancel", "progress_callback", "memory_probe")
                },
            )
            assert all(
                returned["callback_calls"].get(key, 0) > 0
                for key in ("lease_guard", "should_cancel", "progress_callback")
            )
            observed = next(j for j in fidelity["jobs"] if j["id"] == entry["job_id"])
            assert observed["progress_sequence"] > 1
            assert any(
                e["event_type"] == "CLAIMED" and e["worker_id"] == entry["worker_id"]
                for e in observed["lease_events"]
            )

    if not terminal and (len(full) < 2 or any(j["status"] != "COMPLETED" for j in full)):
        recorder.gate("handler", "INCOMPLETE", reason="native continuation has not completed")
    else:
        check("handler", handler, evidence="db-results/production-worker-fidelity.json")

    def authority():
        assert jobs and native["pipeline"]["scope_id"] and native["pipeline"]["refresh_cycle_id"]
        observed = {j["id"]: j for j in fidelity["jobs"]}
        same(set(observed), {job["id"] for job in jobs})
        for job in jobs:
            same(job["related_run_id"], native["run_id"])
            same(job["payload"]["certification_session_id"], identity["execution_id"])
            same(job["payload"]["certification_authorized"], True)
            for key in ("scope_id", "refresh_cycle_id", "acquisition_plan_id"):
                same(job[key], native["pipeline"][key])
            same(observed[job["id"]]["configuration_anchor"], native["configuration_anchor"])
            same(observed[job["id"]]["status"], job["status"])
            if job["parent_job_id"] is not None:
                assert job["parent_job_id"] in observed
            if job["root_job_id"] is not None:
                same(job["root_job_id"], full[0]["id"])

    check(
        "authority", authority, evidence="pipeline-evidence.json; production-worker-fidelity.json"
    )

    def membership():
        assert not any(j["status"] in {"FAILED", "PARTIAL", "CANCELLED"} for j in jobs)
        workflow = native["workflow_key"]
        expected = expected_job_members(manifest)
        children = [job for job in jobs if job["job_type"] != "FULL_PIPELINE"]
        same(
            sorted((j["job_type"], j["request_key"]) for j in children),
            sorted((kind, key.format(workflow=workflow)) for kind, key, _ in expected),
        )
        for kind, key, payload in expected:
            job = next(
                j
                for j in children
                if j["job_type"] == kind and j["request_key"] == key.format(workflow=workflow)
            )
            same(job["workflow_key"], workflow)
            for name, value in payload.items():
                same(job["payload"][name], value)
            same(job["status"], "COMPLETED")
            assert job["started_at"] and job["completed_at"]

    if any(j.get("status") in {"FAILED", "PARTIAL", "CANCELLED"} for j in jobs) or terminal:
        check("job_membership", membership, evidence="pipeline-evidence.json#jobs")
    else:
        recorder.gate("job_membership", "INCOMPLETE", reason="required job closure pending")

    def dependency():
        policy = manifest["conditional_obligations"]["pipeline_dependency"]
        same(len(full), policy["continuation_count"] + 1)
        root, continuation = full
        same(root["result_status"], "WAITING_FOR_CERI_COMPLETION")
        same(root["status"], "COMPLETED")
        same(continuation["status"], "COMPLETED")
        same(continuation["result_status"], "COMPLETED")
        same(len(native["dependencies"]), 1)
        dep = native["dependencies"][0]
        same(dep["dependency_type"], policy["type"])
        same(dep["state"], "COMPLETED")
        same(dep["root_job_id"], root["id"])
        same(dep["continuation_job_id"], continuation["id"])
        same(continuation["parent_job_id"], dep["child_job_id"])
        same(continuation["pipeline_dependency_id"], dep["id"])
        same(continuation["payload"]["pipeline_run_id"], native["pipeline_id"])
        same(continuation["payload"]["resume_from_step"], policy["resume"])
        same(continuation["trigger_source"], "CERI_COMPLETION_BARRIER")
        same(dep["result_json"]["workflow_key"], native["workflow_key"])
        same(
            dep["result_json"]["certified_count"],
            len(manifest["completion"]["domains"]["captures"]),
        )
        same(
            next(j for j in jobs if j["id"] == dep["child_job_id"])["job_type"],
            policy["terminal_child_type"],
        )

    if terminal or len(full) > 2:
        check("dependency", dependency, evidence="pipeline-evidence.json#dependencies")
    else:
        recorder.gate("dependency", "INCOMPLETE", reason="dependency/continuation pending")
    status = native.get("pipeline", {}).get("status")
    recorder.gate(
        "terminal",
        "PASS"
        if terminal
        else "FAILED"
        if status in {"FAILED", "PARTIAL", "CANCELLED"}
        else "INCOMPLETE",
        evidence="pipeline-evidence.json#pipeline",
        reason="" if terminal else "logical pipeline has not completed successfully",
    )

    for wanted in manifest["domains"]["stages"]:
        name = "stage:" + wanted["name"]

        def stage(wanted=wanted):
            found = [r for r in native["stages"] if r["step_name"] == wanted["name"]]
            same(len(found), 1)
            row = found[0]
            same(row["step_order"], wanted["order"])
            same(row["status"], "COMPLETED")
            assert row["started_at"] and row["completed_at"] and not row["error_message"]

        if terminal:
            check(name, stage, evidence="pipeline-evidence.json#stages")
        else:
            recorder.gate(name, "INCOMPLETE", reason="final stage reconciliation pending")
    oracle = bundle.get("db-results/independent-complete-oracle.json", {})
    check(
        "fresh_committed",
        lambda: (
            same(native["fresh_committed_session"], True),
            same(oracle["fresh_committed_session"], True),
            same(oracle["execution_id"], identity["execution_id"]),
            same(oracle["run_id"], native["run_id"]),
            same(oracle["pipeline_id"], native["pipeline_id"]),
            same(oracle["errors"], []),
            same(oracle["expected"], manifest["completion"]),
        ),
        evidence="db-results/independent-complete-oracle.json",
    )
    wanted_relationships = relationship_inventory(manifest)

    def immutable_lineage():
        details = oracle["component_details"]["immutable_evidence"]
        sections = {
            "TECHNICAL": "technical",
            "COMBINED": "combined",
            "RANKING": "ranking",
            "REGIME": "market_regime",
            "SECTOR": "sector",
            "CERI": "captures",
            "SETUP": "setup",
        }
        same(
            sorted((row["kind"], row["semantic_member"]) for row in details),
            sorted(
                (kind, relationship_key(section, row))
                for kind, section in sections.items()
                for row in manifest["completion"]["domains"][section]
            ),
        )
        for row in details:
            same(row["ownership"]["run_id"], {"state": "KNOWN", "value": native["run_id"]})
            same(
                row["ownership"]["pipeline_id"],
                {"state": "UNKNOWN"}
                if row["kind"] == "CERI"
                else {"state": "KNOWN", "value": native["pipeline_id"]},
            )
            context_id = (
                native["ceri_context_id"] if row["kind"] == "CERI" else native["market_context_id"]
            )
            same(row["expected_context_id"], context_id)
            cutoff = manifest["business_time"][
                "ceri_cutoff" if row["kind"] == "CERI" else "market_cutoff"
            ]
            same(
                datetime.fromisoformat(row["temporal"]["calculation_cutoff"]["value"]),
                datetime.fromisoformat(cutoff),
            )
            same(row["temporal"]["as_of_session"]["value"], cutoff[:10])
            same(row["calculation_context"]["market_calculation_context_id"]["value"], context_id)
            config = row["effective_configuration"]
            same(config["identity"], row["configuration"]["effective_configuration"]["value"])
            digest = hashlib.sha256(
                json.dumps(
                    config["semantic"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode("utf-8")
            ).hexdigest()
            same(digest, config["semantic_hash"])
            same(digest, config["identity"]["fingerprint"]["digest"])
            if row["expected_sources"] is not None:
                same(row["sources"], row["expected_sources"])

    check(
        "immutable_lineage",
        immutable_lineage,
        evidence="independent-complete-oracle.json#component_details/immutable_evidence",
    )
    check(
        "comparison_inventory",
        lambda: (
            same(sorted(oracle["actual"]), sorted(KEYS)),
            same(sorted(r["name"] for r in oracle["relationships"]), wanted_relationships),
            same(oracle["declared_relationships"], wanted_relationships),
            same(
                [r["step_name"] for r in native["stages"]],
                [r["name"] for r in manifest["domains"]["stages"]],
            ),
        ),
        evidence="complete oracle inventory; pipeline stages",
    )
    for section, wanted in manifest["completion"]["domains"].items():
        check(
            "section:" + section,
            lambda section=section, wanted=wanted: compare_vector(
                wanted,
                oracle["actual"][section],
                keys=KEYS[section],
                tolerance="0"
                if section == "revisions"
                else "0.000001"
                if section == "captures"
                else "0.00000001",
            ),
            evidence="db-results/independent-complete-oracle.json#actual/" + section,
        )
    for name in wanted_relationships:
        check(
            "relationship:" + name,
            lambda name=name: same(
                [r["passed"] for r in oracle["relationships"] if r["name"] == name], [True]
            ),
            evidence="db-results/independent-complete-oracle.json#relationships/" + name,
        )
    subset = bundle.get("db-results/independent-golden-subset.json", {}).get("actual", {})
    for section, keys in (
        ("raw", ("ticker",)),
        ("combined_members", ("ticker",)),
        ("fundamental", ("ticker",)),
        ("technical_indicators", ("ticker",)),
        ("rank_members", ("profile", "ticker")),
        ("stages", ("order",)),
        ("ceri_sources", ("provider", "dataset", "record_key")),
    ):
        check(
            "subset:" + section,
            lambda section=section, keys=keys: compare_records(
                manifest["domains"][section],
                subset[section],
                keys=keys,
                tolerance="0.00000001" if section == "technical_indicators" else "0",
            ),
            evidence="db-results/independent-golden-subset.json#actual/" + section,
        )
    check(
        "subset:business_time",
        lambda: all(
            datetime.fromisoformat(subset["business_time"][key].replace("Z", "+00:00"))
            == datetime.fromisoformat(value)
            for key, value in manifest["business_time"].items()
        ),
        evidence="independent-golden-subset.json#business_time",
    )

    def ingestion_clock():
        clock = subset["operational_ingestion_clock"]
        assert clock["ingestion_started_at"]
        assert all(
            datetime.fromisoformat(clock["initial_claim_at"])
            <= datetime.fromisoformat(value)
            <= datetime.fromisoformat(clock["continuation_claim_at"])
            for value in clock["ingestion_started_at"]
        )

    check(
        "subset:operational_ingestion_clock",
        ingestion_clock,
        evidence="independent-golden-subset.json#operational_ingestion_clock",
    )

    def diagnostics():
        samples = bundle["runtime-invariants.jsonl"]
        assert samples, "required observer samples absent"
        times = []
        for sample in samples:
            same(sample["execution_id"], identity["execution_id"])
            same(sample["pipeline_id"], native["pipeline_id"])
            same(sample["executed_checks"], list(RUNTIME_DIAGNOSTICS))
            same(sample["failures"], [])
            timestamp = datetime.fromisoformat(sample["observed_at"])
            assert timestamp.tzinfo is not None, "observer timestamp must carry timezone"
            times.append(timestamp)
        started = datetime.fromisoformat(native["pipeline"]["started_at"])
        completed = datetime.fromisoformat(native["pipeline"]["completed_at"])
        assert times == sorted(times), "observer samples out of order"
        assert times[-1] >= completed, "final observer sample did not reach native completion"
        assert any(
            started <= timestamp <= completed and sample["active_job_count"] > 0
            for timestamp, sample in zip(times, samples, strict=True)
        ), "no observation of active work within the native execution interval"
        metrics = bundle["db-results/provider-10-metrics.json"]
        assert metrics["ceri_max_checkpoint_gap_seconds"] < 60
        assert metrics["full_pipeline_control"]["min_progress_sequence"] > 1
        same(metrics["full_pipeline_control"]["max_recovery_count"], 0)
        same(metrics["full_pipeline_control"]["stalled_jobs"], 0)

    check("diagnostics", diagnostics, evidence="runtime-invariants.jsonl; provider-10-metrics.json")
    check(
        "gui_admission",
        lambda: (
            bool(native["gui_comparisons"])
            and bool(native["gui_surfaces"])
            and all(c["passed"] is True for c in native["gui_comparisons"])
            and not native["gui_failures"]
        ),
        evidence="pipeline-evidence.json#gui_comparisons (admission/progress only)",
    )
    check(
        "checks_reached",
        lambda: same(
            bundle["checks-reached.json"],
            {
                "execution_id": identity["execution_id"],
                "test_id": identity["test_id"],
                "all_test_checks_reached": True,
            },
        ),
        evidence="checks-reached.json",
        missing="INCOMPLETE",
    )
    check(
        "cleanup",
        lambda: (
            same(bundle["cleanup.json"]["execution_id"], identity["execution_id"]),
            same(bundle["cleanup.json"]["database_dropped"], True),
            same(bundle["cleanup.json"]["supervisor_exited"], True),
            same(bundle["cleanup.json"]["owned_processes_alive"], []),
        ),
        evidence="cleanup.json",
        missing="INCOMPLETE",
    )
    if invocation.get("exit_code") is None and not invocation.get("failed"):
        recorder.gate(
            "invocation",
            "INCOMPLETE",
            reason="final pytest result not obtained",
            evidence="runner collection + pytest JUnit",
        )
    else:
        check(
            "invocation",
            lambda: (
                same(invocation["collected"], list(EXACT_TEST_IDS)),
                same(invocation["selected"], list(EXACT_TEST_IDS)),
                same(invocation["executed"], invocation["selected"]),
                same(invocation["selected"].count(identity["test_id"]), 1),
                same(invocation["exit_code"], 0),
                same(invocation["failed"], []),
                same(invocation["invocation_id"], identity["invocation_id"]),
            ),
            evidence="runner collection + pytest JUnit",
            missing="INCOMPLETE",
        )
    for path in REQUIRED_ARTIFACTS:
        recorder.gate(
            "artifact:" + path,
            "PASS" if path in hashes else "INCOMPLETE",
            evidence=path,
            reason="" if path in hashes else "required artifact absent",
        )
    sealed = set(hashes) == set(REQUIRED_ARTIFACTS) and all(
        len(value) == 64 for value in hashes.values()
    )
    recorder.gate(
        "evidence_seal",
        "PASS" if sealed else "INCOMPLETE",
        evidence=hashes,
        reason="" if sealed else "required artifact seal incomplete",
    )
    return recorder


@dataclass(frozen=True)
class EvidenceGraphResult:
    manifest: dict[str, Any]
    relationship_count: int
    row_count: int


def build_run_evidence_graph(
    engine: Engine,
    *,
    run_id: int,
    artifact_dir: Path,
    tickers: tuple[str, ...],
) -> EvidenceGraphResult:
    sql_dir = artifact_dir / "sql"
    result_dir = artifact_dir / "db-results"
    sql_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    schema = inspect(engine)
    table_names = sorted(schema.get_table_names())
    entries: list[dict[str, Any]] = []
    executed: set[tuple[str, str]] = set()

    for table_name in table_names:
        columns = {column["name"] for column in schema.get_columns(table_name)}
        for column in RUN_COLUMN_NAMES:
            if column in columns:
                relationship = (
                    "semantic background-job lineage"
                    if table_name == "background_jobs" and column == "related_run_id"
                    else f"direct {column}"
                )
                _append_query(
                    engine,
                    entries,
                    executed,
                    table_name=table_name,
                    relationship=relationship,
                    where_sql=f'"{column}" = :run_id',
                    params={"run_id": run_id},
                    sql_dir=sql_dir,
                    result_dir=result_dir,
                    ordinal=len(entries) + 1,
                )

    indirect_queries = _indirect_queries()
    for table_name, relationship, where_sql in indirect_queries:
        if table_name not in table_names:
            continue
        _append_query(
            engine,
            entries,
            executed,
            table_name=table_name,
            relationship=relationship,
            where_sql=where_sql,
            params={"run_id": run_id, "tickers": list(tickers)},
            sql_dir=sql_dir,
            result_dir=result_dir,
            ordinal=len(entries) + 1,
        )

    manifest = {
        "schema": "swinglens.single-run-evidence-graph.v1",
        "run_id": run_id,
        "database_table_count": len(table_names),
        "run_relationship_count": len(entries),
        "run_row_count": sum(entry["row_count"] for entry in entries),
        "tables": entries,
    }
    (artifact_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    return EvidenceGraphResult(
        manifest=manifest,
        relationship_count=len(entries),
        row_count=manifest["run_row_count"],
    )


def query_rows(
    engine: Engine,
    sql: str,
    params: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(text(sql), params or {}).mappings()]


def database_integrity_checks(engine: Engine, run_id: int) -> list[dict[str, Any]]:
    checks = [
        (
            "run_exists_once",
            "select count(*) as value from upload_runs where id = :run_id",
            1,
        ),
        (
            "no_duplicate_raw_tickers",
            """
            select count(*) as value from (
              select ticker from raw_company_rows where run_id = :run_id
              group by ticker having count(*) > 1
            ) duplicates
            """,
            0,
        ),
        (
            "no_orphan_pipeline_steps",
            """
            select count(*) as value from pipeline_steps ps
            left join pipeline_runs pr on pr.id = ps.pipeline_run_id
            where pr.id is null
            """,
            0,
        ),
        (
            "no_orphan_winner_estimates",
            """
            select count(*) as value from winner_probability_estimates e
            left join winner_prediction_snapshots p on p.id = e.prediction_id
            where p.id is null
            """,
            0,
        ),
        (
            "no_orphan_lifecycle_alerts",
            """
            select count(*) as value from signal_alert_events a
            left join setup_lifecycle_evaluation_runs e on e.id = a.evaluation_run_id
            where a.evaluation_run_id is not null and e.id is null
            """,
            0,
        ),
        (
            "one_ceri_snapshot_per_run_ticker",
            """
            select count(*) as value from ceri_score_snapshots
            where run_id = :run_id
            """,
            8,
        ),
        (
            "no_orphan_ceri_alerts",
            """
            select count(*) as value from ceri_alert_events a
            left join ceri_change_events c on c.id = a.source_change_event_id
            where a.source_change_event_id is not null and c.id is null
            """,
            0,
        ),
        (
            "pipeline_terminal",
            """
            select count(*) as value from pipeline_runs
            where upload_run_id = :run_id and status in ('COMPLETED', 'PARTIAL')
            """,
            1,
        ),
    ]
    results: list[dict[str, Any]] = []
    for name, sql, expected in checks:
        rows = query_rows(engine, sql, {"run_id": run_id})
        actual = int(rows[0]["value"])
        results.append(
            {
                "name": name,
                "expected": expected,
                "actual": actual,
                "passed": actual == expected,
                "sql": " ".join(sql.split()),
            }
        )
    return results


def _append_query(
    engine: Engine,
    entries: list[dict[str, Any]],
    executed: set[tuple[str, str]],
    *,
    table_name: str,
    relationship: str,
    where_sql: str,
    params: dict[str, Any],
    sql_dir: Path,
    result_dir: Path,
    ordinal: int,
) -> None:
    key = (table_name, where_sql)
    if key in executed:
        return
    executed.add(key)
    primary_keys = inspect(engine).get_pk_constraint(table_name).get("constrained_columns") or []
    sql = f'SELECT * FROM "{table_name}" WHERE {where_sql} ORDER BY 1'
    effective_params = dict(params)
    if ":tickers" in sql:
        # PostgreSQL cannot bind a list to IN directly.  The semantic queries use ANY.
        effective_params["tickers"] = params["tickers"]
    rows = query_rows(engine, sql, effective_params)
    if not rows:
        return
    sanitized = [_sanitize_row(row) for row in rows]
    stem = f"{ordinal:03d}-{table_name}"
    (sql_dir / f"{stem}.sql").write_text(
        f"-- relationship: {relationship}\n{sql};\n-- params: {_safe_params(effective_params)}\n",
        encoding="utf-8",
    )
    (result_dir / f"{stem}.json").write_text(
        json.dumps(sanitized, indent=2, sort_keys=True, default=_json_default),
        encoding="utf-8",
    )
    ticker_values = sorted(
        {str(row.get("ticker")) for row in sanitized if row.get("ticker") not in (None, "")}
    )
    entries.append(
        {
            "table": table_name,
            "relationship_to_run": relationship,
            "row_count": len(rows),
            "primary_keys": [
                {column: row.get(column) for column in primary_keys} for row in sanitized
            ],
            "tickers": ticker_values,
            "important_identifiers": _important_identifiers(sanitized),
            "content_hash": _hash_rows(sanitized),
            "sql_artifact": f"sql/{stem}.sql",
            "result_artifact": f"db-results/{stem}.json",
        }
    )


def _indirect_queries() -> tuple[tuple[str, str, str], ...]:
    return (
        (
            "pipeline_steps",
            "pipeline_runs.upload_run_id -> pipeline_steps.pipeline_run_id",
            "pipeline_run_id in (select id from pipeline_runs where upload_run_id = :run_id)",
        ),
        (
            "ib_fetch_items",
            "ib_fetch_runs.run_id -> ib_fetch_items.fetch_run_id",
            "fetch_run_id in (select id from ib_fetch_runs where run_id = :run_id)",
        ),
        (
            "price_bar_revisions",
            "ib_fetch_items -> shared price-bar revision delta",
            "fetch_run_id in (select id from ib_fetch_runs where run_id = :run_id)",
        ),
        (
            "price_bars",
            "shared cache consumed by run ticker/date/timeframe/source",
            "ticker = any(:tickers)",
        ),
        (
            "ib_contracts",
            "shared contract cache consumed by run",
            "ticker = any(:tickers)",
        ),
        (
            "sector_rotation_rows",
            "sector_rotation_snapshots.run_id -> rows.snapshot_id",
            "snapshot_id in (select id from sector_rotation_snapshots where run_id = :run_id)",
        ),
        (
            "setup_lifecycle_events",
            "setup lifecycle evaluation for source run",
            "evaluation_run_id in (select id from setup_lifecycle_evaluation_runs "
            "where source_run_id = :run_id)",
        ),
        (
            "signal_change_events",
            "signal changes emitted by run evaluation",
            "evaluation_run_id in (select id from setup_lifecycle_evaluation_runs "
            "where source_run_id = :run_id)",
        ),
        (
            "signal_alert_events",
            "alerts emitted by run evaluation",
            "evaluation_run_id in (select id from setup_lifecycle_evaluation_runs "
            "where source_run_id = :run_id)",
        ),
        (
            "setup_lifecycle_episodes",
            "episodes whose current/opening snapshot belongs to run",
            "current_snapshot_id in (select id from setup_signal_snapshots "
            "where run_id = :run_id) or opening_snapshot_id in "
            "(select id from setup_signal_snapshots where run_id = :run_id)",
        ),
        (
            "winner_probability_estimates",
            "run prediction -> decision-time probability estimate",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_estimate_evidence_members",
            "run prediction/estimate -> evidence membership",
            "prediction_id in (select id from winner_prediction_snapshots "
            "where run_id = :run_id) or estimate_id in (select e.id from "
            "winner_probability_estimates e join winner_prediction_snapshots p "
            "on p.id=e.prediction_id where p.run_id=:run_id)",
        ),
        (
            "winner_forward_outcomes",
            "run prediction -> forward outcome",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_target_stop_outcomes",
            "run prediction -> target/stop outcome",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_similarity_links",
            "run prediction -> similarity evidence",
            "prediction_id in (select id from winner_prediction_snapshots where run_id = :run_id)",
        ),
        (
            "winner_evidence_manifests",
            "probability estimate -> immutable evidence manifest",
            "id in (select e.evidence_manifest_id from winner_probability_estimates e "
            "join winner_prediction_snapshots p on p.id=e.prediction_id "
            "where p.run_id=:run_id and e.evidence_manifest_id is not null)",
        ),
        (
            "winner_processing_runs",
            "single certification maturation operation produced run-owned outcomes",
            "process_type='WINNER_OUTCOME_MATURATION' and exists ("
            "select 1 from winner_forward_outcomes o join winner_prediction_snapshots p "
            "on p.id=o.prediction_id where p.run_id=:run_id and o.matured_at is not null "
            "and o.is_current_revision)",
        ),
        (
            "background_jobs",
            "run-owned matured outcome -> winner processing run -> background job",
            "id in (select w.background_job_id from winner_processing_runs w where "
            "w.process_type='WINNER_OUTCOME_MATURATION' and exists (select 1 from "
            "winner_forward_outcomes o join winner_prediction_snapshots p "
            "on p.id=o.prediction_id where p.run_id=:run_id and o.matured_at is not null "
            "and o.is_current_revision))",
        ),
        (
            "ceri_companies",
            "CERI run snapshot -> canonical company",
            "id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_revision_features",
            "CERI company evidence consumed by run snapshot",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_estimate_snapshots",
            "CERI estimate evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_guidance_events",
            "CERI guidance evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_catalyst_events",
            "CERI catalyst evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_catalyst_event_revisions",
            "CERI catalyst event -> immutable revisions",
            "catalyst_event_id in (select id from ceri_catalyst_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id))",
        ),
        (
            "ceri_catalyst_sources",
            "CERI catalyst event -> provider source lineage",
            "catalyst_event_id in (select id from ceri_catalyst_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id))",
        ),
        (
            "ceri_derived_features",
            "CERI derived evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id=:run_id)",
        ),
        (
            "ceri_price_response_features",
            "CERI price-response evidence for run companies",
            "company_id in (select company_id from ceri_score_snapshots where run_id=:run_id)",
        ),
        (
            "ceri_source_records",
            "normalized CERI evidence -> provider source records",
            "id in (select source_record_id from ceri_estimate_snapshots where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select source_record_id from ceri_guidance_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select cs.source_record_id from ceri_catalyst_sources cs join ceri_catalyst_events ce "
            "on ce.id=cs.catalyst_event_id where ce.company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id))",
        ),
        (
            "ceri_ingestion_runs",
            "CERI source records -> deterministic manual-provider ingestion",
            "id in (select ingestion_run_id from ceri_source_records where id in "
            "(select source_record_id from ceri_estimate_snapshots where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select source_record_id from ceri_guidance_events where company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id) union "
            "select cs.source_record_id from ceri_catalyst_sources cs join ceri_catalyst_events ce "
            "on ce.id=cs.catalyst_event_id where ce.company_id in "
            "(select company_id from ceri_score_snapshots where run_id=:run_id)))",
        ),
        (
            "ceri_processing_runs",
            "CERI normalization/feature processing scoped to run tickers",
            "scope_json->>'ticker' = any(:tickers) or exists (select 1 from "
            "jsonb_array_elements_text(coalesce(scope_json->'tickers', '[]'::jsonb)) item "
            "where item.value = any(:tickers))",
        ),
        (
            "ceri_change_events",
            "CERI score snapshot delta linked to run",
            "to_snapshot_id in (select id from ceri_score_snapshots where run_id = :run_id)",
        ),
        (
            "ceri_alert_events",
            "CERI change event -> alert",
            "source_change_event_id in (select c.id from ceri_change_events c "
            "join ceri_score_snapshots s on s.id=c.to_snapshot_id "
            "where s.run_id=:run_id)",
        ),
    )


def _sanitize_row(row: dict[str, Any]) -> dict[str, Any]:
    restricted = {
        "raw_json",
        "restricted_normalized_json",
        "source_url",
        "source_reference",
        "file_path",
    }
    return {
        key: "<restricted>" if key in restricted and value not in (None, "") else value
        for key, value in row.items()
    }


def _important_identifiers(rows: list[dict[str, Any]]) -> dict[str, list[Any]]:
    candidates = (
        "id",
        "run_id",
        "source_run_id",
        "upload_run_id",
        "related_run_id",
        "pipeline_run_id",
        "prediction_id",
        "evaluation_run_id",
        "snapshot_id",
        "episode_id",
        "evidence_hash",
        "config_hash",
        "feature_vector_hash",
    )
    result: dict[str, list[Any]] = {}
    for key in candidates:
        values = sorted(
            {row[key] for row in rows if row.get(key) not in (None, "")},
            key=lambda value: str(value),
        )
        if values:
            result[key] = values[:100]
    return result


def _hash_rows(rows: list[dict[str, Any]]) -> str:
    encoded = json.dumps(rows, sort_keys=True, default=_json_default).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _safe_params(params: dict[str, Any]) -> str:
    return json.dumps(params, sort_keys=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    return str(value)
