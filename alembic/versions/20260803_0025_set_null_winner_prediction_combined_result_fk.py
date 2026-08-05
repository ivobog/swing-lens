"""set null winner prediction combined result fk

Revision ID: 0025_winner_combined_result_set_null
Revises: 0024_background_job_request_keys
Create Date: 2026-08-03 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_winner_combined_result_set_null"
down_revision: str | None = "0024_background_job_request_keys"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "winner_prediction_snapshots_combined_result_id_fkey",
        "winner_prediction_snapshots",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "winner_prediction_snapshots_combined_result_id_fkey",
        "winner_prediction_snapshots",
        "combined_results",
        ["combined_result_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "winner_prediction_snapshots_combined_result_id_fkey",
        "winner_prediction_snapshots",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "winner_prediction_snapshots_combined_result_id_fkey",
        "winner_prediction_snapshots",
        "combined_results",
        ["combined_result_id"],
        ["id"],
    )
