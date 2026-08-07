"""add provider-aware CERI license metadata and request telemetry

Revision ID: 0027_ceri_provider_policy_telemetry
Revises: 0026_technical_artifact_cache
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_ceri_provider_policy_telemetry"
down_revision: str | None = "0026_technical_artifact_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in sa.inspect(op.get_bind()).get_columns(table))


def upgrade() -> None:
    for column in (
        sa.Column("normalized_hash", sa.String(length=128), nullable=True),
        sa.Column("license_scope", sa.Text(), nullable=True),
        sa.Column(
            "redistribution_allowed", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("purge_eligible", sa.Boolean(), nullable=False, server_default=sa.false()),
    ):
        if not _has_column("ceri_source_records", column.name):
            op.add_column("ceri_source_records", column)
    op.alter_column("ceri_source_records", "redistribution_allowed", server_default=None)
    op.alter_column("ceri_source_records", "purge_eligible", server_default=None)
    op.create_table(
        "ceri_provider_request_telemetry",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=True),
        sa.Column("endpoint", sa.String(length=128), nullable=False),
        sa.Column("request_key", sa.Text(), nullable=True),
        sa.Column("scope_hash", sa.String(length=128), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("call_cost", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.alter_column("ceri_provider_request_telemetry", "call_cost", server_default=None)
    op.alter_column("ceri_provider_request_telemetry", "retry_count", server_default=None)
    op.create_index(
        "ix_ceri_provider_telemetry_provider_observed",
        "ceri_provider_request_telemetry",
        ["provider", "observed_at"],
    )
    op.create_index(
        "ix_ceri_provider_telemetry_endpoint_observed",
        "ceri_provider_request_telemetry",
        ["endpoint", "observed_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ceri_provider_telemetry_endpoint_observed", table_name="ceri_provider_request_telemetry"
    )
    op.drop_index(
        "ix_ceri_provider_telemetry_provider_observed", table_name="ceri_provider_request_telemetry"
    )
    op.drop_table("ceri_provider_request_telemetry")
    for column in (
        "purge_eligible",
        "redistribution_allowed",
        "license_scope",
        "normalized_hash",
    ):
        if _has_column("ceri_source_records", column):
            op.drop_column("ceri_source_records", column)
