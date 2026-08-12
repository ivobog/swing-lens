"""add additive CERI remediation evidence and ledger fields

Revision ID: 0040_ceri_remediation_ledgers
Revises: 0039_ib_fetch_decision_telemetry
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040_ceri_remediation_ledgers"
down_revision: str | None = "0039_ib_fetch_decision_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    estimate_columns = (
        sa.Column("canonical_period_slot", sa.String(64), nullable=True),
        sa.Column("currency_basis", sa.Text(), nullable=True),
        sa.Column("currency_verified", sa.Boolean(), nullable=True),
        sa.Column("reference_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("known_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("normalization_version", sa.Text(), nullable=True),
    )
    for column in estimate_columns:
        op.add_column("ceri_estimate_snapshots", column)
    op.create_index(
        "ix_ceri_estimates_period_slot_known",
        "ceri_estimate_snapshots",
        ["company_id", "metric", "canonical_period_slot", "known_at"],
    )

    op.add_column(
        "ceri_earnings_actuals",
        sa.Column("provider_consensus_value", sa.Numeric(20, 6), nullable=True),
    )
    op.add_column(
        "ceri_earnings_actuals",
        sa.Column("provider_surprise_pct", sa.Numeric(20, 6), nullable=True),
    )

    for column in (
        sa.Column("accepted_for_scoring", sa.Boolean(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("normalization_version", sa.Text(), nullable=True),
    ):
        op.add_column("ceri_guidance_events", column)

    for column in (
        sa.Column("issuer_relevance", sa.Boolean(), nullable=True),
        sa.Column("relevance_reason", sa.Text(), nullable=True),
        sa.Column("binary_eligible", sa.Boolean(), nullable=True),
    ):
        op.add_column("ceri_catalyst_event_revisions", column)

    for column in (
        sa.Column("period_slot", sa.String(64), nullable=True),
        sa.Column("pct_change_unit", sa.String(32), nullable=True),
        sa.Column("acceleration_unit", sa.String(64), nullable=True),
        sa.Column("baseline_origin", sa.String(64), nullable=True),
    ):
        op.add_column("ceri_revision_features", column)

    for column in (
        sa.Column("opportunity_coverage_pct", sa.Float(), nullable=True),
        sa.Column("opportunity_unrated_reason", sa.Text(), nullable=True),
        sa.Column("opportunity_ledger_json", postgresql.JSONB(), nullable=True),
        sa.Column("confidence_ledger_json", postgresql.JSONB(), nullable=True),
        sa.Column("event_risk_ledger_json", postgresql.JSONB(), nullable=True),
        sa.Column("hash_schema_version", sa.Text(), nullable=True),
    ):
        op.add_column("ceri_score_snapshots", column)


def downgrade() -> None:
    for column in (
        "hash_schema_version",
        "event_risk_ledger_json",
        "confidence_ledger_json",
        "opportunity_ledger_json",
        "opportunity_unrated_reason",
        "opportunity_coverage_pct",
    ):
        op.drop_column("ceri_score_snapshots", column)
    for column in ("baseline_origin", "acceleration_unit", "pct_change_unit", "period_slot"):
        op.drop_column("ceri_revision_features", column)
    for column in ("binary_eligible", "relevance_reason", "issuer_relevance"):
        op.drop_column("ceri_catalyst_event_revisions", column)
    for column in ("normalization_version", "rejection_reason", "accepted_for_scoring"):
        op.drop_column("ceri_guidance_events", column)
    op.drop_column("ceri_earnings_actuals", "provider_surprise_pct")
    op.drop_column("ceri_earnings_actuals", "provider_consensus_value")
    op.drop_index("ix_ceri_estimates_period_slot_known", table_name="ceri_estimate_snapshots")
    for column in (
        "normalization_version",
        "retrieved_at",
        "known_at",
        "reference_at",
        "currency_verified",
        "currency_basis",
        "canonical_period_slot",
    ):
        op.drop_column("ceri_estimate_snapshots", column)
