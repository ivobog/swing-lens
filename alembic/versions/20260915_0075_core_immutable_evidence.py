"""Add immutable core calculation evidence and mutable projection pointers.

Revision ID: 0075_core_immutable_evidence
Revises: 0074_ceri_evidence_quarantine
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0075_core_immutable_evidence"
down_revision: str | None = "0074_ceri_evidence_quarantine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "core_calculation_evidence",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("ranking_profile", sa.Text(), nullable=True),
        sa.Column("calculation_identity_fingerprint", sa.Text(), nullable=False),
        sa.Column(
            "calculation_identity_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("payload_fingerprint", sa.Text(), nullable=False),
        sa.Column("payload_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "source_evidence_ids_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("evidence_key", sa.Text(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING')",
            name="ck_core_calculation_evidence_kind",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["upload_runs.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("evidence_key", name="uq_core_calculation_evidence_key"),
    )
    op.create_index(
        "idx_core_evidence_identity",
        "core_calculation_evidence",
        ["artifact_kind", "calculation_identity_fingerprint"],
    )
    op.create_index(
        "idx_core_evidence_scope",
        "core_calculation_evidence",
        ["artifact_kind", "run_id", "ticker"],
    )

    op.create_table(
        "core_calculation_evidence_sources",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("evidence_id", sa.BigInteger(), nullable=False),
        sa.Column("source_role", sa.String(length=32), nullable=False),
        sa.Column("source_evidence_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "evidence_id <> source_evidence_id", name="ck_core_evidence_source_not_self"
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["core_calculation_evidence.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_evidence_id"], ["core_calculation_evidence.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "evidence_id", "source_role", name="uq_core_evidence_source_role"
        ),
    )
    op.create_index(
        "idx_core_evidence_source_upstream",
        "core_calculation_evidence_sources",
        ["source_evidence_id"],
    )

    op.create_table(
        "core_calculation_current_projections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("run_id", sa.BigInteger(), nullable=False),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("ranking_profile_key", sa.Text(), server_default="", nullable=False),
        sa.Column("evidence_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING')",
            name="ck_core_current_projection_kind",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["upload_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["evidence_id"], ["core_calculation_evidence.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_kind",
            "run_id",
            "ticker",
            "ranking_profile_key",
            name="uq_core_current_projection_scope",
        ),
    )
    op.create_index(
        "idx_core_current_projection_evidence",
        "core_calculation_current_projections",
        ["evidence_id"],
    )

    for table_name in (
        "fundamental_scores",
        "technical_scores",
        "combined_results",
        "ranking_results",
    ):
        op.add_column(table_name, sa.Column("evidence_id", sa.BigInteger(), nullable=True))
        op.create_foreign_key(
            f"fk_{table_name}_evidence",
            table_name,
            "core_calculation_evidence",
            ["evidence_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_index(f"idx_{table_name}_evidence", table_name, ["evidence_id"])


def downgrade() -> None:
    for table_name in (
        "ranking_results",
        "combined_results",
        "technical_scores",
        "fundamental_scores",
    ):
        op.drop_index(f"idx_{table_name}_evidence", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_evidence", table_name, type_="foreignkey")
        op.drop_column(table_name, "evidence_id")
    op.drop_index(
        "idx_core_current_projection_evidence",
        table_name="core_calculation_current_projections",
    )
    op.drop_table("core_calculation_current_projections")
    op.drop_index(
        "idx_core_evidence_source_upstream",
        table_name="core_calculation_evidence_sources",
    )
    op.drop_table("core_calculation_evidence_sources")
    op.drop_index("idx_core_evidence_scope", table_name="core_calculation_evidence")
    op.drop_index("idx_core_evidence_identity", table_name="core_calculation_evidence")
    op.drop_table("core_calculation_evidence")
