"""Explicit new admission of a blocked pipeline's complete requested population."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import PipelineRun, RawCompanyRow, UploadRun
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.pipeline_partial_results import pipeline_scope_tickers
from app.services.scope_refresh_adoption import require_semantic_authority

FULL_UNIVERSE_RECOVERY_V1 = "full-universe-market-data-recovery-v1"


def start_market_data_recovery(
    db: Session, *, source_pipeline_id: int, upload_run_id: int,
) -> PipelineRun:
    """Clone exact retained inputs and enqueue once; never modify the source run.

    A new cutoff, configuration and versioned availability policy are admitted by
    the normal enqueue path. This is intentionally distinct from resuming an old
    frozen plan under different semantics.
    """
    source = db.scalar(
        select(PipelineRun).where(PipelineRun.id == source_pipeline_id).with_for_update()
    )
    if (
        source is None
        or source.upload_run_id != upload_run_id
        or source.status != "BLOCKED"
        or source.current_step != "FETCHING_MARKET_DATA"
    ):
        raise ValueError("MARKET_DATA_RECOVERY_REQUIRES_BLOCKED_SOURCE")
    existing = db.scalar(
        select(PipelineRun)
        .where(
            PipelineRun.result_json["market_data_recovery_source"]["source_pipeline_id"]
            .as_integer() == source_pipeline_id,
        )
        .order_by(PipelineRun.id)
        .limit(1)
    )
    if existing is not None:
        existing._coalesced = True
        return existing

    original_upload = db.get(UploadRun, upload_run_id)
    if original_upload is None:
        raise ValueError("MARKET_DATA_RECOVERY_SOURCE_UPLOAD_REQUIRED")
    authority = require_semantic_authority(source)
    tickers = pipeline_scope_tickers(db, source.id)
    if not tickers:
        raise ValueError("MARKET_DATA_RECOVERY_EMPTY_SCOPE")
    rows = list(db.scalars(
        select(RawCompanyRow)
        .where(RawCompanyRow.run_id == upload_run_id, RawCompanyRow.ticker.in_(tickers))
        .order_by(RawCompanyRow.row_number, RawCompanyRow.id)
    ))
    if {row.ticker.upper() for row in rows} != set(tickers):
        raise ValueError("MARKET_DATA_RECOVERY_SOURCE_ROWS_CHANGED")
    fingerprint = Canonical.fingerprint([
        {column.name: getattr(row, column.name) for column in RawCompanyRow.__table__.columns}
        for row in rows
    ])
    lineage = {
        "contract_version": FULL_UNIVERSE_RECOVERY_V1,
        "source_pipeline_id": source.id,
        "source_upload_run_id": original_upload.id,
        "source_scope_id": authority.scope_id,
        "requested_tickers": list(tickers),
        "source_upload_fingerprint": fingerprint,
    }
    derived = _copy_recovery_upload(db, original_upload, rows, lineage)
    from app.services.pipeline_service import MarketDataPolicy, start_pipeline

    return start_pipeline(
        db, derived.id, market_data_policy=MarketDataPolicy.REQUIRE_IB,
        market_data_recovery_source=lineage,
    )


def _copy_recovery_upload(db, source, rows, lineage):
    from app.services.domain_mutation import (
        DomainMutationContext,
        MutationDomain,
        MutationEntryPointDescriptor,
        MutationEvidenceReference,
        MutationSemanticMode,
        MutationWriterDescriptor,
        fence_mutation_transaction,
    )

    fence_mutation_transaction(
        db,
        DomainMutationContext(
            domain=MutationDomain.RAW,
            semantic_mode=MutationSemanticMode.BOOTSTRAP,
            entrypoint=MutationEntryPointDescriptor(
                "start_market_data_recovery", "EXPLICIT_FULL_UNIVERSE_RECOVERY",
            ),
            writer=MutationWriterDescriptor(
                "start_market_data_recovery", FULL_UNIVERSE_RECOVERY_V1, MutationDomain.RAW,
            ),
            reason="Retain every requested company in an isolated new policy admission",
            evidence=(MutationEvidenceReference(
                "source_file", "retained_upload_rows", str(source.id),
                lineage["source_upload_fingerprint"],
            ),),
        ),
    )
    derived = UploadRun(
        filename=f"Recovery from run {source.id}: {source.filename}",
        file_path=None, row_count=len(rows), status="COMPLETED",
        notes=f"Full requested population from upload {source.id}, "
        f"pipeline {lineage['source_pipeline_id']}; new market-data policy admission.",
    )
    db.add(derived)
    db.flush()
    copied = {}
    for row in rows:
        values = {
            column.name: getattr(row, column.name)
            for column in RawCompanyRow.__table__.columns
            if column.name not in {"id", "run_id", "created_at"}
        }
        target = RawCompanyRow(run_id=derived.id, **values)
        db.add(target)
        copied[str(row.id)] = target
    db.flush()
    lineage["source_raw_row_ids"] = {key: row.id for key, row in copied.items()}
    return derived
