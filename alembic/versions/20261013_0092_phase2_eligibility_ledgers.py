"""Add append-only consumer eligibility, Winner acquisition, and shadow ledgers."""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0092_phase2_eligibility_ledgers"
down_revision: str | None = "0091_readiness_constraints"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb() -> postgresql.JSONB:
    return postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "consumer_eligibility_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=True),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=True),
        sa.Column("producer", sa.String(length=64), nullable=False),
        sa.Column("consumer", sa.String(length=64), nullable=False),
        sa.Column("consumer_edge", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("source_artifact_kind", sa.String(length=64), nullable=False),
        sa.Column("source_evidence_id", sa.BigInteger(), nullable=True),
        sa.Column("source_identity_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("execution_outcome", sa.String(length=32), nullable=False),
        sa.Column("integrity_status", sa.String(length=32), nullable=False),
        sa.Column("completeness_status", sa.String(length=32), nullable=False),
        sa.Column("analytical_confidence", sa.String(length=32), nullable=False),
        sa.Column("eligibility", sa.String(length=32), nullable=False),
        sa.Column("reason_codes_json", _jsonb(), nullable=False),
        sa.Column("warning_classifications_json", _jsonb(), nullable=False),
        sa.Column("decision_payload_json", _jsonb(), nullable=False),
        sa.Column("decision_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("authoritative", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "pipeline_run_id IS NOT NULL OR upload_run_id IS NOT NULL",
            name="ck_consumer_eligibility_run_identity",
        ),
        sa.CheckConstraint(
            "execution_outcome IN ('SUCCEEDED', 'SUCCEEDED_WITH_GAPS', 'FAILED', "
            "'CANCELLED', 'BLOCKED', 'UNKNOWN')",
            name="ck_consumer_eligibility_execution",
        ),
        sa.CheckConstraint(
            "integrity_status IN ('CERTIFIED', 'UNCERTIFIED', 'CONTAMINATED', "
            "'QUARANTINED', 'UNKNOWN')",
            name="ck_consumer_eligibility_integrity",
        ),
        sa.CheckConstraint(
            "completeness_status IN ('COMPLETE', 'PARTIAL', 'INSUFFICIENT', 'UNKNOWN')",
            name="ck_consumer_eligibility_completeness",
        ),
        sa.CheckConstraint(
            "analytical_confidence IN ('HIGH', 'NORMAL', 'LOW', 'INSUFFICIENT', 'UNKNOWN')",
            name="ck_consumer_eligibility_confidence",
        ),
        sa.CheckConstraint(
            "eligibility IN ('INCLUDED', 'OMITTED_OPTIONAL', 'REJECTED_REQUIRED', 'UNDECIDED')",
            name="ck_consumer_eligibility_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"], ["core_calculation_evidence.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_fingerprint", name="uq_consumer_eligibility_decision_fingerprint"
        ),
    )
    op.create_index(
        "idx_consumer_eligibility_run_edge",
        "consumer_eligibility_decisions",
        ["pipeline_run_id", "consumer_edge", "created_at"],
    )
    op.create_index(
        "idx_consumer_eligibility_source",
        "consumer_eligibility_decisions",
        ["source_artifact_kind", "source_identity_fingerprint"],
    )

    op.create_table(
        "winner_acquisition_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=True),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_row_id", sa.BigInteger(), nullable=True),
        sa.Column("prediction_id", sa.BigInteger(), nullable=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("acquisition_outcome", sa.String(length=32), nullable=False),
        sa.Column("eligibility_reason", sa.Text(), nullable=True),
        sa.Column("source_identities_json", _jsonb(), nullable=False),
        sa.Column("source_decisions_json", _jsonb(), nullable=False),
        sa.Column("warning_classifications_json", _jsonb(), nullable=False),
        sa.Column("decision_payload_json", _jsonb(), nullable=False),
        sa.Column("decision_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "acquisition_outcome IN ('INSERTED', 'DUPLICATE', 'EXCLUDED', 'FAILED')",
            name="ck_winner_acquisition_outcome",
        ),
        sa.CheckConstraint(
            "acquisition_outcome <> 'INSERTED' OR prediction_id IS NOT NULL",
            name="ck_winner_acquisition_inserted_prediction",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["raw_row_id"], ["raw_company_rows.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["prediction_id"], ["winner_prediction_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_fingerprint", name="uq_winner_acquisition_decision_fingerprint"
        ),
    )
    op.create_index(
        "idx_winner_acquisition_run_ticker",
        "winner_acquisition_decisions",
        ["pipeline_run_id", "ticker", "created_at"],
    )
    op.create_index(
        "idx_winner_acquisition_prediction",
        "winner_acquisition_decisions",
        ["prediction_id"],
    )

    op.create_table(
        "eligibility_shadow_evaluations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=True),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=True),
        sa.Column("ticker", sa.Text(), nullable=True),
        sa.Column("consumer_edge", sa.String(length=128), nullable=False),
        sa.Column("policy_version", sa.String(length=128), nullable=False),
        sa.Column("source_identity_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("decision_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legacy_outcome", sa.String(length=32), nullable=False),
        sa.Column("proposed_outcome", sa.String(length=32), nullable=False),
        sa.Column("differs", sa.Boolean(), nullable=False),
        sa.Column("frozen_input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("frozen_input_json", _jsonb(), nullable=False),
        sa.Column("proposed_decision_json", _jsonb(), nullable=False),
        sa.Column("reason_codes_json", _jsonb(), nullable=False),
        sa.Column("warning_classifications_json", _jsonb(), nullable=False),
        sa.Column("evaluation_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "pipeline_run_id IS NOT NULL OR upload_run_id IS NOT NULL",
            name="ck_eligibility_shadow_run_identity",
        ),
        sa.CheckConstraint(
            "legacy_outcome IN ('INCLUDED', 'OMITTED_OPTIONAL', 'REJECTED_REQUIRED', 'UNDECIDED')",
            name="ck_eligibility_shadow_legacy_outcome",
        ),
        sa.CheckConstraint(
            "proposed_outcome IN "
            "('INCLUDED', 'OMITTED_OPTIONAL', 'REJECTED_REQUIRED', 'UNDECIDED')",
            name="ck_eligibility_shadow_proposed_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["pipeline_run_id"], ["pipeline_runs.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evaluation_fingerprint", name="uq_eligibility_shadow_evaluation_fingerprint"
        ),
    )
    op.create_index(
        "idx_eligibility_shadow_run_edge",
        "eligibility_shadow_evaluations",
        ["pipeline_run_id", "consumer_edge", "created_at"],
    )
    op.create_index(
        "idx_eligibility_shadow_differs",
        "eligibility_shadow_evaluations",
        ["differs", "created_at"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_phase2_decision_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'PHASE2_DECISION_EVIDENCE_IS_APPEND_ONLY: %', TG_TABLE_NAME;
        END;
        $$
        """
    )
    for table in (
        "consumer_eligibility_decisions",
        "winner_acquisition_decisions",
        "eligibility_shadow_evaluations",
    ):
        op.execute(
            f"CREATE TRIGGER trg_{table}_append_only "
            f"BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION reject_phase2_decision_mutation()"
        )


def downgrade() -> None:
    for table in (
        "eligibility_shadow_evaluations",
        "winner_acquisition_decisions",
        "consumer_eligibility_decisions",
    ):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
    op.execute("DROP FUNCTION IF EXISTS reject_phase2_decision_mutation()")
    op.drop_table("eligibility_shadow_evaluations")
    op.drop_table("winner_acquisition_decisions")
    op.drop_table("consumer_eligibility_decisions")
