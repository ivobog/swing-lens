from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.services.winner_probability.config import (
    ENTRY_MODEL_NEXT_OPEN,
    ESTIMATE_KIND_DECISION_TIME,
    SAME_BAR_CONSERVATIVE_STOP_FIRST,
    WinnerProbabilityConfigError,
    load_winner_probability_config,
    winner_probability_config_hash,
)
from app.services.winner_probability.dtos import (
    WinnerProbabilityFilterError,
    WinnerProbabilityFilters,
)


def test_valid_default_winner_probability_yaml_loads() -> None:
    config = load_winner_probability_config()

    assert config.engine.enabled is False
    assert config.engine.feature_schema_version == "owpe-features-1.0.0"
    assert config.entry_models.production == ENTRY_MODEL_NEXT_OPEN
    assert config.horizon.counting_convention == "ENTRY_SESSION_IS_SESSION_1"
    assert config.horizon.sessions == (1, 3, 5, 10, 20)
    assert config.pending_outcomes["materialize_at_capture"] is True
    assert ESTIMATE_KIND_DECISION_TIME in config.estimate_kinds
    assert config.primary_outcome_definition.id == "T2_5_S2_0_H5_NEXT_OPEN"
    assert config.primary_outcome_definition.same_bar_conflict_policy == (
        SAME_BAR_CONSERVATIVE_STOP_FIRST
    )
    assert config.cohort.prior_strength == 20
    assert "regularized_logistic_regression" in config.model_governance.approved_algorithms
    assert config.model_governance.promotion_gates["require_fresh_drift_metrics"] is True
    assert config.evidence_membership.persistence == "ROWS"
    assert len(config.config_hash) == 64


def test_config_hash_is_stable_for_semantically_identical_input() -> None:
    config = load_winner_probability_config()
    data = asdict(config)
    reordered = {
        "filters": data["filters"],
        "feature_schema": data["feature_schema"],
        "api": data["api"],
        "retention": data["retention"],
        "drift": data["drift"],
        "model_governance": data["model_governance"],
        "cold_start": data["cold_start"],
        "evidence_membership": data["evidence_membership"],
        "evidence_grades": data["evidence_grades"],
        "cohort": data["cohort"],
        "episode": data["episode"],
        "outcome_definitions": data["outcome_definitions"],
        "estimate_views": data["estimate_views"],
        "estimate_kinds": data["estimate_kinds"],
        "pending_outcomes": data["pending_outcomes"],
        "horizon": data["horizon"],
        "entry_models": data["entry_models"],
        "engine": data["engine"],
    }

    assert winner_probability_config_hash(config) == winner_probability_config_hash(reordered)


def test_invalid_percentages_fail_with_actionable_error(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["outcome_definitions"][0].update({"target_pct": -2.5}),
    )

    with pytest.raises(WinnerProbabilityConfigError, match="target_pct must be positive"):
        load_winner_probability_config(path)


def test_duplicate_primary_definitions_fail(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["outcome_definitions"][1].update({"primary": True}),
    )

    with pytest.raises(WinnerProbabilityConfigError, match="exactly one primary"):
        load_winner_probability_config(path)


def test_unordered_evidence_thresholds_fail(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["evidence_grades"]["high"].update({"max_interval_width": 0.50}),
    )

    with pytest.raises(WinnerProbabilityConfigError, match="max_interval_width"):
        load_winner_probability_config(path)


def test_unknown_feature_names_fail(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["feature_schema"]["core_features"].append("made_up_feature"),
    )

    with pytest.raises(WinnerProbabilityConfigError, match="unknown feature"):
        load_winner_probability_config(path)


def test_entry_day_inclusive_horizon_fixture_is_locked() -> None:
    config = load_winner_probability_config()

    assert config.horizon.counting_convention == "ENTRY_SESSION_IS_SESSION_1"
    assert config.primary_outcome_definition.horizon_sessions == 5
    assert 5 in config.horizon.sessions


def test_production_primary_and_diagnostic_definition_ids_cannot_collide(
    tmp_path: Path,
) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["outcome_definitions"][1].update(
            {"id": config["outcome_definitions"][0]["id"]}
        ),
    )

    with pytest.raises(WinnerProbabilityConfigError, match="ids must be unique"):
        load_winner_probability_config(path)


def test_drift_configuration_rejects_invalid_thresholds(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["drift"]["thresholds"].update({"brier_score_delta": 1.5}),
    )

    with pytest.raises(WinnerProbabilityConfigError, match="drift.thresholds.brier"):
        load_winner_probability_config(path)


def test_model_governance_rejects_empty_algorithm_allowlist(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["model_governance"].update({"approved_algorithms": []}),
    )

    with pytest.raises(WinnerProbabilityConfigError, match="approved_algorithms"):
        load_winner_probability_config(path)


def test_filter_dto_accepts_compound_thresholds() -> None:
    filters = WinnerProbabilityFilters(
        probability_min=0.55,
        lower_bound_min=0.45,
        interval_width_max=0.25,
        expected_return_min=2.0,
        median_return_min=1.0,
        mfe_min=3.0,
        mae_max=-1.0,
        target_first_rate_min=0.60,
        evidence_grade="Medium",
        effective_sample_size_min=40,
        earnings_risk="low",
        data_quality="ok",
    )

    assert filters.as_query_params()["probability_min"] == 0.55
    assert filters.as_query_params()["evidence_grade"] == "Medium"


def test_filter_dto_rejects_invalid_or_contradictory_thresholds() -> None:
    with pytest.raises(WinnerProbabilityFilterError, match="probability_min"):
        WinnerProbabilityFilters(probability_min=1.5)

    with pytest.raises(WinnerProbabilityFilterError, match="minimum"):
        WinnerProbabilityFilters(probability_min=0.8, probability_max=0.2)

    with pytest.raises(WinnerProbabilityFilterError, match="evidence_grade"):
        WinnerProbabilityFilters(evidence_grade="Certain")


def _config_with_mutation(tmp_path: Path, mutate: Any) -> Path:
    config = yaml.safe_load(Path("config/winner_probability.yaml").read_text(encoding="utf-8"))
    mutate(config)
    path = tmp_path / "winner_probability.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path
