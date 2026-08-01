from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import numpy as np
from sqlalchemy.orm import Session

from app.models.tables import ProcessingStatus, WinnerModelTrainingRun
from app.services.winner_probability.config import (
    WinnerProbabilityConfig,
    load_winner_probability_config,
)
from app.services.winner_probability.model_artifact_service import artifact_hash

SHADOW_LOGISTIC_ALGORITHM = "regularized_logistic_regression"
GRADIENT_BOOSTED_TREES_ALGORITHM = "gradient_boosted_trees"
APPROVED_SHADOW_ALGORITHMS = frozenset({SHADOW_LOGISTIC_ALGORITHM})


@dataclass(frozen=True)
class ShadowTrainingExample:
    feature_vector: dict[str, Any]
    label: bool
    cutoff_at: datetime
    episode_id: int | None = None
    weight: Decimal = Decimal("1")


@dataclass(frozen=True)
class WalkForwardFold:
    fold_index: int
    train_indices: tuple[int, ...]
    test_indices: tuple[int, ...]
    training_cutoff_at: datetime
    test_start_at: datetime
    test_end_at: datetime
    train_episode_ids: tuple[int, ...]
    test_episode_ids: tuple[int, ...]


@dataclass(frozen=True)
class ShadowTrainingReport:
    algorithm: str
    feature_schema_version: str
    feature_order: tuple[str, ...]
    fold_plan: tuple[WalkForwardFold, ...]
    preprocessing: dict[str, Any]
    metrics: dict[str, Any]
    warnings: tuple[str, ...]
    candidate_artifact_hash: str
    artifact_payload: dict[str, Any]


