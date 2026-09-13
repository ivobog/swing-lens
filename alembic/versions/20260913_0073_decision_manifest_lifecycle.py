"""Split run-start anchors from post-upstream decision manifests.

Revision ID: 0073_decision_manifest_lifecycle
Revises: 0072_ceri_artifact_context_lineage
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0073_decision_manifest_lifecycle"
down_revision: str | None = "0072_ceri_artifact_context_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Historical plans, including failed certification artifacts, are deliberately
    # not backfilled or rewritten. Only newly-created plans carry the run-start anchor.
    op.add_column(
        "transition_preflight_plans",
        sa.Column("run_start_anchor_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "transition_preflight_plans",
        sa.Column("run_start_anchor_fingerprint", sa.Text(), nullable=True),
    )
    op.create_table(
        "transition_decision_handoff_manifests",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("preflight_plan_id", sa.BigInteger(), nullable=False),
        sa.Column("market_calculation_context_id", sa.BigInteger(), nullable=False),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=False),
        sa.Column("run_start_anchor_fingerprint", sa.Text(), nullable=False),
        sa.Column("manifest_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["preflight_plan_id"], ["transition_preflight_plans.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["market_calculation_context_id"],
            ["market_calculation_contexts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("preflight_plan_id", name="uq_transition_handoff_preflight"),
        sa.UniqueConstraint("pipeline_run_id", name="uq_transition_handoff_pipeline"),
        sa.UniqueConstraint("manifest_fingerprint", name="uq_transition_handoff_fingerprint"),
    )
    op.create_index(
        "idx_transition_decision_handoff_run_context",
        "transition_decision_handoff_manifests",
        ["upload_run_id", "market_calculation_context_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_transition_decision_handoff_run_context",
        table_name="transition_decision_handoff_manifests",
    )
    op.drop_table("transition_decision_handoff_manifests")
    op.drop_column("transition_preflight_plans", "run_start_anchor_fingerprint")
    op.drop_column("transition_preflight_plans", "run_start_anchor_json")
