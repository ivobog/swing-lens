from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, PipelineRun, PipelineStep, UploadRun
from app.services.alembic_heads import schema_is_at_head
from app.services.background_job_service import TERMINAL_JOB_STATUSES, enqueue_job
from app.services.background_worker import JobDeferred
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.batched_workflow import (
    CERI_FEATURE_BATCH,
    CERI_NORMALIZE_BATCH,
    CERI_PROVIDER_INGEST_BATCH,
    CeriBatchJobSpec,
    _ensure_ceri_companies,
)
from app.services.ceri.config import load_ceri_config
from app.services.certification_runtime import (
    CERI_FEATURE_CERTIFICATION_ALLOWED_PROVIDER_DATASETS,
    CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION,
    CERI_FEATURE_CERTIFICATION_MAX_TICKERS,
    CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
    CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY,
    CertificationRuntimeViolation,
    ceri_feature_certification_root_payload,
    is_certification_mode,
    queue_isolation_status,
)
from app.services.market_calculation_context_service import create_pipeline_market_context
from app.services.pipeline_service import PipelineStatus, PipelineStepStatus
from app.services.scope_refresh_adoption import (
    admit_frozen_operation,
    bind_semantic_authority,
    require_semantic_authority,
    retained_scope_members,
)
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
from app.settings import Settings, get_settings

CERI_FEATURE_CERTIFICATION_STEP = "CERI_FEATURE_CERTIFICATION"
CERI_FEATURE_CERTIFICATION_PRIORITY = 70
CERI_FEATURE_CERTIFICATION_MAX_RETRIES = 0
_TICKER_PATTERN = re.compile(r"[A-Z][A-Z0-9.-]{0,14}")
_PROHIBITED_PERMISSION_KEYS = (
    "allow_score_capture",
    "allow_change_detection",
    "allow_alerts",
    "allow_setup_publication",
    "allow_winner_publication",
    "allow_lifecycle_publication",
    "allow_downstream_continuation",
)
_ALLOWED_CHILD_JOB_TYPES = frozenset(
    {CERI_PROVIDER_INGEST_BATCH, CERI_NORMALIZE_BATCH, CERI_FEATURE_BATCH}
)


@dataclass(frozen=True)
class CeriFeatureCertificationRequest:
    tickers: tuple[str, ...]
    provider_datasets: tuple[str, ...] = ("eodhd:earnings", "eodhd:estimates")
    checkpoint_interval: int = 1
    cutoff_at: datetime | None = None
    stop_boundary: str = CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY
    request_key: str = ""
    requested_by: str | None = None


@dataclass(frozen=True)
class CeriFeatureCertificationPlan:
    workflow_key: str
    tickers: tuple[str, ...]
    provider_datasets: dict[str, tuple[str, ...]]
    jobs: tuple[CeriBatchJobSpec, ...]

    @property
    def provider_batches(self) -> int:
        return sum(job.job_type == CERI_PROVIDER_INGEST_BATCH for job in self.jobs)

    @property
    def normalization_batches(self) -> int:
        return sum(job.job_type == CERI_NORMALIZE_BATCH for job in self.jobs)

    @property
    def feature_batches(self) -> int:
        return sum(job.job_type == CERI_FEATURE_BATCH for job in self.jobs)


@dataclass(frozen=True)
class CeriFeatureCertificationAdmission:
    upload_run_id: int
    pipeline_run_id: int
    root_job_id: int
    workflow_key: str


