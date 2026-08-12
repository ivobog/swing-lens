from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.services.setup_lifecycle.config import (
    SetupLifecycleConfigError,
    data_quality_label_for,
    generic_fallback_allowed,
    load_setup_lifecycle_config,
    setup_lifecycle_config_hash,
)
from app.services.setup_lifecycle.enums import DataQualityLabel, SetupFamily


def test_valid_default_setup_lifecycle_yaml_loads() -> None:
    config = load_setup_lifecycle_config()

    assert config.engine.enabled is False
    assert config.engine.version == "slse-1.2.0"
    assert config.engine.schema_version == "slse-snapshot-1.0.0"
    assert config.engine.trigger_authority == "COMPLETED_DAILY_CLOSE"
    assert config.engine.diagnostic_high_cross_enabled is True
    assert config.states.terminal == (
        config.states.transition_precedence[0],
        config.states.transition_precedence[-1],
    )
    assert config.families.precedence == (
        SetupFamily.BREAKOUT,
        SetupFamily.PULLBACK,
        SetupFamily.VCP,
        SetupFamily.CONTINUATION,
        SetupFamily.GENERIC,
    )
    assert len(config.config_hash) == 64


def test_config_hash_is_stable_for_semantically_identical_input() -> None:
    config = load_setup_lifecycle_config()
    data = asdict(config)
    data["signal_registry"] = [
        asdict(definition) for definition in config.signal_registry.definitions()
    ]
    reordered = {
        "reconstructed_origin": data["reconstructed_origin"],
        "retention": data["retention"],
        "replay": data["replay"],
        "api": data["api"],
        "alerts": data["alerts"],
        "signal_registry": data["signal_registry"],
        "actionability": data["actionability"],
        "data_quality_labels": data["data_quality_labels"],
        "confidence": data["confidence"],
        "episodes": data["episodes"],
        "families": data["families"],
        "phases": data["phases"],
        "states": data["states"],
        "canonicalization": data["canonicalization"],
        "engine": data["engine"],
    }

    assert setup_lifecycle_config_hash(config) == setup_lifecycle_config_hash(reordered)


def test_data_quality_labels_are_deterministic() -> None:
    config = load_setup_lifecycle_config()

    assert (
        data_quality_label_for(
            config,
            required_feature_coverage=1.0,
            fresh_completed_bar=True,
            context_complete=True,
        )
        is DataQualityLabel.HIGH
    )
    assert (
        data_quality_label_for(
            config,
            required_feature_coverage=0.95,
            fresh_completed_bar=True,
            context_complete=False,
        )
        is DataQualityLabel.NORMAL
    )
    assert (
        data_quality_label_for(
            config,
            required_feature_coverage=0.75,
            fresh_completed_bar=True,
            context_complete=False,
            inferred_required_feature=True,
        )
        is DataQualityLabel.LOW
    )
    assert (
        data_quality_label_for(
            config,
            required_feature_coverage=1.0,
            fresh_completed_bar=True,
            context_complete=True,
            hard_required_absent=True,
        )
        is DataQualityLabel.INSUFFICIENT
    )


def test_generic_fallback_does_not_shadow_supported_family() -> None:
    config = load_setup_lifecycle_config()

    assert generic_fallback_allowed(config, None, None) is True
    assert generic_fallback_allowed(config, SetupFamily.BREAKOUT, 49) is True
    assert generic_fallback_allowed(config, SetupFamily.BREAKOUT, 50) is False
    assert generic_fallback_allowed(config, SetupFamily.PULLBACK, 80) is False


def test_high_cross_signal_is_diagnostic_and_close_trigger_is_authoritative() -> None:
    config = load_setup_lifecycle_config()

    close_trigger = config.signal_registry.require("close_trigger_cross")
    high_cross = config.signal_registry.require("intraday_high_trigger_cross_diagnostic")

    assert close_trigger.is_close_authoritative_trigger is True
    assert close_trigger.diagnostic_only is False
    assert high_cross.is_diagnostic_high_cross is True
    assert high_cross.diagnostic_only is True


def test_stable_error_codes_and_performance_targets_are_validated() -> None:
    config = load_setup_lifecycle_config()

    assert config.api.capture_evaluation_target_seconds == 60
    assert config.api.p95_target_ms == 500
    assert config.api.performance_fixture_min_snapshots == 100000
    assert "INVALID_CONFIGURATION" in config.api.error_codes
    assert "RUN_LIFECYCLE_NOT_FOUND" in config.api.error_codes


def test_reconstructed_origin_is_excluded_by_default() -> None:
    config = load_setup_lifecycle_config()

    assert config.alerts.reconstructed_origin_excluded is True
    assert config.reconstructed_origin.exclude_from_live_alerts is True
    assert config.reconstructed_origin.exclude_from_live_alert_statistics is True
    assert config.reconstructed_origin.exclude_from_owpe_export is True


def test_replay_authority_and_purge_policy_are_locked() -> None:
    config = load_setup_lifecycle_config()

    assert config.replay.output_authoritative_by_default is False
    assert config.replay.promotion_requires_explicit_admin_action is True
    assert config.replay.promotion_requires_confirmation is True
    assert config.replay.persisted_replay_creates_parallel_version is True
    assert config.retention.retain_immutable_evidence_indefinitely is True
    assert config.retention.purge_enabled is False
    assert config.retention.purge_preview_required is True
    assert config.retention.purge_confirmation_required is True
    assert config.retention.purge_audit_required is True


def test_invalid_transition_precedence_fails(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["states"]["transition_precedence"].append("READY"),
    )

    with pytest.raises(SetupLifecycleConfigError, match="transition_precedence"):
        load_setup_lifecycle_config(path)


def test_invalid_generic_shadowing_policy_fails(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["families"]["generic_fallback"].update(
            {"min_supported_family_confidence_to_block_generic": 101}
        ),
    )

    with pytest.raises(SetupLifecycleConfigError, match="generic fallback confidence"):
        load_setup_lifecycle_config(path)


def test_missing_stable_error_code_fails(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["api"]["error_codes"].remove("INVALID_CURSOR"),
    )

    with pytest.raises(SetupLifecycleConfigError, match="INVALID_CURSOR"):
        load_setup_lifecycle_config(path)


def test_reconstructed_origin_alert_inclusion_fails(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["reconstructed_origin"].update(
            {"exclude_from_live_alerts": False}
        ),
    )

    with pytest.raises(SetupLifecycleConfigError, match="reconstructed origin"):
        load_setup_lifecycle_config(path)


def test_high_cross_cannot_become_close_authoritative(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["signals"]["intraday_high_trigger_cross_diagnostic"].update(
            {"trigger_authority": "close"}
        ),
    )

    with pytest.raises(SetupLifecycleConfigError, match="diagnostic high-cross"):
        load_setup_lifecycle_config(path)


def _config_with_mutation(tmp_path: Path, mutate: Any) -> Path:
    config = yaml.safe_load(Path("config/setup_lifecycle.yaml").read_text(encoding="utf-8"))
    mutate(config)
    path = tmp_path / "setup_lifecycle.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path
