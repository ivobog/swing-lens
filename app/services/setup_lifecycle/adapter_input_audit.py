from __future__ import annotations

import ast
import inspect
from dataclasses import dataclass
from importlib import import_module

from app.services.setup_lifecycle.config import SetupLifecycleConfig, load_setup_lifecycle_config


@dataclass(frozen=True)
class AdapterInputSpec:
    adapter: str
    signal_key: str
    business_meaning: str
    srs_sdd_rule: str
    source_entity: str
    source_path: str
    source_effective_date: str
    snapshot_builder_mapping: str
    signals_json_key: str
    required: bool
    null_behavior: str
    derivation: str | None = None
    history_requirement: str | None = None


_SIGNALS: dict[str, tuple[str, str, str, str, str, bool, str]] = {
    "setup_score": (
        "setup readiness strength",
        "SDD 6.1 scores; family tracking/ready thresholds",
        "TechnicalScore",
        "technical_scores.setup_score",
        "direct persisted scalar",
        True,
        "missing prevents family readiness",
    ),
    "technical_score": (
        "overall technical strength",
        "SDD 6.1 scores",
        "TechnicalScore",
        "technical_scores.dual_score",
        "direct persisted scalar",
        True,
        "missing prevents safe tracking",
    ),
    "trend_score": (
        "trend-direction agreement",
        "SDD 10.2 signal agreement",
        "TechnicalScore",
        "technical_scores.trend_score",
        "direct persisted scalar",
        False,
        "missing contributes neutral 0.5 agreement",
    ),
    "classification": (
        "family and danger classification",
        "SDD 8.2 family selection; SDD 10.2 agreement",
        "TechnicalScore",
        "technical_scores.classification",
        "direct persisted scalar",
        True,
        "missing prevents classification agreement",
    ),
    "stage": (
        "technical stage context",
        "SDD 6.1 classification group",
        "TechnicalScore",
        "technical_scores.stage",
        "direct persisted scalar",
        False,
        "missing remains null",
    ),
    "feature_flags": (
        "point-in-time technical setup flags",
        "SDD 6.1 flexible setup evidence",
        "TechnicalScore",
        "technical_scores.feature_flags_json",
        "direct persisted set",
        False,
        "missing becomes an empty set without positive evidence",
    ),
    "warning_flags": (
        "point-in-time danger flags",
        "SDD 6.1 risk evidence",
        "TechnicalScore",
        "technical_scores.warning_flags_json",
        "direct persisted set",
        False,
        "missing becomes an empty set without fabricating safety",
    ),
    "relative_strength": (
        "stock relative strength",
        "SDD 6.1 leadership; SDD 10.2 agreement",
        "TechnicalScore",
        "technical_scores.relative_strength_score",
        "direct persisted scalar",
        False,
        "missing contributes neutral 0.5 agreement",
    ),
    "leadership_score": (
        "cross-sectional leadership strength",
        "SDD 6.1 leadership; SDD 10.2 agreement",
        "TechnicalScore",
        "technical_scores.leadership_score",
        "direct persisted scalar",
        False,
        "missing contributes neutral 0.5 agreement",
    ),
    "distance_to_pivot_pct": (
        "completed-close distance from pivot",
        "SDD 8.3 breakout readiness",
        "PriceBar + raw source",
        "derived from close_price and pivot_price",
        "_distance_to_pivot_pct",
        False,
        "missing blocks pivot-distance readiness",
    ),
    "close_trigger_cross": (
        "completed close at or above trigger",
        "SRS completed-close authority; SDD 8.3/8.4",
        "PriceBar + raw source",
        "derived from close_price and trigger_price",
        "_crossed",
        True,
        "missing/false cannot trigger",
    ),
    "close_price": (
        "completed daily close",
        "SRS FR-003/005; SDD 6.1",
        "PriceBar",
        "price_bars.close",
        "direct preferred completed bar",
        True,
        "missing is a hard required-data absence",
    ),
    "trigger_price": (
        "family trigger or pivot reference",
        "SDD 8.3/8.4",
        "RawCompanyRow / normalized snapshot",
        "raw_json.trigger_price or pivot_price",
        "promoted trigger_price",
        False,
        "missing prevents ATR-from-trigger derivation",
    ),
    "atr_value": (
        "current ATR price unit",
        "SDD 8.3 extension by ATR",
        "TechnicalScore",
        "technical_scores.debug_json.derived.atr",
        "immutable debug extraction",
        False,
        "missing prevents ATR extension only",
    ),
    "range_percentile_252": (
        "current range percentile",
        "SDD 8.3/8.4 contraction evidence",
        "TechnicalScore",
        "technical_scores.range_percentile_252",
        "direct persisted scalar",
        False,
        "missing supplies no range-contraction evidence",
    ),
    "volume_percentile_252": (
        "current volume percentile",
        "SDD 8.3/8.4 dry-up evidence",
        "TechnicalScore",
        "technical_scores.volume_percentile_252",
        "direct persisted scalar",
        False,
        "missing supplies no percentile dry-up evidence",
    ),
    "tightness_score": (
        "box/base tightness",
        "SDD 8.3 improving tightness",
        "TechnicalScore",
        "technical_scores.box_tightness_score",
        "direct persisted scalar",
        False,
        "missing supplies no tightness evidence",
    ),
    "vcp_score": (
        "volatility-contraction quality",
        "SDD 8.2/8.3 VCP evidence",
        "TechnicalScore",
        "technical_scores.vcp_score",
        "direct persisted scalar",
        False,
        "missing supplies no VCP-score evidence",
    ),
    "volume_dry_up": (
        "technical dry-up flag",
        "SDD 8.3 VOLUME_DRY_UP",
        "TechnicalScore",
        "technical_scores.debug_json.derived.volume_dry_up",
        "immutable debug extraction",
        False,
        "missing/false supplies no dry-up evidence",
    ),
    "range_contraction": (
        "technical contraction flag",
        "SDD 8.3 RANGE_CONTRACTION",
        "TechnicalScore",
        "technical_scores.v4_debug_json.contraction.range_contraction",
        "immutable explainability extraction",
        False,
        "missing/false supplies no contraction evidence",
    ),
    "red_volume_declining": (
        "selling pressure is declining",
        "SDD 8.4 SELLING_PRESSURE_DECLINING",
        "TechnicalScore",
        "technical_scores.debug_json.derived.red_vol_declining",
        "immutable debug extraction",
        False,
        "missing/false supplies no direct declining-volume evidence",
    ),
    "held_near_support": (
        "configured support held",
        "SDD 8.4 support approach/test",
        "TechnicalScore",
        "technical_scores.debug_json.derived.held_near_support",
        "immutable debug extraction",
        False,
        "missing/false cannot establish support hold",
    ),
    "pullback_depth_pct": (
        "retreat from recent high",
        "SDD 8.4 pullback structure",
        "TechnicalScore",
        "technical_scores.debug_json.derived.pullback_depth_pct",
        "immutable debug extraction",
        False,
        "missing remains null and is audit-only",
    ),
    "failed_breakout": (
        "explicit upstream breakout invalidation",
        "SDD 8.3/8.4 failure-first rule",
        "TechnicalScore",
        "technical_scores.debug_json.derived.failed_breakout",
        "immutable debug extraction",
        False,
        "missing/false is not treated as failure",
    ),
    "fresh_breakout": (
        "explicit completed-close breakout evidence",
        "SDD 8.3 breakout family selection and trigger evidence",
        "TechnicalScore",
        "technical_scores.debug_json.derived.fresh_breakout",
        "immutable debug extraction",
        False,
        "missing/false supplies no direct breakout evidence",
    ),
    "box_failure": (
        "explicit box/base structural failure",
        "SDD 8.3 failure evidence",
        "TechnicalScore",
        "technical_scores.v4_debug_json.box.box_failure",
        "immutable explainability extraction",
        False,
        "missing/false is not treated as failure",
    ),
    "heavy_mid_ma_break": (
        "high-volume break of mid-term support",
        "SDD 8.4 support-break failure",
        "TechnicalScore",
        "technical_scores.debug_json.derived.heavy_mid_ma_break",
        "immutable debug extraction",
        False,
        "missing/false is not treated as failure",
    ),
    "volume_ratio": (
        "relative volume confirmation",
        "SDD 8.3/8.4 optional volume confirmation",
        "TechnicalScore",
        "technical_scores.debug_json.derived.volume_ratio",
        "immutable debug extraction",
        False,
        "missing supplies no volume confirmation",
    ),
}


