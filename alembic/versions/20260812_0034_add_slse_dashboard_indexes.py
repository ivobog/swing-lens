"""add SLSE dashboard query indexes

Revision ID: 0034_slse_dashboard_indexes
Revises: 0033_retain_histogram_raw
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0034_slse_dashboard_indexes"
down_revision: str | None = "0033_retain_histogram_raw"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "idx_setup_signal_snapshots_canonical_date_id",
        "setup_signal_snapshots",
        ["data_as_of_date", "id"],
        postgresql_where=sa.text("is_canonical"),
    )
    op.create_index(
        "idx_setup_lifecycle_events_dashboard_order",
        "setup_lifecycle_events",
        ["effective_date", "id"],
        postgresql_where=sa.text(
            "is_current_version AND event_type IN "
            "('EPISODE_OPENED', 'STATE_TRANSITION', 'PHASE_TRANSITION')"
        ),
    )
    op.create_index(
        "idx_setup_lifecycle_events_confidence_order",
        "setup_lifecycle_events",
        ["confidence_score", "id"],
        postgresql_where=sa.text(
            "is_current_version AND event_type IN "
            "('EPISODE_OPENED', 'STATE_TRANSITION', 'PHASE_TRANSITION')"
        ),
    )
    op.create_index(
        "idx_setup_lifecycle_events_dashboard_compound",
        "setup_lifecycle_events",
        [
            "setup_family",
            "to_state",
            "actionability_after",
            "effective_date",
            "id",
        ],
        postgresql_where=sa.text(
            "is_current_version AND event_type IN "
            "('EPISODE_OPENED', 'STATE_TRANSITION', 'PHASE_TRANSITION')"
        ),
    )
    op.create_index(
        "idx_signal_change_events_dashboard_order",
        "signal_change_events",
        ["effective_date", "id"],
    )
    op.create_index(
        "idx_signal_change_events_current_snapshot",
        "signal_change_events",
        ["current_snapshot_id"],
    )
    op.create_index(
        "idx_setup_signal_snapshots_dashboard_compound",
        "setup_signal_snapshots",
        [
            "sector",
            "primary_setup_family",
            "lifecycle_state_candidate",
            "actionability_candidate",
            "confidence_score",
            "setup_score",
            "id",
        ],
        postgresql_where=sa.text("is_canonical"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_setup_signal_snapshots_dashboard_compound",
        table_name="setup_signal_snapshots",
    )
    op.drop_index(
        "idx_signal_change_events_current_snapshot",
        table_name="signal_change_events",
    )
    op.drop_index(
        "idx_signal_change_events_dashboard_order",
        table_name="signal_change_events",
    )
    op.drop_index(
        "idx_setup_lifecycle_events_dashboard_order",
        table_name="setup_lifecycle_events",
    )
    op.drop_index(
        "idx_setup_lifecycle_events_confidence_order",
        table_name="setup_lifecycle_events",
    )
    op.drop_index(
        "idx_setup_lifecycle_events_dashboard_compound",
        table_name="setup_lifecycle_events",
    )
    op.drop_index(
        "idx_setup_signal_snapshots_canonical_date_id",
        table_name="setup_signal_snapshots",
    )
