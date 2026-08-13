"""add durable SEC filing document extraction state

Revision ID: 0041_sec_incremental_documents
Revises: 0040_ceri_remediation_ledgers
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0041_sec_incremental_documents"
down_revision: str | None = "0040_ceri_remediation_ledgers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ceri_sec_filing_documents",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cik", sa.String(32), nullable=False),
        sa.Column("accession_number", sa.String(64), nullable=False),
        sa.Column("document_name", sa.Text(), nullable=False),
        sa.Column("ticker_hint", sa.String(32), nullable=True),
        sa.Column("form", sa.String(32), nullable=True),
        sa.Column("filing_date", sa.Date(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("last_downloaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_content_hash", sa.String(128), nullable=True),
        sa.Column("last_content_bytes", sa.BigInteger(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "cik", "accession_number", "document_name", name="uq_ceri_sec_filing_document_identity"
        ),
    )
    op.create_index(
        "ix_ceri_sec_filing_documents_cik_date", "ceri_sec_filing_documents", ["cik", "filing_date"]
    )
    op.create_index(
        "ix_ceri_sec_filing_documents_accession", "ceri_sec_filing_documents", ["accession_number"]
    )
    op.create_table(
        "ceri_sec_document_extractions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("document_id", sa.BigInteger(), nullable=False),
        sa.Column("dataset", sa.String(64), nullable=False),
        sa.Column("processor_signature", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("record_count", sa.Integer(), nullable=True),
        sa.Column("worker_id", sa.Text(), nullable=True),
        sa.Column("execution_token", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('PENDING','RUNNING','COMPLETED_WITH_RECORDS',"
            "'COMPLETED_NO_RECORDS','FAILED_RETRYABLE','FAILED_PERMANENT',"
            "'CANCELLED')",
            name="ck_ceri_sec_document_extractions_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["ceri_sec_filing_documents.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id",
            "dataset",
            "processor_signature",
            name="uq_ceri_sec_document_extraction_identity",
        ),
    )
    op.create_index(
        "ix_ceri_sec_document_extractions_claim",
        "ceri_sec_document_extractions",
        ["dataset", "processor_signature", "status", "lease_expires_at"],
    )
    op.create_index(
        "ix_ceri_sec_document_extractions_document",
        "ceri_sec_document_extractions",
        ["document_id"],
    )
    op.create_table(
        "ceri_sec_sync_states",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("cik", sa.String(32), nullable=False),
        sa.Column("dataset", sa.String(64), nullable=False),
        sa.Column("processor_signature", sa.Text(), nullable=False),
        sa.Column("bootstrap_completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("latest_filing_date", sa.Date(), nullable=True),
        sa.Column("document_count", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cik", "dataset", "processor_signature", name="uq_ceri_sec_sync_state"),
    )
    op.create_index(
        "ix_ceri_sec_sync_states_cik_dataset", "ceri_sec_sync_states", ["cik", "dataset"]
    )


def downgrade() -> None:
    op.drop_index("ix_ceri_sec_sync_states_cik_dataset", table_name="ceri_sec_sync_states")
    op.drop_table("ceri_sec_sync_states")
    op.drop_index(
        "ix_ceri_sec_document_extractions_document", table_name="ceri_sec_document_extractions"
    )
    op.drop_index(
        "ix_ceri_sec_document_extractions_claim", table_name="ceri_sec_document_extractions"
    )
    op.drop_table("ceri_sec_document_extractions")
    op.drop_index("ix_ceri_sec_filing_documents_accession", table_name="ceri_sec_filing_documents")
    op.drop_index("ix_ceri_sec_filing_documents_cik_date", table_name="ceri_sec_filing_documents")
    op.drop_table("ceri_sec_filing_documents")
