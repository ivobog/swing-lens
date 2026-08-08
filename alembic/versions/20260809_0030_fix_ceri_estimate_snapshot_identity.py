"""fix CERI estimate snapshot identity

Revision ID: 0030_fix_ceri_estimate_snapshot_identity
Revises: 0029_ceri_wave4_evidence_features
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0030_fix_ceri_estimate_snapshot_identity"
down_revision: str | None = "0029_ceri_wave4_evidence_features"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A canonical observation can legitimately have several source records:
    # provider corrections, repeated observations, and competing providers are
    # append-only evidence. Retry idempotency is instead one normalized
    # snapshot per immutable source record.
    op.drop_constraint(
        "uq_ceri_estimate_snapshots_observation",
        "ceri_estimate_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ceri_estimate_snapshots_source_record",
        "ceri_estimate_snapshots",
        ["source_record_id"],
    )
    op.create_index(
        "ix_ceri_estimate_snapshots_canonical_observation",
        "ceri_estimate_snapshots",
        [
            "company_id",
            "metric",
            "period_type",
            "fiscal_period_end",
            "canonical_observation_key",
        ],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_estimate_snapshots_canonical_observation",
        table_name="ceri_estimate_snapshots",
    )
    op.drop_constraint(
        "uq_ceri_estimate_snapshots_source_record",
        "ceri_estimate_snapshots",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_ceri_estimate_snapshots_observation",
        "ceri_estimate_snapshots",
        [
            "company_id",
            "metric",
            "period_type",
            "fiscal_period_end",
            "canonical_observation_key",
        ],
    )
