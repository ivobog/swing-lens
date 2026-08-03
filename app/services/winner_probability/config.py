from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import yaml

from app.services.winner_probability.feature_schema import (
    FEATURE_SCHEMA_VERSION,
    FeatureSchemaError,
    FeatureSchemaRegistry,
)

WINNER_PROBABILITY_CONFIG_PATH = Path("config/winner_probability.yaml")

ENTRY_MODEL_NEXT_OPEN = "NEXT_OPEN"
ENTRY_MODEL_SIGNAL_CLOSE_DIAGNOSTIC = "SIGNAL_CLOSE_DIAGNOSTIC"
ESTIMATE_KIND_DECISION_TIME = "DECISION_TIME"
ESTIMATE_KIND_LATEST_RESCORE = "LATEST_RESCORE"
VIEW_AS_OF = "AS_OF"
VIEW_CURRENT = "CURRENT"
SAME_BAR_CONSERVATIVE_STOP_FIRST = "CONSERVATIVE_STOP_FIRST"

VALID_ENTRY_MODELS = frozenset(
    {ENTRY_MODEL_NEXT_OPEN, ENTRY_MODEL_SIGNAL_CLOSE_DIAGNOSTIC}
)
VALID_ESTIMATE_KINDS = frozenset(
    {ESTIMATE_KIND_DECISION_TIME, ESTIMATE_KIND_LATEST_RESCORE}
)
VALID_ESTIMATE_VIEWS = frozenset(
    {ESTIMATE_KIND_DECISION_TIME, ESTIMATE_KIND_LATEST_RESCORE, VIEW_AS_OF, VIEW_CURRENT}
)
VALID_SAME_BAR_POLICIES = frozenset({SAME_BAR_CONSERVATIVE_STOP_FIRST})
VALID_EVIDENCE_MEMBERSHIP_POLICIES = frozenset({"ROWS", "MANIFEST", "ROWS_WITH_MANIFEST"})
VALID_HASH_ALGORITHMS = frozenset({"sha256"})

REQUIRED_SECTIONS = (
    "engine",
    "entry_models",
    "horizon",
    "pending_outcomes",
    "estimate_kinds",
    "estimate_views",
    "outcome_definitions",
    "episode",
    "cohort",
    "evidence_grades",
    "evidence_membership",
    "cold_start",
    "drift",
    "model_governance",
    "retention",
    "api",
    "feature_schema",
    "filters",
)

REQUIRED_PERMANENT_RETENTION = frozenset(
    {
        "prediction_snapshots",
        "outcome_revisions",
        "decision_time_estimates",
        "evidence_membership",
        "evidence_manifests",
        "model_versions",
        "cohort_versions",
        "training_runs",
        "lifecycle_events",
    }
)


class WinnerProbabilityConfigError(ValueError):
    pass


@dataclass(frozen=True)
class EngineConfig:
    enabled: bool
    feature_schema_version: str
    calculation_version: str


@dataclass(frozen=True)
class EntryModelsConfig:
    production: str
    diagnostics: tuple[str, ...]


@dataclass(frozen=True)
class HorizonConfig:
    counting_convention: str
    sessions: tuple[int, ...]


@dataclass(frozen=True)
class OutcomeDefinitionConfig:
    id: str
    label: str
    entry_model: str
    horizon_sessions: int
    target_pct: float
    stop_pct: float
    same_bar_conflict_policy: str
    primary: bool


@dataclass(frozen=True)
class EpisodeConfig:
    cooldown_sessions: int
    dependency_keys: tuple[str, ...]


@dataclass(frozen=True)
class CohortLevelConfig:
    level: str
    dimensions: tuple[str, ...]
    min_effective_n: int


@dataclass(frozen=True)
class CohortConfig:
    prior_strength: float
    prior_probability: float
    min_coverage: float
    max_interval_width: float
    hierarchy: tuple[CohortLevelConfig, ...]


@dataclass(frozen=True)
class EvidenceGradeConfig:
    min_effective_n: int
    max_interval_width: float


