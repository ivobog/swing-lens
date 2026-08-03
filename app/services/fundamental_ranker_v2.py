import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from app.services.column_mapper import MappedCsvRow
from app.services.fundamental_components_v2 import (
    COMPONENT_FORMULA_FIELDS,
    COMPONENT_SCORE_KEYS,
    score_components_v2,
)
from app.services.fundamental_coverage_service import calculate_coverage_v2
from app.services.fundamental_warning_service import (
    BALANCE_SHEET_STRESS,
    EARNINGS_QUALITY_RISK,
    FORWARD_QUALITY_WEAK,
    POOR_CASH_CONVERSION,
    build_warning_flags_v2,
)

FUNDAMENTALS_V2_CONFIG_KEYS = {
    "model_version",
    "weights",
    "missing_data",
    "thresholds",
    "field_priorities",
    "coverage_only_fields",
    "components",
}
MISSING_DATA_KEYS = {
    "critical_field_penalty",
    "high_field_penalty",
    "medium_field_penalty",
    "low_field_penalty",
    "max_penalty",
    "sparse_data_coverage_threshold",
}
FIELD_PRIORITY_KEYS = {"critical", "high", "medium", "low"}
THRESHOLD_KEYS = {
    "sloan_ratio_good_max",
    "sloan_ratio_warning_min",
    "sloan_ratio_danger_min",
    "roa_good",
    "roa_excellent",
    "roic_good",
    "roic_excellent",
    "liabilities_to_assets_good_max",
    "liabilities_to_assets_warning_min",
    "quick_ratio_good",
    "quick_ratio_weak",
    "current_ratio_weak",
    "dividend_payout_safe_max",
    "dividend_payout_warning_min",
    "total_assets_growth_high",
    "capex_growth_high",
    "fcf_to_net_income_good",
    "debt_to_ebitda_warning",
    "net_debt_to_ebitda_warning",
    "dollar_volume_weak",
    "dollar_volume_good",
    "atr_pct_high",
    "beta_high",
}
QUALITY_RISK_LABEL = "Quality risk"


class FundamentalsConfigError(ValueError):
    pass


@dataclass(frozen=True)
class FundamentalsV2Config:
    data: dict[str, Any]
    config_hash: str

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def items(self):
        return self.data.items()


@dataclass(frozen=True)
class FundamentalScoreV2Result:
    ticker: str
    growth_quality_score: float
    profitability_quality_score: float
    fcf_quality_score: float
    earnings_quality_score: float
    capital_efficiency_score: float
    balance_sheet_quality_score: float
    valuation_quality_score: float
    forward_quality_score: float
    shareholder_quality_score: float
    liquidity_risk_score: float
    data_coverage_score: float
    missing_data_penalty: float
    fundamental_score: float
    fundamental_label: str
    warning_flags: list[str]
    explanation: str
    debug: dict[str, Any]


def score_rows_v2(rows: list[MappedCsvRow]) -> list[FundamentalScoreV2Result]:
    config = load_fundamentals_v2_config()
    return [score_row_v2(row, config=config) for row in rows if row.ticker]


