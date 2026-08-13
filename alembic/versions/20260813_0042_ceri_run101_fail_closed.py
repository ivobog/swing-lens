"""make CERI guidance acceptance fail closed and add review lineage

Revision ID: 0042_ceri_run101_fail_closed
Revises: 0041_sec_incremental_documents
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042_ceri_run101_fail_closed"
down_revision: str | None = "0041_sec_incremental_documents"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ceri_ingestion_runs",
        sa.Column("deployment_identity_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "ceri_processing_runs",
        sa.Column("deployment_identity_json", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "ceri_provider_request_telemetry",
        sa.Column("response_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.add_column(
        "ceri_provider_request_telemetry",
        sa.Column("stored_bytes", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Unknown legacy acceptance is not evidence of approval. Never mass-promote.
    op.execute(
        "UPDATE ceri_guidance_events "
        "SET accepted_for_scoring = FALSE, "
        "rejection_reason = COALESCE(rejection_reason, 'LEGACY_ACCEPTANCE_UNKNOWN') "
        "WHERE accepted_for_scoring IS NULL"
    )
    op.alter_column(
        "ceri_guidance_events",
        "accepted_for_scoring",
        existing_type=sa.Boolean(),
        nullable=False,
        server_default=sa.false(),
    )
    for column in (
        sa.Column("acceptance_reason", sa.Text(), nullable=True),
        sa.Column("review_state", sa.String(32), nullable=True),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("clean_evidence_excerpt_hash", sa.String(128), nullable=True),
        sa.Column("processor_signature", sa.Text(), nullable=True),
    ):
        op.add_column("ceri_guidance_events", column)
    for column in (
        sa.Column("comparison_mode", sa.String(64), nullable=True),
        sa.Column("current_source_record_id", sa.BigInteger(), nullable=True),
        sa.Column("baseline_source_record_id", sa.BigInteger(), nullable=True),
        sa.Column("provider_retrospective_source_record_id", sa.BigInteger(), nullable=True),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reference_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("ceri_revision_features", column)
    op.add_column(
        "ceri_estimate_snapshots",
        sa.Column("source_provider", sa.String(64), nullable=True),
    )
    for column in (
        sa.Column("event_kind", sa.String(32), nullable=True),
        sa.Column("acquisition_policy", sa.String(32), nullable=True),
        sa.Column("provider_consensus_semantics", sa.Text(), nullable=True),
    ):
        op.add_column("ceri_earnings_actuals", column)
    for name, column in (
        ("fk_ceri_revision_current_source", "current_source_record_id"),
        ("fk_ceri_revision_baseline_source", "baseline_source_record_id"),
        ("fk_ceri_revision_retrospective_source", "provider_retrospective_source_record_id"),
    ):
        op.create_foreign_key(
            name,
            "ceri_revision_features",
            "ceri_source_records",
            [column],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.drop_column("ceri_provider_request_telemetry", "stored_bytes")
    op.drop_column("ceri_provider_request_telemetry", "response_bytes")
    op.drop_column("ceri_processing_runs", "deployment_identity_json")
    op.drop_column("ceri_ingestion_runs", "deployment_identity_json")
    for column in (
        "provider_consensus_semantics",
        "acquisition_policy",
        "event_kind",
    ):
        op.drop_column("ceri_earnings_actuals", column)
    op.drop_column("ceri_estimate_snapshots", "source_provider")
    for name in (
        "fk_ceri_revision_retrospective_source",
        "fk_ceri_revision_baseline_source",
        "fk_ceri_revision_current_source",
    ):
        op.drop_constraint(name, "ceri_revision_features", type_="foreignkey")
    for column in (
        "reference_at",
        "known_at",
        "provider_retrospective_source_record_id",
        "baseline_source_record_id",
        "current_source_record_id",
        "comparison_mode",
    ):
        op.drop_column("ceri_revision_features", column)
    for column in (
        "processor_signature",
        "clean_evidence_excerpt_hash",
        "expires_at",
        "accepted_by",
        "accepted_at",
        "review_state",
        "acceptance_reason",
    ):
        op.drop_column("ceri_guidance_events", column)
    op.alter_column(
        "ceri_guidance_events",
        "accepted_for_scoring",
        existing_type=sa.Boolean(),
        nullable=True,
        server_default=None,
    )
