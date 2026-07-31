"""create setup lifecycle tables

Revision ID: 0017_create_setup_lifecycle_tables
Revises: 0016_add_winner_probability_engine
Create Date: 2026-08-01 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op
from app.models.tables import (
    SetupLifecycleAdministrativeAuditEvent,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationRun,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    SignalAlertRule,
    SignalChangeEvent,
)

revision: str = "0017_create_setup_lifecycle_tables"
down_revision: str | None = "0016_add_winner_probability_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


SETUP_LIFECYCLE_TABLES = (
    SetupLifecycleEvaluationRun.__table__,
    SetupSignalSnapshot.__table__,
    SetupLifecycleEpisode.__table__,
    SetupLifecycleEvent.__table__,
    SignalChangeEvent.__table__,
    SignalAlertRule.__table__,
    SignalAlertEvent.__table__,
    SetupLifecycleAdministrativeAuditEvent.__table__,
)

SETUP_LIFECYCLE_TABLE_NAMES = (
    "setup_lifecycle_evaluation_runs",
    "setup_signal_snapshots",
    "setup_lifecycle_episodes",
    "setup_lifecycle_events",
    "signal_change_events",
    "signal_alert_rules",
    "signal_alert_events",
    "setup_lifecycle_administrative_audit_events",
)


def upgrade() -> None:
    bind = op.get_bind()
    for table in SETUP_LIFECYCLE_TABLES:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(SETUP_LIFECYCLE_TABLES):
        table.drop(bind=bind, checkfirst=False)
