"""add sector rotation tables

Revision ID: 0013_add_sector_rotation_tables
Revises: 0012_add_market_regime_snapshots
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_add_sector_rotation_tables"
down_revision: str | None = "0012_add_market_regime_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb_empty_object() -> sa.TextClause:
    return sa.text("'{}'::jsonb")


def _jsonb_empty_array() -> sa.TextClause:
    return sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "sector_rotation_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("market_regime_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("calculation_version", sa.String(length=32), nullable=False),
        sa.Column("config_version", sa.String(length=32), nullable=True),
        sa.Column("config_hash", sa.String(length=64), nullable=True),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("default_ranking_profile", sa.String(length=64), nullable=True),
        sa.Column("benchmark_ticker", sa.String(length=16), nullable=True),
        sa.Column("sector_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("ticker_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("leading_sector", sa.String(length=128), nullable=True),
        sa.Column("weakest_sector", sa.String(length=128), nullable=True),
        sa.Column("riskiest_sector", sa.String(length=128), nullable=True),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.Column(
            "warning_flags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_array(),
            nullable=False,
        ),
        sa.Column(
            "debug_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["market_regime_snapshot_id"],
            ["market_regime_snapshots.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "as_of_date",
            "calculation_version",
            "config_hash",
            "mode",
            name="uq_sector_rotation_snapshots_run_date_version_mode",
        ),
    )
    op.create_index(
        "idx_sector_rotation_snapshot_run_date",
        "sector_rotation_snapshots",
        ["run_id", "as_of_date"],
    )
    op.create_index(
        "idx_sector_rotation_snapshot_date",
        "sector_rotation_snapshots",
        ["as_of_date"],
    )

    op.create_table(
        "sector_rotation_rows",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("sector", sa.String(length=128), nullable=False),
        sa.Column("sector_slug", sa.String(length=160), nullable=False),
        sa.Column("sector_proxy_ticker", sa.String(length=16), nullable=True),
        sa.Column("ticker_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("universe_share", sa.Float(), nullable=True),
        sa.Column("average_fundamental_score", sa.Float(), nullable=True),
        sa.Column("average_technical_score", sa.Float(), nullable=True),
        sa.Column("average_final_score", sa.Float(), nullable=True),
        sa.Column("average_profile_score", sa.Float(), nullable=True),
        sa.Column("top_10_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("top_25_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("top_50_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("top_25_share", sa.Float(), nullable=True),
        sa.Column("buyable_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("watch_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("danger_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("buyable_share", sa.Float(), nullable=True),
        sa.Column("watch_share", sa.Float(), nullable=True),
        sa.Column("danger_share", sa.Float(), nullable=True),
        sa.Column("clean_pullback_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("breakout_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("vcp_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "tight_base_breakout_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "extended_or_overheated_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "missing_fundamental_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column(
            "missing_technical_count",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
        sa.Column("universe_leadership_score", sa.Float(), nullable=True),
        sa.Column("etf_rotation_score", sa.Float(), nullable=True),
        sa.Column("sector_final_score", sa.Float(), nullable=True),
        sa.Column("rotation_state", sa.String(length=64), nullable=False),
        sa.Column("sector_permission", sa.String(length=64), nullable=False),
        sa.Column("position_size_multiplier", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("previous_rank", sa.Integer(), nullable=True),
        sa.Column("current_rank", sa.Integer(), nullable=True),
        sa.Column("rank_change", sa.Integer(), nullable=True),
        sa.Column("score_change", sa.Float(), nullable=True),
        sa.Column(
            "profile_distribution_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.Column(
            "setup_distribution_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.Column(
            "warning_distribution_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.Column(
            "etf_metrics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.Column(
            "component_scores_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.Column(
            "reason_codes_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_array(),
            nullable=False,
        ),
        sa.Column(
            "warning_flags_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_array(),
            nullable=False,
        ),
        sa.Column(
            "debug_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=_jsonb_empty_object(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["sector_rotation_snapshots.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id",
            "sector_slug",
            name="uq_sector_rotation_rows_snapshot_sector_slug",
        ),
    )
    op.create_index(
        "idx_sector_rotation_rows_snapshot_rank",
        "sector_rotation_rows",
        ["snapshot_id", "current_rank"],
    )
    op.create_index(
        "idx_sector_rotation_rows_sector_slug",
        "sector_rotation_rows",
        ["sector_slug"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_sector_rotation_rows_sector_slug",
        table_name="sector_rotation_rows",
    )
    op.drop_index(
        "idx_sector_rotation_rows_snapshot_rank",
        table_name="sector_rotation_rows",
    )
    op.drop_table("sector_rotation_rows")
    op.drop_index(
        "idx_sector_rotation_snapshot_date",
        table_name="sector_rotation_snapshots",
    )
    op.drop_index(
        "idx_sector_rotation_snapshot_run_date",
        table_name="sector_rotation_snapshots",
    )
    op.drop_table("sector_rotation_snapshots")
