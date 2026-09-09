"""Bind transition preflight evidence to one frozen pipeline context.

Revision ID: 0071_transition_preflight_plan
Revises: 0070_ceri_price_response_pit_context
Create Date: 2026-09-09
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0071_transition_preflight_plan"
down_revision: str | None = "0070_ceri_price_response_pit_context"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "transition_preflight_plans",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("market_calculation_context_id", sa.BigInteger(), nullable=False),
        sa.Column("upload_run_id", sa.BigInteger(), nullable=False),
        sa.Column("pipeline_run_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("candidate_classification", sa.String(length=32), nullable=False),
        sa.Column(
            "tickers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "selection_keys_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "expected_pointers_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "predicted_snapshot_identities_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "candidate_results_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence_fingerprint", sa.Text(), nullable=False),
        sa.Column("technical_reconstruction_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stale_reason", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('RESERVED', 'CONSUMED', 'CANCELLED', 'EXPIRED', 'STALE')",
            name="ck_transition_preflight_plan_status",
        ),
        sa.ForeignKeyConstraint(
            ["market_calculation_context_id"],
            ["market_calculation_contexts.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(["upload_run_id"], ["upload_runs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["pipeline_run_id"], ["pipeline_runs.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "market_calculation_context_id", name="uq_transition_preflight_plan_context"
        ),
        sa.UniqueConstraint("pipeline_run_id", name="uq_transition_preflight_plan_pipeline"),
        sa.UniqueConstraint("idempotency_key", name="uq_transition_preflight_plan_idempotency"),
    )
    op.create_index(
        "idx_transition_preflight_plans_upload_status",
        "transition_preflight_plans",
        ["upload_run_id", "status"],
    )
    op.create_index(
        "idx_transition_preflight_plans_expiry",
        "transition_preflight_plans",
        ["status", "expires_at"],
    )


def downgrade() -> None:
    op.drop_index("idx_transition_preflight_plans_expiry", table_name="transition_preflight_plans")
    op.drop_index(
        "idx_transition_preflight_plans_upload_status",
        table_name="transition_preflight_plans",
    )
    op.drop_table("transition_preflight_plans")