def build_ceri_feature_certification_plan(
    *,
    workflow_key: str,
    tickers: tuple[str, ...],
    provider_datasets: dict[str, tuple[str, ...]],
    checkpoint_interval: int,
) -> CeriFeatureCertificationPlan:
    symbols = _validate_tickers(tickers)
    datasets = _validate_provider_datasets(provider_datasets)
    if checkpoint_interval != 1:
        raise ValueError("CERI_FEATURE_CERTIFICATION_CHECKPOINT_INTERVAL_MUST_EQUAL_ONE")

    jobs: list[CeriBatchJobSpec] = []
    normalization_count = 0
    priorities = {"estimates": 80, "earnings": 90}
    for provider, names in datasets.items():
        for dataset in names:
            priority = priorities[dataset]
            provider_key = f"{workflow_key}:provider:{provider}:{dataset}:0001"
            jobs.append(
                CeriBatchJobSpec(
                    job_type=CERI_PROVIDER_INGEST_BATCH,
                    request_key=provider_key,
                    priority=priority,
                    payload={
                        "workflow_key": workflow_key,
                        "request_key": provider_key,
                        "provider": provider,
                        "dataset": dataset,
                        "tickers": list(symbols),
                        "batch_index": 1,
                        "checkpoint_interval": 1,
                    },
                )
            )
            normalize_key = f"{workflow_key}:normalize:{provider}:{dataset}:0001"
            jobs.append(
                CeriBatchJobSpec(
                    job_type=CERI_NORMALIZE_BATCH,
                    request_key=normalize_key,
                    priority=priority + 1,
                    payload={
                        "workflow_key": workflow_key,
                        "request_key": normalize_key,
                        "provider": provider,
                        "dataset": dataset,
                        "tickers": list(symbols),
                        "batch_index": 1,
                        "checkpoint_interval": 1,
                    },
                )
            )
            normalization_count += 1

    feature_key = f"{workflow_key}:feature:0001"
    jobs.append(
        CeriBatchJobSpec(
            job_type=CERI_FEATURE_BATCH,
            request_key=feature_key,
            priority=130,
            payload={
                "workflow_key": workflow_key,
                "request_key": feature_key,
                "tickers": list(symbols),
                "batch_index": 1,
                "expected_normalization_batches": normalization_count,
                "checkpoint_interval": 1,
            },
        )
    )
    return CeriFeatureCertificationPlan(
        workflow_key=workflow_key,
        tickers=symbols,
        provider_datasets=datasets,
        jobs=tuple(jobs),
    )