_ADAPTER_KEYS: dict[str, tuple[str, ...]] = {
    "breakout": (
        "setup_score", "trend_score", "technical_score", "classification", "stage",
        "feature_flags", "relative_strength", "leadership_score", "distance_to_pivot_pct",
        "close_trigger_cross", "close_price", "trigger_price", "atr_value",
        "range_percentile_252", "volume_percentile_252", "tightness_score",
        "volume_dry_up", "range_contraction", "failed_breakout", "fresh_breakout",
        "box_failure",
    ),
    "pullback": (
        "setup_score", "trend_score", "technical_score", "classification", "stage",
        "feature_flags", "relative_strength", "leadership_score", "close_trigger_cross",
        "close_price", "trigger_price", "atr_value", "range_percentile_252",
        "volume_percentile_252", "red_volume_declining", "held_near_support",
        "pullback_depth_pct", "failed_breakout", "heavy_mid_ma_break", "volume_ratio",
    ),
    "vcp": (
        "setup_score", "trend_score", "technical_score", "classification", "stage",
        "feature_flags", "relative_strength", "leadership_score", "close_trigger_cross",
        "close_price", "trigger_price", "atr_value", "range_contraction", "vcp_score",
        "volume_percentile_252", "failed_breakout", "box_failure",
    ),
    "continuation": (
        "setup_score", "trend_score", "technical_score", "classification", "stage",
        "feature_flags", "relative_strength", "leadership_score", "close_trigger_cross",
        "close_price", "trigger_price", "atr_value", "range_percentile_252",
        "failed_breakout", "box_failure", "heavy_mid_ma_break",
    ),
    "generic": (
        "setup_score", "trend_score", "technical_score", "classification",
        "relative_strength", "leadership_score", "close_trigger_cross",
    ),
}


