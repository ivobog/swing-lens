"""create ranking results

Revision ID: 0011_create_ranking_results
Revises: 0010_add_earnings_risk_gate
Create Date: 2026-07-09 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_create_ranking_results"
down_revision: str | None = "0010_add_earnings_risk_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ranking_results",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("raw_row_id", sa.BigInteger(), nullable=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("company_name", sa.Text(), nullable=True),
        sa.Column("sector", sa.Text(), nullable=True),
        sa.Column("ranking_profile", sa.Text(), nullable=False),
        sa.Column("ranking_label", sa.Text(), nullable=False),
        sa.Column("profile_rank", sa.Integer(), nullable=False),
        sa.Column("profile_score", sa.Numeric(), nullable=False),
        sa.Column("technical_profile_score", sa.Numeric(), nullable=True),
        sa.Column("fundamental_score", sa.Numeric(), nullable=True),
        sa.Column("base_technical_score", sa.Numeric(), nullable=True),
        sa.Column("technical_classification", sa.Text(), nullable=True),
        sa.Column("fundamental_label", sa.Text(), nullable=True),
        sa.Column("decision_label", sa.Text(), nullable=False),
        sa.Column("position_size_hint", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "warning_flags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "penalties_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "gates_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "component_scores_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "debug_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("upcoming_earnings_date", sa.Date(), nullable=True),
        sa.Column("days_until_earnings", sa.Integer(), nullable=True),
        sa.Column("earnings_risk_level", sa.Text(), nullable=True),
        sa.Column("is_complete", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("has_warning", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "has_fundamental",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "has_technical",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("sort_bucket", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["raw_row_id"], ["raw_company_rows.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "ranking_profile",
            "ticker",
            name="uq_ranking_results_run_profile_ticker",
        ),
    )
    op.create_index("idx_ranking_results_run_id", "ranking_results", ["run_id"])
    op.create_index("idx_ranking_results_ticker", "ranking_results", ["ticker"])
    op.create_index("idx_ranking_results_profile", "ranking_results", ["ranking_profile"])
    op.create_index(
        "idx_ranking_results_run_profile_rank",
        "ranking_results",
        ["run_id", "ranking_profile", "profile_rank"],
    )
    op.create_index(
        "idx_ranking_results_run_profile_score",
        "ranking_results",
        ["run_id", "ranking_profile", "profile_score"],
    )
    op.create_index(
        "idx_ranking_results_earnings_risk",
        "ranking_results",
        ["earnings_risk_level"],
    )


def downgrade() -> None:
    op.drop_index("idx_ranking_results_earnings_risk", table_name="ranking_results")
    op.drop_index("idx_ranking_results_run_profile_score", table_name="ranking_results")
    op.drop_index("idx_ranking_results_run_profile_rank", table_name="ranking_results")
    op.drop_index("idx_ranking_results_profile", table_name="ranking_results")
    op.drop_index("idx_ranking_results_ticker", table_name="ranking_results")
    op.drop_index("idx_ranking_results_run_id", table_name="ranking_results")
    op.drop_table("ranking_results")
