"""add immutable snapshot evidence

Revision ID: 0023_immutable_snapshot_evidence
Revises: 0022_price_bar_revisions_ceri_corrections
Create Date: 2026-08-02 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023_immutable_snapshot_evidence"
down_revision: str | None = "0022_price_bar_revisions_ceri_corrections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("combined_results", sa.Column("calculation_version", sa.Text(), nullable=True))
    op.add_column("combined_results", sa.Column("config_hash", sa.Text(), nullable=True))
    op.add_column(
        "combined_results",
        sa.Column(
            "debug_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )

    op.add_column(
        "market_regime_snapshots",
        sa.Column("evidence_hash", sa.Text(), server_default="legacy", nullable=False),
    )
    op.add_column(
        "market_regime_snapshots",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "market_regime_snapshots",
        sa.Column(
            "is_current_revision",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "market_regime_snapshots",
        sa.Column("superseded_by_snapshot_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "market_regime_snapshots",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_market_regime_snapshots_superseded_by_snapshot",
        "market_regime_snapshots",
        "market_regime_snapshots",
        ["superseded_by_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uq_market_regime_snapshots_run_date_version",
        "market_regime_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_market_regime_snapshots_run_date_version",
        "market_regime_snapshots",
        ["run_id", "as_of_date", "calculation_version", "config_version", "revision"],
    )
    op.create_index(
        "idx_market_regime_snapshots_evidence_hash",
        "market_regime_snapshots",
        ["evidence_hash"],
    )
    op.create_index(
        "idx_market_regime_snapshots_current_revision",
        "market_regime_snapshots",
        ["is_current_revision", "as_of_date"],
    )

    op.add_column(
        "sector_rotation_snapshots",
        sa.Column("evidence_hash", sa.Text(), server_default="legacy", nullable=False),
    )
    op.add_column(
        "sector_rotation_snapshots",
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
    )
    op.add_column(
        "sector_rotation_snapshots",
        sa.Column(
            "is_current_revision",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.add_column(
        "sector_rotation_snapshots",
        sa.Column("superseded_by_snapshot_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "sector_rotation_snapshots",
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sector_rotation_snapshots_superseded_by_snapshot",
        "sector_rotation_snapshots",
        "sector_rotation_snapshots",
        ["superseded_by_snapshot_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.drop_constraint(
        "uq_sector_rotation_snapshots_run_date_version_mode",
        "sector_rotation_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_sector_rotation_snapshots_run_date_version_mode",
        "sector_rotation_snapshots",
        ["run_id", "as_of_date", "calculation_version", "config_hash", "mode", "revision"],
    )
    op.create_index(
        "idx_sector_rotation_snapshot_evidence_hash",
        "sector_rotation_snapshots",
        ["evidence_hash"],
    )
    op.create_index(
        "idx_sector_rotation_snapshot_current_revision",
        "sector_rotation_snapshots",
        ["is_current_revision", "as_of_date"],
    )

    op.drop_constraint(
        "uq_setup_signal_snapshots_run_identity",
        "setup_signal_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_setup_signal_snapshots_run_identity",
        "setup_signal_snapshots",
        [
            "run_id",
            "ticker",
            "timeframe",
            "data_as_of_date",
            "engine_version",
            "config_hash",
            "source_data_hash",
        ],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_setup_signal_snapshots_run_identity",
        "setup_signal_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_setup_signal_snapshots_run_identity",
        "setup_signal_snapshots",
        ["run_id", "ticker", "timeframe", "engine_version", "config_hash"],
    )

    op.drop_index(
        "idx_sector_rotation_snapshot_current_revision",
        table_name="sector_rotation_snapshots",
    )
    op.drop_index(
        "idx_sector_rotation_snapshot_evidence_hash",
        table_name="sector_rotation_snapshots",
    )
    op.drop_constraint(
        "uq_sector_rotation_snapshots_run_date_version_mode",
        "sector_rotation_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_sector_rotation_snapshots_run_date_version_mode",
        "sector_rotation_snapshots",
        ["run_id", "as_of_date", "calculation_version", "config_hash", "mode"],
    )
    op.drop_constraint(
        "fk_sector_rotation_snapshots_superseded_by_snapshot",
        "sector_rotation_snapshots",
        type_="foreignkey",
    )
    op.drop_column("sector_rotation_snapshots", "superseded_at")
    op.drop_column("sector_rotation_snapshots", "superseded_by_snapshot_id")
    op.drop_column("sector_rotation_snapshots", "is_current_revision")
    op.drop_column("sector_rotation_snapshots", "revision")
    op.drop_column("sector_rotation_snapshots", "evidence_hash")

    op.drop_index(
        "idx_market_regime_snapshots_current_revision",
        table_name="market_regime_snapshots",
    )
    op.drop_index(
        "idx_market_regime_snapshots_evidence_hash",
        table_name="market_regime_snapshots",
    )
    op.drop_constraint(
        "uq_market_regime_snapshots_run_date_version",
        "market_regime_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_market_regime_snapshots_run_date_version",
        "market_regime_snapshots",
        ["run_id", "as_of_date", "calculation_version", "config_version"],
    )
    op.drop_constraint(
        "fk_market_regime_snapshots_superseded_by_snapshot",
        "market_regime_snapshots",
        type_="foreignkey",
    )
    op.drop_column("market_regime_snapshots", "superseded_at")
    op.drop_column("market_regime_snapshots", "superseded_by_snapshot_id")
    op.drop_column("market_regime_snapshots", "is_current_revision")
    op.drop_column("market_regime_snapshots", "revision")
    op.drop_column("market_regime_snapshots", "evidence_hash")

    op.drop_column("combined_results", "debug_json")
    op.drop_column("combined_results", "config_hash")
    op.drop_column("combined_results", "calculation_version")
