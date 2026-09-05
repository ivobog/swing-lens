from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from app.db import Base
from app.models.tables import (
    BackgroundJob,
    WinnerEstimateEvidenceMember,
    WinnerForwardOutcome,
    WinnerModelVersion,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)

OWPE_TABLES = {
    "winner_prediction_snapshots",
    "winner_prediction_episodes",
    "winner_forward_outcomes",
    "winner_target_stop_outcomes",
    "winner_outcome_definitions",
    "winner_cohort_definitions",
    "winner_cohort_statistics",
    "winner_probability_estimates",
    "winner_estimate_evidence_members",
    "winner_evidence_manifests",
    "winner_model_versions",
    "winner_calibration_bins",
    "winner_drift_metrics",
    "winner_processing_runs",
    "winner_model_training_runs",
    "winner_model_lifecycle_events",
    "winner_similarity_links",
}


def test_winner_probability_metadata_includes_all_phase_2_tables() -> None:
    assert OWPE_TABLES.issubset(Base.metadata.tables)


def test_prediction_snapshot_table_includes_audit_and_filter_columns() -> None:
    table = Base.metadata.tables["winner_prediction_snapshots"]

    for column_name in [
        "prediction_as_of_date",
        "source_data_cutoff_at",
        "captured_at",
        "planned_entry_session",
        "entry_schedule_status",
        "entry_data_status",
        "eligibility_status",
        "run_id",
        "ticker",
        "setup_family",
        "setup_classification",
        "ranking_profile",
        "fundamental_score",
        "technical_score",
        "combined_score",
        "market_regime",
        "market_risk_state",
        "sector_state",
        "sector_rank",
        "suggested_target_pct",
        "suggested_stop_pct",
        "reward_risk",
        "upcoming_earnings_date",
        "days_until_earnings",
        "earnings_risk_level",
        "technical_data_quality",
        "fundamental_coverage",
        "universe_provenance",
        "screener_provenance",
        "feature_schema_version",
        "feature_vector_hash",
        "config_hash",
        "calculation_version",
        "feature_json",
        "source_ids_json",
        "warning_flags_json",
        "lineage_json",
        "reconstruction_method",
        "retention_class",
    ]:
        assert column_name in table.c


def test_prediction_snapshot_constraints_and_indexes_are_defined() -> None:
    table = WinnerPredictionSnapshot.__table__
    constraints = {constraint.name for constraint in table.constraints}
    indexes = {index.name for index in table.indexes}

    assert "uq_winner_prediction_snapshots_natural_revision" in constraints
    assert {
        "idx_winner_prediction_snapshots_run_ticker",
        "idx_winner_prediction_snapshots_ticker_as_of",
        "idx_winner_prediction_snapshots_eligibility",
        "idx_winner_prediction_snapshots_profile",
        "idx_winner_prediction_snapshots_regime_sector",
        "idx_winner_prediction_snapshots_earnings_quality",
        "idx_winner_prediction_snapshots_active_revision",
    }.issubset(indexes)

    active_index = next(
        index
        for index in table.indexes
        if index.name == "idx_winner_prediction_snapshots_active_revision"
    )
    assert active_index.unique
    assert active_index.dialect_options["postgresql"]["where"] is not None
    combined_result_fk = next(iter(table.c.combined_result_id.foreign_keys))
    assert combined_result_fk.ondelete == "SET NULL"


def test_outcome_tables_define_pending_and_revision_identities() -> None:
    forward_constraints = {
        constraint.name for constraint in WinnerForwardOutcome.__table__.constraints
    }
    forward_indexes = {index.name for index in WinnerForwardOutcome.__table__.indexes}
    target_constraints = {
        constraint.name for constraint in WinnerTargetStopOutcome.__table__.constraints
    }
    target_indexes = {index.name for index in WinnerTargetStopOutcome.__table__.indexes}

    assert "uq_winner_forward_outcomes_prediction_entry_horizon_revision" in forward_constraints
    assert {
        "idx_winner_forward_outcomes_status_due",
        "idx_winner_forward_outcomes_prediction_entry_horizon",
        "idx_winner_forward_outcomes_current_revision",
        "idx_winner_forward_outcomes_bar_lineage_current",
    }.issubset(forward_indexes)
    assert "uq_winner_target_stop_outcomes_prediction_definition_revision" in target_constraints
    assert {
        "idx_winner_target_stop_outcomes_status",
        "idx_winner_target_stop_outcomes_prediction_definition",
        "idx_winner_target_stop_outcomes_current_revision",
    }.issubset(target_indexes)