@dataclass(frozen=True)
class EvidenceMembershipConfig:
    persistence: str
    include_outcome_revision: bool
    compressed_manifest_threshold: int
    manifest_hash_algorithm: str


@dataclass(frozen=True)
class DriftConfig:
    windows_sessions: dict[str, int]
    thresholds: dict[str, float | int]


@dataclass(frozen=True)
class ModelGovernanceConfig:
    approved_algorithms: tuple[str, ...]
    promotion_gates: dict[str, float | bool]


@dataclass(frozen=True)
class RetentionConfig:
    permanent: tuple[str, ...]
    rebuildable: tuple[str, ...]
    operational_log_retention_days: int


@dataclass(frozen=True)
class ApiConfig:
    default_view: str
    default_estimate_kind: str
    max_as_of_window_days: int
    max_page_size: int


@dataclass(frozen=True)
class FeatureSchemaConfig:
    version: str
    core_features: tuple[str, ...]


@dataclass(frozen=True)
class WinnerProbabilityConfig:
    engine: EngineConfig
    entry_models: EntryModelsConfig
    horizon: HorizonConfig
    pending_outcomes: dict[str, bool]
    estimate_kinds: tuple[str, ...]
    estimate_views: dict[str, str]
    outcome_definitions: tuple[OutcomeDefinitionConfig, ...]
    episode: EpisodeConfig
    cohort: CohortConfig
    evidence_grades: dict[str, EvidenceGradeConfig]
    evidence_membership: EvidenceMembershipConfig
    cold_start: dict[str, bool | int]
    drift: DriftConfig
    model_governance: ModelGovernanceConfig
    retention: RetentionConfig
    api: ApiConfig
    feature_schema: FeatureSchemaConfig
    filters: dict[str, Any]
    config_hash: str

    @property
    def primary_outcome_definition(self) -> OutcomeDefinitionConfig:
        return next(outcome for outcome in self.outcome_definitions if outcome.primary)


def load_winner_probability_config(
    path: Path = WINNER_PROBABILITY_CONFIG_PATH,
) -> WinnerProbabilityConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    if not isinstance(raw, dict):
        raise WinnerProbabilityConfigError("winner probability config must be a mapping")

    for section in REQUIRED_SECTIONS:
        if section not in raw:
            raise WinnerProbabilityConfigError(f"{section} is required")

    try:
        registry = FeatureSchemaRegistry(_mapping(raw, "engine").get("feature_schema_version"))
    except FeatureSchemaError as exc:
        raise WinnerProbabilityConfigError(str(exc)) from exc
    parsed = WinnerProbabilityConfig(
        engine=_parse_engine(_mapping(raw, "engine")),
        entry_models=_parse_entry_models(_mapping(raw, "entry_models")),
        horizon=_parse_horizon(_mapping(raw, "horizon")),
        pending_outcomes=_parse_pending_outcomes(_mapping(raw, "pending_outcomes")),
        estimate_kinds=_parse_estimate_kinds(raw.get("estimate_kinds")),
        estimate_views=_parse_estimate_views(_mapping(raw, "estimate_views")),
        outcome_definitions=_parse_outcome_definitions(raw.get("outcome_definitions")),
        episode=_parse_episode(_mapping(raw, "episode"), registry),
        cohort=_parse_cohort(_mapping(raw, "cohort"), registry),
        evidence_grades=_parse_evidence_grades(_mapping(raw, "evidence_grades")),
        evidence_membership=_parse_evidence_membership(
            _mapping(raw, "evidence_membership")
        ),
        cold_start=_parse_cold_start(_mapping(raw, "cold_start")),
        drift=_parse_drift(_mapping(raw, "drift")),
        model_governance=_parse_model_governance(_mapping(raw, "model_governance")),
        retention=_parse_retention(_mapping(raw, "retention")),
        api=_parse_api(_mapping(raw, "api")),
        feature_schema=_parse_feature_schema(_mapping(raw, "feature_schema"), registry),
        filters=_parse_filters(_mapping(raw, "filters")),
        config_hash="",
    )
    _validate_cross_section_rules(parsed)
    return _with_hash(parsed)