class ShadowModelTrainingService:
    def build_walk_forward_folds(
        self,
        examples: tuple[ShadowTrainingExample, ...],
        *,
        fold_count: int = 3,
        min_train_groups: int = 1,
    ) -> tuple[WalkForwardFold, ...]:
        if fold_count <= 0:
            raise ValueError("fold_count must be positive")
        ordered = _ordered_examples(examples)
        groups = _episode_groups(ordered)
        if len(groups) <= min_train_groups:
            return ()
        test_groups = groups[min_train_groups:]
        chunks = _chunks(test_groups, min(fold_count, len(test_groups)))
        folds: list[WalkForwardFold] = []
        for fold_index, chunk in enumerate(chunks, start=1):
            test_episode_keys = {group.episode_key for group in chunk}
            test_indices = tuple(
                index
                for group in chunk
                for index in group.example_indices
            )
            test_start = min(ordered[index].cutoff_at for index in test_indices)
            train_indices = tuple(
                index
                for index, example in enumerate(ordered)
                if example.cutoff_at < test_start
                and _episode_key(example, index) not in test_episode_keys
            )
            if not train_indices or not test_indices:
                continue
            folds.append(
                WalkForwardFold(
                    fold_index=fold_index,
                    train_indices=train_indices,
                    test_indices=test_indices,
                    training_cutoff_at=max(ordered[index].cutoff_at for index in train_indices),
                    test_start_at=test_start,
                    test_end_at=max(ordered[index].cutoff_at for index in test_indices),
                    train_episode_ids=_episode_ids(ordered, train_indices),
                    test_episode_ids=_episode_ids(ordered, test_indices),
                )
            )
        return tuple(folds)

    def train_shadow_report(
        self,
        examples: tuple[ShadowTrainingExample, ...],
        *,
        feature_names: tuple[str, ...],
        outcome_definition_id: int,
        training_cutoff_at: datetime,
        algorithm: str = SHADOW_LOGISTIC_ALGORITHM,
        fold_count: int = 3,
        regularization_strength: float = 1.0,
        learning_rate: float = 0.2,
        iterations: int = 400,
        cohort_baseline_probability: float | None = None,
        config: WinnerProbabilityConfig | None = None,
    ) -> ShadowTrainingReport:
        if algorithm not in APPROVED_SHADOW_ALGORITHMS:
            raise ValueError(f"{algorithm} is not approved for shadow training")
        if not feature_names:
            raise ValueError("feature_names must not be empty")
        config = config or load_winner_probability_config()
        eligible = tuple(example for example in examples if example.cutoff_at < training_cutoff_at)
        folds = self.build_walk_forward_folds(eligible, fold_count=fold_count)
        warnings: list[str] = []
        if not folds:
            warnings.append("insufficient_walk_forward_folds")

        fold_metrics = [
            _fit_and_score_fold(
                eligible,
                fold,
                feature_names,
                regularization_strength=regularization_strength,
                learning_rate=learning_rate,
                iterations=iterations,
                cohort_baseline_probability=cohort_baseline_probability,
            )
            for fold in folds
        ]
        final_preprocessing = _fit_preprocessor(eligible, feature_names)
        final_x = _transform_examples(eligible, final_preprocessing)
        final_y = np.array([1.0 if example.label else 0.0 for example in eligible])
        coefficients, intercept = _fit_logistic_regression(
            final_x,
            final_y,
            regularization_strength=regularization_strength,
            learning_rate=learning_rate,
            iterations=iterations,
        )
        metrics = _aggregate_metrics(fold_metrics, eligible, cohort_baseline_probability)
        artifact_payload = {
            "algorithm": algorithm,
            "outcome_definition_id": outcome_definition_id,
            "feature_schema_version": config.feature_schema.version,
            "training_cutoff_at": training_cutoff_at.isoformat(),
            "feature_order": list(feature_names),
            "coefficient_order": final_preprocessing["output_columns"],
            "coefficients": [round(float(value), 10) for value in coefficients],
            "intercept": round(float(intercept), 10),
            "regularization_strength": regularization_strength,
            "preprocessing": final_preprocessing,
            "fold_metrics": fold_metrics,
        }
        return ShadowTrainingReport(
            algorithm=algorithm,
            feature_schema_version=config.feature_schema.version,
            feature_order=feature_names,
            fold_plan=folds,
            preprocessing=final_preprocessing,
            metrics=metrics,
            warnings=tuple(warnings),
            candidate_artifact_hash=artifact_hash(artifact_payload),
            artifact_payload=artifact_payload,
        )

    def persist_training_run(
        self,
        db: Session,
        *,
        report: ShadowTrainingReport,
        outcome_definition_id: int,
        training_cutoff_at: datetime,
        background_job_id: int | None = None,
        candidate_model_version_id: int | None = None,
    ) -> WinnerModelTrainingRun:
        row = WinnerModelTrainingRun(
            background_job_id=background_job_id,
            candidate_model_version_id=candidate_model_version_id,
            outcome_definition_id=outcome_definition_id,
            status=ProcessingStatus.COMPLETED,
            algorithm=report.algorithm,
            feature_schema_version=report.feature_schema_version,
            training_cutoff_at=training_cutoff_at,
            fold_plan_json={
                "folds": [
                    {
                        "fold_index": fold.fold_index,
                        "train_indices": list(fold.train_indices),
                        "test_indices": list(fold.test_indices),
                        "training_cutoff_at": fold.training_cutoff_at.isoformat(),
                        "test_start_at": fold.test_start_at.isoformat(),
                        "test_end_at": fold.test_end_at.isoformat(),
                        "train_episode_ids": list(fold.train_episode_ids),
                        "test_episode_ids": list(fold.test_episode_ids),
                    }
                    for fold in report.fold_plan
                ]
            },
            preprocessing_json=report.preprocessing,
            metrics_json=report.metrics,
            warnings_json=list(report.warnings),
            artifact_hash=report.candidate_artifact_hash,
            started_at=training_cutoff_at,
            completed_at=_utcnow(),
        )
        db.add(row)
        db.flush()
        return row


@dataclass(frozen=True)
class _EpisodeGroup:
    episode_key: tuple[str, int]
    example_indices: tuple[int, ...]


