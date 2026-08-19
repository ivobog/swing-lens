"""add bounded SEC guidance normalization lookup indexes

Revision ID: 0048_sec_guidance_normalization_performance
Revises: 0047_ceri_feature_rebuild_performance
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0048_sec_guidance_normalization_performance"
down_revision: str | None = "0047_ceri_feature_rebuild_performance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_ceri_guidance_events_prior_lookup",
        "ceri_guidance_events",
        ["company_id", "metric", "period_type", "effective_at", "id"],
    )
    op.create_index(
        "ix_ceri_source_records_ingestion_id",
        "ceri_source_records",
        ["ingestion_run_id", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_source_records_ingestion_id",
        table_name="ceri_source_records",
    )
    op.drop_index(
        "ix_ceri_guidance_events_prior_lookup",
        table_name="ceri_guidance_events",
    )