def admit_ceri_feature_certification(
    db: Session,
    request: CeriFeatureCertificationRequest,
    *,
    settings: Settings | None = None,
) -> CeriFeatureCertificationAdmission:
    runtime_settings = settings or get_settings()
    if not is_certification_mode(runtime_settings):
        raise CertificationRuntimeViolation(
            "CERI_FEATURE_CERTIFICATION_MODE_REQUIRED",
            "feature-only certification roots may be admitted only in certification mode",
        )
    if not schema_is_at_head(db.connection()):
        raise CertificationRuntimeViolation(
            "CERI_FEATURE_CERTIFICATION_SCHEMA_HEAD_REQUIRED",
            "the database schema must match the repository head",
        )
    isolation = queue_isolation_status(db)
    if not isolation.isolated:
        raise CertificationRuntimeViolation(
            "CERI_FEATURE_CERTIFICATION_QUEUE_NOT_ISOLATED",
            f"{isolation.unrelated_runnable_jobs} unrelated runnable jobs exist",
        )

    tickers = _validate_tickers(request.tickers)
    provider_datasets = _validate_provider_dataset_tokens(request.provider_datasets)
    if request.checkpoint_interval != 1:
        raise ValueError("CERI_FEATURE_CERTIFICATION_CHECKPOINT_INTERVAL_MUST_EQUAL_ONE")
    if request.stop_boundary != CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY:
        raise ValueError("CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY_REQUIRED")
    request_key = str(request.request_key).strip()
    if not request_key or len(request_key) > 200:
        raise ValueError("CERI_FEATURE_CERTIFICATION_REQUEST_KEY_REQUIRED")

    config = load_ceri_config()
    workflow_key = f"ceri:feature-certification:{request_key}:{config.config_hash}"
    plan = build_ceri_feature_certification_plan(
        workflow_key=workflow_key,
        tickers=tickers,
        provider_datasets=provider_datasets,
        checkpoint_interval=request.checkpoint_interval,
    )
    cutoff_at = request.cutoff_at or datetime.now(UTC)
    authority = admit_frozen_operation(
        db,
        operation_kind="ceri-feature-certification",
        subject_kind="ticker",
        members=tuple(ScopeMember("TICKER", ticker) for ticker in tickers),
        cycle_key=workflow_key,
        business_cutoff=cutoff_at,
        provider_source_class="CERI_EODHD",
        request_type=CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
        requirements=tuple(
            AcquisitionRequirement(f"CERI_{provider.upper()}_{dataset.upper()}")
            for provider, datasets in provider_datasets.items()
            for dataset in datasets
        ),
        policy_identity=CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION,
        scope_definition={
            "tickers": list(tickers),
            "provider_datasets": {
                provider: list(datasets) for provider, datasets in provider_datasets.items()
            },
            "checkpoint_interval": 1,
            "stop_boundary": CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY,
        },
        configuration_identity=config.config_hash,
        refresh_reason="EXPLICIT_FEATURE_CERTIFICATION_ADMISSION",
    )
    upload_run = UploadRun(
        filename=f"{request_key}.ceri-feature-certification.json",
        file_path=None,
        row_count=len(tickers),
        status="PROCESSING",
        notes=json.dumps(
            {
                "certification_contract_version": CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION,
                "tickers": list(tickers),
                "provider_datasets": {
                    provider: list(datasets) for provider, datasets in provider_datasets.items()
                },
                "stop_boundary": CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY,
            },
            sort_keys=True,
        ),
    )
    db.add(upload_run)
    db.flush()
    pipeline = PipelineRun(
        upload_run_id=upload_run.id,
        status=PipelineStatus.CERI_FEATURE_CERTIFYING,
        current_step=CERI_FEATURE_CERTIFICATION_STEP,
        requested_by=request.requested_by,
        started_at=datetime.now(UTC),
        message="Bounded CERI feature-only certification admitted.",
    )
    bind_semantic_authority(pipeline, authority)
    db.add(pipeline)
    db.flush()
    step = PipelineStep(
        pipeline_run_id=pipeline.id,
        step_name=CERI_FEATURE_CERTIFICATION_STEP,
        step_order=1,
        status=PipelineStepStatus.PENDING,
        started_at=None,
        message="Provider acquisition, normalization, and one feature batch only.",
        retry_count=0,
    )
    db.add(step)
    cutoff = create_pipeline_market_context(
        db,
        pipeline,
        cutoff_at=cutoff_at,
        reason="CERI_FEATURE_CERTIFICATION_FROZEN_AT_ENQUEUE",
    )
    step.status = PipelineStepStatus.RUNNING
    step.started_at = pipeline.started_at
    _ensure_ceri_companies(db, tickers)

    permissions = {key: False for key in _PROHIBITED_PERMISSION_KEYS}
    root_payload = {
        **ceri_feature_certification_root_payload(settings=runtime_settings),
        "workflow_key": workflow_key,
        "request_key": request_key,
        "run_id": upload_run.id,
        "pipeline_run_id": pipeline.id,
        "tickers": list(tickers),
        "provider_datasets": {
            provider: list(datasets) for provider, datasets in provider_datasets.items()
        },
        "checkpoint_interval": 1,
        "feature_batch_count": plan.feature_batches,
        "configuration_identity": config.config_hash,
        "calculation_context_id": cutoff.context_id,
        "cutoff_at": CanonicalEvidenceSerializer.canonicalize(cutoff.cutoff_at),
        "as_of_session": cutoff.latest_completed_session.isoformat(),
        "calendar_version": cutoff.calendar_version,
        **authority.as_dict(),
        **permissions,
    }
    root = enqueue_job(
        db,
        CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
        root_payload,
        related_run_id=upload_run.id,
        priority=CERI_FEATURE_CERTIFICATION_PRIORITY,
        max_retries=CERI_FEATURE_CERTIFICATION_MAX_RETRIES,
        request_key=request_key,
        workflow_key=workflow_key,
        single_flight_workflow=True,
        trigger_source="EXPLICIT_CERTIFICATION_CLI",
    )
    bind_semantic_authority(root, authority)
    pipeline.result_json = {
        "background_job_id": root.id,
        "feature_certification_root_job_id": root.id,
        "feature_certification_workflow_key": workflow_key,
        "certification_contract_version": CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION,
        "stop_boundary": CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY,
        "tickers": list(tickers),
        "provider_datasets": root_payload["provider_datasets"],
        "checkpoint_interval": 1,
        "feature_batch_count": 1,
        **permissions,
    }
    db.flush()
    return CeriFeatureCertificationAdmission(
        upload_run_id=int(upload_run.id),
        pipeline_run_id=int(pipeline.id),
        root_job_id=int(root.id),
        workflow_key=workflow_key,
    )


