"""add append-only OWPE pre-1.1 training compatibility artifacts

Revision ID: 0046_owpe_pre11_training_compatibility
Revises: 0045_ceri_changes_alerts_semantics
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0046_owpe_pre11_training_compatibility"
down_revision: str | None = "0045_ceri_changes_alerts_semantics"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "winner_training_eligibility_decisions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("prediction_id", sa.BigInteger(), nullable=False),
        sa.Column("policy_version", sa.Text(), nullable=False),
        sa.Column("training_family", sa.Text(), nullable=False),
        sa.Column("compatibility_bridge_version", sa.Text(), nullable=False),
        sa.Column("source_feature_schema_version", sa.Text(), nullable=False),
        sa.Column("source_calculation_version", sa.Text(), nullable=False),
        sa.Column("source_config_hash", sa.Text(), nullable=False),
        sa.Column("target_feature_schema_version", sa.Text(), nullable=False),
        sa.Column("target_calculation_version", sa.Text(), nullable=False),
        sa.Column("target_config_hash", sa.Text(), nullable=False),
        sa.Column("target_outcome_definition_id", sa.BigInteger(), nullable=False),
        sa.Column("classification_status", sa.Text(), nullable=False),
        sa.Column("training_allowed", sa.Boolean(), nullable=False),
        sa.Column(
            "reason_codes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "feature_compatibility_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "config_compatibility_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("outcome_compatibility_status", sa.Text(), nullable=False),
        sa.Column("pit_status", sa.Text(), nullable=False),
        sa.Column("episode_status", sa.Text(), nullable=False),
        sa.Column("quality_status", sa.Text(), nullable=False),
        sa.Column("reconstruction_method", sa.Text()),
        sa.Column("source_manifest_hash", sa.Text(), nullable=False),
        sa.Column("evidence_manifest_hash", sa.Text()),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("supersedes_decision_id", sa.BigInteger()),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("decision_hash", sa.Text(), nullable=False),
        sa.Column("classified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("classified_by", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["prediction_id"], ["winner_prediction_snapshots.id"]),
        sa.ForeignKeyConstraint(
            ["target_outcome_definition_id"], ["winner_outcome_definitions.id"]
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"], ["winner_training_eligibility_decisions.id"]
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_hash", name="uq_winner_training_eligibility_decision_hash"),
        sa.UniqueConstraint(
            "prediction_id",
            "training_family",
            "policy_version",
            "revision",
            name="uq_winner_training_eligibility_decision_revision",
        ),
    )
    op.create_index(
        "idx_winner_training_eligibility_decision_lookup",
        "winner_training_eligibility_decisions",
        ["training_family", "target_outcome_definition_id", "training_allowed"],
    )

    op.create_table(
        "winner_training_outcome_replays",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("eligibility_decision_id", sa.BigInteger(), nullable=False),
        sa.Column("prediction_id", sa.BigInteger(), nullable=False),
        sa.Column("target_outcome_definition_id", sa.BigInteger(), nullable=False),
        sa.Column("training_family", sa.Text(), nullable=False),
        sa.Column("reconstruction_method", sa.Text(), nullable=False),
        sa.Column("replay_policy_version", sa.Text(), nullable=False),
        sa.Column("compatibility_bridge_version", sa.Text(), nullable=False),
        sa.Column("source_forward_outcome_id", sa.BigInteger()),
        sa.Column("entry_model", sa.Text(), nullable=False),
        sa.Column("horizon_sessions", sa.Integer(), nullable=False),
        sa.Column("entry_session", sa.Date(), nullable=False),
        sa.Column("due_session", sa.Date(), nullable=False),
        sa.Column("entry_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("exit_price", sa.Numeric(18, 6), nullable=False),
        sa.Column("close_return_pct", sa.Numeric(12, 6), nullable=False),
        sa.Column("mfe_pct", sa.Numeric(12, 6), nullable=False),
        sa.Column("mae_pct", sa.Numeric(12, 6), nullable=False),
        sa.Column("target_pct", sa.Numeric(10, 4), nullable=False),
        sa.Column("stop_pct", sa.Numeric(10, 4), nullable=False),
        sa.Column("target_hit", sa.Boolean(), nullable=False),
        sa.Column("stop_hit", sa.Boolean(), nullable=False),
        sa.Column("first_event", sa.Text(), nullable=False),
        sa.Column("event_session", sa.Date()),
        sa.Column("same_bar_conflict", sa.Boolean(), nullable=False),
        sa.Column("primary_winner", sa.Boolean(), nullable=False),
        sa.Column("optimistic_winner", sa.Boolean(), nullable=False),
        sa.Column("conservative_winner", sa.Boolean(), nullable=False),
        sa.Column("bar_lineage_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_bar_lineage_hash", sa.Text(), nullable=False),
        sa.Column("source_revision_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("supersedes_replay_id", sa.BigInteger()),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("replay_hash", sa.Text(), nullable=False),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("replayed_by", sa.Text(), nullable=False),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["eligibility_decision_id"], ["winner_training_eligibility_decisions.id"]
        ),
        sa.ForeignKeyConstraint(["prediction_id"], ["winner_prediction_snapshots.id"]),
        sa.ForeignKeyConstraint(
            ["target_outcome_definition_id"], ["winner_outcome_definitions.id"]
        ),
        sa.ForeignKeyConstraint(["source_forward_outcome_id"], ["winner_forward_outcomes.id"]),
        sa.ForeignKeyConstraint(["supersedes_replay_id"], ["winner_training_outcome_replays.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("replay_hash", name="uq_winner_training_outcome_replay_hash"),
        sa.UniqueConstraint(
            "eligibility_decision_id",
            "revision",
            name="uq_winner_training_outcome_replay_revision",
        ),
    )
    op.create_index(
        "idx_winner_training_outcome_replay_lookup",
        "winner_training_outcome_replays",
        ["training_family", "target_outcome_definition_id", "status"],
    )

    op.add_column(
        "winner_estimate_evidence_members",
        sa.Column("eligibility_decision_id", sa.BigInteger()),
    )
    op.add_column(
        "winner_estimate_evidence_members", sa.Column("outcome_replay_id", sa.BigInteger())
    )
    op.add_column(
        "winner_estimate_evidence_members",
        sa.Column("evidence_origin", sa.Text(), server_default="NATIVE_1_1", nullable=False),
    )
    op.create_foreign_key(
        "fk_winner_evidence_member_eligibility_decision",
        "winner_estimate_evidence_members",
        "winner_training_eligibility_decisions",
        ["eligibility_decision_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_winner_evidence_member_outcome_replay",
        "winner_estimate_evidence_members",
        "winner_training_outcome_replays",
        ["outcome_replay_id"],
        ["id"],
    )

    # Defense in depth: revisions are INSERT-only.  The service layer also never
    # issues UPDATE/DELETE against these two ledgers.
    op.execute(
        """
        CREATE FUNCTION reject_owpe_compatibility_ledger_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'OWPE compatibility ledgers are append-only';
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER trg_winner_training_decision_append_only
        BEFORE UPDATE OR DELETE ON winner_training_eligibility_decisions
        FOR EACH ROW EXECUTE FUNCTION reject_owpe_compatibility_ledger_mutation();
        CREATE TRIGGER trg_winner_training_replay_append_only
        BEFORE UPDATE OR DELETE ON winner_training_outcome_replays
        FOR EACH ROW EXECUTE FUNCTION reject_owpe_compatibility_ledger_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_winner_training_replay_append_only "
        "ON winner_training_outcome_replays"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_winner_training_decision_append_only "
        "ON winner_training_eligibility_decisions"
    )
    op.execute("DROP FUNCTION IF EXISTS reject_owpe_compatibility_ledger_mutation()")
    op.drop_constraint(
        "fk_winner_evidence_member_outcome_replay",
        "winner_estimate_evidence_members",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_winner_evidence_member_eligibility_decision",
        "winner_estimate_evidence_members",
        type_="foreignkey",
    )
    op.drop_column("winner_estimate_evidence_members", "evidence_origin")
    op.drop_column("winner_estimate_evidence_members", "outcome_replay_id")
    op.drop_column("winner_estimate_evidence_members", "eligibility_decision_id")
    op.drop_index(
        "idx_winner_training_outcome_replay_lookup",
        table_name="winner_training_outcome_replays",
    )
    op.drop_table("winner_training_outcome_replays")
    op.drop_index(
        "idx_winner_training_eligibility_decision_lookup",
        table_name="winner_training_eligibility_decisions",
    )
    op.drop_table("winner_training_eligibility_decisions")
