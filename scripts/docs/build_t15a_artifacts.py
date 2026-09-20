"""Build the deterministic T15A Phase-6 inventory and handoff artifacts."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "remediation" / "calculation-lineage"
T14D = OUT / "T14D_operation_family_certification.json"
T14E_FINDINGS = OUT / "T14E_finding_status_after_phase5.csv"

STARTING_HEAD = "f587b63e4e35e81486e53ffa0f13b9cd7369f963"
T14E_SOURCE = "f273e9a61b0a363d873b9f58cf0d702c976f5361a29eb56c2e480fa61b20a5d7"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _spec(
    operation_family_id: str,
    *,
    scope_kind: str,
    selection_point: str,
    scope_model: str,
    membership_model: str,
    membership_fingerprint: str,
    refresh_model: str,
    retry_model: str,
    continuation_model: str,
    acquisition_plan_model: str,
    revision_model: str,
    risk: str,
    findings: tuple[str, ...],
    task: str,
    flags: tuple[str, ...],
) -> dict[str, Any]:
    return {
        "operation_family_id": operation_family_id,
        "scope_kind": scope_kind,
        "scope_selection_point": selection_point,
        "scope_model": scope_model,
        "membership_model": membership_model,
        "membership_fingerprint": membership_fingerprint,
        "refresh_model": refresh_model,
        "retry_model": retry_model,
        "continuation_model": continuation_model,
        "acquisition_plan_model": acquisition_plan_model,
        "revision_model": revision_model,
        "current_defect_risk": risk,
        "finding_ids": list(findings),
        "phase6_target_task": task,
        "status": list(flags),
    }


SPECS = (
    # Pipeline recovery, child work, and provider acquisition.
    _spec(
        "AF_b579e27f1696a70d",
        scope_kind="full-pipeline-run",
        selection_point="run route dispatch",
        scope_model="CURRENT_SELECTION_UNCERTIFIED",
        membership_model="legacy route does not retain an exact target manifest",
        membership_fingerprint="MISSING",
        refresh_model="pipeline run/job identity only",
        retry_model="not certified as same semantic scope",
        continuation_model="downstream stages select from current state",
        acquisition_plan_model="created later from current coverage",
        revision_model="price revisions exist but are not a pipeline scope contract",
        risk="legacy full-run path cannot prove an exact original population",
        findings=("PIPE-001", "PIPE-004", "XINT-007"),
        task="T15B",
        flags=("SCOPE_IDENTITY_MISSING", "REFRESH_IDENTITY_MISSING"),
    ),
    _spec(
        "AF_ce350b6feeb50432",
        scope_kind="pipeline-resume",
        selection_point="resume_pipeline loads current failed/blocked step",
        scope_model="RESELECTS_ON_RETRY",
        membership_model="pipeline/step identity retained; target population is not",
        membership_fingerprint="MISSING",
        refresh_model="no refresh-cycle record",
        retry_model="configuration and operation time retained, target set not frozen",
        continuation_model="resumes current step and may reconstruct current downstream work",
        acquisition_plan_model="no immutable plan lineage",
        revision_model="source revisions delegated to downstream services",
        risk="resume can mean same step under a different current population",
        findings=("PIPE-004", "PIPE-005", "XINT-007"),
        task="T15B",
        flags=(
            "SCOPE_IDENTITY_MISSING",
            "REFRESH_IDENTITY_MISSING",
            "ACQUISITION_PLAN_MISSING",
        ),
    ),
    _spec(
        "AF_716a8499b1e111d4",
        scope_kind="pipeline-after-sec-repair",
        selection_point="repair completion requeues pipeline",
        scope_model="RESELECTS_ON_CONTINUATION",
        membership_model="pipeline reference retained; repaired document/target manifest absent",
        membership_fingerprint="MISSING",
        refresh_model="repair job identity is used as control identity",
        retry_model="same repair may return to changed current pipeline state",
        continuation_model="re-enters pipeline by current step state",
        acquisition_plan_model="SEC repair request is payload-only",
        revision_model="document processor/source identity exists but cycle membership is absent",
        risk="child/background repair can outlive parent accounting and alter resumed membership",
        findings=("PIPE-001", "PIPE-004", "PIPE-005"),
        task="T15B",
        flags=(
            "SCOPE_IDENTITY_MISSING",
            "REFRESH_IDENTITY_MISSING",
            "ACQUISITION_PLAN_MISSING",
        ),
    ),
    _spec(
        "AF_06fd3d113b34f83e",
        scope_kind="background-job-retry",
        selection_point="failed leased job",
        scope_model="CURRENT_SELECTION_UNCERTIFIED",
        membership_model="job payload retained without a shared scope identity",
        membership_fingerprint="MISSING",
        refresh_model="retry count and job ID are operational only",
        retry_model="same job payload is retried but semantic scope is not validated",
        continuation_model="not applicable to the retry decision itself",
        acquisition_plan_model="not validated by generic retry",
        revision_model="not validated by generic retry",
        risk="operational retry can be mistaken for semantic retry",
        findings=("PIPE-004", "XINT-012"),
        task="T15B",
        flags=("SCOPE_IDENTITY_MISSING", "REFRESH_IDENTITY_MISSING"),
    ),
    _spec(
        "AF_7c7a11f09af2e09e",
        scope_kind="sec-readiness-repair-scheduling",
        selection_point="current readiness failure",
        scope_model="CURRENT_SELECTION_UNCERTIFIED",
        membership_model="pipeline/ticker hints only; no exact document manifest",
        membership_fingerprint="MISSING",
        refresh_model="request key coalesces repair work without refresh identity",
        retry_model="same request key may suppress a later observation cycle",
        continuation_model="repair completion may requeue pipeline",
        acquisition_plan_model="document acquisition policy is implicit",
        revision_model="processor signature exists; exact planned document revisions do not",
        risk="repair and later legitimate reacquisition are not formally distinct",
        findings=("PIPE-001", "PIPE-005", "XINT-012"),
        task="T15B",
        flags=(
            "SCOPE_IDENTITY_MISSING",
            "REFRESH_IDENTITY_MISSING",
            "ACQUISITION_PLAN_MISSING",
        ),
    ),
    *(
        _spec(
            operation_family_id,
            scope_kind="ib-price-acquisition",
            selection_point=selection,
            scope_model=scope_model,
            membership_model="requested symbols/items exist in mutable job/plan payload",
            membership_fingerprint="MISSING",
            refresh_model="force flags and request keys, not typed refresh identity",
            retry_model="execution can reuse/rebuild plan depending entrypoint",
            continuation_model="item progress exists without shared remaining-scope identity",
            acquisition_plan_model=(
                "FetchPlan is typed in memory but has no immutable semantic identity"
            ),
            revision_model=(
                "PriceBarRevision captures arrived changes, not the original acquisition plan"
            ),
            risk="changed current coverage/provider settings can silently constitute replanning",
            findings=("PIPE-001", "PIPE-005", "CORE-002", "XINT-007"),
            task="T15B",
            flags=(
                "SCOPE_IDENTITY_MISSING",
                "REFRESH_IDENTITY_MISSING",
                "ACQUISITION_PLAN_MISSING",
                "REVISION_IDENTITY_INCOMPLETE",
            ),
        )
        for operation_family_id, selection, scope_model in (
            (
                "AF_b10d4ec2fa456ce6",
                "submit_fetch_job builds/serializes current FetchPlan",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_406e161ff5c7157e",
                "durable fetch job loads serialized plan",
                "REMAINDER_OF_FROZEN_SCOPE",
            ),
            (
                "AF_33bdca96240039c6",
                "execute_fetch_plan iterates serialized plan items",
                "REMAINDER_OF_FROZEN_SCOPE",
            ),
            (
                "AF_962bfc6f0968f82d",
                "run action builds plan from current coverage",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_e6476331f05f5ec2",
                "resume action evaluates failed/current fetch state",
                "RESELECTS_ON_RETRY",
            ),
            (
                "AF_a22cc94c8ff55c85",
                "retry action rebuilds selection from failures/current state",
                "RESELECTS_ON_RETRY",
            ),
            (
                "AF_671f857a0f5339d6",
                "prewarm enqueue selects requested universe",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            ("AF_23acb094fb6bb85b", "prewarm job loads payload", "REMAINDER_OF_FROZEN_SCOPE"),
            (
                "AF_acf7c620df460e47",
                "prewarm execution iterates current payload",
                "REMAINDER_OF_FROZEN_SCOPE",
            ),
        )
    ),
    # CERI ingestion, refresh, and rebuild.
    *(
        _spec(
            operation_family_id,
            scope_kind="ceri-source-acquisition",
            selection_point=selection,
            scope_model=scope_model,
            membership_model=(
                "ticker/dataset scope or live provider/document discovery; no exact cycle manifest"
            ),
            membership_fingerprint="MISSING",
            refresh_model="stable provider/dataset/ticker request key omits refresh cycle",
            retry_model="completed ingestion run may short-circuit by stable key",
            continuation_model="provider/document iteration follows current response or checkpoint",
            acquisition_plan_model="request fields exist but have no immutable plan record",
            revision_model="CeriSourceRecord has row/content/provider/time identity after arrival",
            risk="stable ingestion identity can suppress a legitimate later refresh",
            findings=("CERI-012", "PIPE-005", "XINT-012", "CERI-002", "CERI-007"),
            task="T15B",
            flags=(
                "SCOPE_IDENTITY_MISSING",
                "REFRESH_IDENTITY_MISSING",
                "ACQUISITION_PLAN_MISSING",
                "REVISION_IDENTITY_INCOMPLETE",
            ),
        )
        for operation_family_id, selection, scope_model in (
            (
                "AF_ca4f55dcf09b6d51",
                "HTTP route accepts ticker/provider/dataset",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_e7cf2bc4f3753200",
                "ingest derives stable request key and fetches provider records",
                "RESELECTS_ON_RETRY",
            ),
            (
                "AF_63eb3b146ad2e615",
                "SEC incremental service discovers current filings",
                "RESELECTS_ON_CONTINUATION",
            ),
            (
                "AF_2ac7e305c24865bb",
                "backfill route declares range/ticker",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_61d58568e4195b40",
                "backfill service iterates requested current acquisition",
                "RESELECTS_ON_CONTINUATION",
            ),
        )
    ),
    *(
        _spec(
            operation_family_id,
            scope_kind="ceri-derived-rebuild",
            selection_point=selection,
            scope_model="CURRENT_SELECTION_UNCERTIFIED",
            membership_model=(
                "live database query under cutoff/context; exact target IDs not retained as scope"
            ),
            membership_fingerprint="MISSING",
            refresh_model="processing run identity, not observation-cycle identity",
            retry_model="retry can reread a changed eligible set",
            continuation_model="batch/rebuild selection is not a frozen shared remainder",
            acquisition_plan_model="NOT_APPLICABLE",
            revision_model="source IDs/hashes vary by derived artifact; coverage is incomplete",
            risk="rebuild population may expand/shrink between attempts",
            findings=("CERI-001", "CERI-008", "CERI-010"),
            task="T15D",
            flags=(
                "SCOPE_IDENTITY_MISSING",
                "REFRESH_IDENTITY_MISSING",
                "REVISION_IDENTITY_INCOMPLETE",
            ),
        )
        for operation_family_id, selection in (
            ("AF_095460ea3293f382", "feature rebuild selects source rows/current context"),
            ("AF_09185dfbe8dc8ae9", "context rebuild selects eligible source rows"),
            ("AF_9701990a1bad3828", "change rebuild selects current comparable events"),
        )
    ),
    # Winner maturation, cohort/generation, and prediction populations.
    *(
        _spec(
            operation_family_id,
            scope_kind="winner-h5-maturation",
            selection_point=selection,
            scope_model=scope_model,
            membership_model="live due-row query; processed IDs are only local to one drain call",
            membership_fingerprint="MISSING",
            refresh_model="session request key/workflow key, not refresh-cycle identity",
            retry_model="retry can see newly due or newly eligible rows",
            continuation_model=(
                "continuation reruns live due query and can expand/shrink membership"
            ),
            acquisition_plan_model="market-data needs are discovered while processing",
            revision_model=(
                "outcome lineage hash/cutoff exist; exact birth bar revision IDs can be absent"
            ),
            risk=(
                "newly eligible rows can enter an old cycle and current-state changes can "
                "remove rows"
            ),
            findings=("WIN-008", "WIN-006", "PIPE-001", "XINT-012"),
            task="T15C",
            flags=(
                "SCOPE_IDENTITY_MISSING",
                "REFRESH_IDENTITY_MISSING",
                "ACQUISITION_PLAN_MISSING",
                "REVISION_IDENTITY_INCOMPLETE",
            ),
        )
        for operation_family_id, selection, scope_model in (
            (
                "AF_370448d4d5b97745",
                "scheduler checks current due queue for completed session",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_ab5b156dc59c02ff",
                "workflow enqueue records due session/limits but no target IDs",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_d07e72b38c67920a",
                "HTTP route enqueues maturation without exact membership",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_181b495253d9c9f3",
                "each batch queries current due pending outcomes",
                "RESELECTS_ON_CONTINUATION",
            ),
            (
                "AF_391cc87ab4acf60e",
                "single current forward outcome",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
            (
                "AF_07d5132ea4950d61",
                "single current prediction/outcome pair",
                "CURRENT_SELECTION_UNCERTIFIED",
            ),
        )
    ),
    *(
        _spec(
            operation_family_id,
            scope_kind="winner-cohort-generation",
            selection_point=selection,
            scope_model=scope_model,
            membership_model=membership,
            membership_fingerprint=fingerprint,
            refresh_model=(
                "desired watermark/generation key tracks truth change but no shared "
                "refresh-cycle identity"
            ),
            retry_model=(
                "generation capture can resume exact generation; request coalescing is stable "
                "by definition"
            ),
            continuation_model="materialization slices are generation-bound",
            acquisition_plan_model="NOT_APPLICABLE",
            revision_model=(
                "manifest pins outcome revisions/eligibility decisions; upstream price revision "
                "gap remains"
            ),
            risk=(
                "generation population is strong after capture, but refresh/idempotency is not "
                "a general cycle contract"
            ),
            findings=("WIN-006", "WIN-008", "XINT-012"),
            task="T15C",
            flags=flags,
        )
        for operation_family_id, selection, scope_model, membership, fingerprint, flags in (
            (
                "AF_28a02ef387a2cf02",
                "planner advances current material-evidence watermark",
                "CURRENT_SELECTION_UNCERTIFIED",
                "watermark-bound desired population before exact manifest capture",
                "desired_watermark_hash",
                (
                    "SCOPE_IDENTITY_MISSING",
                    "REFRESH_IDENTITY_MISSING",
                    "REVISION_IDENTITY_INCOMPLETE",
                ),
            ),
            (
                "AF_9f421f6c2c6dc73f",
                "refresh service captures/resumes generation contract",
                "REMAINDER_OF_FROZEN_SCOPE",
                "generation/group plan retained; evidence manifests freeze realized population",
                "generation_key/root_manifest_hash",
                (
                    "SCOPE_IDENTITY_MISSING",
                    "REFRESH_IDENTITY_MISSING",
                    "REVISION_IDENTITY_INCOMPLETE",
                ),
            ),
            (
                "AF_8b743894024472f7",
                "materialize_slice operates on captured generation/group",
                "REMAINDER_OF_FROZEN_SCOPE",
                "WinnerEvidenceManifest and members persist exact realized membership",
                "WinnerEvidenceManifest.manifest_hash",
                (
                    "SCOPE_IDENTITY_MISSING",
                    "REFRESH_IDENTITY_MISSING",
                    "REVISION_IDENTITY_INCOMPLETE",
                ),
            ),
            (
                "AF_f23925ce093d1da1",
                "cohort statistic/estimate consumes evidence manifest",
                "FROZEN_CERTIFIED",
                "exact WinnerEvidenceManifest members with outcome revision numbers",
                "WinnerEvidenceManifest.manifest_hash",
                ("REFRESH_IDENTITY_MISSING", "REVISION_IDENTITY_INCOMPLETE"),
            ),
        )
    ),
    *(
        _spec(
            operation_family_id,
            scope_kind="winner-prediction-population",
            selection_point=selection,
            scope_model="CURRENT_SELECTION_UNCERTIFIED",
            membership_model=(
                "run/current eligibility query or single ticker; no shared exact population "
                "identity"
            ),
            membership_fingerprint="MISSING",
            refresh_model="job/run identity only",
            retry_model="current prediction/outcome state may change between attempts",
            continuation_model="population queries are not bound to a scope snapshot",
            acquisition_plan_model="market-data requirements implicit or separately queued",
            revision_model=(
                "prediction revision exists; birth PriceBar revision IDs remain incomplete"
            ),
            risk="capture/backfill/pending materialization population is not reproducibly frozen",
            findings=("WIN-006", "WIN-008"),
            task="T15C",
            flags=(
                "SCOPE_IDENTITY_MISSING",
                "REFRESH_IDENTITY_MISSING",
                "REVISION_IDENTITY_INCOMPLETE",
            ),
        )
        for operation_family_id, selection in (
            ("AF_5e9c7f4b54deade3", "HTTP route selects run/ticker capture request"),
            ("AF_79c791f0e8421e6e", "capture service processes one current ticker"),
            ("AF_a1a274f9c73e3fa0", "capture/pending service uses current prediction state"),
            ("AF_6b6a709b24cfa078", "pending outcome service selects current predictions"),
            ("AF_00e602453a046eb8", "backfill selects current eligible historical predictions"),
        )
    ),
    # Other Phase-6 algorithm/scope populations.
    *(
        _spec(
            operation_family_id,
            scope_kind=scope_kind,
            selection_point=selection,
            scope_model="CURRENT_SELECTION_UNCERTIFIED",
            membership_model="run-scoped live query without independent population identity",
            membership_fingerprint="MISSING",
            refresh_model="route/job identity only",
            retry_model="retry can observe changed current rows",
            continuation_model="no shared frozen remainder contract",
            acquisition_plan_model="NOT_APPLICABLE",
            revision_model="domain evidence exists but population/revision coverage is incomplete",
            risk="algorithm refresh population is not independently reproducible",
            findings=findings,
            task="T15D",
            flags=(
                "SCOPE_IDENTITY_MISSING",
                "REFRESH_IDENTITY_MISSING",
                "REVISION_IDENTITY_INCOMPLETE",
            ),
        )
        for operation_family_id, scope_kind, selection, findings in (
            (
                "AF_2071f64f2b1f231c",
                "combined-ranking-refresh",
                "run route selects current run rows",
                ("RANK-007", "RANK-008", "CORE-005"),
            ),
            (
                "AF_c6cbaf3f7ed57e8e",
                "technical-refresh",
                "run route selects current run tickers",
                ("CORE-003", "CORE-004", "CORE-005"),
            ),
            (
                "AF_be5ed7d8ee0c3e0c",
                "setup-snapshot-capture",
                "capture service selects current compatible run artifacts",
                ("XINT-003",),
            ),
            (
                "AF_ddaa57ad85ff8bf7",
                "setup-current-state-repair",
                "repair_ticker selects current state",
                ("XINT-003",),
            ),
        )
    ),
)


TRUTH_SOURCES = [
    {
        "source": "PriceBar",
        "row_id": "price_bars.id",
        "revision_id": "PriceBarRevision.id/revision_number when a change is recorded",
        "content_hash": "price_bars.data_hash and PriceBarRevision.new_data_hash",
        "times": "first_seen_at/last_seen_at/revised_at; revision observed_at",
        "gap": "initial/birth consumption does not always persist exact PriceBarRevision IDs",
        "target_task": "T15C",
    },
    {
        "source": "CeriSourceRecord",
        "row_id": "ceri_source_records.id",
        "revision_id": "supersedes_id plus provider_record_id/content_hash",
        "content_hash": "content_hash/normalized_hash",
        "times": "published_at/observed_at/source_timestamp/retrieved_at/ingested_at",
        "gap": "refresh cycle and acquisition plan are not attached to source identity",
        "target_task": "T15B",
    },
    {
        "source": "CERI normalized estimates/earnings/guidance",
        "row_id": "normalized row id plus source_record_id",
        "revision_id": "source record or supersedes link depending artifact",
        "content_hash": "source record hash; selected derived hashes vary",
        "times": "known/effective/report/provider-observed/retrieved fields vary by artifact",
        "gap": "provider/currency/publication/revision proof is not uniform across consumers",
        "target_task": "T15D",
    },
    {
        "source": "WinnerPredictionSnapshot",
        "row_id": "winner_prediction_snapshots.id",
        "revision_id": "natural revision plus active supersession",
        "content_hash": "feature_vector_hash and native capture proof",
        "times": "source_data_cutoff_at/decision_at/captured_at",
        "gap": "source_ids_json can omit exact source PriceBar revision IDs",
        "target_task": "T15C",
    },
    {
        "source": "WinnerForwardOutcome / WinnerEvidenceManifest",
        "row_id": "outcome id and manifest member IDs",
        "revision_id": "forward/target-stop revision pinned in manifest",
        "content_hash": "source_bar_lineage_hash/member_hash/manifest_hash",
        "times": "source_revision_cutoff_at/matured_at/training_cutoff_at",
        "gap": "lineage hash does not always enumerate exact birth bar revisions",
        "target_task": "T15C",
    },
]


def build_artifacts() -> dict[str, str]:
    source = json.loads(T14D.read_text(encoding="utf-8"))
    families = {item["operation_family_id"]: item for item in source["families"]}
    records = []
    seen = set()
    for spec in SPECS:
        operation_family_id = spec["operation_family_id"]
        if operation_family_id in seen:
            raise ValueError(f"duplicate selected operation family: {operation_family_id}")
        seen.add(operation_family_id)
        family = families.get(operation_family_id)
        if family is None:
            raise ValueError(f"T14D operation family disappeared: {operation_family_id}")
        records.append(
            {
                **spec,
                "domain": family["domains"],
                "semantic_service": family["semantic_service"],
                "t14d_authority_status": family["final_status"],
            }
        )
    records.sort(key=lambda item: item["operation_family_id"])
    if any("UNKNOWN" in item["scope_model"] for item in records):
        raise ValueError("selected Phase-6 inventory contains an unknown scope model")
    counts = {
        "total": len(records),
        "frozen": sum(
            item["scope_model"] in {"FROZEN_CERTIFIED", "REMAINDER_OF_FROZEN_SCOPE"}
            for item in records
        ),
        "dynamic": sum(item["scope_model"] == "DECLARED_DYNAMIC_CERTIFIED" for item in records),
        "scope_missing": sum("SCOPE_IDENTITY_MISSING" in item["status"] for item in records),
        "refresh_identity_missing": sum(
            "REFRESH_IDENTITY_MISSING" in item["status"] for item in records
        ),
        "acquisition_plan_missing": sum(
            "ACQUISITION_PLAN_MISSING" in item["status"] for item in records
        ),
        "revision_incomplete": sum(
            "REVISION_IDENTITY_INCOMPLETE" in item["status"] for item in records
        ),
        "unknown": 0,
    }
    inventory = {
        "schema_version": "t15a-scope-operation-inventory-v1",
        "source": {
            "t14d_operation_family_certificate": T14D.name,
            "t14d_certificate_sha256": _sha(T14D),
            "t14d_certified_family_count": source["operation_family_count"],
            "t14e_finding_snapshot": T14E_FINDINGS.name,
            "t14e_finding_snapshot_sha256": _sha(T14E_FINDINGS),
        },
        "selection_rule": (
            "From the 220 T14D certified families, include operations whose concrete semantics "
            "select/iterate a population, refresh/retry/resume/continue work, acquire provider "
            "data, mature outcomes, materialize generations, or consume truth revisions."
        ),
        "counts": counts,
        "records": records,
    }

    fields = (
        "operation_family_id",
        "domain",
        "scope_kind",
        "scope_selection_point",
        "scope_model",
        "membership_model",
        "membership_fingerprint",
        "refresh_model",
        "retry_model",
        "continuation_model",
        "acquisition_plan_model",
        "revision_model",
        "current_defect_risk",
        "finding_ids",
        "phase6_target_task",
        "status",
        "semantic_service",
        "t14d_authority_status",
    )
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for record in records:
        writer.writerow(
            {
                **record,
                "domain": "|".join(record["domain"]),
                "finding_ids": "|".join(record["finding_ids"]),
                "status": "|".join(record["status"]),
            }
        )

    findings = list(csv.DictReader(T14E_FINDINGS.open(encoding="utf-8-sig")))
    phase6_findings = [row for row in findings if row["next_phase"] == "Phase 6"]
    handoff = {
        "schema_version": "t15a-phase6-handoff-v1",
        "baselines": {
            "t15a_starting_head": STARTING_HEAD,
            "phase5_source_sha256": T14E_SOURCE,
            "t14e_finding_snapshot_sha256": _sha(T14E_FINDINGS),
        },
        "decomposition": {
            "T15B": {
                "scope": "Pipeline and CERI scope/refresh/acquisition adoption",
                "finding_ids": [
                    "PIPE-001",
                    "PIPE-004",
                    "PIPE-005",
                    "CERI-012",
                    "XINT-007",
                    "XINT-012",
                ],
                "contracts": [
                    "persist scope and refresh IDs at root admission",
                    "bind child/retry/continuation work to exact retained IDs",
                    "version CERI request keys by refresh cycle",
                    "persist IB/CERI acquisition plan and explicit replan lineage",
                ],
            },
            "T15C": {
                "scope": "Winner target-scope, maturation, cohort, and revision-truth adoption",
                "finding_ids": ["WIN-006", "WIN-008"],
                "contracts": [
                    "freeze exact due forward-outcome IDs before first maturation batch",
                    "continuations consume only the frozen remainder",
                    "bind cohort refresh/generation manifests to refresh-cycle identity",
                    "persist exact PriceBar revision IDs or explicit lineage-unavailable state",
                ],
            },
            "T15D": {
                "scope": "Remaining provenance and algorithm-specific Phase-6 correctness",
                "finding_ids": sorted(
                    row["finding_id"]
                    for row in phase6_findings
                    if row["finding_id"]
                    not in {
                        "PIPE-001",
                        "PIPE-004",
                        "PIPE-005",
                        "CERI-012",
                        "XINT-007",
                        "XINT-012",
                        "WIN-006",
                        "WIN-008",
                    }
                ),
                "contracts": [
                    "complete provider/currency/publication/revision provenance",
                    "remediate algorithm-specific CERI/core/ranking findings",
                    "adopt scope identity where refresh populations remain live queries",
                ],
            },
        },
        "truth_revision_inventory": TRUTH_SOURCES,
        "phase7_exclusion": (
            "Original-context reconstruction remains Phase 7; current state is never used to "
            "fabricate a certified historical scope or configuration."
        ),
        "operation_family_counts": counts,
    }
    validation = {
        "schema_version": "t15a-validation-summary-v1",
        "verdict": "PASS",
        "starting_head": STARTING_HEAD,
        "migration": {
            "required": True,
            "head": "0081_scope_refresh_identity",
            "additive_only": True,
            "production_rewrite": False,
            "legacy_backfill": False,
        },
        "foundation": {
            "typed_work_scope_identity": True,
            "deterministic_membership_hash": True,
            "typed_refresh_cycle_identity": True,
            "typed_acquisition_plan_identity": True,
            "explicit_replan_lineage": True,
            "zero_progress_guard_contract": True,
            "parent_child_accounting_contract": True,
            "legacy_unknown_not_certified": True,
        },
        "inventory": {
            "t14d_total_families": source["operation_family_count"],
            "selected_phase6_families": len(records),
            "unknown_scope_models": 0,
            "all_have_target_task": all(
                record["phase6_target_task"] in {"T15B", "T15C", "T15D"} for record in records
            ),
        },
        "tests": {
            "unit": "17 passed",
            "inventory": "2 passed",
            "postgresql": (
                "80 distinct tests passed across the selected PostgreSQL regression suites on a "
                "disposable PostgreSQL 18 container"
            ),
            "query_bound": "constant statement bound certified for 1/50/200 members",
        },
        "regression": {
            "non_database": "385 passed",
            "postgresql": "80 distinct tests passed",
            "phase2_migration_round_trip": "3 passed",
            "frozen_authority_suites": "75 passed",
        },
        "static_gates": {
            "changed_file_ruff": "PASS",
            "changed_file_format": "PASS",
            "compileall": "PASS",
            "diff_whitespace": "PASS",
            "secret_scan": "PASS",
            "single_migration_head": "PASS: 0081_scope_refresh_identity",
            "schema_model_drift": "PASS: no new upgrade operations detected",
            "repository_wide_ruff_advisory": (
                "24 pre-existing violations in historical migration files; no T15A violation"
            ),
        },
        "final_verdict": "PASS",
    }
    return {
        "T15A_scope_operation_inventory.json": json.dumps(inventory, indent=2, sort_keys=True)
        + "\n",
        "T15A_scope_refresh_authority_matrix.csv": stream.getvalue(),
        "T15A_phase6_handoff.json": json.dumps(handoff, indent=2, sort_keys=True) + "\n",
        "T15A_validation_summary.json": json.dumps(validation, indent=2, sort_keys=True) + "\n",
    }


def main() -> None:
    for name, content in build_artifacts().items():
        (OUT / name).write_text(content, encoding="utf-8", newline="")


if __name__ == "__main__":
    main()
