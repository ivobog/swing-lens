"""Add explicit Winner estimate lifecycle.

Revision ID: 0060_winner_estimate_lifecycle
Revises: 0059_winner_market_data_obligations
Create Date: 2026-09-06
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0060_winner_estimate_lifecycle"
down_revision = "0059_winner_market_data_obligations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "winner_probability_estimates",
        sa.Column(
            "lifecycle_status",
            sa.Text(),
            server_default="CANDIDATE",
            nullable=True,
        ),
    )
    op.add_column(
        "winner_probability_estimates",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "winner_probability_estimates",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        UPDATE winner_probability_estimates AS e
        SET lifecycle_status = CASE
              WHEN g.status = 'PUBLISHED' THEN 'PUBLISHED'
              ELSE 'SUPERSEDED'
            END,
            published_at = CASE
              WHEN g.status = 'PUBLISHED' THEN COALESCE(g.published_at, e.created_at)
              ELSE g.published_at
            END,
            superseded_at = CASE
              WHEN g.status <> 'PUBLISHED'
                THEN COALESCE(g.completed_at, g.published_at, e.created_at)
              ELSE NULL
            END
        FROM winner_cohort_generations AS g
        WHERE e.cohort_generation_id = g.id
        """
    )
    op.execute(
        """
        UPDATE winner_probability_estimates
        SET lifecycle_status = 'PUBLISHED', published_at = created_at
        WHERE cohort_generation_id IS NULL
        """
    )
    op.alter_column("winner_probability_estimates", "lifecycle_status", nullable=False)
    op.create_check_constraint(
        "ck_winner_probability_estimates_lifecycle_status",
        "winner_probability_estimates",
        "lifecycle_status IN ('CANDIDATE', 'PUBLISHED', 'SUPERSEDED')",
    )
    op.create_check_constraint(
        "ck_winner_probability_estimates_lifecycle_timestamps",
        "winner_probability_estimates",
        "(lifecycle_status = 'CANDIDATE' AND published_at IS NULL "
        "AND superseded_at IS NULL) OR "
        "(lifecycle_status = 'PUBLISHED' AND published_at IS NOT NULL "
        "AND superseded_at IS NULL) OR "
        "(lifecycle_status = 'SUPERSEDED' AND superseded_at IS NOT NULL)",
    )
    op.create_index(
        "idx_winner_probability_estimates_serving",
        "winner_probability_estimates",
        [
            "lifecycle_status",
            "prediction_id",
            "outcome_definition_id",
            "estimate_kind",
            "training_cutoff_at",
            "created_at",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_winner_probability_estimates_serving",
        table_name="winner_probability_estimates",
    )
    op.drop_constraint(
        "ck_winner_probability_estimates_lifecycle_timestamps",
        "winner_probability_estimates",
        type_="check",
    )
    op.drop_constraint(
        "ck_winner_probability_estimates_lifecycle_status",
        "winner_probability_estimates",
        type_="check",
    )
    op.drop_column("winner_probability_estimates", "superseded_at")
    op.drop_column("winner_probability_estimates", "published_at")
    op.drop_column("winner_probability_estimates", "lifecycle_status")
