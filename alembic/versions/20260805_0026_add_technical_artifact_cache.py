"""add price series versions and technical feature artifacts

Revision ID: 0026_technical_artifact_cache
Revises: 0025_winner_combined_result_set_null
Create Date: 2026-08-05 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_technical_artifact_cache"
down_revision: str | None = "0025_winner_combined_result_set_null"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "price_series_versions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), nullable=False),
        sa.Column("what_to_show", sa.Text(), nullable=False),
        sa.Column("series_version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column("bar_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("first_bar_date", sa.Date(), nullable=True),
        sa.Column("latest_bar_date", sa.Date(), nullable=True),
        sa.Column(
            "last_changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "timeframe",
            "what_to_show",
            name="uq_price_series_versions_identity",
        ),
    )
    op.create_index(
        "idx_price_series_versions_latest",
        "price_series_versions",
        ["latest_bar_date", "ticker"],
    )
    op.execute(
        sa.text(
            """
            INSERT INTO price_series_versions (
                ticker,
                timeframe,
                what_to_show,
                series_version,
                bar_count,
                first_bar_date,
                latest_bar_date
            )
            SELECT
                ticker,
                timeframe,
                what_to_show,
                1,
                count(*)::integer,
                min(bar_date),
                max(bar_date)
            FROM price_bars
            GROUP BY ticker, timeframe, what_to_show
            ON CONFLICT (ticker, timeframe, what_to_show) DO NOTHING
            """
        )
    )

    op.create_table(
        "technical_feature_artifacts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.Text(), server_default="1 day", nullable=False),
        sa.Column("artifact_kind", sa.Text(), nullable=False),
        sa.Column("input_signature", sa.Text(), nullable=False),
        sa.Column("artifact_schema_version", sa.Text(), nullable=False),
        sa.Column("technical_engine_version", sa.Text(), nullable=False),
        sa.Column("scoring_config_hash", sa.Text(), nullable=False),
        sa.Column("input_versions_json", postgresql.JSONB(), nullable=False),
        sa.Column("artifact_json", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column(
            "warning_flags_json",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "last_used_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('LOCAL', 'RELATIVE')",
            name="ck_technical_feature_artifacts_kind",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "timeframe",
            "artifact_kind",
            "input_signature",
            name="uq_technical_feature_artifacts_signature",
        ),
    )
    op.create_index(
        "idx_technical_feature_artifacts_last_used",
        "technical_feature_artifacts",
        ["last_used_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_technical_feature_artifacts_last_used",
        table_name="technical_feature_artifacts",
    )
    op.drop_table("technical_feature_artifacts")
    op.drop_index(
        "idx_price_series_versions_latest",
        table_name="price_series_versions",
    )
    op.drop_table("price_series_versions")