def execute_ceri_feature_certification_job(
    db: Session, job: BackgroundJob
) -> dict[str, Any]:
    payload = dict(job.payload_json or {})
    tickers, provider_datasets = _validate_root_payload(payload)
    workflow_key = str(job.workflow_key or payload.get("workflow_key") or "")
    if not workflow_key.startswith("ceri:feature-certification:"):
        raise ValueError("CERI_FEATURE_CERTIFICATION_WORKFLOW_KEY_INVALID")
    pipeline_id = int(payload.get("pipeline_run_id") or 0)
    pipeline = db.scalar(
        select(PipelineRun).where(PipelineRun.id == pipeline_id).with_for_update()
    )
    if pipeline is None:
        raise ValueError("CERI_FEATURE_CERTIFICATION_PIPELINE_REQUIRED")
    if pipeline.status != PipelineStatus.CERI_FEATURE_CERTIFYING:
        raise ValueError(f"CERI_FEATURE_CERTIFICATION_PIPELINE_NOT_ACTIVE:{pipeline.status}")
    authority = require_semantic_authority(job)
    retained_tickers = tuple(
        member.subject_id for member in retained_scope_members(db, authority.scope_id)
    )
    if retained_tickers != tickers:
        raise ValueError("CERI_FEATURE_CERTIFICATION_SCOPE_MISMATCH")

    plan = build_ceri_feature_certification_plan(
        workflow_key=workflow_key,
        tickers=tickers,
        provider_datasets=provider_datasets,
        checkpoint_interval=1,
    )
    common_payload = {
        "run_id": int(payload.get("run_id") or job.related_run_id),
        "pipeline_run_id": pipeline.id,
        "feature_certification_root_job_id": job.id,
        "stop_boundary": CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY,
        **{
            key: payload[key]
            for key in ("calculation_context_id", "cutoff_at", "as_of_session", "calendar_version")
        },
        **authority.as_dict(),
    }
    for spec in plan.jobs:
        child = enqueue_job(
            db,
            spec.job_type,
            {**spec.payload, **common_payload},
            related_run_id=int(payload.get("run_id") or job.related_run_id),
            priority=spec.priority,
            max_retries=3,
            request_key=spec.request_key,
            workflow_key=workflow_key,
            parent_job_id=job.id,
            trigger_source=CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
        )
        bind_semantic_authority(child, authority, required_for_parent_completion=True)
    db.flush()

    children = list(
        db.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.workflow_key == workflow_key,
                BackgroundJob.job_type.in_(_ALLOWED_CHILD_JOB_TYPES),
            )
            .order_by(BackgroundJob.priority, BackgroundJob.id)
        )
    )
    if len(children) != len(plan.jobs):
        raise ValueError("CERI_FEATURE_CERTIFICATION_GRAPH_CARDINALITY_MISMATCH")
    if any(child.job_type not in _ALLOWED_CHILD_JOB_TYPES for child in children):
        raise ValueError("CERI_FEATURE_CERTIFICATION_PROHIBITED_CHILD")
    failed = [
        child
        for child in children
        if child.status in {"FAILED", "BLOCKED", "CANCELLED", "PARTIAL", "STALE"}
    ]
    if failed:
        raise ValueError(
            "CERI_FEATURE_CERTIFICATION_CHILD_FAILED:"
            + ",".join(f"{child.id}:{child.status}" for child in failed)
        )
    if any(child.status not in TERMINAL_JOB_STATUSES for child in children):
        raise JobDeferred("waiting for bounded CERI feature certification graph", delay_seconds=2)

    feature_jobs = [child for child in children if child.job_type == CERI_FEATURE_BATCH]
    if len(feature_jobs) != 1 or feature_jobs[0].status != "COMPLETED":
        raise ValueError("CERI_FEATURE_CERTIFICATION_FEATURE_BATCH_NOT_CERTIFIED")
    completed_at = datetime.now(UTC)
    pipeline.status = PipelineStatus.FEATURE_CERTIFIED
    pipeline.current_step = CERI_FEATURE_CERTIFICATION_STEP
    pipeline.completed_at = completed_at
    pipeline.message = "CERI feature-only certification completed at the declared boundary."
    pipeline.error_message = None
    pipeline.result_json = {
        **(pipeline.result_json or {}),
        "feature_certification_state": PipelineStatus.FEATURE_CERTIFIED,
        "feature_job_id": feature_jobs[0].id,
        "child_job_ids": [child.id for child in children],
        "completed_at": completed_at.isoformat(),
    }
    step = db.scalar(
        select(PipelineStep).where(
            PipelineStep.pipeline_run_id == pipeline.id,
            PipelineStep.step_name == CERI_FEATURE_CERTIFICATION_STEP,
        )
    )
    if step is None:
        raise ValueError("CERI_FEATURE_CERTIFICATION_STEP_REQUIRED")
    step.status = PipelineStepStatus.COMPLETED
    step.completed_at = completed_at
    step.message = pipeline.message
    upload_run = db.get(UploadRun, pipeline.upload_run_id)
    if upload_run is None:
        raise ValueError("CERI_FEATURE_CERTIFICATION_UPLOAD_RUN_REQUIRED")
    upload_run.status = "COMPLETED"
    upload_run.processed_at = completed_at
    db.flush()
    return {
        "job_type": CERI_FEATURE_CERTIFICATION_ROOT_JOB_TYPE,
        "status": PipelineStatus.FEATURE_CERTIFIED,
        "pipeline_run_id": pipeline.id,
        "feature_job_id": feature_jobs[0].id,
        "child_job_ids": [child.id for child in children],
        "stop_boundary": CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY,
    }


