"""add market regime snapshots

Revision ID: 0012_add_market_regime_snapshots
Revises: 0011_create_ranking_results
Create Date: 2026-07-28 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_add_market_regime_snapshots"
down_revision: str | None = "0011_create_ranking_results"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_regime_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("calculation_version", sa.String(length=32), nullable=False),
        sa.Column("config_version", sa.String(length=64), nullable=True),
        sa.Column("regime", sa.String(length=64), nullable=False),
        sa.Column("risk_state", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("risk_off", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("gate_ok", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column("action_summary", sa.Text(), nullable=False),
        sa.Column(
            "position_size_multiplier",
            sa.Float(),
            server_default=sa.text("1.0"),
            nullable=False,
        ),
        sa.Column(
            "preferred_profiles_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "allowed_profiles_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reduced_profiles_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "blocked_profiles_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "allowed_setups_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "blocked_setups_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "input_symbols_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "index_health_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "universe_participation_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "sector_leadership_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "reasons_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "warnings_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "debug_json",
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
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "run_id",
            "as_of_date",
            "calculation_version",
            "config_version",
            name="uq_market_regime_snapshots_run_date_version",
        ),
    )
    op.create_index(
        "idx_market_regime_snapshots_as_of_date",
        "market_regime_snapshots",
        ["as_of_date"],
    )
    op.create_index(
        "idx_market_regime_snapshots_run_id",
        "market_regime_snapshots",
        ["run_id"],
    )
    op.create_index(
        "idx_market_regime_snapshots_regime",
        "market_regime_snapshots",
        ["regime"],
    )
    op.create_index(
        "idx_market_regime_snapshots_risk_state",
        "market_regime_snapshots",
        ["risk_state"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_market_regime_snapshots_risk_state",
        table_name="market_regime_snapshots",
    )
    op.drop_index("idx_market_regime_snapshots_regime", table_name="market_regime_snapshots")
    op.drop_index("idx_market_regime_snapshots_run_id", table_name="market_regime_snapshots")
    op.drop_index(
        "idx_market_regime_snapshots_as_of_date",
        table_name="market_regime_snapshots",
    )
    op.drop_table("market_regime_snapshots")
