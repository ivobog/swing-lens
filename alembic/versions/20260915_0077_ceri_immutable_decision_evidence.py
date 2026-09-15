"""Extend shared immutable evidence to CERI decisions.

Revision ID: 0077_ceri_decision_evidence
Revises: 0076_regime_sector_evidence
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0077_ceri_decision_evidence"
down_revision: str | None = "0076_regime_sector_evidence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_EVIDENCE_KINDS = (
    "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING', "
    "'REGIME', 'SECTOR', 'CERI')"
)
_PREVIOUS_EVIDENCE_KINDS = (
    "artifact_kind IN ('FUNDAMENTAL', 'TECHNICAL', 'COMBINED', 'RANKING', 'REGIME', 'SECTOR')"
)
_REMOVE_CERI_EVIDENCE = sa.text(
    """
    WITH RECURSIVE doomed(id) AS (
        SELECT id FROM core_calculation_evidence WHERE artifact_kind = 'CERI'
        UNION
        SELECT edge.evidence_id
        FROM core_calculation_evidence_sources AS edge
        JOIN doomed ON edge.source_evidence_id = doomed.id
    ),
    deleted_projections AS (
        DELETE FROM core_calculation_current_projections
        WHERE evidence_id IN (SELECT id FROM doomed)
    ),
    deleted_edges AS (
        DELETE FROM core_calculation_evidence_sources
        WHERE evidence_id IN (SELECT id FROM doomed)
           OR source_evidence_id IN (SELECT id FROM doomed)
    )
    DELETE FROM core_calculation_evidence
    WHERE id IN (SELECT id FROM doomed)
    """
)


def upgrade() -> None:
    for table_name, constraint_name in (
        ("core_calculation_evidence", "ck_core_calculation_evidence_kind"),
        ("core_calculation_current_projections", "ck_core_current_projection_kind"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(constraint_name, table_name, _EVIDENCE_KINDS)

    op.create_index(
        "uq_core_current_projection_global_ticker_scope",
        "core_calculation_current_projections",
        ["artifact_kind", "ticker", "ranking_profile_key"],
        unique=True,
        postgresql_where=sa.text("run_id IS NULL AND ticker IS NOT NULL"),
    )
    op.add_column("ceri_score_snapshots", sa.Column("evidence_id", sa.BigInteger(), nullable=True))
    op.create_foreign_key(
        "fk_ceri_score_snapshots_evidence",
        "ceri_score_snapshots",
        "core_calculation_evidence",
        ["evidence_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_ceri_score_snapshots_evidence", "ceri_score_snapshots", ["evidence_id"])


def downgrade() -> None:
    op.drop_index("ix_ceri_score_snapshots_evidence", table_name="ceri_score_snapshots")
    op.drop_constraint(
        "fk_ceri_score_snapshots_evidence",
        "ceri_score_snapshots",
        type_="foreignkey",
    )
    op.drop_column("ceri_score_snapshots", "evidence_id")
    op.execute(_REMOVE_CERI_EVIDENCE)
    op.drop_index(
        "uq_core_current_projection_global_ticker_scope",
        table_name="core_calculation_current_projections",
    )

    for table_name, constraint_name in (
        ("core_calculation_current_projections", "ck_core_current_projection_kind"),
        ("core_calculation_evidence", "ck_core_calculation_evidence_kind"),
    ):
        op.drop_constraint(constraint_name, table_name, type_="check")
        op.create_check_constraint(constraint_name, table_name, _PREVIOUS_EVIDENCE_KINDS)
