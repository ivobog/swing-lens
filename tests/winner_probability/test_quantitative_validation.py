from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.models.tables import WinnerModelTrainingRun
from app.services.winner_probability.model_training import (
    GRADIENT_BOOSTED_TREES_ALGORITHM,
    ShadowModelTrainingService,
    ShadowTrainingExample,
)


def test_walk_forward_folds_never_leak_future_or_same_episode_groups() -> None:
    examples = tuple(
        ShadowTrainingExample(
            feature_vector={"combined_score": index},
            label=index >= 4,
            cutoff_at=_cutoff(index),
            episode_id=index,
        )
        for index in range(8)
    )

    folds = ShadowModelTrainingService().build_walk_forward_folds(
        examples,
        fold_count=3,
        min_train_groups=2,
    )

    assert folds
    for fold in folds:
        assert fold.training_cutoff_at < fold.test_start_at
        assert set(fold.train_episode_ids).isdisjoint(fold.test_episode_ids)
        assert all(examples[index].cutoff_at < fold.test_start_at for index in fold.train_indices)


def test_shadow_candidate_report_compares_model_against_baselines() -> None:
    examples = tuple(
        ShadowTrainingExample(
            feature_vector={
                "combined_score": Decimal(str(index)),
                "setup_family": "Breakout" if index % 2 == 0 else "Pullback",
            },
            label=index >= 6,
            cutoff_at=_cutoff(index),
            episode_id=index,
        )
        for index in range(12)
    )

    report = ShadowModelTrainingService().train_shadow_report(
        examples,
        feature_names=("combined_score", "setup_family"),
        outcome_definition_id=4,
        training_cutoff_at=_cutoff(13),
        fold_count=3,
        cohort_baseline_probability=0.5,
    )

    assert report.algorithm == "regularized_logistic_regression"
    assert report.feature_order == ("combined_score", "setup_family")
    assert report.candidate_artifact_hash
    assert report.metrics["sample_n"] == 12
    assert report.metrics["fold_count"] == len(report.fold_plan)
    assert "model_log_loss" in report.metrics
    assert "model_brier_score" in report.metrics
    assert "global_baseline_log_loss" in report.metrics
    assert "global_baseline_brier_score" in report.metrics
    assert "cohort_baseline_log_loss" in report.metrics
    assert "cohort_baseline_brier_score" in report.metrics
    assert report.metrics["independent_episode_count"] == 12
    assert report.artifact_payload["preprocessing"]["feature_order"] == [
        "combined_score",
        "setup_family",
    ]


def test_shadow_training_run_is_persisted_with_fold_artifacts_and_hash() -> None:
    db = TrainingFakeDb()
    examples = tuple(
        ShadowTrainingExample(
            feature_vector={"combined_score": Decimal(str(index))},
            label=index >= 4,
            cutoff_at=_cutoff(index),
            episode_id=index,
        )
        for index in range(8)
    )
    service = ShadowModelTrainingService()
    report = service.train_shadow_report(
        examples,
        feature_names=("combined_score",),
        outcome_definition_id=4,
        training_cutoff_at=_cutoff(10),
        fold_count=2,
    )

    row = service.persist_training_run(
        db,
        report=report,
        outcome_definition_id=4,
        training_cutoff_at=_cutoff(10),
    )

    assert isinstance(row, WinnerModelTrainingRun)
    assert row.status == "COMPLETED"
    assert row.algorithm == "regularized_logistic_regression"
    assert row.artifact_hash == report.candidate_artifact_hash
    assert row.fold_plan_json["folds"]
    assert row.preprocessing_json["feature_order"] == ["combined_score"]
    assert db.flushes == 1


def test_unapproved_gradient_boosted_trees_are_not_trained() -> None:
    with pytest.raises(ValueError, match="not approved"):
        ShadowModelTrainingService().train_shadow_report(
            (
                ShadowTrainingExample(
                    feature_vector={"combined_score": Decimal("5")},
                    label=True,
                    cutoff_at=_cutoff(1),
                    episode_id=1,
                ),
            ),
            feature_names=("combined_score",),
            outcome_definition_id=4,
            training_cutoff_at=_cutoff(2),
            algorithm=GRADIENT_BOOSTED_TREES_ALGORITHM,
        )


class TrainingFakeDb:
    def __init__(self) -> None:
        self.rows = []
        self.flushes = 0
        self._next_id = 1

    def add(self, row) -> None:
        if getattr(row, "id", None) is None:
            row.id = self._next_id
            self._next_id += 1
        self.rows.append(row)

    def flush(self) -> None:
        self.flushes += 1


def _cutoff(index: int) -> datetime:
    return datetime(2026, 7, 1, 21, 0, tzinfo=UTC) + timedelta(days=index)