def winner_probability_config_hash(config: WinnerProbabilityConfig | dict[str, Any]) -> str:
    payload = _normalized_config_payload(config)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _with_hash(config: WinnerProbabilityConfig) -> WinnerProbabilityConfig:
    return replace(config, config_hash=winner_probability_config_hash(config))


def _normalized_config_payload(config: WinnerProbabilityConfig | dict[str, Any]) -> str:
    if isinstance(config, WinnerProbabilityConfig):
        data = asdict(config)
        data.pop("config_hash", None)
    else:
        data = dict(config)
        data.pop("config_hash", None)
    return json.dumps(data, sort_keys=True, separators=(",", ":"), default=str)


def _parse_engine(raw: dict[str, Any]) -> EngineConfig:
    enabled = raw.get("enabled")
    if not isinstance(enabled, bool):
        raise WinnerProbabilityConfigError("engine.enabled must be boolean")
    schema_version = _required_text(raw, "engine.feature_schema_version")
    if schema_version != FEATURE_SCHEMA_VERSION:
        raise WinnerProbabilityConfigError(
            f"engine.feature_schema_version must be {FEATURE_SCHEMA_VERSION}"
        )
    return EngineConfig(
        enabled=enabled,
        feature_schema_version=schema_version,
        calculation_version=_required_text(raw, "engine.calculation_version"),
    )


def _parse_entry_models(raw: dict[str, Any]) -> EntryModelsConfig:
    production = _required_text(raw, "entry_models.production")
    _require_choice(production, VALID_ENTRY_MODELS, "entry_models.production")
    diagnostics = tuple(_text_list(raw.get("diagnostics", []), "entry_models.diagnostics"))
    for model in diagnostics:
        _require_choice(model, VALID_ENTRY_MODELS, "entry_models.diagnostics")
    if production in diagnostics:
        raise WinnerProbabilityConfigError("entry_models.diagnostics cannot include production")
    return EntryModelsConfig(production=production, diagnostics=diagnostics)


def _parse_horizon(raw: dict[str, Any]) -> HorizonConfig:
    convention = _required_text(raw, "horizon.counting_convention")
    if convention != "ENTRY_SESSION_IS_SESSION_1":
        raise WinnerProbabilityConfigError(
            "horizon.counting_convention must be ENTRY_SESSION_IS_SESSION_1"
        )
    sessions = tuple(int(_number(value, "horizon.sessions")) for value in _list(raw, "sessions"))
    if any(value <= 0 for value in sessions):
        raise WinnerProbabilityConfigError("horizon.sessions values must be positive")
    if tuple(sorted(set(sessions))) != sessions:
        raise WinnerProbabilityConfigError("horizon.sessions must be unique and ascending")
    return HorizonConfig(counting_convention=convention, sessions=sessions)


def _parse_pending_outcomes(raw: dict[str, Any]) -> dict[str, bool]:
    value = raw.get("materialize_at_capture")
    if not isinstance(value, bool):
        raise WinnerProbabilityConfigError(
            "pending_outcomes.materialize_at_capture must be boolean"
        )
    return {"materialize_at_capture": value}


def _parse_estimate_kinds(raw: Any) -> tuple[str, ...]:
    kinds = tuple(_text_list(raw, "estimate_kinds"))
    if len(set(kinds)) != len(kinds):
        raise WinnerProbabilityConfigError("estimate_kinds contains duplicates")
    missing = sorted(VALID_ESTIMATE_KINDS - set(kinds))
    if missing:
        raise WinnerProbabilityConfigError(
            f"estimate_kinds missing required kind(s): {', '.join(missing)}"
        )
    unknown = sorted(set(kinds) - VALID_ESTIMATE_KINDS)
    if unknown:
        raise WinnerProbabilityConfigError(
            f"estimate_kinds contains unknown kind(s): {', '.join(unknown)}"
        )
    return kinds