def score_row_v2(
    row: MappedCsvRow,
    config: FundamentalsV2Config | Mapping[str, Any] | None = None,
) -> FundamentalScoreV2Result:
    config = config or load_fundamentals_v2_config()
    values = row.canonical
    component_scores = score_components_v2(values, config["thresholds"])
    coverage = calculate_coverage_v2(values, config)
    warning_flags = build_warning_flags_v2(
        values=values,
        component_scores=component_scores,
        coverage=coverage,
        thresholds=config["thresholds"],
        sparse_data_coverage_threshold=float(config["missing_data"]["sparse_data_coverage_threshold"]),
    )
    score = _weighted_score(component_scores, config["weights"]) - coverage.missing_data_penalty
    score = _clamp(score)
    label = _label_for_score(score, component_scores, warning_flags)
    explanation = _explain(label, component_scores, warning_flags, coverage.data_coverage_score)

    return FundamentalScoreV2Result(
        ticker=row.ticker,
        growth_quality_score=component_scores["growth_quality_score"],
        profitability_quality_score=component_scores["profitability_quality_score"],
        fcf_quality_score=component_scores["fcf_quality_score"],
        earnings_quality_score=component_scores["earnings_quality_score"],
        capital_efficiency_score=component_scores["capital_efficiency_score"],
        balance_sheet_quality_score=component_scores["balance_sheet_quality_score"],
        valuation_quality_score=component_scores["valuation_quality_score"],
        forward_quality_score=component_scores["forward_quality_score"],
        shareholder_quality_score=component_scores["shareholder_quality_score"],
        liquidity_risk_score=component_scores["liquidity_risk_score"],
        data_coverage_score=coverage.data_coverage_score,
        missing_data_penalty=coverage.missing_data_penalty,
        fundamental_score=score,
        fundamental_label=label,
        warning_flags=warning_flags,
        explanation=explanation,
        debug={
            "model_version": config["model_version"],
            "config_hash": _config_hash(config),
            "component_scores": component_scores,
            "component_coverage": coverage.component_coverage,
            "coverage": {
                "available_scoring_fields": coverage.available_scoring_fields,
                "total_scoring_fields": coverage.total_scoring_fields,
                "coverage_ratio": coverage.coverage_ratio,
                "data_coverage_score": coverage.data_coverage_score,
                "missing_core_fields": coverage.missing_core_fields,
                "missing_high_fields": coverage.missing_high_fields,
                "missing_fields_by_priority": coverage.missing_fields_by_priority,
            },
            "warnings": warning_flags,
            "parse_diagnostics": coverage.parse_diagnostics,
            "canonical_fields_present": sorted(values.keys()),
        },
    )


def load_fundamentals_v2_config(
    path: Path = Path("config/fundamentals_v2.yaml"),
) -> FundamentalsV2Config:
    with path.open("r", encoding="utf-8") as handle:
        return parse_fundamentals_v2_config(yaml.safe_load(handle) or {})


def parse_fundamentals_v2_config(raw: Mapping[str, Any]) -> FundamentalsV2Config:
    data = _plain_dict(raw)
    _validate_config(data)
    return FundamentalsV2Config(
        data=data,
        config_hash=fundamentals_v2_config_hash(data),
    )


def fundamentals_v2_config_hash(config: FundamentalsV2Config | Mapping[str, Any]) -> str:
    data = config.data if isinstance(config, FundamentalsV2Config) else _plain_dict(config)
    encoded = json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def to_decimal(value: float) -> Decimal:
    return Decimal(str(round(value, 4)))


def _weighted_score(component_scores: dict[str, float], weights: dict[str, Any]) -> float:
    return round(
        sum(component_scores[key] * float(weight) for key, weight in weights.items()),
        4,
    )


def _label_for_score(
    score: float,
    components: dict[str, float],
    warning_flags: list[str],
) -> str:
    flag_set = set(warning_flags)
    if BALANCE_SHEET_STRESS in flag_set or POOR_CASH_CONVERSION in flag_set:
        return "Value trap risk"
    if EARNINGS_QUALITY_RISK in flag_set or FORWARD_QUALITY_WEAK in flag_set:
        return QUALITY_RISK_LABEL
    if (
        score >= 7.6
        and components["profitability_quality_score"] >= 7
        and components["fcf_quality_score"] >= 6.5
        and components["earnings_quality_score"] >= 6.5
    ):
        return "Clean compounder"
    if score >= 6.7:
        return "High-quality quant"
    if score >= 5.0:
        return "Mixed but interesting"
    return "Low priority"


def _explain(
    label: str,
    components: dict[str, float],
    warning_flags: list[str],
    data_coverage_score: float,
) -> str:
    strongest = max(components, key=components.get)
    weakest = min(components, key=components.get)
    parts = [
        f"{label}: strongest area is {_display_component(strongest)}",
        f"weakest area is {_display_component(weakest)}",
        f"data coverage {data_coverage_score:.1f}/10",
    ]
    if warning_flags:
        parts.append(f"warnings: {', '.join(warning_flags[:3])}")
    return "; ".join(parts) + "."


