"""Add durable Winner decision time and temporal validity ledger.

Revision ID: 0058_winner_temporal_integrity
Revises: 0057_winner_maturation
Create Date: 2026-09-05
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0058_winner_temporal_integrity"
down_revision = "0057_winner_maturation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deliberately nullable for historical rows: captured_at is not silently
    # promoted into an authoritative decision timestamp.
    op.add_column(
        "winner_prediction_snapshots",
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "idx_winner_prediction_snapshots_decision_at",
        "winner_prediction_snapshots",
        ["decision_at"],
    )
    op.create_table(
        "winner_temporal_validity_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("prediction_id", sa.BigInteger(), nullable=False),
        sa.Column("validation_sequence", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("entry_timing_valid", sa.Boolean(), nullable=False),
        sa.Column("source_cutoff_valid", sa.Boolean(), nullable=False),
        sa.Column("semantic_input_time_valid", sa.Boolean(), nullable=True),
        sa.Column("evidence_eligible", sa.Boolean(), nullable=False),
        sa.Column(
            "reason_codes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("validation_version", sa.Text(), nullable=False),
        sa.Column("decision_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_session", sa.Date(), nullable=False),
        sa.Column("entry_open_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "evaluated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("evaluated_by", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('VALID', 'EXECUTION_INVALID', 'LOOKAHEAD_INVALID', "
            "'TEMPORAL_LINEAGE_UNRESOLVED')",
            name="ck_winner_temporal_validity_status",
        ),
        sa.CheckConstraint(
            "NOT evidence_eligible OR (status = 'VALID' AND entry_timing_valid "
            "AND source_cutoff_valid AND semantic_input_time_valid IS TRUE)",
            name="ck_winner_temporal_validity_evidence_consistency",
        ),
        sa.CheckConstraint(
            "entry_timing_valid = (decision_at < entry_open_at)",
            name="ck_winner_temporal_validity_entry_boundary",
        ),
        sa.ForeignKeyConstraint(
            ["prediction_id"],
            ["winner_prediction_snapshots.id"],
            name="fk_winner_temporal_validity_prediction",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "prediction_id",
            "validation_sequence",
            name="uq_winner_temporal_validity_prediction_sequence",
        ),
    )
    op.create_index(
        "idx_winner_temporal_validity_prediction_sequence",
        "winner_temporal_validity_decisions",
        ["prediction_id", "validation_sequence"],
    )
    op.create_index(
        "idx_winner_temporal_validity_evidence_eligible",
        "winner_temporal_validity_decisions",
        ["evidence_eligible", "prediction_id"],
    )
    op.add_column(
        "winner_cohort_refresh_state",
        sa.Column(
            "desired_temporal_validity_decision_id",
            sa.BigInteger(),
            server_default="0",
            nullable=False,
        ),
    )
    for table_name in (
        "winner_evidence_manifest_members",
        "winner_estimate_evidence_members",
    ):
        op.add_column(
            table_name,
            sa.Column("temporal_validity_decision_id", sa.BigInteger(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table_name}_temporal_validity",
            table_name,
            "winner_temporal_validity_decisions",
            ["temporal_validity_decision_id"],
            ["id"],
        )
        op.create_index(
            f"idx_{table_name}_temporal_validity",
            table_name,
            ["temporal_validity_decision_id"],
        )
    op.execute(
        """
        CREATE FUNCTION reject_winner_temporal_validity_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'winner temporal validity decisions are append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_winner_temporal_validity_append_only
        BEFORE UPDATE OR DELETE ON winner_temporal_validity_decisions
        FOR EACH ROW EXECUTE FUNCTION reject_winner_temporal_validity_mutation()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_winner_temporal_validity_append_only "
        "ON winner_temporal_validity_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_winner_temporal_validity_mutation()")
    for table_name in (
        "winner_estimate_evidence_members",
        "winner_evidence_manifest_members",
    ):
        op.drop_index(
            f"idx_{table_name}_temporal_validity",
            table_name=table_name,
        )
        op.drop_constraint(
            f"fk_{table_name}_temporal_validity",
            table_name,
            type_="foreignkey",
        )
        op.drop_column(table_name, "temporal_validity_decision_id")
    op.drop_column(
        "winner_cohort_refresh_state",
        "desired_temporal_validity_decision_id",
    )
    op.drop_index(
        "idx_winner_temporal_validity_evidence_eligible",
        table_name="winner_temporal_validity_decisions",
    )
    op.drop_index(
        "idx_winner_temporal_validity_prediction_sequence",
        table_name="winner_temporal_validity_decisions",
    )
    op.drop_table("winner_temporal_validity_decisions")
    op.drop_index(
        "idx_winner_prediction_snapshots_decision_at",
        table_name="winner_prediction_snapshots",
    )
    op.drop_column("winner_prediction_snapshots", "decision_at")
