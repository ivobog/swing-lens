from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy.dialects.postgresql import JSONB

from app.db import Base
from app.models.ceri_tables import (
    CERI_TABLES,
    CeriAlertEvent,
    CeriAlertRule,
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriCompany,
    CeriCompanyAlias,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriIngestionRun,
    CeriProcessingRun,
    CeriPurgeAudit,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)

CERI_TABLE_NAMES = {
    "ceri_companies",
    "ceri_company_aliases",
    "ceri_ingestion_runs",
    "ceri_source_records",
    "ceri_estimate_snapshots",
    "ceri_earnings_actuals",
    "ceri_guidance_events",
    "ceri_catalyst_events",
    "ceri_catalyst_sources",
    "ceri_revision_features",
    "ceri_score_snapshots",
    "ceri_change_events",
    "ceri_manual_reviews",
    "ceri_processing_runs",
    "ceri_catalyst_event_revisions",
    "ceri_alert_rules",
    "ceri_alert_events",
    "ceri_purge_audits",
}


def test_ceri_metadata_includes_all_phase_2_tables() -> None:
    assert CERI_TABLE_NAMES.issubset(Base.metadata.tables)
    assert {table.name for table in CERI_TABLES} == CERI_TABLE_NAMES


def test_source_record_preserves_provider_lineage_and_restrictions() -> None:
    table = CeriSourceRecord.__table__

    for column_name in [
        "ingestion_run_id",
        "provider",
        "provider_terms_version",
        "dataset",
        "provider_record_id",
        "company_hint_json",
        "published_at",
        "observed_at",
        "ingested_at",
        "source_url",
        "source_reference",
        "raw_json",
        "restricted_normalized_json",
        "content_hash",
        "idempotency_key",
        "export_policy",
        "provider_retention_deadline",
        "supersedes_id",
        "correction_type",
        "quarantine_reason",
    ]:
        assert column_name in table.c

    assert isinstance(table.c.raw_json.type, JSONB)
    assert isinstance(table.c.restricted_normalized_json.type, JSONB)
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "uq_ceri_source_records_provider_record" in constraint_names
    assert "uq_ceri_source_records_idempotency" in constraint_names


def test_ingestion_runs_preserve_audit_counts_and_checkpoints() -> None:
    table = CeriIngestionRun.__table__

    for column_name in [
        "provider",
        "provider_terms_version",
        "dataset",
        "scope_json",
        "status",
        "request_key",
        "config_version",
        "config_hash",
        "quota_state_json",
        "retry_count",
        "checkpoint_json",
        "requested_count",
        "fetched_count",
        "inserted_count",
        "deduplicated_count",
        "corrected_count",
        "quarantined_count",
        "failed_count",
        "warning_count",
        "errors_json",
        "warnings_json",
        "duration_ms",
        "started_at",
        "completed_at",
    ]:
        assert column_name in table.c

    assert isinstance(table.c.scope_json.type, JSONB)
    assert isinstance(table.c.quota_state_json.type, JSONB)
    assert isinstance(table.c.checkpoint_json.type, JSONB)
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "uq_ceri_ingestion_runs_request_key" in constraint_names


def test_estimate_snapshot_fields_keep_missing_values_nullable() -> None:
    table = CeriEstimateSnapshot.__table__

    for nullable_field in [
        "consensus",
        "high",
        "low",
        "analyst_count",
        "upward_count",
        "downward_count",
        "conversion_rate",
        "effective_at",
        "effective_session",
    ]:
        assert table.c[nullable_field].nullable is True

    for column_name in [
        "source_currency",
        "source_scale",
        "canonical_currency",
        "canonical_scale",
        "conversion_source_record_id",
        "conversion_effective_at",
        "canonical_observation_key",
        "original_fields_json",
        "quality_flags_json",
    ]:
        assert column_name in table.c

    assert isinstance(table.c.quality_flags_json.type, JSONB)
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "uq_ceri_estimate_snapshots_observation" in constraint_names


def test_revision_score_change_alert_and_purge_indexes_are_defined() -> None:
    revision_constraints = {
        constraint.name for constraint in CeriRevisionFeature.__table__.constraints
    }
    score_constraints = {constraint.name for constraint in CeriScoreSnapshot.__table__.constraints}
    change_constraints = {constraint.name for constraint in CeriChangeEvent.__table__.constraints}
    alert_rule_constraints = {constraint.name for constraint in CeriAlertRule.__table__.constraints}
    alert_event_constraints = {
        constraint.name for constraint in CeriAlertEvent.__table__.constraints
    }
    purge_constraints = {constraint.name for constraint in CeriPurgeAudit.__table__.constraints}
    catalyst_revision_indexes = {
        index.name: index for index in CeriCatalystEventRevision.__table__.indexes
    }

    assert "uq_ceri_revision_features_identity" in revision_constraints
    assert "uq_ceri_score_snapshots_run_company_version" in score_constraints
    assert "uq_ceri_change_events_dedup_key" in change_constraints
    assert "uq_ceri_alert_rules_rule_id" in alert_rule_constraints
    assert "uq_ceri_alert_events_event_key" in alert_event_constraints
    assert "uq_ceri_purge_audits_preview_scope" in purge_constraints
    assert "uq_ceri_catalyst_event_current_revision" in catalyst_revision_indexes
    assert catalyst_revision_indexes["uq_ceri_catalyst_event_current_revision"].unique
    assert (
        catalyst_revision_indexes["uq_ceri_catalyst_event_current_revision"]
        .dialect_options["postgresql"]["where"]
        is not None
    )