def _validate_root_payload(
    payload: dict[str, Any],
) -> tuple[tuple[str, ...], dict[str, tuple[str, ...]]]:
    if payload.get("certification_contract_version") != CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION:
        raise ValueError("CERI_FEATURE_CERTIFICATION_CONTRACT_VERSION_INVALID")
    if payload.get("stop_boundary") != CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY:
        raise ValueError("CERI_FEATURE_CERTIFICATION_STOP_BOUNDARY_REQUIRED")
    if payload.get("checkpoint_interval") != 1 or payload.get("feature_batch_count") != 1:
        raise ValueError("CERI_FEATURE_CERTIFICATION_BATCH_CONTRACT_INVALID")
    if any(payload.get(key) is not False for key in _PROHIBITED_PERMISSION_KEYS):
        raise ValueError("CERI_FEATURE_CERTIFICATION_DOWNSTREAM_PERMISSION_FORBIDDEN")
    tickers = _validate_tickers(tuple(payload.get("tickers") or ()))
    raw_datasets = payload.get("provider_datasets")
    if not isinstance(raw_datasets, dict):
        raise ValueError("CERI_FEATURE_CERTIFICATION_PROVIDER_SCOPE_REQUIRED")
    datasets = _validate_provider_datasets(
        {str(provider): tuple(names) for provider, names in raw_datasets.items()}
    )
    return tickers, datasets


def _validate_tickers(tickers: tuple[str, ...]) -> tuple[str, ...]:
    if not isinstance(tickers, tuple):
        raise TypeError("CERI_FEATURE_CERTIFICATION_TICKERS_MUST_BE_A_TUPLE")
    normalized = tuple(str(ticker).strip().upper() for ticker in tickers)
    if len(normalized) != CERI_FEATURE_CERTIFICATION_MAX_TICKERS:
        raise ValueError("CERI_FEATURE_CERTIFICATION_EXACTLY_TWO_TICKERS_REQUIRED")
    if len(set(normalized)) != len(normalized):
        raise ValueError("CERI_FEATURE_CERTIFICATION_DUPLICATE_TICKER")
    if any(not _TICKER_PATTERN.fullmatch(ticker) for ticker in normalized):
        raise ValueError("CERI_FEATURE_CERTIFICATION_INVALID_TICKER")
    return tuple(sorted(normalized))


def _validate_provider_dataset_tokens(
    values: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    if not isinstance(values, tuple):
        raise TypeError("CERI_FEATURE_CERTIFICATION_PROVIDER_DATASETS_MUST_BE_A_TUPLE")
    parsed: dict[str, list[str]] = {}
    for value in values:
        provider, separator, dataset = str(value).strip().lower().partition(":")
        if not separator or not provider or not dataset:
            raise ValueError("CERI_FEATURE_CERTIFICATION_PROVIDER_DATASET_INVALID")
        parsed.setdefault(provider, []).append(dataset)
    return _validate_provider_datasets(
        {provider: tuple(datasets) for provider, datasets in parsed.items()}
    )


def _validate_provider_datasets(
    provider_datasets: dict[str, tuple[str, ...]],
) -> dict[str, tuple[str, ...]]:
    normalized = {
        str(provider).strip().lower(): tuple(sorted(str(name).strip().lower() for name in names))
        for provider, names in provider_datasets.items()
    }
    if normalized != CERI_FEATURE_CERTIFICATION_ALLOWED_PROVIDER_DATASETS:
        raise ValueError("CERI_FEATURE_CERTIFICATION_PROVIDER_SCOPE_INVALID")
    return normalized