def _fit_and_score_fold(
    examples: tuple[ShadowTrainingExample, ...],
    fold: WalkForwardFold,
    feature_names: tuple[str, ...],
    *,
    regularization_strength: float,
    learning_rate: float,
    iterations: int,
    cohort_baseline_probability: float | None,
) -> dict[str, Any]:
    train = tuple(examples[index] for index in fold.train_indices)
    test = tuple(examples[index] for index in fold.test_indices)
    preprocessing = _fit_preprocessor(train, feature_names)
    x_train = _transform_examples(train, preprocessing)
    y_train = np.array([1.0 if example.label else 0.0 for example in train])
    x_test = _transform_examples(test, preprocessing)
    y_test = np.array([1.0 if example.label else 0.0 for example in test])
    coefficients, intercept = _fit_logistic_regression(
        x_train,
        y_train,
        regularization_strength=regularization_strength,
        learning_rate=learning_rate,
        iterations=iterations,
    )
    predictions = _predict(x_test, coefficients, intercept)
    global_baseline = _bounded_probability(float(np.mean(y_train)))
    cohort_baseline = _bounded_probability(cohort_baseline_probability or global_baseline)
    return {
        "fold_index": fold.fold_index,
        "train_n": len(train),
        "test_n": len(test),
        "model_log_loss": _log_loss(y_test, predictions),
        "model_brier_score": _brier_score(y_test, predictions),
        "global_baseline_log_loss": _baseline_log_loss(y_test, global_baseline),
        "cohort_baseline_log_loss": _baseline_log_loss(y_test, cohort_baseline),
    }


def _fit_preprocessor(
    examples: tuple[ShadowTrainingExample, ...],
    feature_names: tuple[str, ...],
) -> dict[str, Any]:
    numeric_features: list[str] = []
    categorical_features: dict[str, list[str]] = {}
    numeric_stats: dict[str, dict[str, float]] = {}

    for feature_name in feature_names:
        values = [example.feature_vector.get(feature_name) for example in examples]
        non_missing = [value for value in values if not _missing(value)]
        if non_missing and all(_is_number(value) for value in non_missing):
            numeric_features.append(feature_name)
            numeric_values = np.array([float(value) for value in non_missing], dtype=float)
            mean = float(np.mean(numeric_values))
            std = float(np.std(numeric_values))
            numeric_stats[feature_name] = {"mean": mean, "std": std if std > 0 else 1.0}
        else:
            vocab = sorted({str(value) for value in non_missing})
            categorical_features[feature_name] = vocab

    output_columns = [
        *numeric_features,
        *[
            f"{feature_name}={value}"
            for feature_name, vocab in categorical_features.items()
            for value in vocab
        ],
    ]
    return {
        "feature_order": list(feature_names),
        "numeric_features": numeric_features,
        "categorical_features": categorical_features,
        "numeric_stats": numeric_stats,
        "output_columns": output_columns,
        "missing_policy": "numeric_mean_and_categorical_zero_fold_local",
    }


def _transform_examples(
    examples: tuple[ShadowTrainingExample, ...],
    preprocessing: dict[str, Any],
) -> np.ndarray:
    rows: list[list[float]] = []
    for example in examples:
        values: list[float] = []
        for feature_name in preprocessing["numeric_features"]:
            stats = preprocessing["numeric_stats"][feature_name]
            raw = example.feature_vector.get(feature_name)
            value = stats["mean"] if _missing(raw) else float(raw)
            values.append((value - stats["mean"]) / stats["std"])
        for feature_name, vocab in preprocessing["categorical_features"].items():
            raw = example.feature_vector.get(feature_name)
            values.extend(1.0 if str(raw) == value else 0.0 for value in vocab)
        rows.append(values)
    if not rows:
        return np.zeros((0, len(preprocessing["output_columns"])), dtype=float)
    return np.array(rows, dtype=float)


def _fit_logistic_regression(
    x: np.ndarray,
    y: np.ndarray,
    *,
    regularization_strength: float,
    learning_rate: float,
    iterations: int,
) -> tuple[np.ndarray, float]:
    if x.shape[0] == 0:
        return np.zeros(x.shape[1], dtype=float), 0.0
    coefficients = np.zeros(x.shape[1], dtype=float)
    intercept = 0.0
    regularization = max(float(regularization_strength), 0.0)
    for _ in range(iterations):
        predictions = _predict(x, coefficients, intercept)
        error = predictions - y
        gradient = (x.T @ error) / len(y) + regularization * coefficients / len(y)
        intercept_gradient = float(np.mean(error))
        coefficients -= learning_rate * gradient
        intercept -= learning_rate * intercept_gradient
    return coefficients, intercept


