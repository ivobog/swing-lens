"""Extend immutable evidence to Market Regime and Sector Rotation.

Revision ID: 0076_regime_sector_evidence
Revises: 0075_core_immutable_evidence
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0076_regime_sector_evidence"
down_revision: str | None = "0075_core_immutable_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EVIDENCE_KINDS = (
    "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING', "
    "'REGIME', 'SECTOR')"
)
_CORE_EVIDENCE_KINDS = (
    "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING')"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_core_calculation_evidence_kind",
        "core_calculation_evidence",
        type_="check",
    )
    op.create_check_constraint(
        "ck_core_calculation_evidence_kind",
        "core_calculation_evidence",
        _EVIDENCE_KINDS,
    )
    op.alter_column("core_calculation_evidence", "run_id", nullable=True)
    op.alter_column("core_calculation_evidence", "ticker", nullable=True)

    op.alter_column(
        "core_calculation_evidence_sources",
        "source_role",
        existing_type=sa.String(length=32),
        type_=sa.String(length=128),
        existing_nullable=False,
    )

    op.drop_constraint(
        "ck_core_current_projection_kind",
        "core_calculation_current_projections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_core_current_projection_kind",
        "core_calculation_current_projections",
        _EVIDENCE_KINDS,
    )
    op.drop_constraint(
        "uq_core_current_projection_scope",
        "core_calculation_current_projections",
        type_="unique",
    )
    op.alter_column("core_calculation_current_projections", "run_id", nullable=True)
    op.alter_column("core_calculation_current_projections", "ticker", nullable=True)
    op.create_index(
        "uq_core_current_projection_ticker_scope",
        "core_calculation_current_projections",
        ["artifact_kind", "run_id", "ticker", "ranking_profile_key"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL AND ticker IS NOT NULL"),
    )
    op.create_index(
        "uq_core_current_projection_context_run_scope",
        "core_calculation_current_projections",
        ["artifact_kind", "run_id", "ranking_profile_key"],
        unique=True,
        postgresql_where=sa.text("run_id IS NOT NULL AND ticker IS NULL"),
    )
    op.create_index(
        "uq_core_current_projection_global_scope",
        "core_calculation_current_projections",
        ["artifact_kind", "ranking_profile_key"],
        unique=True,
        postgresql_where=sa.text("run_id IS NULL AND ticker IS NULL"),
    )

    for table_name in ("market_regime_snapshots", "sector_rotation_snapshots"):
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
    for table_name in ("sector_rotation_snapshots", "market_regime_snapshots"):
        op.drop_index(f"idx_{table_name}_evidence", table_name=table_name)
        op.drop_constraint(f"fk_{table_name}_evidence", table_name, type_="foreignkey")
        op.drop_column(table_name, "evidence_id")

    op.drop_index(
        "uq_core_current_projection_global_scope",
        table_name="core_calculation_current_projections",
    )
    op.drop_index(
        "uq_core_current_projection_context_run_scope",
        table_name="core_calculation_current_projections",
    )
    op.drop_index(
        "uq_core_current_projection_ticker_scope",
        table_name="core_calculation_current_projections",
    )
    op.alter_column("core_calculation_current_projections", "ticker", nullable=False)
    op.alter_column("core_calculation_current_projections", "run_id", nullable=False)
    op.create_unique_constraint(
        "uq_core_current_projection_scope",
        "core_calculation_current_projections",
        ["artifact_kind", "run_id", "ticker", "ranking_profile_key"],
    )
    op.drop_constraint(
        "ck_core_current_projection_kind",
        "core_calculation_current_projections",
        type_="check",
    )
    op.create_check_constraint(
        "ck_core_current_projection_kind",
        "core_calculation_current_projections",
        _CORE_EVIDENCE_KINDS,
    )

    op.alter_column(
        "core_calculation_evidence_sources",
        "source_role",
        existing_type=sa.String(length=128),
        type_=sa.String(length=32),
        existing_nullable=False,
    )

    op.alter_column("core_calculation_evidence", "ticker", nullable=False)
    op.alter_column("core_calculation_evidence", "run_id", nullable=False)
    op.drop_constraint(
        "ck_core_calculation_evidence_kind",
        "core_calculation_evidence",
        type_="check",
    )
    op.create_check_constraint(
        "ck_core_calculation_evidence_kind",
        "core_calculation_evidence",
        _CORE_EVIDENCE_KINDS,
    )