def _parse_estimate_views(raw: dict[str, Any]) -> dict[str, str]:
    required_keys = {"decision_time", "latest_rescore", "as_of", "current"}
    missing = sorted(required_keys - set(raw))
    if missing:
        raise WinnerProbabilityConfigError(
            f"estimate_views missing view(s): {', '.join(missing)}"
        )
    views = {key: _required_text(raw, f"estimate_views.{key}") for key in required_keys}
    for key, value in views.items():
        _require_choice(value, VALID_ESTIMATE_VIEWS, f"estimate_views.{key}")
    return dict(sorted(views.items()))


def _parse_outcome_definitions(raw: Any) -> tuple[OutcomeDefinitionConfig, ...]:
    rows = raw if isinstance(raw, list) else None
    if not rows:
        raise WinnerProbabilityConfigError("outcome_definitions must be a non-empty list")
    outcomes = tuple(_parse_outcome_definition(row, index) for index, row in enumerate(rows))
    ids = [outcome.id for outcome in outcomes]
    if len(set(ids)) != len(ids):
        raise WinnerProbabilityConfigError("outcome_definitions ids must be unique")
    primary_count = sum(outcome.primary for outcome in outcomes)
    if primary_count != 1:
        raise WinnerProbabilityConfigError("outcome_definitions must define exactly one primary")
    return outcomes


def _parse_outcome_definition(raw: Any, index: int) -> OutcomeDefinitionConfig:
    if not isinstance(raw, dict):
        raise WinnerProbabilityConfigError(f"outcome_definitions[{index}] must be a mapping")
    entry_model = _required_text(raw, f"outcome_definitions[{index}].entry_model")
    _require_choice(entry_model, VALID_ENTRY_MODELS, f"outcome_definitions[{index}].entry_model")
    target_pct = _number(raw.get("target_pct"), f"outcome_definitions[{index}].target_pct")
    stop_pct = _number(raw.get("stop_pct"), f"outcome_definitions[{index}].stop_pct")
    if target_pct <= 0:
        raise WinnerProbabilityConfigError("outcome_definitions target_pct must be positive")
    if stop_pct <= 0:
        raise WinnerProbabilityConfigError("outcome_definitions stop_pct must be positive")
    policy = _required_text(raw, f"outcome_definitions[{index}].same_bar_conflict_policy")
    _require_choice(
        policy,
        VALID_SAME_BAR_POLICIES,
        f"outcome_definitions[{index}].same_bar_conflict_policy",
    )
    primary = raw.get("primary", False)
    if not isinstance(primary, bool):
        raise WinnerProbabilityConfigError("outcome_definitions.primary must be boolean")
    return OutcomeDefinitionConfig(
        id=_required_text(raw, f"outcome_definitions[{index}].id"),
        label=_required_text(raw, f"outcome_definitions[{index}].label"),
        entry_model=entry_model,
        horizon_sessions=int(
            _number(raw.get("horizon_sessions"), f"outcome_definitions[{index}].horizon_sessions")
        ),
        target_pct=float(target_pct),
        stop_pct=float(stop_pct),
        same_bar_conflict_policy=policy,
        primary=primary,
    )


def _parse_episode(raw: dict[str, Any], registry: FeatureSchemaRegistry) -> EpisodeConfig:
    cooldown = int(_number(raw.get("cooldown_sessions"), "episode.cooldown_sessions"))
    if cooldown <= 0:
        raise WinnerProbabilityConfigError("episode.cooldown_sessions must be positive")
    dependency_keys = tuple(_text_list(raw.get("dependency_keys"), "episode.dependency_keys"))
    if not dependency_keys:
        raise WinnerProbabilityConfigError("episode.dependency_keys must not be empty")
    try:
        registry.require_feature_names(dependency_keys, "episode.dependency_keys")
    except FeatureSchemaError as exc:
        raise WinnerProbabilityConfigError(str(exc)) from exc
    return EpisodeConfig(cooldown_sessions=cooldown, dependency_keys=dependency_keys)


