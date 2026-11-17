"""Keep closed CERI stage barriers out of executable queue selection."""

from __future__ import annotations

from sqlalchemy import Integer, and_, case, cast, exists, func, not_, or_, select
from sqlalchemy.orm import Session, aliased

from app.models.tables import BackgroundJob
from app.services.background_job_service import TERMINAL_JOB_STATUSES

# These are the same workflow-wide barriers enforced by the batch handlers.
STAGE_PREREQUISITES = {
    "CERI_NORMALIZE_BATCH": ("CERI_PROVIDER_INGEST_BATCH", None),
    "CERI_FEATURE_BATCH": ("CERI_NORMALIZE_BATCH", "expected_normalization_batches"),
    "CERI_RUN_FINALIZE": ("CERI_FEATURE_BATCH", "expected_feature_batches"),
}
TERMINAL = tuple(sorted(TERMINAL_JOB_STATUSES))


def _workflow_expression(job):
    return func.coalesce(
        func.nullif(job.workflow_key, ""),
        func.nullif(job.payload_json["workflow_key"].as_string(), ""),
    )


def _expected_expression(field):
    if field is None:
        return 0
    value = BackgroundJob.payload_json[field].as_string()
    # Invalid payloads must reach the handler's validation without aborting claims
    # for every other workflow. CASE protects the cast from malformed JSON.
    return case(
        (value.op("~")(r"^[0-9]{1,9}$"), cast(value, Integer)),
        else_=0,
    )


def ceri_stage_claim_predicate():
    """Ready stages and unsuccessful terminal stages may reach their handlers.

    A missing workflow identity is left to handler validation. Existing rows with
    old priorities are covered; changing only newly generated plans is insufficient.
    """
    upstream = aliased(BackgroundJob)
    workflow = _workflow_expression(BackgroundJob)
    clauses = []
    for stage, (prerequisite, expected_field) in STAGE_PREREQUISITES.items():
        scope = (
            _workflow_expression(upstream) == workflow,
            upstream.job_type == prerequisite,
        )
        rows_exist = exists(select(upstream.id).where(*scope).correlate(BackgroundJob))
        unfinished = exists(
            select(upstream.id)
            .where(
                *scope,
                upstream.status.not_in(TERMINAL),
            )
            .correlate(BackgroundJob)
        )
        unsuccessful = exists(
            select(upstream.id)
            .where(
                *scope,
                upstream.status.in_(TERMINAL),
                upstream.status.not_in(("COMPLETED", "PARTIAL")),
            )
            .correlate(BackgroundJob)
        )
        expected = _expected_expression(expected_field)
        count = (
            select(func.count(upstream.id))
            .where(*scope)
            .correlate(
                BackgroundJob,
            )
            .scalar_subquery()
        )
        count_matches = or_(expected == 0, count == expected)
        clauses.append(
            and_(
                BackgroundJob.job_type == stage,
                rows_exist,
                not_(unfinished),
                or_(unsuccessful, count_matches),
            )
        )
    return or_(
        BackgroundJob.job_type.not_in(tuple(STAGE_PREREQUISITES)),
        workflow.is_(None),
        *clauses,
    )


def ceri_dependency_wait(db: Session, job: BackgroundJob | None) -> dict:
    """Read-only status for a batch held outside the executable queue."""
    if job is None or job.status not in {"QUEUED", "RECOVERING", "RUNNING"}:
        return {}
    prerequisite = STAGE_PREREQUISITES.get(job.job_type)
    payload = job.payload_json or {}
    workflow = job.workflow_key or payload.get("workflow_key")
    if prerequisite is None or not workflow:
        return {}
    stage, expected_field = prerequisite
    total, unfinished, unsuccessful = db.execute(
        select(
            func.count(BackgroundJob.id),
            func.count(BackgroundJob.id).filter(BackgroundJob.status.not_in(TERMINAL)),
            func.count(BackgroundJob.id).filter(
                BackgroundJob.status.in_(TERMINAL),
                BackgroundJob.status.not_in(("COMPLETED", "PARTIAL")),
            ),
        ).where(
            _workflow_expression(BackgroundJob) == str(workflow),
            BackgroundJob.job_type == stage,
        )
    ).one()
    try:
        expected = int(payload.get(expected_field) or 0) if expected_field else 0
    except (ValueError, TypeError):
        expected = 0
    if total and not unfinished and (unsuccessful or not expected or total == expected):
        return {}
    label = {
        "CERI_PROVIDER_INGEST_BATCH": "provider ingestion",
        "CERI_NORMALIZE_BATCH": "normalization",
        "CERI_FEATURE_BATCH": "feature calculation",
    }[stage]
    return {
        "prerequisite": stage,
        "label": label,
        "unfinished_batches": int(unfinished),
        "total_batches": int(total),
        "expected_batches": expected,
        "waiting_since": job.created_at,
    }