def test_background_jobs_define_winner_maturation_lineage_and_single_flight() -> None:
    table = BackgroundJob.__table__
    indexes = {index.name: index for index in table.indexes}

    assert {
        "root_job_id",
        "parent_job_id",
        "workflow_key",
        "continuation_depth",
        "trigger_source",
    }.issubset(table.c.keys())
    assert "idx_background_jobs_root_job_id" in indexes
    assert "idx_background_jobs_parent_job_id" in indexes
    single_flight = indexes["uq_background_jobs_active_winner_maturation_workflow"]
    assert single_flight.unique
    predicate = str(single_flight.dialect_options["postgresql"]["where"])
    assert "WINNER_OUTCOME_MATURATION" in predicate
    assert "RECOVERING" in predicate


def test_probability_estimate_and_evidence_membership_identities_are_defined() -> None:
    estimate_constraints = {
        constraint.name for constraint in WinnerProbabilityEstimate.__table__.constraints
    }
    estimate_indexes = {index.name for index in WinnerProbabilityEstimate.__table__.indexes}
    evidence_constraints = {
        constraint.name for constraint in WinnerEstimateEvidenceMember.__table__.constraints
    }
    evidence_indexes = {index.name for index in WinnerEstimateEvidenceMember.__table__.indexes}

    assert "uq_winner_probability_estimates_identity" in estimate_constraints
    assert {
        "idx_winner_probability_estimates_prediction_outcome_kind",
        "idx_winner_probability_estimates_model_created",
        "idx_winner_probability_estimates_probability",
        "idx_winner_probability_estimates_lower_bound",
        "idx_winner_probability_estimates_grade_n",
    }.issubset(estimate_indexes)
    assert "uq_winner_estimate_evidence_members_estimate_outcome_revision" in (evidence_constraints)
    assert {
        "idx_winner_estimate_evidence_members_estimate_outcome",
        "idx_winner_estimate_evidence_members_estimate_episode",
        "idx_winner_estimate_evidence_members_prediction_asof",
    }.issubset(evidence_indexes)


def test_model_artifact_metadata_is_required() -> None:
    table = WinnerModelVersion.__table__
    constraints = {constraint.name for constraint in table.constraints}

    for column_name in [
        "artifact_schema_version",
        "artifact_format",
        "artifact_hash",
        "artifact_size_bytes",
        "dependency_versions_json",
        "hyperparameters_json",
        "metrics_json",
        "preprocessing_json",
        "calibration_json",
    ]:
        assert column_name in table.c
    assert table.c.artifact_hash.nullable is False
    assert table.c.artifact_schema_version.nullable is False
    assert "ck_winner_model_versions_artifact_hash" in constraints
    assert "ck_winner_model_versions_schema_version" in constraints


def test_winner_probability_models_accept_representative_values() -> None:
    prediction = WinnerPredictionSnapshot(
        run_id=7,
        ticker="MSFT",
        prediction_as_of_date=date(2026, 7, 31),
        source_data_cutoff_at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
        entry_schedule_status="PLANNED",
        entry_data_status="PENDING",
        eligibility_status="ELIGIBLE",
        feature_schema_version="owpe-features-1.0.0",
        feature_vector_hash="feature-hash",
        config_hash="config-hash",
        calculation_version="owpe-calc-1.0.0",
        feature_json={"ticker": "MSFT"},
    )
    forward = WinnerForwardOutcome(
        prediction_id=1,
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        status="PENDING",
        entry_session=date(2026, 8, 3),
        due_session=date(2026, 8, 7),
    )
    estimate = WinnerProbabilityEstimate(
        prediction_id=1,
        outcome_definition_id=1,
        estimate_kind="DECISION_TIME",
        source="INSUFFICIENT",
        source_version="cohort-v1",
        training_cutoff_at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
        evidence_grade="Insufficient",
        config_hash="config-hash",
        feature_schema_version="owpe-features-1.0.0",
    )
    member = WinnerEstimateEvidenceMember(
        estimate_id=1,
        prediction_id=2,
        outcome_id=1,
        outcome_revision=1,
        inclusion_weight=Decimal("1.0"),
        included_as_of=datetime(2026, 7, 30, 21, 0, tzinfo=UTC),
        inclusion_cutoff_at=datetime(2026, 7, 31, 21, 0, tzinfo=UTC),
    )

    assert prediction.feature_json == {"ticker": "MSFT"}
    assert forward.status == "PENDING"
    assert estimate.evidence_grade == "Insufficient"
    assert member.inclusion_weight == Decimal("1.0")


def test_winner_probability_migration_follows_current_head() -> None:
    migration = Path("alembic/versions/20260731_0016_add_winner_probability_engine.py").read_text(
        encoding="utf-8"
    )

    assert 'revision: str = "0016_add_winner_probability_engine"' in migration
    assert 'down_revision: str | None = "0015_harden_job_leases"' in migration
    for table_name in sorted(OWPE_TABLES):
        assert table_name in migration