def _parse_cohort(raw: dict[str, Any], registry: FeatureSchemaRegistry) -> CohortConfig:
    prior_strength = _number(raw.get("prior_strength"), "cohort.prior_strength")
    prior_probability = _ratio(raw.get("prior_probability"), "cohort.prior_probability")
    min_coverage = _ratio(raw.get("min_coverage"), "cohort.min_coverage")
    max_interval_width = _ratio(raw.get("max_interval_width"), "cohort.max_interval_width")
    if prior_strength <= 0:
        raise WinnerProbabilityConfigError("cohort.prior_strength must be positive")
    hierarchy_rows = _list(raw, "hierarchy")
    hierarchy = tuple(
        _parse_cohort_level(row, index, registry)
        for index, row in enumerate(hierarchy_rows)
    )
    levels = [row.level for row in hierarchy]
    if len(set(levels)) != len(levels):
        raise WinnerProbabilityConfigError("cohort.hierarchy levels must be unique")
    return CohortConfig(
        prior_strength=float(prior_strength),
        prior_probability=prior_probability,
        min_coverage=min_coverage,
        max_interval_width=max_interval_width,
        hierarchy=hierarchy,
    )


def _parse_cohort_level(
    raw: Any,
    index: int,
    registry: FeatureSchemaRegistry,
) -> CohortLevelConfig:
    if not isinstance(raw, dict):
        raise WinnerProbabilityConfigError(f"cohort.hierarchy[{index}] must be a mapping")
    level = _required_text(raw, f"cohort.hierarchy[{index}].level")
    dimensions = tuple(_text_list(raw.get("dimensions"), f"cohort.hierarchy[{index}].dimensions"))
    if not dimensions:
        raise WinnerProbabilityConfigError("cohort.hierarchy dimensions must not be empty")
    feature_dimensions = tuple(value for value in dimensions if value != "global")
    try:
        registry.require_feature_names(
            feature_dimensions,
            f"cohort.hierarchy[{index}].dimensions",
        )
    except FeatureSchemaError as exc:
        raise WinnerProbabilityConfigError(str(exc)) from exc
    min_effective_n = int(
        _number(raw.get("min_effective_n"), f"cohort.hierarchy[{index}].min_effective_n")
    )
    if min_effective_n <= 0:
        raise WinnerProbabilityConfigError("cohort.hierarchy min_effective_n must be positive")
    return CohortLevelConfig(
        level=level,
        dimensions=dimensions,
        min_effective_n=min_effective_n,
    )


def _parse_evidence_grades(raw: dict[str, Any]) -> dict[str, EvidenceGradeConfig]:
    required = ("high", "medium", "low", "insufficient")
    missing = [grade for grade in required if grade not in raw]
    if missing:
        raise WinnerProbabilityConfigError(f"evidence_grades missing: {', '.join(missing)}")
    grades = {
        grade: _parse_evidence_grade(_mapping(raw, grade), f"evidence_grades.{grade}")
        for grade in required
    }
    if not (
        grades["high"].min_effective_n
        >= grades["medium"].min_effective_n
        >= grades["low"].min_effective_n
        >= grades["insufficient"].min_effective_n
    ):
        raise WinnerProbabilityConfigError("evidence_grades min_effective_n must be ordered")
    if not (
        grades["high"].max_interval_width
        <= grades["medium"].max_interval_width
        <= grades["low"].max_interval_width
        <= grades["insufficient"].max_interval_width
    ):
        raise WinnerProbabilityConfigError("evidence_grades max_interval_width must be ordered")
    return grades