def adapter_input_specs() -> tuple[AdapterInputSpec, ...]:
    specs: list[AdapterInputSpec] = []
    for adapter, keys in _ADAPTER_KEYS.items():
        for key in keys:
            meaning, rule, entity, path, mapping, required, null_behavior = _SIGNALS[key]
            history = None
            derivation = None
            if key == "close_trigger_cross":
                history = (
                    "consecutive prior canonical true values derive hold/follow-through "
                    "sessions"
                )
            elif key in {"range_percentile_252", "volume_percentile_252", "technical_score"}:
                history = "ordered prior canonical values derive improvement/persistence"
            if key in {"close_price", "trigger_price", "atr_value"}:
                derivation = "(close_price - trigger_price) / atr_value for extension"
            specs.append(
                AdapterInputSpec(
                    adapter=adapter,
                    signal_key=key,
                    business_meaning=meaning,
                    srs_sdd_rule=rule,
                    source_entity=entity,
                    source_path=path,
                    source_effective_date="snapshot data_as_of_date; source must be no newer",
                    snapshot_builder_mapping=mapping,
                    signals_json_key=key,
                    required=required,
                    null_behavior=null_behavior,
                    derivation=derivation,
                    history_requirement=history,
                )
            )
    return tuple(specs)


def audit_adapter_input_coverage(
    config: SetupLifecycleConfig | None = None,
) -> tuple[str, ...]:
    config = config or load_setup_lifecycle_config()
    specs = adapter_input_specs()
    documented = {(item.adapter, item.signal_key) for item in specs}
    issues: list[str] = []
    for adapter, keys in _ADAPTER_KEYS.items():
        for key in keys:
            if key not in config.signal_registry:
                issues.append(f"{adapter}.{key}: not registered in production signal registry")
        used = _literal_signal_keys(adapter)
        for key in sorted(used):
            if (adapter, key) not in documented:
                issues.append(f"{adapter}.{key}: undocumented adapter magic signal")
    return tuple(issues)


def _literal_signal_keys(adapter: str) -> set[str]:
    module = import_module(f"app.services.setup_lifecycle.{adapter}_adapter")
    tree = ast.parse(inspect.getsource(module))
    keys: set[str] = set()
    signal_calls = {
        "signal_value": 1,
        "signal_number": 1,
        "signal_optional_number": 1,
        "signal_bool": 1,
        "signal_text": 1,
        "consecutive_true_sessions": 2,
        "numeric_history_is_improving": 2,
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        index = signal_calls.get(name or "")
        if index is None or len(node.args) <= index:
            continue
        value = node.args[index]
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            keys.add(value.value)
    return keys
