"""Add Winner estimate replacement lineage and publication audit.

Revision ID: 0061_winner_estimate_policy
Revises: 0060_winner_estimate_lifecycle
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0061_winner_estimate_policy"
down_revision = "0060_winner_estimate_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "winner_probability_estimates",
        sa.Column("supersedes_estimate_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "winner_probability_estimates",
        sa.Column("reconstruction_category", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_winner_probability_estimates_supersedes",
        "winner_probability_estimates",
        "winner_probability_estimates",
        ["supersedes_estimate_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute(
        """
        UPDATE winner_probability_estimates
        SET supersedes_estimate_id = (metadata_json->>'existing_estimate_id')::bigint
        WHERE lifecycle_status = 'CANDIDATE'
          AND metadata_json ? 'existing_estimate_id'
          AND (metadata_json->>'existing_estimate_id') ~ '^[0-9]+$'
        """
    )
    op.execute(
        """
        WITH latest_temporal AS MATERIALIZED (
          SELECT DISTINCT ON(prediction_id) prediction_id,evidence_eligible
          FROM winner_temporal_validity_decisions
          ORDER BY prediction_id,validation_sequence DESC,id DESC
        ), candidates AS MATERIALIZED (
          SELECT id candidate_id,supersedes_estimate_id
          FROM winner_probability_estimates
          WHERE lifecycle_status='CANDIDATE'
            AND estimate_kind='DECISION_TIME'
            AND supersedes_estimate_id IS NOT NULL
        ), membership_summary AS (
          SELECT c.candidate_id,
            count(m.id) member_count,
            bool_or(NOT t.evidence_eligible) FILTER (
              WHERE t.prediction_id IS NOT NULL
            ) has_invalid,
            bool_or(t.prediction_id IS NULL) FILTER (
              WHERE m.id IS NOT NULL
            ) has_unverifiable
          FROM candidates c
          LEFT JOIN winner_estimate_evidence_members m
            ON m.estimate_id=c.supersedes_estimate_id
          LEFT JOIN latest_temporal t ON t.prediction_id=m.prediction_id
          GROUP BY c.candidate_id
        ), classified AS (
          SELECT candidate_id,
            CASE
              WHEN coalesce(has_invalid,false) THEN 'DIRECTLY_CONTAMINATED'
              WHEN member_count=0 THEN 'NO_ORIGINAL_EVIDENCE'
              WHEN coalesce(has_unverifiable,false) THEN 'LEGACY_EVIDENCE_UNVERIFIABLE'
              ELSE 'OTHER_POINT_IN_TIME_UNRECONSTRUCTABLE'
            END reconstruction_category
          FROM membership_summary
        )
        UPDATE winner_probability_estimates c
        SET reconstruction_category=classified.reconstruction_category
        FROM classified WHERE c.id=classified.candidate_id
        """
    )
    op.create_index(
        "uq_winner_probability_estimates_supersedes",
        "winner_probability_estimates",
        ["supersedes_estimate_id"],
        unique=True,
        postgresql_where=sa.text("supersedes_estimate_id IS NOT NULL"),
    )
    op.create_check_constraint(
        "ck_winner_probability_estimates_reconstruction_category",
        "winner_probability_estimates",
        "reconstruction_category IS NULL OR reconstruction_category IN ("
        "'DIRECTLY_CONTAMINATED','LEGACY_EVIDENCE_UNVERIFIABLE',"
        "'NO_ORIGINAL_EVIDENCE','OTHER_POINT_IN_TIME_UNRECONSTRUCTABLE')",
    )
    op.create_table(
        "winner_estimate_publication_requests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("request_key", sa.Text(), nullable=False),
        sa.Column("actor", sa.Text(), nullable=False),
        sa.Column("generation_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_generation_id", sa.BigInteger(), nullable=False),
        sa.Column("generation_key", sa.Text(), nullable=False),
        sa.Column("transition_manifest_hash", sa.Text(), nullable=False),
        sa.Column("candidate_manifest_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "result_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status = 'COMPLETED'",
            name="ck_winner_estimate_publication_request_status",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id"],
            ["winner_cohort_generations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["previous_generation_id"],
            ["winner_cohort_generations.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "request_key", name="uq_winner_estimate_publication_request_key"
        ),
    )
    op.create_index(
        "idx_winner_estimate_publication_request_generation",
        "winner_estimate_publication_requests",
        ["generation_id", "completed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_winner_estimate_publication_request_generation",
        table_name="winner_estimate_publication_requests",
    )
    op.drop_constraint(
        "ck_winner_probability_estimates_reconstruction_category",
        "winner_probability_estimates",
        type_="check",
    )
    op.drop_table("winner_estimate_publication_requests")
    op.drop_index(
        "uq_winner_probability_estimates_supersedes",
        table_name="winner_probability_estimates",
    )
    op.drop_constraint(
        "fk_winner_probability_estimates_supersedes",
        "winner_probability_estimates",
        type_="foreignkey",
    )
    op.drop_column("winner_probability_estimates", "supersedes_estimate_id")
    op.drop_column("winner_probability_estimates", "reconstruction_category")