def _parse_evidence_grade(raw: dict[str, Any], field_name: str) -> EvidenceGradeConfig:
    min_effective_n = int(_number(raw.get("min_effective_n"), f"{field_name}.min_effective_n"))
    max_interval_width = _ratio(raw.get("max_interval_width"), f"{field_name}.max_interval_width")
    if min_effective_n < 0:
        raise WinnerProbabilityConfigError(f"{field_name}.min_effective_n must be non-negative")
    return EvidenceGradeConfig(
        min_effective_n=min_effective_n,
        max_interval_width=max_interval_width,
    )


def _parse_evidence_membership(raw: dict[str, Any]) -> EvidenceMembershipConfig:
    persistence = _required_text(raw, "evidence_membership.persistence")
    _require_choice(
        persistence,
        VALID_EVIDENCE_MEMBERSHIP_POLICIES,
        "evidence_membership.persistence",
    )
    include_revision = raw.get("include_outcome_revision")
    if not isinstance(include_revision, bool):
        raise WinnerProbabilityConfigError(
            "evidence_membership.include_outcome_revision must be boolean"
        )
    threshold = int(
        _number(
            raw.get("compressed_manifest_threshold"),
            "evidence_membership.compressed_manifest_threshold",
        )
    )
    if threshold <= 0:
        raise WinnerProbabilityConfigError(
            "evidence_membership.compressed_manifest_threshold must be positive"
        )
    algorithm = _required_text(raw, "evidence_membership.manifest_hash_algorithm")
    _require_choice(
        algorithm,
        VALID_HASH_ALGORITHMS,
        "evidence_membership.manifest_hash_algorithm",
    )
    return EvidenceMembershipConfig(
        persistence=persistence,
        include_outcome_revision=include_revision,
        compressed_manifest_threshold=threshold,
        manifest_hash_algorithm=algorithm,
    )


def _parse_cold_start(raw: dict[str, Any]) -> dict[str, bool | int]:
    show_counts = raw.get("show_raw_evidence_counts")
    show_reasons = raw.get("show_insufficient_reasons")
    if not isinstance(show_counts, bool):
        raise WinnerProbabilityConfigError("cold_start.show_raw_evidence_counts must be boolean")
    if not isinstance(show_reasons, bool):
        raise WinnerProbabilityConfigError("cold_start.show_insufficient_reasons must be boolean")
    minimum_display_n = int(_number(raw.get("minimum_display_n"), "cold_start.minimum_display_n"))
    if minimum_display_n < 0:
        raise WinnerProbabilityConfigError("cold_start.minimum_display_n must be non-negative")
    return {
        "show_raw_evidence_counts": show_counts,
        "show_insufficient_reasons": show_reasons,
        "minimum_display_n": minimum_display_n,
    }


def _parse_drift(raw: dict[str, Any]) -> DriftConfig:
    windows = {
        key: int(_number(value, f"drift.windows_sessions.{key}"))
        for key, value in _mapping(raw, "windows_sessions").items()
    }
    if any(value <= 0 for value in windows.values()):
        raise WinnerProbabilityConfigError("drift.windows_sessions values must be positive")
    thresholds = {
        key: _number(value, f"drift.thresholds.{key}")
        for key, value in _mapping(raw, "thresholds").items()
    }
    for field in ("brier_score_delta", "ece_delta", "win_rate_delta", "psi"):
        _ratio(thresholds.get(field), f"drift.thresholds.{field}")
    min_sample = int(_number(thresholds.get("min_sample"), "drift.thresholds.min_sample"))
    if min_sample <= 0:
        raise WinnerProbabilityConfigError("drift.thresholds.min_sample must be positive")
    thresholds["min_sample"] = min_sample
    return DriftConfig(windows_sessions=windows, thresholds=thresholds)


