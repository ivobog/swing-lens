"""add Wave 4 evidence, price response, and derived feature lineage

Revision ID: 0029_ceri_wave4_evidence_features
Revises: 0028_ceri_observation_and_guidance_lineage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0029_ceri_wave4_evidence_features"
down_revision: str | None = "0028_ceri_observation_and_guidance_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_column(table: str, column: str) -> bool:
    return any(item["name"] == column for item in _inspector().get_columns(table))


def _has_table(table: str) -> bool:
    return table in _inspector().get_table_names()


def _has_index(table: str, index: str) -> bool:
    return any(item["name"] == index for item in _inspector().get_indexes(table))


_LICENSED_PROJECTION_KEYS = {
    "ticker",
    "symbol",
    "provider_company_id",
    "cik",
    "exchange",
    "currency",
    "metric",
    "period_type",
    "period_label",
    "fiscal_period_end",
    "fiscal_year",
    "source_currency",
    "canonical_currency",
    "source_scale",
    "canonical_scale",
    "fiscal_quarter",
    "consensus",
    "high",
    "low",
    "analyst_count",
    "upward_count",
    "downward_count",
    "actual",
    "actual_value",
    "estimate",
    "surprise_pct",
    "surprise_absolute",
    "earnings_date",
    "report_at",
    "report_session",
    "event_type",
    "category",
    "subtype",
    "subject_key",
    "status",
    "direction",
    "expected_date",
    "date_confidence",
    "source_confidence",
    "materiality",
    "action",
    "management_claim",
    "low_value",
    "high_value",
    "point_value",
    "unit",
    "comparison_basis",
    "confidence",
    "extraction_confidence",
    "comparison_confidence",
    "manual_review_required",
    "source_timestamp",
    "provider_observed_at",
    "observed_at",
    "effective_at",
    "effective_session",
    "trend_baseline_days",
    "trend_baseline_window_days",
    "baseline_origin",
    "current_observation_reference",
    "provider_record_id",
}


def _project_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        return {}
    return {key: payload[key] for key in _LICENSED_PROJECTION_KEYS if key in payload}


def _remediate_legacy_source_payloads() -> None:
    bind = op.get_bind()
    table = sa.table(
        "ceri_source_records",
        sa.column("id", sa.BigInteger()),
        sa.column("provider", sa.String()),
        sa.column("raw_json", JSONB()),
        sa.column("restricted_normalized_json", JSONB()),
        sa.column("payload_remediation_version", sa.Text()),
        sa.column("source_url", sa.Text()),
    )
    for row in bind.execute(
        sa.select(
            table.c.id,
            table.c.provider,
            table.c.raw_json,
            table.c.restricted_normalized_json,
        ).where(table.c.provider.in_(["eodhd", "sec"]))
    ).mappings():
        original = row["raw_json"] or row["restricted_normalized_json"] or {}
        bind.execute(
            table.update()
            .where(table.c.id == row["id"])
            .values(
                raw_json=None,
                restricted_normalized_json=_project_payload(original),
                payload_remediation_version="wave4-evidence-projection-v1",
                source_url=None,
            )
        )


def upgrade() -> None:
    for table, name, typ in (
        ("ceri_source_records", "source_timestamp", sa.DateTime(timezone=True)),
        ("ceri_source_records", "payload_remediation_version", sa.Text()),
        ("ceri_estimate_snapshots", "provider_observed_at", sa.DateTime(timezone=True)),
        ("ceri_estimate_snapshots", "source_timestamp", sa.DateTime(timezone=True)),
        ("ceri_estimate_snapshots", "trend_baseline_window_days", sa.Integer()),
        ("ceri_estimate_snapshots", "baseline_origin", sa.String(64)),
        ("ceri_estimate_snapshots", "current_observation_reference", sa.Text()),
        ("ceri_score_snapshots", "alignment_context_json", JSONB()),
        ("ceri_score_snapshots", "evidence_lineage_json", JSONB()),
    ):
        if not _has_column(table, name):
            op.add_column(table, sa.Column(name, typ, nullable=True))

    if not _has_table("ceri_price_response_features"):
        op.create_table(
            "ceri_price_response_features",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "company_id",
                sa.BigInteger(),
                sa.ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("ticker", sa.String(32), nullable=False),
            sa.Column("event_type", sa.String(64), nullable=False),
            sa.Column("event_id", sa.BigInteger()),
            sa.Column("event_effective_at", sa.DateTime(timezone=True)),
            sa.Column("event_effective_session", sa.Date()),
            sa.Column("reaction_session", sa.Date()),
            sa.Column("benchmark", sa.String(32)),
            sa.Column("metrics_json", JSONB()),
            sa.Column("reasons_json", JSONB()),
            sa.Column("warnings_json", JSONB()),
            sa.Column("price_bar_ids_json", JSONB()),
            sa.Column("evidence_hash", sa.Text(), nullable=False),
            sa.Column("event_key", sa.Text(), nullable=False),
            sa.Column("config_version", sa.Text(), nullable=False),
            sa.Column("config_hash", sa.Text(), nullable=False),
            sa.Column("calculation_version", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint("event_key", name="uq_ceri_price_response_event_key"),
        )
    if not _has_table("ceri_derived_features"):
        op.create_table(
            "ceri_derived_features",
            sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
            sa.Column(
                "company_id",
                sa.BigInteger(),
                sa.ForeignKey("ceri_companies.id", ondelete="RESTRICT"),
                nullable=False,
            ),
            sa.Column("feature_family", sa.String(64), nullable=False),
            sa.Column("feature_key", sa.Text(), nullable=False),
            sa.Column("as_of_session", sa.Date(), nullable=False),
            sa.Column("value_json", JSONB()),
            sa.Column("source_ids_json", JSONB()),
            sa.Column("evidence_hash", sa.Text(), nullable=False),
            sa.Column("config_version", sa.Text(), nullable=False),
            sa.Column("config_hash", sa.Text(), nullable=False),
            sa.Column("calculation_version", sa.Text(), nullable=False),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.UniqueConstraint(
                "company_id",
                "feature_family",
                "feature_key",
                "as_of_session",
                "config_hash",
                "calculation_version",
                name="uq_ceri_derived_features_identity",
            ),
        )
    for table, name, columns in (
        (
            "ceri_source_records",
            "ix_ceri_source_records_provider_source_timestamp",
            ["provider", "source_timestamp"],
        ),
        (
            "ceri_estimate_snapshots",
            "ix_ceri_estimates_trend_baseline_reference",
            ["current_observation_reference", "trend_baseline_window_days"],
        ),
        (
            "ceri_price_response_features",
            "ix_ceri_price_response_company_session",
            ["company_id", "reaction_session"],
        ),
        (
            "ceri_derived_features",
            "ix_ceri_derived_features_company_session",
            ["company_id", "as_of_session"],
        ),
    ):
        if _has_table(table) and not _has_index(table, name):
            op.create_index(name, table, columns)
    _remediate_legacy_source_payloads()


def downgrade() -> None:
    for table, name in (
        ("ceri_derived_features", "ix_ceri_derived_features_company_session"),
        ("ceri_price_response_features", "ix_ceri_price_response_company_session"),
        ("ceri_estimate_snapshots", "ix_ceri_estimates_trend_baseline_reference"),
        ("ceri_source_records", "ix_ceri_source_records_provider_source_timestamp"),
    ):
        if _has_table(table) and _has_index(table, name):
            op.drop_index(name, table_name=table)
    if _has_table("ceri_derived_features"):
        op.drop_table("ceri_derived_features")
    if _has_table("ceri_price_response_features"):
        op.drop_table("ceri_price_response_features")
    for table, name in (
        ("ceri_score_snapshots", "evidence_lineage_json"),
        ("ceri_score_snapshots", "alignment_context_json"),
        ("ceri_estimate_snapshots", "current_observation_reference"),
        ("ceri_estimate_snapshots", "baseline_origin"),
        ("ceri_estimate_snapshots", "trend_baseline_window_days"),
        ("ceri_estimate_snapshots", "source_timestamp"),
        ("ceri_estimate_snapshots", "provider_observed_at"),
        ("ceri_source_records", "source_timestamp"),
        ("ceri_source_records", "payload_remediation_version"),
    ):
        if _has_column(table, name):
            op.drop_column(table, name)
