"""add history pagination indexes

Revision ID: 0009_history_indexes
Revises: 0008_add_pipeline_tables
Create Date: 2026-07-07 00:00:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_history_indexes"
down_revision: str | None = "0008_add_pipeline_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index("idx_upload_runs_uploaded_at_desc", "upload_runs", ["uploaded_at"])
    op.create_index("idx_upload_runs_status", "upload_runs", ["status"])
    op.create_index("idx_combined_results_ticker", "combined_results", ["ticker"])
    op.create_index(
        "idx_combined_results_decision",
        "combined_results",
        ["combined_decision"],
    )
    op.create_index("idx_combined_results_score", "combined_results", ["final_score"])
    op.create_index("idx_combined_results_warning", "combined_results", ["has_warning"])
    op.create_index("idx_combined_results_complete", "combined_results", ["is_complete"])


def downgrade() -> None:
    op.drop_index("idx_combined_results_complete", table_name="combined_results")
    op.drop_index("idx_combined_results_warning", table_name="combined_results")
    op.drop_index("idx_combined_results_score", table_name="combined_results")
    op.drop_index("idx_combined_results_decision", table_name="combined_results")
    op.drop_index("idx_combined_results_ticker", table_name="combined_results")
    op.drop_index("idx_upload_runs_status", table_name="upload_runs")
    op.drop_index("idx_upload_runs_uploaded_at_desc", table_name="upload_runs")
