"""Enforce explicit lineage for pipeline-owned CERI temporal artifacts.

Revision ID: 0072_ceri_artifact_context_lineage
Revises: 0071_transition_preflight_plan
Create Date: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0072_ceri_artifact_context_lineage"
down_revision: str | None = "0071_transition_preflight_plan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TEMPORAL_TABLES = (
    "ceri_revision_features",
    "ceri_derived_features",
    "ceri_feature_build_states",
)


def upgrade() -> None:
    for table in TEMPORAL_TABLES:
        if table != "ceri_derived_features":
            op.add_column(
                table,
                sa.Column("calculation_cutoff_at", sa.DateTime(timezone=True), nullable=True),
            )
            op.add_column(table, sa.Column("calendar_version", sa.Text(), nullable=True))
        op.add_column(table, sa.Column("calculation_context_id", sa.BigInteger(), nullable=True))
        op.add_column(
            table,
            sa.Column(
                "ownership_mode",
                sa.String(length=32),
                nullable=False,
                server_default="LEGACY_UNKNOWN",
            ),
        )
        op.create_foreign_key(
            f"fk_{table}_calculation_context",
            table,
            "market_calculation_contexts",
            ["calculation_context_id"],
            ["id"],
            ondelete="SET NULL",
        )
        op.create_check_constraint(
            f"ck_{table}_pipeline_context",
            table,
            "ownership_mode <> 'PIPELINE' OR "
            "(calculation_context_id IS NOT NULL AND calculation_cutoff_at IS NOT NULL "
            "AND calendar_version IS NOT NULL)",
        )
        op.create_check_constraint(
            f"ck_{table}_ownership_mode",
            table,
            "ownership_mode IN ('PIPELINE', 'STANDALONE', 'LEGACY_UNKNOWN')",
        )
        op.create_index(f"ix_{table}_context", table, ["calculation_context_id"])
        # The temporary default classifies only rows present at migration time.
        # Every future raw or ORM write must provide an explicit ownership mode.
        op.alter_column(table, "ownership_mode", server_default=None)

    op.add_column(
        "ceri_price_response_features",
        sa.Column(
            "ownership_mode",
            sa.String(length=32),
            nullable=False,
            server_default="LEGACY_UNKNOWN",
        ),
    )
    op.create_check_constraint(
        "ck_ceri_price_response_features_pipeline_context",
        "ceri_price_response_features",
        "ownership_mode <> 'PIPELINE' OR "
        "(calculation_context_id IS NOT NULL AND calculation_cutoff_at IS NOT NULL "
        "AND calendar_version IS NOT NULL)",
    )
    op.create_check_constraint(
        "ck_ceri_price_response_features_ownership_mode",
        "ceri_price_response_features",
        "ownership_mode IN ('PIPELINE', 'STANDALONE', 'LEGACY_UNKNOWN')",
    )
    op.alter_column("ceri_price_response_features", "ownership_mode", server_default=None)

    identities = {
        "ceri_revision_features": (
            "company_id",
            "metric",
            "period_key",
            "as_of_session",
            "window_days",
            "config_hash",
            "calculation_version",
            "ownership_mode",
            "calculation_context_id",
        ),
        "ceri_derived_features": (
            "company_id",
            "feature_family",
            "feature_key",
            "as_of_session",
            "config_hash",
            "calculation_version",
            "ownership_mode",
            "calculation_context_id",
        ),
        "ceri_feature_build_states": (
            "company_id",
            "as_of_session",
            "historical_view_mode",
            "config_hash",
            "calculation_version",
            "ownership_mode",
            "calculation_context_id",
        ),
    }
    for table, columns in identities.items():
        constraint = {
            "ceri_revision_features": "uq_ceri_revision_features_identity",
            "ceri_derived_features": "uq_ceri_derived_features_identity",
            "ceri_feature_build_states": "uq_ceri_feature_build_states_identity",
        }[table]
        op.drop_constraint(constraint, table, type_="unique")
        op.execute(
            sa.text(
                f"ALTER TABLE {table} ADD CONSTRAINT {constraint} "
                f"UNIQUE NULLS NOT DISTINCT ({', '.join(columns)})"
            )
        )


def downgrade() -> None:
    identities = {
        "ceri_revision_features": (
            "company_id",
            "metric",
            "period_key",
            "as_of_session",
            "window_days",
            "config_hash",
            "calculation_version",
        ),
        "ceri_derived_features": (
            "company_id",
            "feature_family",
            "feature_key",
            "as_of_session",
            "config_hash",
            "calculation_version",
        ),
        "ceri_feature_build_states": (
            "company_id",
            "as_of_session",
            "historical_view_mode",
            "config_hash",
            "calculation_version",
        ),
    }
    for table, columns in identities.items():
        constraint = {
            "ceri_revision_features": "uq_ceri_revision_features_identity",
            "ceri_derived_features": "uq_ceri_derived_features_identity",
            "ceri_feature_build_states": "uq_ceri_feature_build_states_identity",
        }[table]
        op.drop_constraint(constraint, table, type_="unique")
        op.create_unique_constraint(constraint, table, list(columns))

    op.execute(
        "ALTER TABLE ceri_price_response_features DROP CONSTRAINT IF EXISTS "
        "ck_ceri_price_response_features_ownership_mode"
    )
    op.drop_constraint(
        "ck_ceri_price_response_features_pipeline_context",
        "ceri_price_response_features",
        type_="check",
    )
    op.drop_column("ceri_price_response_features", "ownership_mode")
    for table in reversed(TEMPORAL_TABLES):
        op.drop_index(f"ix_{table}_context", table_name=table)
        op.execute(f"ALTER TABLE {table} DROP CONSTRAINT IF EXISTS ck_{table}_ownership_mode")
        op.drop_constraint(f"ck_{table}_pipeline_context", table, type_="check")
        op.drop_constraint(f"fk_{table}_calculation_context", table, type_="foreignkey")
        op.drop_column(table, "ownership_mode")
        op.drop_column(table, "calculation_context_id")
        if table != "ceri_derived_features":
            op.drop_column(table, "calendar_version")
            op.drop_column(table, "calculation_cutoff_at")
