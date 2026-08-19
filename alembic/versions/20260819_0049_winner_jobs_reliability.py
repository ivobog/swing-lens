# ruff: noqa: E501
"""add Winner evidence watermarks and cohort generations

Revision ID: 0049_winner_jobs_reliability
Revises: 0048_sec_guidance_normalization_performance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0049_winner_jobs_reliability"
down_revision: str | None = "0048_sec_guidance_normalization_performance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "winner_cohort_refresh_state",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("outcome_definition_id", sa.BigInteger(), nullable=False),
        sa.Column("feature_schema_version", sa.Text(), nullable=False),
        sa.Column("calculation_version", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("eligibility_policy_version", sa.Text(), nullable=False),
        sa.Column("compatibility_policy_version", sa.Text(), nullable=False),
        sa.Column("cohort_algorithm_version", sa.Text(), nullable=False),
        sa.Column(
            "desired_forward_revision_id", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "desired_target_stop_revision_id", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "desired_eligibility_decision_id", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column(
            "desired_training_replay_id", sa.BigInteger(), server_default="0", nullable=False
        ),
        sa.Column("desired_watermark_hash", sa.Text(), nullable=False),
        sa.Column("published_generation_id", sa.BigInteger(), nullable=True),
        sa.Column("published_watermark_hash", sa.Text(), nullable=True),
        sa.Column("last_full_scan_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_zero_due_backlog_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("current_due_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("current_deferred_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("oldest_due_session", sa.Date(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["outcome_definition_id"], ["winner_outcome_definitions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "outcome_definition_id",
            "feature_schema_version",
            "calculation_version",
            "config_hash",
            "eligibility_policy_version",
            "compatibility_policy_version",
            "cohort_algorithm_version",
            name="uq_winner_cohort_refresh_state_contract",
        ),
    )
    op.create_index(
        "idx_winner_cohort_refresh_state_published",
        "winner_cohort_refresh_state",
        ["published_generation_id"],
    )
    op.create_table(
        "winner_cohort_generations",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("generation_key", sa.Text(), nullable=False),
        sa.Column("refresh_state_id", sa.BigInteger(), nullable=False),
        sa.Column("outcome_definition_id", sa.BigInteger(), nullable=False),
        sa.Column("watermark_hash", sa.Text(), nullable=False),
        sa.Column("watermark_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("feature_schema_version", sa.Text(), nullable=False),
        sa.Column("calculation_version", sa.Text(), nullable=False),
        sa.Column("config_hash", sa.Text(), nullable=False),
        sa.Column("eligibility_policy_version", sa.Text(), nullable=False),
        sa.Column("compatibility_policy_version", sa.Text(), nullable=False),
        sa.Column("cohort_algorithm_version", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("training_cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("planned_group_count", sa.Integer(), nullable=True),
        sa.Column("completed_group_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("failed_group_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("evidence_row_count", sa.Integer(), nullable=True),
        sa.Column("root_manifest_hash", sa.Text(), nullable=True),
        sa.Column(
            "checkpoint_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column(
            "metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["outcome_definition_id"], ["winner_outcome_definitions.id"]),
        sa.ForeignKeyConstraint(
            ["refresh_state_id"], ["winner_cohort_refresh_state.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_key", name="uq_winner_cohort_generations_key"),
    )
    op.create_foreign_key(
        "fk_winner_refresh_state_published_generation",
        "winner_cohort_refresh_state",
        "winner_cohort_generations",
        ["published_generation_id"],
        ["id"],
    )
    op.create_index(
        "idx_winner_cohort_generations_state_status",
        "winner_cohort_generations",
        ["refresh_state_id", "status"],
    )
    op.create_index(
        "idx_winner_cohort_generations_published",
        "winner_cohort_generations",
        ["outcome_definition_id", "published_at"],
        postgresql_where=sa.text("status = 'PUBLISHED'"),
    )
    op.create_table(
        "winner_evidence_manifest_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("manifest_id", sa.BigInteger(), nullable=False),
        sa.Column("member_ordinal", sa.Integer(), nullable=False),
        sa.Column("prediction_id", sa.BigInteger(), nullable=False),
        sa.Column("forward_outcome_id", sa.BigInteger(), nullable=False),
        sa.Column("forward_revision", sa.Integer(), nullable=False),
        sa.Column("target_stop_outcome_id", sa.BigInteger(), nullable=False),
        sa.Column("target_stop_revision", sa.Integer(), nullable=False),
        sa.Column("eligibility_decision_id", sa.BigInteger(), nullable=True),
        sa.Column("outcome_replay_id", sa.BigInteger(), nullable=True),
        sa.Column("evidence_origin", sa.Text(), nullable=False),
        sa.Column("episode_id", sa.BigInteger(), nullable=True),
        sa.Column("inclusion_weight", sa.Numeric(18, 8), nullable=False),
        sa.Column("primary_winner", sa.Boolean(), nullable=False),
        sa.Column("member_hash", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["manifest_id"], ["winner_evidence_manifests.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["prediction_id"], ["winner_prediction_snapshots.id"]),
        sa.ForeignKeyConstraint(["forward_outcome_id"], ["winner_forward_outcomes.id"]),
        sa.ForeignKeyConstraint(
            ["eligibility_decision_id"], ["winner_training_eligibility_decisions.id"]
        ),
        sa.ForeignKeyConstraint(["outcome_replay_id"], ["winner_training_outcome_replays.id"]),
        sa.ForeignKeyConstraint(["episode_id"], ["winner_prediction_episodes.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "manifest_id", "member_ordinal", name="uq_winner_manifest_members_ordinal"
        ),
        sa.UniqueConstraint("manifest_id", "member_hash", name="uq_winner_manifest_members_hash"),
    )
    op.create_index(
        "idx_winner_manifest_members_prediction",
        "winner_evidence_manifest_members",
        ["prediction_id"],
    )
    op.create_index(
        "idx_winner_manifest_members_forward_revision",
        "winner_evidence_manifest_members",
        ["forward_outcome_id", "forward_revision"],
    )

    op.add_column(
        "winner_cohort_statistics", sa.Column("generation_id", sa.BigInteger(), nullable=True)
    )
    op.add_column(
        "winner_cohort_statistics",
        sa.Column("evidence_manifest_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_winner_cohort_statistics_generation",
        "winner_cohort_statistics",
        "winner_cohort_generations",
        ["generation_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_foreign_key(
        "fk_winner_cohort_statistics_manifest",
        "winner_cohort_statistics",
        "winner_evidence_manifests",
        ["evidence_manifest_id"],
        ["id"],
    )
    op.create_index(
        "uq_winner_cohort_statistics_generation_definition",
        "winner_cohort_statistics",
        ["generation_id", "cohort_definition_id"],
        unique=True,
        postgresql_where=sa.text("generation_id IS NOT NULL"),
    )
    op.add_column(
        "winner_probability_estimates",
        sa.Column("cohort_generation_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_winner_probability_estimates_generation",
        "winner_probability_estimates",
        "winner_cohort_generations",
        ["cohort_generation_id"],
        ["id"],
    )
    op.create_index(
        "uq_winner_probability_estimates_generation_identity",
        "winner_probability_estimates",
        [
            "prediction_id",
            "outcome_definition_id",
            "estimate_kind",
            "cohort_generation_id",
            "source_version",
        ],
        unique=True,
        postgresql_where=sa.text("cohort_generation_id IS NOT NULL"),
    )

    for name, type_ in (
        ("attempt_no", sa.Integer()),
        ("attempt_correlation_id", sa.Text()),
        ("cohort_generation_id", sa.BigInteger()),
        ("superseded_by_processing_run_id", sa.BigInteger()),
        ("terminal_reason_code", sa.Text()),
        ("last_checkpoint_at", sa.DateTime(timezone=True)),
    ):
        op.add_column("winner_processing_runs", sa.Column(name, type_, nullable=True))
    op.create_foreign_key(
        "fk_winner_processing_runs_generation",
        "winner_processing_runs",
        "winner_cohort_generations",
        ["cohort_generation_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_winner_processing_runs_superseded_by",
        "winner_processing_runs",
        "winner_processing_runs",
        ["superseded_by_processing_run_id"],
        ["id"],
    )
    op.create_index(
        "idx_winner_processing_runs_generation_attempt",
        "winner_processing_runs",
        ["cohort_generation_id", "attempt_no"],
    )

    op.add_column(
        "winner_forward_outcomes", sa.Column("pending_reason_code", sa.Text(), nullable=True)
    )
    op.add_column(
        "winner_forward_outcomes",
        sa.Column("last_attempted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "winner_forward_outcomes",
        sa.Column("retry_not_before_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "winner_forward_outcomes",
        sa.Column("last_attempted_bar_watermark", sa.Text(), nullable=True),
    )
    op.create_index(
        "idx_winner_forward_outcomes_h5_due_retry",
        "winner_forward_outcomes",
        [
            "status",
            "is_current_revision",
            "entry_model",
            "horizon_sessions",
            "retry_not_before_at",
            "due_session",
            "id",
        ],
    )
    op.create_index(
        "idx_winner_prediction_snapshots_current_contract",
        "winner_prediction_snapshots",
        [
            "eligibility_status",
            "config_hash",
            "calculation_version",
            "feature_schema_version",
            "id",
        ],
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_index(
        "idx_winner_target_stop_outcomes_generation_source",
        "winner_target_stop_outcomes",
        ["outcome_definition_id", "status", "is_current_revision", "id"],
    )
    op.create_index(
        "idx_winner_target_stop_outcomes_forward_current",
        "winner_target_stop_outcomes",
        ["forward_outcome_id", "outcome_definition_id", "is_current_revision"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_winner_prediction_snapshots_current_contract",
        table_name="winner_prediction_snapshots",
    )
    op.drop_index(
        "idx_winner_target_stop_outcomes_forward_current", table_name="winner_target_stop_outcomes"
    )
    op.drop_index(
        "idx_winner_target_stop_outcomes_generation_source",
        table_name="winner_target_stop_outcomes",
    )
    op.drop_index("idx_winner_forward_outcomes_h5_due_retry", table_name="winner_forward_outcomes")
    for name in (
        "last_attempted_bar_watermark",
        "retry_not_before_at",
        "last_attempted_at",
        "pending_reason_code",
    ):
        op.drop_column("winner_forward_outcomes", name)
    op.drop_index(
        "idx_winner_processing_runs_generation_attempt", table_name="winner_processing_runs"
    )
    op.drop_constraint(
        "fk_winner_processing_runs_superseded_by", "winner_processing_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_winner_processing_runs_generation", "winner_processing_runs", type_="foreignkey"
    )
    for name in (
        "last_checkpoint_at",
        "terminal_reason_code",
        "superseded_by_processing_run_id",
        "cohort_generation_id",
        "attempt_correlation_id",
        "attempt_no",
    ):
        op.drop_column("winner_processing_runs", name)
    op.drop_index(
        "uq_winner_probability_estimates_generation_identity",
        table_name="winner_probability_estimates",
    )
    op.drop_constraint(
        "fk_winner_probability_estimates_generation",
        "winner_probability_estimates",
        type_="foreignkey",
    )
    op.drop_column("winner_probability_estimates", "cohort_generation_id")
    op.drop_index(
        "uq_winner_cohort_statistics_generation_definition", table_name="winner_cohort_statistics"
    )
    op.drop_constraint(
        "fk_winner_cohort_statistics_manifest", "winner_cohort_statistics", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_winner_cohort_statistics_generation", "winner_cohort_statistics", type_="foreignkey"
    )
    op.drop_column("winner_cohort_statistics", "evidence_manifest_id")
    op.drop_column("winner_cohort_statistics", "generation_id")
    op.drop_table("winner_evidence_manifest_members")
    op.drop_constraint(
        "fk_winner_refresh_state_published_generation",
        "winner_cohort_refresh_state",
        type_="foreignkey",
    )
    op.drop_table("winner_cohort_generations")
    op.drop_table("winner_cohort_refresh_state")
