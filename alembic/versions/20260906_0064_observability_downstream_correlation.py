"""Propagate causality into CERI runs and provider telemetry.

Revision ID: 0064_observability_downstream_correlation
Revises: 0063_observability_triggered_by_job
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0064_observability_downstream_correlation"
down_revision = "0063_observability_triggered_by_job"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table_name in ("ceri_processing_runs", "ceri_provider_request_telemetry"):
        op.add_column(table_name, sa.Column("root_correlation_id", sa.Text(), nullable=True))
        op.add_column(table_name, sa.Column("causation_id", sa.Text(), nullable=True))
        op.add_column(table_name, sa.Column("background_job_id", sa.BigInteger(), nullable=True))
        op.add_column(
            table_name,
            sa.Column("triggered_by_request_id", sa.Text(), nullable=True),
        )
        op.create_foreign_key(
            f"fk_{table_name}_background_job",
            table_name,
            "background_jobs",
            ["background_job_id"],
            ["id"],
            ondelete="SET NULL",
        )
    op.create_index(
        "ix_ceri_processing_runs_root",
        "ceri_processing_runs",
        ["root_correlation_id", "created_at"],
    )
    op.create_index(
        "ix_ceri_provider_telemetry_root",
        "ceri_provider_request_telemetry",
        ["root_correlation_id", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_provider_telemetry_root",
        table_name="ceri_provider_request_telemetry",
    )
    op.drop_index("ix_ceri_processing_runs_root", table_name="ceri_processing_runs")
    for table_name in reversed(("ceri_processing_runs", "ceri_provider_request_telemetry")):
        op.drop_constraint(
            f"fk_{table_name}_background_job",
            table_name,
            type_="foreignkey",
        )
        for column_name in (
            "triggered_by_request_id",
            "background_job_id",
            "causation_id",
            "root_correlation_id",
        ):
            op.drop_column(table_name, column_name)
