"""add winner probability engine

Revision ID: 0016_add_winner_probability_engine
Revises: 0015_harden_job_leases
Create Date: 2026-07-31 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.models.tables import (
    WinnerCalibrationBin,
    WinnerCohortDefinition,
    WinnerCohortStatistic,
    WinnerDriftMetric,
    WinnerEstimateEvidenceMember,
    WinnerEvidenceManifest,
    WinnerForwardOutcome,
    WinnerModelLifecycleEvent,
    WinnerModelTrainingRun,
    WinnerModelVersion,
    WinnerOutcomeDefinition,
    WinnerPredictionEpisode,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerProcessingRun,
    WinnerSimilarityLink,
    WinnerTargetStopOutcome,
)

revision: str = "0016_add_winner_probability_engine"
down_revision: str | None = "0015_harden_job_leases"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


OWPE_TABLES = (
    WinnerPredictionEpisode.__table__,
    WinnerOutcomeDefinition.__table__,
    WinnerPredictionSnapshot.__table__,
    WinnerForwardOutcome.__table__,
    WinnerTargetStopOutcome.__table__,
    WinnerCohortDefinition.__table__,
    WinnerCohortStatistic.__table__,
    WinnerEvidenceManifest.__table__,
    WinnerModelVersion.__table__,
    WinnerProbabilityEstimate.__table__,
    WinnerEstimateEvidenceMember.__table__,
    WinnerCalibrationBin.__table__,
    WinnerDriftMetric.__table__,
    WinnerProcessingRun.__table__,
    WinnerModelTrainingRun.__table__,
    WinnerModelLifecycleEvent.__table__,
    WinnerSimilarityLink.__table__,
)

OWPE_TABLE_NAMES = (
    "winner_prediction_episodes",
    "winner_outcome_definitions",
    "winner_prediction_snapshots",
    "winner_forward_outcomes",
    "winner_target_stop_outcomes",
    "winner_cohort_definitions",
    "winner_cohort_statistics",
    "winner_evidence_manifests",
    "winner_model_versions",
    "winner_probability_estimates",
    "winner_estimate_evidence_members",
    "winner_calibration_bins",
    "winner_drift_metrics",
    "winner_processing_runs",
    "winner_model_training_runs",
    "winner_model_lifecycle_events",
    "winner_similarity_links",
)


def upgrade() -> None:
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    bind = op.get_bind()
    for table in OWPE_TABLES:
        table.create(bind=bind, checkfirst=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(OWPE_TABLES):
        table.drop(bind=bind, checkfirst=False)