def test_revision_features_preserve_phase_5_lineage_fields() -> None:
    table = CeriRevisionFeature.__table__

    for column_name in [
        "source_observation_ids_json",
        "provider_selection_reason",
        "unavailable_reason",
        "evidence_hash",
    ]:
        assert column_name in table.c

    assert isinstance(table.c.source_observation_ids_json.type, JSONB)


def test_earnings_actuals_preserve_consensus_selection_reason() -> None:
    table = CeriEarningsActual.__table__

    assert "consensus_snapshot_id" in table.c
    assert "consensus_selection_reason" in table.c


def test_upload_run_fk_retains_ceri_score_snapshot_on_run_deletion() -> None:
    run_fk = next(
        fk
        for fk in CeriScoreSnapshot.__table__.c.run_id.foreign_keys
        if fk.column.table.name == "upload_runs"
    )

    assert run_fk.ondelete == "SET NULL"
    assert CeriScoreSnapshot.__table__.c.source_run_id_text.nullable is True


def test_processing_runs_preserve_checkpoint_and_execution_fencing_fields() -> None:
    table = CeriProcessingRun.__table__

    for column_name in [
        "job_type",
        "status",
        "deterministic_request_key",
        "scope_json",
        "config_version",
        "config_hash",
        "cutoff_at",
        "retry_count",
        "checkpoint_json",
        "heartbeat_at",
        "execution_token",
        "duration_ms",
    ]:
        assert column_name in table.c

    assert isinstance(table.c.checkpoint_json.type, JSONB)
    constraint_names = {constraint.name for constraint in table.constraints}
    assert "uq_ceri_processing_runs_request_key" in constraint_names


def test_ceri_models_accept_representative_values() -> None:
    company = CeriCompany(ticker="MSFT", exchange="NASDAQ", company_name="Microsoft")
    alias = CeriCompanyAlias(
        company_id=1,
        provider="manual",
        alias_type="ticker",
        alias_value="MSFT",
        confidence="High",
    )
    source = CeriSourceRecord(
        provider="manual",
        dataset="estimates",
        provider_record_id="manual-est-1",
        published_at=datetime(2026, 8, 1, 20, 15, tzinfo=UTC),
        observed_at=datetime(2026, 8, 1, 20, 15, tzinfo=UTC),
        content_hash="content-hash",
        idempotency_key="manual:estimates:manual-est-1",
        raw_json={"ticker": "MSFT"},
    )
    estimate = CeriEstimateSnapshot(
        source_record_id=1,
        company_id=1,
        metric="EPS_DILUTED",
        fiscal_period_end=date(2026, 12, 31),
        period_type="ANNUAL",
        consensus=Decimal("14.72"),
        analyst_count=None,
        canonical_observation_key="msft-eps-2026",
    )
    catalyst = CeriCatalystEvent(
        company_id=1,
        category="CONTRACT",
        subject_key="multi-year-contract",
    )
    score = CeriScoreSnapshot(
        run_id=None,
        source_run_id_text="run-7",
        company_id=1,
        ticker="MSFT",
        as_of_session=date(2026, 8, 1),
        cutoff_at=datetime(2026, 8, 1, 20, 15, tzinfo=UTC),
        opportunity_score=8.2,
        event_risk_score=3.1,
        data_confidence="High",
        coverage_pct=91.0,
        posture="Improving",
        config_version="2026-07-31",
        config_hash="hash",
        calculation_version="ceri-1.0.0",
        evidence_hash="evidence",
    )

    assert company.ticker == "MSFT"
    assert alias.confidence == "High"
    assert source.raw_json == {"ticker": "MSFT"}
    assert estimate.analyst_count is None
    assert catalyst.subject_key == "multi-year-contract"
    assert score.source_run_id_text == "run-7"


def test_ceri_migration_follows_current_head_and_lists_tables() -> None:
    migration = Path("alembic/versions/20260801_0018_add_ceri_tables.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "0018_add_ceri_tables"' in migration
    assert 'down_revision: str | None = "0017_create_setup_lifecycle_tables"' in migration
    assert "CERI_TABLE_NAMES" in migration


def test_ceri_ingestion_audit_migration_follows_ceri_schema_head() -> None:
    migration = Path(
        "alembic/versions/20260801_0019_add_ceri_ingestion_audit_fields.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0019_add_ceri_ingestion_audit_fields"' in migration
    assert 'down_revision: str | None = "0018_add_ceri_tables"' in migration
    assert "retry_count" in migration
    assert "checkpoint_json" in migration
    assert "duration_ms" in migration
    assert "_add_column_if_missing" in migration


def test_ceri_revision_feature_lineage_migration_follows_ingestion_audit_head() -> None:
    migration = Path(
        "alembic/versions/20260801_0020_add_ceri_revision_feature_lineage.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0020_add_ceri_revision_feature_lineage"' in migration
    assert 'down_revision: str | None = "0019_add_ceri_ingestion_audit_fields"' in migration
    assert "source_observation_ids_json" in migration
    assert "evidence_hash" in migration
    assert "_add_column_if_missing" in migration


def test_ceri_earnings_consensus_reason_migration_follows_revision_feature_head() -> None:
    migration = Path(
        "alembic/versions/20260801_0021_add_ceri_earnings_consensus_reason.py"
    ).read_text(encoding="utf-8")

    assert 'revision: str = "0021_add_ceri_earnings_consensus_reason"' in migration
    assert 'down_revision: str | None = "0020_add_ceri_revision_feature_lineage"' in migration
    assert "consensus_selection_reason" in migration
    assert "_add_column_if_missing" in migration