def _display_component(component: str) -> str:
    return component.replace("_score", "").replace("_", " ")


def _clamp(value: float, lower: float = 0.0, upper: float = 10.0) -> float:
    return round(max(lower, min(upper, value)), 4)


def _config_hash(config: FundamentalsV2Config | Mapping[str, Any]) -> str:
    if isinstance(config, FundamentalsV2Config):
        return config.config_hash
    return fundamentals_v2_config_hash(config)


def _validate_config(config: dict[str, Any]) -> None:
    _require_keys(config, FUNDAMENTALS_V2_CONFIG_KEYS, "fundamentals_v2")
    _reject_unknown_keys(config, FUNDAMENTALS_V2_CONFIG_KEYS, "fundamentals_v2")
    if not str(config["model_version"]).strip():
        raise FundamentalsConfigError("fundamentals_v2.model_version is required")
    _validate_weights(config["weights"])
    _validate_missing_data(config["missing_data"])
    _validate_thresholds(config["thresholds"])
    _validate_field_priorities(config["field_priorities"])
    _validate_components(config["components"])
    _validate_coverage_only(config)


def _validate_weights(weights: Any) -> None:
    if not isinstance(weights, dict):
        raise FundamentalsConfigError("fundamentals_v2.weights must be a mapping")
    _require_keys(weights, set(COMPONENT_SCORE_KEYS), "fundamentals_v2.weights")
    _reject_unknown_keys(weights, set(COMPONENT_SCORE_KEYS), "fundamentals_v2.weights")
    total = 0.0
    for key, value in weights.items():
        number = _positive_number(value, f"fundamentals_v2.weights.{key}")
        total += number
    if round(total, 6) != 1.0:
        raise FundamentalsConfigError("fundamentals_v2.weights must sum to 1.0")


def _validate_missing_data(config: Any) -> None:
    if not isinstance(config, dict):
        raise FundamentalsConfigError("fundamentals_v2.missing_data must be a mapping")
    _require_keys(config, MISSING_DATA_KEYS, "fundamentals_v2.missing_data")
    _reject_unknown_keys(config, MISSING_DATA_KEYS, "fundamentals_v2.missing_data")
    for key, value in config.items():
        _number_between(value, f"fundamentals_v2.missing_data.{key}", lower=0.0, upper=10.0)
    threshold = float(config["sparse_data_coverage_threshold"])
    if not 0 < threshold <= 1:
        raise FundamentalsConfigError(
            "fundamentals_v2.missing_data.sparse_data_coverage_threshold must be in (0, 1]"
        )


def _validate_thresholds(thresholds: Any) -> None:
    if not isinstance(thresholds, dict):
        raise FundamentalsConfigError("fundamentals_v2.thresholds must be a mapping")
    _require_keys(thresholds, THRESHOLD_KEYS, "fundamentals_v2.thresholds")
    _reject_unknown_keys(thresholds, THRESHOLD_KEYS, "fundamentals_v2.thresholds")
    for key, value in thresholds.items():
        _number_between(value, f"fundamentals_v2.thresholds.{key}", lower=0.0, upper=None)
    _require_increasing(
        thresholds,
        ["sloan_ratio_good_max", "sloan_ratio_warning_min", "sloan_ratio_danger_min"],
    )
    _require_increasing(thresholds, ["roa_good", "roa_excellent"])
    _require_increasing(thresholds, ["roic_good", "roic_excellent"])
    _require_increasing(
        thresholds,
        ["liabilities_to_assets_good_max", "liabilities_to_assets_warning_min"],
    )
    _require_increasing(thresholds, ["dollar_volume_weak", "dollar_volume_good"])
    if float(thresholds["dividend_payout_safe_max"]) >= float(
        thresholds["dividend_payout_warning_min"]
    ):
        raise FundamentalsConfigError(
            "dividend_payout_safe_max must be lower than dividend_payout_warning_min"
        )