def _parse_model_governance(raw: dict[str, Any]) -> ModelGovernanceConfig:
    approved_algorithms = tuple(
        _text_list(raw.get("approved_algorithms"), "model_governance.approved_algorithms")
    )
    if not approved_algorithms:
        raise WinnerProbabilityConfigError(
            "model_governance.approved_algorithms must not be empty"
        )
    if len(set(approved_algorithms)) != len(approved_algorithms):
        raise WinnerProbabilityConfigError(
            "model_governance.approved_algorithms contains duplicates"
        )
    gates = _mapping(raw, "promotion_gates")
    min_log_loss_improvement = _ratio(
        gates.get("min_log_loss_improvement"),
        "model_governance.promotion_gates.min_log_loss_improvement",
    )
    min_brier_improvement = _ratio(
        gates.get("min_brier_improvement"),
        "model_governance.promotion_gates.min_brier_improvement",
    )
    require_calibration_bins = gates.get("require_calibration_bins")
    require_fresh_drift_metrics = gates.get("require_fresh_drift_metrics")
    if not isinstance(require_calibration_bins, bool):
        raise WinnerProbabilityConfigError(
            "model_governance.promotion_gates.require_calibration_bins must be boolean"
        )
    if not isinstance(require_fresh_drift_metrics, bool):
        raise WinnerProbabilityConfigError(
            "model_governance.promotion_gates.require_fresh_drift_metrics must be boolean"
        )
    return ModelGovernanceConfig(
        approved_algorithms=approved_algorithms,
        promotion_gates={
            "min_log_loss_improvement": min_log_loss_improvement,
            "min_brier_improvement": min_brier_improvement,
            "require_calibration_bins": require_calibration_bins,
            "require_fresh_drift_metrics": require_fresh_drift_metrics,
        },
    )


def _parse_retention(raw: dict[str, Any]) -> RetentionConfig:
    permanent = tuple(_text_list(raw.get("permanent"), "retention.permanent"))
    rebuildable = tuple(_text_list(raw.get("rebuildable"), "retention.rebuildable"))
    missing = sorted(REQUIRED_PERMANENT_RETENTION - set(permanent))
    if missing:
        raise WinnerProbabilityConfigError(
            f"retention.permanent missing required class(es): {', '.join(missing)}"
        )
    days = int(
        _number(
            raw.get("operational_log_retention_days"),
            "retention.operational_log_retention_days",
        )
    )
    if days <= 0:
        raise WinnerProbabilityConfigError(
            "retention.operational_log_retention_days must be positive"
        )
    return RetentionConfig(
        permanent=permanent,
        rebuildable=rebuildable,
        operational_log_retention_days=days,
    )


def _parse_api(raw: dict[str, Any]) -> ApiConfig:
    default_view = _required_text(raw, "api.default_view")
    _require_choice(default_view, VALID_ESTIMATE_VIEWS, "api.default_view")
    default_kind = _required_text(raw, "api.default_estimate_kind")
    _require_choice(default_kind, VALID_ESTIMATE_KINDS, "api.default_estimate_kind")
    max_window = int(_number(raw.get("max_as_of_window_days"), "api.max_as_of_window_days"))
    max_page_size = int(_number(raw.get("max_page_size"), "api.max_page_size"))
    if max_window <= 0:
        raise WinnerProbabilityConfigError("api.max_as_of_window_days must be positive")
    if max_page_size <= 0:
        raise WinnerProbabilityConfigError("api.max_page_size must be positive")
    return ApiConfig(
        default_view=default_view,
        default_estimate_kind=default_kind,
        max_as_of_window_days=max_window,
        max_page_size=max_page_size,
    )


def _parse_feature_schema(
    raw: dict[str, Any],
    registry: FeatureSchemaRegistry,
) -> FeatureSchemaConfig:
    version = _required_text(raw, "feature_schema.version")
    if version != registry.version:
        raise WinnerProbabilityConfigError(
            "feature_schema.version must match engine schema version"
        )
    core_features = tuple(_text_list(raw.get("core_features"), "feature_schema.core_features"))
    if not core_features:
        raise WinnerProbabilityConfigError("feature_schema.core_features must not be empty")
    try:
        registry.require_feature_names(core_features, "feature_schema.core_features")
    except FeatureSchemaError as exc:
        raise WinnerProbabilityConfigError(str(exc)) from exc
    return FeatureSchemaConfig(version=version, core_features=core_features)