def _predict(x: np.ndarray, coefficients: np.ndarray, intercept: float) -> np.ndarray:
    logits = np.clip(x @ coefficients + intercept, -35, 35)
    return 1.0 / (1.0 + np.exp(-logits))


def _aggregate_metrics(
    fold_metrics: list[dict[str, Any]],
    examples: tuple[ShadowTrainingExample, ...],
    cohort_baseline_probability: float | None,
) -> dict[str, Any]:
    labels = np.array([1.0 if example.label else 0.0 for example in examples])
    global_baseline = _bounded_probability(float(np.mean(labels))) if len(labels) else 0.5
    cohort_baseline = _bounded_probability(cohort_baseline_probability or global_baseline)
    return {
        "sample_n": len(examples),
        "fold_count": len(fold_metrics),
        "model_log_loss": _mean_metric(fold_metrics, "model_log_loss"),
        "model_brier_score": _mean_metric(fold_metrics, "model_brier_score"),
        "global_baseline_probability": global_baseline,
        "global_baseline_log_loss": _mean_metric(fold_metrics, "global_baseline_log_loss"),
        "cohort_baseline_probability": cohort_baseline,
        "cohort_baseline_log_loss": _mean_metric(fold_metrics, "cohort_baseline_log_loss"),
    }


def _ordered_examples(
    examples: tuple[ShadowTrainingExample, ...],
) -> tuple[ShadowTrainingExample, ...]:
    return tuple(
        sorted(
            examples,
            key=lambda example: (example.cutoff_at, example.episode_id or 0),
        )
    )


def _episode_groups(examples: tuple[ShadowTrainingExample, ...]) -> tuple[_EpisodeGroup, ...]:
    groups: dict[tuple[str, int], list[int]] = {}
    for index, example in enumerate(examples):
        groups.setdefault(_episode_key(example, index), []).append(index)
    return tuple(
        _EpisodeGroup(episode_key=key, example_indices=tuple(indices))
        for key, indices in sorted(
            groups.items(),
            key=lambda item: min(examples[index].cutoff_at for index in item[1]),
        )
    )


def _chunks(items: tuple[_EpisodeGroup, ...], count: int) -> tuple[tuple[_EpisodeGroup, ...], ...]:
    chunk_size = max(math.ceil(len(items) / count), 1)
    return tuple(
        tuple(items[index : index + chunk_size])
        for index in range(0, len(items), chunk_size)
    )


def _episode_key(example: ShadowTrainingExample, fallback_index: int) -> tuple[str, int]:
    if example.episode_id is not None:
        return ("episode", example.episode_id)
    return ("row", fallback_index)


def _episode_ids(
    examples: tuple[ShadowTrainingExample, ...],
    indices: tuple[int, ...],
) -> tuple[int, ...]:
    return tuple(
        sorted(
            {
                examples[index].episode_id
                for index in indices
                if examples[index].episode_id is not None
            }
        )
    )


def _log_loss(y: np.ndarray, predictions: np.ndarray) -> float:
    clipped = np.clip(predictions, 1e-6, 1 - 1e-6)
    return round(float(-np.mean(y * np.log(clipped) + (1 - y) * np.log(1 - clipped))), 6)


def _brier_score(y: np.ndarray, predictions: np.ndarray) -> float:
    return round(float(np.mean((predictions - y) ** 2)), 6)


def _baseline_log_loss(y: np.ndarray, probability: float) -> float:
    return _log_loss(y, np.full(len(y), probability, dtype=float))


def _mean_metric(rows: list[dict[str, Any]], key: str) -> float | None:
    if not rows:
        return None
    return round(float(sum(row[key] for row in rows) / len(rows)), 6)


def _bounded_probability(value: float) -> float:
    if math.isnan(value):
        return 0.5
    return min(max(value, 1e-6), 1 - 1e-6)


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float | Decimal | np.number) and not isinstance(value, bool)


def _missing(value: Any) -> bool:
    return value is None or value == ""


def _utcnow() -> datetime:
    return datetime.now(UTC)