def _validate_field_priorities(priorities: Any) -> None:
    if not isinstance(priorities, dict):
        raise FundamentalsConfigError("fundamentals_v2.field_priorities must be a mapping")
    _require_keys(priorities, FIELD_PRIORITY_KEYS, "fundamentals_v2.field_priorities")
    _reject_unknown_keys(priorities, FIELD_PRIORITY_KEYS, "fundamentals_v2.field_priorities")
    for priority, fields in priorities.items():
        _validate_string_list(fields, f"fundamentals_v2.field_priorities.{priority}")


def _validate_components(components: Any) -> None:
    if not isinstance(components, dict):
        raise FundamentalsConfigError("fundamentals_v2.components must be a mapping")
    _require_keys(components, set(COMPONENT_SCORE_KEYS), "fundamentals_v2.components")
    _reject_unknown_keys(components, set(COMPONENT_SCORE_KEYS), "fundamentals_v2.components")
    for component, component_config in components.items():
        if not isinstance(component_config, dict):
            raise FundamentalsConfigError(
                f"fundamentals_v2.components.{component} must be a mapping"
            )
        _require_keys(component_config, {"fields"}, f"fundamentals_v2.components.{component}")
        _reject_unknown_keys(
            component_config,
            {"fields"},
            f"fundamentals_v2.components.{component}",
        )
        fields = _validate_string_list(
            component_config["fields"],
            f"fundamentals_v2.components.{component}.fields",
        )
        unsupported = sorted(set(fields) - COMPONENT_FORMULA_FIELDS[component])
        if unsupported:
            raise FundamentalsConfigError(
                f"fundamentals_v2.components.{component}.fields contains non-formula fields: "
                f"{', '.join(unsupported)}"
            )


def _validate_coverage_only(config: dict[str, Any]) -> None:
    coverage_only = _validate_string_list(
        config.get("coverage_only_fields", []),
        "fundamentals_v2.coverage_only_fields",
    )
    component_fields = {
        field
        for component in config["components"].values()
        for field in component["fields"]
    }
    priority_fields = {
        field
        for fields in config["field_priorities"].values()
        for field in fields
    }
    overlap = sorted(set(coverage_only) & component_fields)
    if overlap:
        raise FundamentalsConfigError(
            f"coverage_only_fields overlap component formula fields: {', '.join(overlap)}"
        )
    unclassified_priority_fields = sorted(priority_fields - component_fields - set(coverage_only))
    if unclassified_priority_fields:
        raise FundamentalsConfigError(
            "field_priorities includes fields that are neither formula fields nor coverage-only: "
            f"{', '.join(unclassified_priority_fields)}"
        )


def _require_keys(value: Mapping[str, Any], required: set[str], path: str) -> None:
    missing = sorted(required - set(value))
    if missing:
        raise FundamentalsConfigError(f"{path} is missing required keys: {', '.join(missing)}")


def _reject_unknown_keys(value: Mapping[str, Any], allowed: set[str], path: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise FundamentalsConfigError(f"{path} has unknown keys: {', '.join(unknown)}")


def _validate_string_list(value: Any, path: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise FundamentalsConfigError(f"{path} must be a list of strings")
    duplicates = sorted({item for item in value if value.count(item) > 1})
    if duplicates:
        raise FundamentalsConfigError(f"{path} has duplicate fields: {', '.join(duplicates)}")
    return value


def _positive_number(value: Any, path: str) -> float:
    return _number_between(value, path, lower=0.0, upper=None)


def _number_between(value: Any, path: str, *, lower: float, upper: float | None) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FundamentalsConfigError(f"{path} must be numeric") from exc
    if number < lower:
        raise FundamentalsConfigError(f"{path} must be >= {lower:g}")
    if upper is not None and number > upper:
        raise FundamentalsConfigError(f"{path} must be <= {upper:g}")
    return number


def _require_increasing(thresholds: Mapping[str, Any], keys: list[str]) -> None:
    values = [float(thresholds[key]) for key in keys]
    if values != sorted(values) or len(set(values)) != len(values):
        raise FundamentalsConfigError(f"thresholds must be strictly increasing: {', '.join(keys)}")


def _plain_dict(value: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, FundamentalsV2Config):
        return value.data
    if not isinstance(value, Mapping):
        raise FundamentalsConfigError("fundamentals_v2 config must be a mapping")
    return dict(value)
