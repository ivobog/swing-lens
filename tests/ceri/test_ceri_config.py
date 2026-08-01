from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.services.ceri.config import (
    CeriConfigError,
    ceri_config_hash,
    load_ceri_config,
)
from app.services.ceri.dtos import CeriFilterError, CeriFilters
from app.services.ceri.enums import (
    CatalystCategory,
    CeriConfidenceLabel,
    CeriMetric,
    CeriPeriodType,
    HistoricalViewMode,
)


def test_valid_default_ceri_yaml_loads() -> None:
    config = load_ceri_config()

    assert config.engine.enabled is False
    assert config.engine.calculation_version == "ceri-1.0.0"
    assert config.engine.timezone == "America/New_York"
    assert config.engine.daily_cutoff_time.hour == 16
    assert config.engine.daily_cutoff_time.minute == 15
    assert config.providers.priority[0].value == "manual"
    assert config.metrics.required == (CeriMetric.EPS_DILUTED, CeriMetric.REVENUE)
    assert CeriPeriodType.CURRENT_QUARTER in config.metrics.period_types
    assert config.revision.windows_days == (7, 30, 90)
    assert config.missing_values.preserve_nulls is True
    assert config.currency_conversion.require_verified_basis is True
    assert len(config.config_hash) == 64


def test_config_hash_is_stable_for_semantically_identical_input() -> None:
    config = load_ceri_config()
    data = asdict(config)
    reordered = {
        "api_error_codes": data["api_error_codes"],
        "taxonomy": data["taxonomy"],
        "retention": data["retention"],
        "exports": data["exports"],
        "posture": data["posture"],
        "alerts": data["alerts"],
        "backfill": data["backfill"],
        "enabled_categories": data["enabled_categories"],
        "change_thresholds": data["change_thresholds"],
        "confidence": data["confidence"],
        "event_risk": data["event_risk"],
        "opportunity_weights": data["opportunity_weights"],
        "currency_conversion": data["currency_conversion"],
        "missing_values": data["missing_values"],
        "revision": data["revision"],
        "metrics": data["metrics"],
        "datasets": data["datasets"],
        "providers": data["providers"],
        "engine": data["engine"],
    }

    assert ceri_config_hash(config) == ceri_config_hash(reordered)


def test_invalid_opportunity_weights_fail(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["opportunity_weights"].update({"price_response": 0.10}),
    )

    with pytest.raises(CeriConfigError, match="opportunity weights"):
        load_ceri_config(path)


def test_unknown_taxonomy_category_fails(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["taxonomy"]["enabled_categories"].append("MADE_UP"),
    )

    with pytest.raises(CeriConfigError, match="taxonomy.enabled_categories"):
        load_ceri_config(path)


def test_unknown_provider_capability_fails(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["providers"]["capabilities"]["manual"].append("telepathy"),
    )

    with pytest.raises(CeriConfigError, match="providers.capabilities.manual"):
        load_ceri_config(path)


def test_impossible_threshold_order_fails(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["confidence"].update({"normal_min": 9.0}),
    )

    with pytest.raises(CeriConfigError, match="confidence thresholds"):
        load_ceri_config(path)


def test_timezone_and_effective_session_policy_are_validated(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["engine"].update({"timezone": "UTC"}),
    )

    with pytest.raises(CeriConfigError, match="America/New_York"):
        load_ceri_config(path)


def test_missing_value_policy_rejects_zero_fill(tmp_path: Path) -> None:
    path = _config_with_mutation(
        tmp_path,
        lambda config: config["missing_values"].update({"forbid_zero_fill_defaults": False}),
    )

    with pytest.raises(CeriConfigError, match="zero-as-null"):
        load_ceri_config(path)


def test_disabled_all_datasets_fails(tmp_path: Path) -> None:
    def mutate(config: dict[str, Any]) -> None:
        for dataset in config["datasets"].values():
            dataset["enabled"] = False

    path = _config_with_mutation(tmp_path, mutate)

    with pytest.raises(CeriConfigError, match="at least one dataset"):
        load_ceri_config(path)


def test_filter_dto_accepts_compound_ceri_filters() -> None:
    filters = CeriFilters(
        opportunity_min=7.5,
        risk_max=4.0,
        confidence=CeriConfidenceLabel.HIGH,
        eps_revision_30d_min=3.0,
        breadth_min=0.4,
        event_category=CatalystCategory.CONTRACT,
        mode=HistoricalViewMode.AS_KNOWN,
    )

    params = filters.as_query_params()

    assert params["opportunity_min"] == 7.5
    assert params["confidence"] == "High"
    assert params["event_category"] == "CONTRACT"


def test_filter_dto_rejects_invalid_filters() -> None:
    with pytest.raises(CeriFilterError, match="opportunity_min"):
        CeriFilters(opportunity_min=12.0)

    with pytest.raises(CeriFilterError, match="breadth_min"):
        CeriFilters(breadth_min=1.5)

    with pytest.raises(CeriFilterError, match="sort"):
        CeriFilters(sort="future_alpha")


def _config_with_mutation(tmp_path: Path, mutate: Any) -> Path:
    config = yaml.safe_load(Path("config/ceri.yaml").read_text(encoding="utf-8"))
    mutate(config)
    path = tmp_path / "ceri.yaml"
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return path
