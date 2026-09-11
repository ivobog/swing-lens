"""Separate lifecycle snapshot evidence from mutable current selection.

Revision ID: 0069_lifecycle_current_selection
Revises: 0068_market_calc_context
Create Date: 2026-09-08
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0069_lifecycle_current_selection"
down_revision: str | None = "0068_market_calc_context"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "setup_signal_snapshot_current_selections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("data_as_of_date", sa.Date(), nullable=False),
        sa.Column("selected_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("selected_run_id", sa.BigInteger(), nullable=True),
        sa.Column("selected_evaluation_run_id", sa.BigInteger(), nullable=True),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("selection_reason", sa.Text(), nullable=False),
        sa.Column(
            "selection_decision_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["selected_snapshot_id"], ["setup_signal_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["selected_run_id"], ["upload_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["selected_evaluation_run_id"],
            ["setup_lifecycle_evaluation_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "ticker",
            "timeframe",
            "data_as_of_date",
            name="uq_setup_signal_snapshot_current_selection_key",
        ),
        sa.UniqueConstraint(
            "selected_snapshot_id",
            name="uq_setup_signal_snapshot_current_selection_snapshot",
        ),
    )
    op.create_index(
        "idx_setup_signal_snapshot_current_selection_run",
        "setup_signal_snapshot_current_selections",
        ["selected_run_id"],
    )
    op.create_index(
        "idx_setup_signal_snapshot_current_selection_evaluation",
        "setup_signal_snapshot_current_selections",
        ["selected_evaluation_run_id"],
    )

    op.create_table(
        "setup_signal_snapshot_selection_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("timeframe", sa.String(length=16), nullable=False),
        sa.Column("data_as_of_date", sa.Date(), nullable=False),
        sa.Column("selection_revision", sa.Integer(), nullable=False),
        sa.Column("previous_snapshot_id", sa.BigInteger(), nullable=True),
        sa.Column("selected_snapshot_id", sa.BigInteger(), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=True),
        sa.Column("evaluation_run_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column(
            "decision_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("event_key", sa.Text(), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["previous_snapshot_id"], ["setup_signal_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["selected_snapshot_id"], ["setup_signal_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["evaluation_run_id"],
            ["setup_lifecycle_evaluation_runs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("event_key", name="uq_setup_signal_snapshot_selection_event_key"),
        sa.UniqueConstraint(
            "ticker",
            "timeframe",
            "data_as_of_date",
            "selection_revision",
            name="uq_setup_signal_snapshot_selection_event_revision",
        ),
    )
    op.create_index(
        "idx_setup_signal_snapshot_selection_events_selected",
        "setup_signal_snapshot_selection_events",
        ["selected_snapshot_id"],
    )
    op.create_index(
        "idx_setup_signal_snapshot_selection_events_run",
        "setup_signal_snapshot_selection_events",
        ["run_id", "evaluation_run_id"],
    )

    # The legacy flag is the only deterministic source for current state at the
    # migration boundary.  Capture that observation without inventing past
    # selection intervals or supersession timestamps.
    op.execute(
        sa.text(
            """
            INSERT INTO setup_signal_snapshot_current_selections (
                ticker, timeframe, data_as_of_date, selected_snapshot_id,
                selected_run_id, selected_evaluation_run_id, revision,
                selection_reason, selection_decision_json
            )
            SELECT ticker, timeframe, data_as_of_date, id, run_id,
                   evaluation_run_id, 1, 'LEGACY_CURRENT_FLAG_BOOTSTRAP',
                   jsonb_build_object(
                       'source', 'setup_signal_snapshots.is_canonical',
                       'historical_intervals_reconstructed', false
                   )
            FROM setup_signal_snapshots
            WHERE is_canonical
            """
        )
    )

    # Multiple snapshots may truthfully record that they were selected when
    # their own run completed. Current state is now enforced by the pointer.
    op.drop_index("uq_setup_signal_snapshots_canonical_day", table_name="setup_signal_snapshots")
    op.create_index(
        "idx_setup_signal_snapshots_canonical_at_decision",
        "setup_signal_snapshots",
        ["ticker", "timeframe", "data_as_of_date"],
        postgresql_where=sa.text("is_canonical"),
    )


def downgrade() -> None:
    # Translate the pointer back to the legacy one-current-row representation.
    # This is intentionally lossy administrative compatibility for a downgrade;
    # immutable evidence semantics require remaining at revision 0069 or later.
    op.execute(
        sa.text(
            """
            UPDATE setup_signal_snapshots AS snapshot
            SET is_canonical = EXISTS (
                SELECT 1
                FROM setup_signal_snapshot_current_selections AS selection
                WHERE selection.selected_snapshot_id = snapshot.id
            ),
            superseded_by_snapshot_id = CASE
                WHEN EXISTS (
                    SELECT 1
                    FROM setup_signal_snapshot_current_selections AS selection
                    WHERE selection.ticker = snapshot.ticker
                      AND selection.timeframe = snapshot.timeframe
                      AND selection.data_as_of_date = snapshot.data_as_of_date
                      AND selection.selected_snapshot_id <> snapshot.id
                ) THEN (
                    SELECT selection.selected_snapshot_id
                    FROM setup_signal_snapshot_current_selections AS selection
                    WHERE selection.ticker = snapshot.ticker
                      AND selection.timeframe = snapshot.timeframe
                      AND selection.data_as_of_date = snapshot.data_as_of_date
                )
                ELSE NULL
            END
            """
        )
    )
    op.drop_index(
        "idx_setup_signal_snapshots_canonical_at_decision",
        table_name="setup_signal_snapshots",
    )
    op.create_index(
        "uq_setup_signal_snapshots_canonical_day",
        "setup_signal_snapshots",
        ["ticker", "timeframe", "data_as_of_date"],
        unique=True,
        postgresql_where=sa.text("is_canonical"),
    )

    op.drop_index(
        "idx_setup_signal_snapshot_selection_events_run",
        table_name="setup_signal_snapshot_selection_events",
    )
    op.drop_index(
        "idx_setup_signal_snapshot_selection_events_selected",
        table_name="setup_signal_snapshot_selection_events",
    )
    op.drop_table("setup_signal_snapshot_selection_events")
    op.drop_index(
        "idx_setup_signal_snapshot_current_selection_evaluation",
        table_name="setup_signal_snapshot_current_selections",
    )
    op.drop_index(
        "idx_setup_signal_snapshot_current_selection_run",
        table_name="setup_signal_snapshot_current_selections",
    )
    op.drop_table("setup_signal_snapshot_current_selections")
