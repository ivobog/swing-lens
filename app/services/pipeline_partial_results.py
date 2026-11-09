"""Explicit subset admission; the source pipeline and its scope remain immutable."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PipelineRun, RawCompanyRow, UploadRun
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.market_data_session_readiness import load_current_session_readiness
from app.services.scope_refresh_adoption import require_semantic_authority, retained_scope_members


def pipeline_scope_tickers(db: Session, pipeline_run_id: int) -> tuple[str, ...]:
    pipeline = db.get(PipelineRun, pipeline_run_id)
    if pipeline is None:
        raise ValueError("PIPELINE_SCOPE_REQUIRED")
    authority = require_semantic_authority(pipeline)
    return tuple(
        member.subject_id
        for member in retained_scope_members(db, authority.scope_id)
        if member.subject_type == "TICKER"
    )


def filter_pipeline_rows(db, rows, pipeline_run_id):
    """Use the admitted cohort even when an upload contains additional symbols."""
    if isinstance(db, Session) and pipeline_run_id is not None:
        pipeline = db.get(PipelineRun, pipeline_run_id)
        if pipeline is not None and pipeline.scope_id:
            allowed = set(pipeline_scope_tickers(db, pipeline_run_id))
            return [row for row in rows if row.ticker.upper() in allowed]
    return rows


def prepare_partial_scope(
    db: Session, *, source_pipeline_id: int, upload_run_id: int, excluded_tickers: tuple[str, ...]
) -> dict:
    source = db.scalar(
        select(PipelineRun).where(PipelineRun.id == source_pipeline_id).with_for_update()
    )
    if (
        source is None
        or source.upload_run_id != upload_run_id
        or source.status != "BLOCKED"
        or source.current_step != "FETCHING_MARKET_DATA"
    ):
        raise ValueError("PARTIAL_SOURCE_MUST_BE_MARKET_DATA_BLOCKED")
    readiness = load_current_session_readiness(db, pipeline_run_id=source.id)
    if readiness is None or not readiness.technical_tickers or not readiness.blockers:
        raise ValueError("PARTIAL_SCOPE_REQUIRES_READY_TICKERS_AND_BLOCKERS")
    excluded = tuple(sorted({ticker.strip().upper() for ticker in excluded_tickers}))
    if set(excluded) != {row.ticker for row in readiness.blockers}:
        raise ValueError("PARTIAL_EXCLUSIONS_CHANGED: review the current missing tickers")
    if any(
        row.disposition not in {"TRANSIENT_FAILURE", "REQUIRED_DATA_UNAVAILABLE"}
        for row in readiness.blockers
    ):
        raise ValueError("PARTIAL_EXCLUSION_REQUIRES_RESOLVED_DATA_FAILURE")
    members = pipeline_scope_tickers(db, source.id)
    if {row.ticker for row in readiness.rows} != set(members):
        raise ValueError("PARTIAL_SOURCE_READINESS_INCOMPLETE")
    if not set(excluded) < set(members):
        raise ValueError("PARTIAL_SCOPE_MUST_RETAIN_TICKERS")
    return {
        "version": "explicit-partial-scope-v1",
        "source_pipeline_id": source.id,
        "source_upload_run_id": upload_run_id,
        "source_scope_id": require_semantic_authority(source).scope_id,
        "requested_tickers": list(members),
        "excluded_tickers": list(excluded),
        "included_tickers": [
            ticker for ticker in members if ticker in readiness.downstream_tickers
        ],
        "accepted_inactive_tickers": list(readiness.accepted_inactive_tickers),
        "exclusions": {
            row.ticker: {
                "reason_code": row.reason_code,
                "expected_session": row.expected_session.isoformat(),
                "latest_bar_session": (
                    row.latest_bar_session.isoformat() if row.latest_bar_session else None
                ),
                "source_disposition_id": row.id,
            }
            for row in readiness.blockers
        },
    }


def clone_partial_upload(db: Session, source: UploadRun, scope: dict) -> UploadRun:
    """Isolate mutable score projections so the original checkpoint stays resumable."""
    from app.services.domain_mutation import (
        DomainMutationContext,
        MutationDomain,
        MutationEntryPointDescriptor,
        MutationEvidenceReference,
        MutationSemanticMode,
        MutationWriterDescriptor,
        fence_mutation_transaction,
    )

    rows = list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == source.id)
            .order_by(RawCompanyRow.row_number)
        )
    )
    source_fingerprint = CanonicalEvidenceSerializer.fingerprint(
        [
            {column.name: getattr(row, column.name) for column in RawCompanyRow.__table__.columns}
            for row in rows
        ]
    )
    included = set(scope["included_tickers"])
    rows = [row for row in rows if row.ticker.upper() in included]
    if {row.ticker.upper() for row in rows} != included:
        raise ValueError("PARTIAL_SOURCE_ROWS_CHANGED")
    fence_mutation_transaction(
        db,
        DomainMutationContext(
            domain=MutationDomain.RAW,
            semantic_mode=MutationSemanticMode.BOOTSTRAP,
            entrypoint=MutationEntryPointDescriptor("clone_partial_upload", "EXPLICIT_PARTIAL_RUN"),
            writer=MutationWriterDescriptor(
                "clone_partial_upload", "partial-upload-v1", MutationDomain.RAW
            ),
            reason="Derive isolated inputs for the explicitly admitted subset",
            evidence=(
                MutationEvidenceReference(
                    "source_file", "retained_upload_rows", str(source.id), source_fingerprint
                ),
            ),
        ),
    )
    derived = UploadRun(
        filename=f"Partial from run {source.id}: {source.filename}",
        file_path=None,
        row_count=len(rows),
        status="COMPLETED",
        notes=f"Derived from upload {source.id}, pipeline {scope['source_pipeline_id']}; "
        f"excluded from scoring: {', '.join(scope['excluded_tickers'])}",
    )
    db.add(derived)
    db.flush()
    source_rows = {}
    for row in rows:
        values = {
            column.name: getattr(row, column.name)
            for column in RawCompanyRow.__table__.columns
            if column.name not in {"id", "run_id", "created_at"}
        }
        copied = RawCompanyRow(run_id=derived.id, **values)
        db.add(copied)
        source_rows[str(row.id)] = copied
    db.flush()
    scope["source_upload_fingerprint"] = source_fingerprint
    scope["source_raw_row_ids"] = {key: row.id for key, row in source_rows.items()}
    return derived