def _parse_filters(raw: dict[str, Any]) -> dict[str, Any]:
    for field in (
        "probability",
        "lower_bound",
        "interval_width",
        "target_first_rate",
    ):
        limits = _mapping(raw, field)
        _ratio(limits.get("min"), f"filters.{field}.min")
        _ratio(limits.get("max"), f"filters.{field}.max")
        if float(limits["min"]) > float(limits["max"]):
            raise WinnerProbabilityConfigError(f"filters.{field}.min must be <= max")
    for field in ("expected_return_pct", "median_return_pct", "mfe_pct", "mae_pct"):
        limits = _mapping(raw, field)
        _number(limits.get("min"), f"filters.{field}.min")
        _number(limits.get("max"), f"filters.{field}.max")
        if float(limits["min"]) > float(limits["max"]):
            raise WinnerProbabilityConfigError(f"filters.{field}.min must be <= max")
    sample = _mapping(raw, "effective_sample_size")
    if int(_number(sample.get("min"), "filters.effective_sample_size.min")) < 0:
        raise WinnerProbabilityConfigError("filters.effective_sample_size.min must be non-negative")
    for field in ("evidence_grade", "earnings_risk", "data_quality"):
        allowed = _text_list(_mapping(raw, field).get("allowed"), f"filters.{field}.allowed")
        if not allowed:
            raise WinnerProbabilityConfigError(f"filters.{field}.allowed must not be empty")
    return raw


def _validate_cross_section_rules(config: WinnerProbabilityConfig) -> None:
    if config.engine.feature_schema_version != config.feature_schema.version:
        raise WinnerProbabilityConfigError("engine and feature schema versions must match")
    if config.primary_outcome_definition.entry_model != config.entry_models.production:
        raise WinnerProbabilityConfigError(
            "primary outcome definition must use production entry model"
        )
    for outcome in config.outcome_definitions:
        if outcome.horizon_sessions not in config.horizon.sessions:
            raise WinnerProbabilityConfigError(
                f"{outcome.id} horizon_sessions must be configured in horizon.sessions"
            )
        if (
            outcome.entry_model in config.entry_models.diagnostics
            and outcome.id == config.primary_outcome_definition.id
        ):
            raise WinnerProbabilityConfigError(
                "production and diagnostic entry models cannot share an outcome identifier"
            )
    if config.api.default_estimate_kind not in config.estimate_kinds:
        raise WinnerProbabilityConfigError("api.default_estimate_kind must be configured")


def _mapping(raw: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = raw.get(field_name)
    if not isinstance(value, dict):
        raise WinnerProbabilityConfigError(f"{field_name} must be a mapping")
    return value


def _list(raw: dict[str, Any], field_name: str) -> list[Any]:
    value = raw.get(field_name)
    if not isinstance(value, list):
        raise WinnerProbabilityConfigError(f"{field_name} must be a list")
    return value


def _text_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise WinnerProbabilityConfigError(f"{field_name} must be a list")
    result = [str(item).strip() for item in value]
    if any(not item for item in result):
        raise WinnerProbabilityConfigError(f"{field_name} values must be text")
    return result


def _required_text(raw: dict[str, Any], field_name: str) -> str:
    key = field_name.split(".")[-1]
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise WinnerProbabilityConfigError(f"{field_name} is required")
    return value.strip()


def _number(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise WinnerProbabilityConfigError(f"{field_name} must be numeric") from exc


def _ratio(value: Any, field_name: str) -> float:
    number = _number(value, field_name)
    if number < 0 or number > 1:
        raise WinnerProbabilityConfigError(f"{field_name} must be between 0 and 1")
    return float(number)


def _require_choice(value: str, allowed: frozenset[str], field_name: str) -> None:
    if value not in allowed:
        raise WinnerProbabilityConfigError(
            f"{field_name} must be one of {', '.join(sorted(allowed))}"
        )
