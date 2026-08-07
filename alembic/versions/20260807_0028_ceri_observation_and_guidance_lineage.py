"""add CERI retrieval timestamp and structured guidance values

Revision ID: 0028_ceri_observation_and_guidance_lineage
Revises: 0027_ceri_provider_policy_telemetry
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028_ceri_observation_and_guidance_lineage"
down_revision: str | None = "0027_ceri_provider_policy_telemetry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    if not _has_column("ceri_source_records", "retrieved_at"):
        op.add_column(
            "ceri_source_records",
            sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=True),
        )
    guidance_columns = (
        ("point_value", sa.Numeric(20, 6)),
        ("unit", sa.String(32)),
        ("currency", sa.String(16)),
        ("evidence_locator", sa.Text()),
        ("filing_accession", sa.String(64)),
    )
    for name, column_type in guidance_columns:
        if not _has_column("ceri_guidance_events", name):
            op.add_column("ceri_guidance_events", sa.Column(name, column_type, nullable=True))

    op.create_index(
        "ix_ceri_source_records_provider_retrieved",
        "ceri_source_records",
        ["provider", "retrieved_at"],
    )
    op.create_index(
        "ix_ceri_guidance_events_source_accession",
        "ceri_guidance_events",
        ["source_record_id", "filing_accession"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_guidance_events_source_accession", table_name="ceri_guidance_events"
    )
    op.drop_index(
        "ix_ceri_source_records_provider_retrieved", table_name="ceri_source_records"
    )
    for name in ("filing_accession", "evidence_locator", "currency", "unit", "point_value"):
        if _has_column("ceri_guidance_events", name):
            op.drop_column("ceri_guidance_events", name)
    if _has_column("ceri_source_records", "retrieved_at"):
        op.drop_column("ceri_source_records", "retrieved_at")
