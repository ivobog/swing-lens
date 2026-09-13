"""Add append-only CERI evidence dispositions.

Revision ID: 0074_ceri_evidence_quarantine
Revises: 0073_decision_manifest_lifecycle
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0074_ceri_evidence_quarantine"
down_revision: str | None = "0073_decision_manifest_lifecycle"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ceri_evidence_dispositions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ceri_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("reason_code", sa.String(length=128), nullable=False),
        sa.Column("incident_reference", sa.Text(), nullable=False),
        sa.Column("actor_source", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "event_fingerprint",
            sa.Text(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "disposition IN ('ELIGIBLE', 'EXCLUDED')",
            name="ck_ceri_evidence_dispositions_value",
        ),
        sa.ForeignKeyConstraint(
            ["ceri_snapshot_id"],
            ["ceri_score_snapshots.id"],
            name="fk_ceri_evidence_dispositions_snapshot",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "event_fingerprint",
            name="uq_ceri_evidence_dispositions_event_fingerprint",
        ),
    )
    op.create_index(
        "ix_ceri_evidence_dispositions_snapshot_effective",
        "ceri_evidence_dispositions",
        ["ceri_snapshot_id", sa.text("created_at DESC"), sa.text("id DESC")],
        postgresql_include=["disposition"],
    )
    op.create_index(
        "ix_ceri_evidence_dispositions_incident",
        "ceri_evidence_dispositions",
        ["incident_reference", "disposition"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_ceri_evidence_disposition_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'CERI evidence dispositions are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_ceri_evidence_dispositions_append_only
        BEFORE UPDATE OR DELETE ON ceri_evidence_dispositions
        FOR EACH ROW EXECUTE FUNCTION reject_ceri_evidence_disposition_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_ceri_evidence_dispositions_append_only "
        "ON ceri_evidence_dispositions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_ceri_evidence_disposition_mutation()")
    op.drop_index(
        "ix_ceri_evidence_dispositions_incident",
        table_name="ceri_evidence_dispositions",
    )
    op.drop_index(
        "ix_ceri_evidence_dispositions_snapshot_effective",
        table_name="ceri_evidence_dispositions",
    )
    op.drop_table("ceri_evidence_dispositions")
