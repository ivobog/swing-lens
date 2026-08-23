"""Deterministic, research-only Technical Scoring v5.1 overlay policies.

The module deliberately treats v4 as the ranking control.  Extension and trigger
state may filter candidates or order names inside fixed v4 score bands; they never
replace the production scorer and they never read forward outcomes when assigning a
state or rank.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

from app.services.technical_v5_calibration import time_ordered_split_labels
from app.services.technical_v5_forensics import (
    cluster_mean_ci,
    outcome_metrics,
    within_run_spearman,
)

RESEARCH_VERSION = "5.1.0-research.1"
CONFIG_PATH = Path("config/technical_v51_research.yaml")
EXTENSION_STATES = ("HEALTHY", "MODERATE", "EXTENDED", "EXTREME")
TRIGGER_STATES = (
    "INVALIDATED",
    "TOO_FAR_BELOW",
    "APPROACHING",
    "NEAR",
    "AT_TRIGGER",
    "FRESHLY_TRIGGERED",
    "BEYOND_TRIGGER",
    "EXTENDED_BEYOND_TRIGGER",
    "NOT_APPLICABLE",
)
CANONICAL_ACTIONS = (
    "ENTER",
    "WATCH",
    "WAIT_FOR_TRIGGER",
    "DEFENSIVE",
    "AVOID",
    "NO_TRADE",
    "FILTERED",
)
HORIZONS = (1, 3, 5, 10)
RAW_EXTENSION_COLUMNS = (
    "ema20_extension_pct",
    "sma50_extension_pct",
    "ema20_extension_atr",
    "sma50_extension_atr",
    "extension_percentile",
    "rsi",
    "roc10",
    "roc21",
    "stage",
    "climax_risk",
    "trigger_distance_atr",
)
TRIGGER_DIAGNOSTIC_COLUMNS = (
    "volume_confirmation",
    "strong_close_ratio",
    "breakout_volume_confirmed",
    "breakout_volume_percentile",
    "gap_up_pct",
    "gap_exhaustion",
    "forward_return_1d",
    "ema20_extension_pct",
    "sma50_extension_pct",
    "setup_type",
    "stage",
    "market_regime",
    "v4_score",
    "v5_TS",
    "v5_SQ",
    "v5_EQ",
)
REQUIRED_COLUMNS = {
    "run_id",
    "ticker",
    "decision_date",
    "input_signature",
    "reconstruction_status",
    "v4_score",
    "v4_action",
    "v5_TS",
    "v5_SQ",
    "v5_EQ",
    "v5_TCS",
    "trigger_state",
    "danger_state",
    "setup_type",
    "market_regime",
    "sector",
    *RAW_EXTENSION_COLUMNS,
}


def load_research_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    research = _mapping(config, "research")
    if str(research.get("version")) != RESEARCH_VERSION:
        raise ValueError(f"research.version must be {RESEARCH_VERSION}")
    if bool(research.get("production_enabled")):
        raise ValueError("v5.1 overlay config must remain research-only")
    threshold_sets = _mapping(config, "extension_threshold_sets")
    if not 1 <= len(threshold_sets) <= 3:
        raise ValueError("define one to three fixed extension threshold sets")
    active = str(research.get("active_extension_threshold_set"))
    if active not in threshold_sets:
        raise ValueError("active extension threshold set is missing")
    for set_id, thresholds in threshold_sets.items():
        if not isinstance(thresholds, dict):
            raise ValueError(f"extension threshold set {set_id} must be a mapping")
        for field, values in thresholds.items():
            if not isinstance(values, list) or len(values) != 3:
                raise ValueError(f"{set_id}.{field} must contain three thresholds")
            numeric = [float(value) for value in values]
            if numeric != sorted(numeric):
                raise ValueError(f"{set_id}.{field} thresholds must be ascending")
    trigger = _mapping(config, "trigger_policy")
    unknown = set(trigger.get("preference", {})) - set(TRIGGER_STATES)
    if unknown:
        raise ValueError(f"unknown trigger states in preference: {sorted(unknown)}")
    return config


def stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_research_frame(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"v5.1 source dataset is missing columns: {', '.join(missing)}")
    result = frame.copy()
    result["decision_date"] = pd.to_datetime(result.decision_date).dt.date
    duplicated = result.duplicated(["run_id", "ticker"], keep=False)
    if duplicated.any():
        raise ValueError("v5.1 source dataset must be unique by run_id and ticker")
    allowed_reconstruction = {"CURRENT_SERIES_RECONCILED", "REVISION_HISTORY_RECONCILED"}
    observed = set(result.reconstruction_status.dropna().astype(str))
    if not observed or not observed <= allowed_reconstruction:
        raise ValueError(f"unsafe or unknown reconstruction statuses: {sorted(observed)}")
    result["trigger_state_canonical"] = result.trigger_state.map(normalize_trigger_state)
    if result.trigger_state_canonical.isna().any():
        values = sorted(result.loc[result.trigger_state_canonical.isna(), "trigger_state"].unique())
        raise ValueError(f"unknown historical trigger states: {values}")
    for column in ("v4_score", "v5_TS", "v5_SQ", "v5_EQ", "v5_TCS"):
        result[column] = pd.to_numeric(result[column], errors="coerce")
    if result[["v4_score", "v5_TS", "v5_TCS"]].isna().any().any():
        raise ValueError("baseline score columns may not be missing")
    return result


def normalize_trigger_state(value: Any) -> str | None:
    text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return text if text in TRIGGER_STATES else None


def add_extension_states(frame: pd.DataFrame, config: Mapping[str, Any]) -> pd.DataFrame:
    result = frame.copy()
    sets = _mapping(config, "extension_threshold_sets")
    active = str(_mapping(config, "research")["active_extension_threshold_set"])
    for set_id, thresholds in sets.items():
        classified = result.apply(
            lambda row, limits=thresholds, name=set_id: classify_extension_row(
                row, limits, threshold_set_id=name
            ),
            axis=1,
            result_type="expand",
        )
        classified.columns = [
            f"extension_state_{set_id}",
            f"extension_maturity_score_{set_id}",
            f"extension_evidence_count_{set_id}",
            f"extension_reasons_{set_id}",
        ]
        result = pd.concat([result, classified], axis=1)
    result["extension_threshold_set_id"] = active
    result["extension_state"] = result[f"extension_state_{active}"]
    result["extension_maturity_score"] = result[f"extension_maturity_score_{active}"]
    result["extension_evidence_count"] = result[f"extension_evidence_count_{active}"]
    result["extension_reasons"] = result[f"extension_reasons_{active}"]
    return result


def classify_extension_row(
    row: Mapping[str, Any] | pd.Series,
    thresholds: Mapping[str, Iterable[float]],
    *,
    threshold_set_id: str,
) -> tuple[str, float, int, str]:
    """Classify maturity using decision-time inputs only.

    Two agreeing inputs are required for EXTENDED/EXTREME.  A single extended input
    raises the state only to MODERATE.  Stage 4 or climax >= the extreme threshold is
    an explicit hard override; Stage 3 or an extended climax is an extended override.
    """
    values = {
        "ema20_extension_pct": _number(row.get("ema20_extension_pct")),
        "sma50_extension_pct": _number(row.get("sma50_extension_pct")),
        "ema20_extension_atr": _number(row.get("ema20_extension_atr")),
        "sma50_extension_atr": _number(row.get("sma50_extension_atr")),
        "extension_percentile": _number(row.get("extension_percentile")),
        "rsi": _number(row.get("rsi")),
        "roc10": _number(row.get("roc10")),
        "roc21": _number(row.get("roc21")),
        "climax_risk": _number(row.get("climax_risk")),
        "trigger_extension_atr": _trigger_extension(row.get("trigger_distance_atr")),
    }
    severities: dict[str, int] = {}
    for field, value in values.items():
        if value is None:
            continue
        limits = [float(item) for item in thresholds[field]]
        severities[field] = sum(value >= limit for limit in limits)
    stage = str(row.get("stage") or "").strip().lower().replace("_", " ")
    climax = values.get("climax_risk")
    climax_limits = [float(item) for item in thresholds["climax_risk"]]
    if stage == "stage 4" or (climax is not None and climax >= climax_limits[2]):
        level = 3
    elif stage == "stage 3" or (climax is not None and climax >= climax_limits[1]):
        level = 2
    elif sum(value >= 3 for value in severities.values()) >= 2:
        level = 3
    elif sum(value >= 2 for value in severities.values()) >= 2:
        level = 2
    elif sum(value >= 1 for value in severities.values()) >= 2 or any(
        value >= 2 for value in severities.values()
    ):
        level = 1
    else:
        level = 0
    evidence_count = len(severities)
    maturity_score = (
        round(100.0 * sum(severities.values()) / (3.0 * evidence_count), 4)
        if evidence_count
        else 0.0
    )
    reasons = ";".join(
        f"{field}:{severity}" for field, severity in sorted(severities.items()) if severity
    )
    if stage in {"stage 3", "stage 4"}:
        reasons = f"stage:{stage[-1]}" + (f";{reasons}" if reasons else "")
    return EXTENSION_STATES[level], maturity_score, evidence_count, reasons


def extension_forensics(
    frame: pd.DataFrame, config: Mapping[str, Any], *, bootstrap_samples: int = 300
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    raw = [
        "run_id",
        "ticker",
        "decision_date",
        "input_signature",
        *RAW_EXTENSION_COLUMNS,
    ]
    for set_id in _mapping(config, "extension_threshold_sets"):
        state_column = f"extension_state_{set_id}"
        score_column = f"extension_maturity_score_{set_id}"
        evidence_column = f"extension_evidence_count_{set_id}"
        reasons_column = f"extension_reasons_{set_id}"
        for source_record in frame[
            [*raw, state_column, score_column, evidence_column, reasons_column]
        ].to_dict("records"):
            record = dict(source_record)
            state = record.pop(state_column)
            score = record.pop(score_column)
            evidence = record.pop(evidence_column)
            reasons = record.pop(reasons_column)
            rows.append(
                {
                    "record_type": "OBSERVATION",
                    "threshold_set_id": set_id,
                    **record,
                    "extension_state": state,
                    "extension_maturity_score": score,
                    "extension_evidence_count": evidence,
                    "extension_reasons": reasons,
                }
            )
        for state, group in frame.groupby(state_column, dropna=False):
            rows.append(
                {
                    "record_type": "STATE_SUMMARY",
                    "threshold_set_id": set_id,
                    "extension_state": state,
                    **research_metrics(group, bootstrap_samples=bootstrap_samples),
                }
            )
        for ts_threshold in (7.0, 7.5, 8.0):
            high_ts = frame[frame.v5_TS >= ts_threshold]
            for label, states in (
                ("LOW_EXTENSION", {"HEALTHY", "MODERATE"}),
                ("HIGH_EXTENSION", {"EXTENDED", "EXTREME"}),
            ):
                group = high_ts[high_ts[state_column].isin(states)]
                rows.append(
                    {
                        "record_type": "TS_EXTENSION_COHORT",
                        "threshold_set_id": set_id,
                        "ts_threshold": ts_threshold,
                        "extension_group": label,
                        **research_metrics(group, bootstrap_samples=bootstrap_samples),
                    }
                )
            if set_id == str(_mapping(config, "research")["active_extension_threshold_set"]):
                for label, mask in (
                    ("LOW_EXTENSION_LT_P50", high_ts.extension_percentile.lt(50.0)),
                    ("HIGH_EXTENSION_GE_P80", high_ts.extension_percentile.ge(80.0)),
                ):
                    group = high_ts[mask]
                    rows.append(
                        {
                            "record_type": "TS_EXTENSION_PERCENTILE_REPLICATION",
                            "threshold_set_id": "PERSISTED_EXTENSION_PERCENTILE",
                            "ts_threshold": ts_threshold,
                            "extension_group": label,
                            **research_metrics(group, bootstrap_samples=bootstrap_samples),
                        }
                    )
    return pd.DataFrame(rows)


def trigger_forensics(frame: pd.DataFrame, *, bootstrap_samples: int = 300) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for state, group in frame.groupby("trigger_state_canonical", dropna=False):
        rows.append(
            {
                "record_type": "STATE_SUMMARY",
                "trigger_state": state,
                **research_metrics(group, bootstrap_samples=bootstrap_samples),
            }
        )
    comparison = frame[
        frame.trigger_state_canonical.isin({"AT_TRIGGER", "FRESHLY_TRIGGERED"})
    ]
    observation_columns = [
        "run_id",
        "ticker",
        "decision_date",
        "input_signature",
        "trigger_state_canonical",
        "trigger_distance_atr",
        *[column for column in TRIGGER_DIAGNOSTIC_COLUMNS if column in frame],
    ]
    for record in comparison[observation_columns].to_dict("records"):
        rows.append({"record_type": "AT_VS_FRESH_OBSERVATION", **record})
    for field in TRIGGER_DIAGNOSTIC_COLUMNS:
        if field not in comparison:
            continue
        bucketed = _diagnostic_bucket(comparison, field)
        for (state, value), group in bucketed.groupby(
            ["trigger_state_canonical", "_diagnostic_value"], dropna=False
        ):
            rows.append(
                {
                    "record_type": "AT_VS_FRESH_DIAGNOSTIC",
                    "trigger_state": state,
                    "diagnostic": field,
                    "diagnostic_value": value,
                    **research_metrics(group, bootstrap_samples=bootstrap_samples),
                }
            )
    return pd.DataFrame(rows)


def build_candidate_rankings(
    frame: pd.DataFrame,
    config: Mapping[str, Any],
    *,
    candidate_config_hash: str,
    git_commit: str,
) -> pd.DataFrame:
    """Apply the fixed candidate policies without consulting any outcome column."""
    research = _mapping(config, "research")
    trigger_policy = _mapping(config, "trigger_policy")
    danger_labels = set(_mapping(config, "danger").get("labels", []))
    band_size = float(research["v4_secondary_sort_band_size"])
    preference = {str(key): int(value) for key, value in trigger_policy["preference"].items()}
    eligible_triggers = set(trigger_policy["eligible_states"])
    base = frame.copy()
    base["v4_band"] = np.floor(base.v4_score / band_size).astype(int)
    base["extension_order"] = base.extension_state.map(
        dict(zip(EXTENSION_STATES, range(4), strict=True))
    )
    base["trigger_order"] = base.trigger_state_canonical.map(preference)
    base["has_danger_label"] = base.danger_state.fillna("").astype(str).isin(danger_labels)
    input_columns = [
        "run_id",
        "ticker",
        "decision_date",
        "input_signature",
        "v4_score",
        "v5_TCS",
        "v5_TS",
        "v5_SQ",
        "v5_EQ",
        "v4_action",
        "extension_state",
        "extension_threshold_set_id",
        "extension_maturity_score",
        "trigger_state_canonical",
        "trigger_distance_atr",
        "danger_state",
        "setup_type",
        "market_regime",
        "sector",
        *RAW_EXTENSION_COLUMNS,
    ]
    outcome_columns = [
        column
        for column in base.columns
        if column.startswith(("forward_return_", "MFE_", "MAE_", "time_to_MFE_"))
        or column in {"first_hit_10d", "time_to_target"}
    ]
    input_columns = list(dict.fromkeys([*input_columns, *outcome_columns]))
    records: list[pd.DataFrame] = []
    policy_specs = (
        ("M0_V4", "CONTROL", lambda data: pd.Series(True, index=data.index), ["v4_score"]),
        (
            "M1A_V4_FILTER_EXTREME",
            "EXTENSION",
            lambda data: data.extension_state.ne("EXTREME"),
            ["v4_score"],
        ),
        (
            "M1B_V4_FILTER_EXTENDED_EXTREME",
            "EXTENSION",
            lambda data: data.extension_state.isin({"HEALTHY", "MODERATE"}),
            ["v4_score"],
        ),
        (
            "M1C_V4_EXTENSION_SECONDARY",
            "EXTENSION",
            lambda data: pd.Series(True, index=data.index),
            ["v4_band", "extension_order", "v4_score"],
        ),
        (
            "M2_V4_TRIGGER",
            "TRIGGER",
            lambda data: data.trigger_state_canonical.isin(eligible_triggers),
            ["v4_band", "trigger_order", "v4_score"],
        ),
        (
            "M3_V4_EXTENSION_TRIGGER",
            "EXTENSION_TRIGGER",
            lambda data: data.extension_state.ne("EXTREME")
            & data.trigger_state_canonical.isin(eligible_triggers),
            ["v4_band", "extension_order", "trigger_order", "v4_score"],
        ),
        (
            "M4_TS_GATE_6.0_V4",
            "TS_GATE",
            lambda data: data.v5_TS.ge(6.0),
            ["v4_score"],
        ),
        (
            "M4_TS_GATE_6.5_V4",
            "TS_GATE",
            lambda data: data.v5_TS.ge(6.5),
            ["v4_score"],
        ),
        (
            "M4_TS_GATE_7.0_V4",
            "TS_GATE",
            lambda data: data.v5_TS.ge(7.0),
            ["v4_score"],
        ),
        (
            "V5_BASELINE",
            "FROZEN_CONTROL",
            lambda data: pd.Series(True, index=data.index),
            ["v5_TCS"],
        ),
    )
    for danger_variant in ("NO_DANGER_EXCLUSION", "EXCLUDE_DANGER"):
        for candidate_id, family, eligibility, sort_columns in policy_specs:
            if candidate_id == "V5_BASELINE" and danger_variant == "EXCLUDE_DANGER":
                continue
            eligible = eligibility(base)
            if danger_variant == "EXCLUDE_DANGER":
                eligible &= ~base.has_danger_label
            candidate = base.loc[eligible, input_columns].copy()
            candidate["candidate_id"] = candidate_id
            candidate["candidate_family"] = family
            candidate["candidate_version"] = RESEARCH_VERSION
            candidate["config_hash"] = candidate_config_hash
            candidate["git_commit"] = git_commit
            candidate["danger_variant"] = danger_variant
            candidate["candidate_score"] = candidate[
                "v5_TCS" if candidate_id == "V5_BASELINE" else "v4_score"
            ]
            sort_ascending = []
            for column in sort_columns:
                sort_ascending.append(column in {"extension_order", "trigger_order"})
                if column not in candidate:
                    candidate[column] = base.loc[candidate.index, column]
            candidate = candidate.sort_values(
                ["run_id", *sort_columns, "ticker"],
                ascending=[True, *sort_ascending, True],
                kind="mergesort",
            )
            candidate["candidate_rank"] = candidate.groupby("run_id").cumcount() + 1
            candidate["eligible_count"] = candidate.groupby("run_id").ticker.transform("size")
            candidate["candidate_rank_pct"] = 1.0 - (
                (candidate.candidate_rank - 1) / candidate.eligible_count
            )
            candidate["candidate_rank_score"] = (
                candidate.eligible_count - candidate.candidate_rank + 1
            )
            records.append(candidate)
    return pd.concat(records, ignore_index=True, sort=False)


def candidate_evaluation(
    rankings: pd.DataFrame,
    source: pd.DataFrame,
    *,
    bootstrap_samples: int = 300,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    split_source = source[source.forward_return_10d.notna()]
    date_labels = time_ordered_split_labels(split_source.decision_date)
    split_map = dict(zip(split_source.decision_date.astype(str), date_labels, strict=False))
    work = rankings.copy()
    work["split"] = work.decision_date.astype(str).map(split_map)
    scopes: list[tuple[str, pd.DataFrame]] = [("all", work)]
    split_work = work[work.forward_return_10d.notna()]
    scopes.extend((name, group) for name, group in split_work.groupby("split", sort=False))
    selections = (
        ("ALL_ELIGIBLE", lambda data: pd.Series(True, index=data.index)),
        ("TOP_10_PCT", lambda data: data.candidate_rank_pct >= 0.90),
        ("TOP_20_PCT", lambda data: data.candidate_rank_pct >= 0.80),
    )
    rows: list[dict[str, Any]] = []
    selected_rows: list[pd.DataFrame] = []
    for scope, scoped in scopes:
        for (danger_variant, candidate_id), eligible in scoped.groupby(
            ["danger_variant", "candidate_id"], sort=False
        ):
            for selection, selector in selections:
                selected = eligible[selector(eligible)].copy()
                selected["selection"] = selection
                selected["evaluation_scope"] = scope
                selected_rows.append(selected)
                turnover = _selection_turnover(
                    scoped,
                    selected,
                    candidate_id=candidate_id,
                    danger_variant=danger_variant,
                    selection=selection,
                    selector=selector,
                )
                paired = _paired_selection_delta(
                    scoped,
                    selected,
                    candidate_id=candidate_id,
                    danger_variant=danger_variant,
                    selector=selector,
                )
                scope_source = (
                    source
                    if scope == "all"
                    else split_source[
                        split_source.decision_date.astype(str).map(split_map).eq(scope)
                    ]
                )
                row = {
                    "candidate_id": candidate_id,
                    "candidate_version": eligible.candidate_version.iloc[0],
                    "config_hash": eligible.config_hash.iloc[0],
                    "git_commit": eligible.git_commit.iloc[0],
                    "decision_date": "MULTIPLE",
                    "decision_date_coverage": ";".join(
                        sorted(str(value) for value in eligible.decision_date.unique())
                    ),
                    "input_signature": stable_hash(
                        sorted(eligible.input_signature.astype(str).unique())
                    ),
                    "danger_variant": danger_variant,
                    "evaluation_scope": scope,
                    "selection": selection,
                    "coverage": len(eligible) / max(1, len(scope_source)),
                    "candidate_count": len(eligible),
                    "independent_dates": selected.decision_date.nunique(),
                    "rank_correlation_5d": within_run_spearman(
                        eligible, "candidate_rank_score", "forward_return_5d"
                    ),
                    "rank_correlation_10d": within_run_spearman(
                        eligible, "candidate_rank_score", "forward_return_10d"
                    ),
                    "rank_correlation_vs_v4": _rank_correlation_vs_v4(
                        scoped, eligible, danger_variant
                    ),
                    "candidate_turnover_vs_v4": turnover,
                    **research_metrics(selected, bootstrap_samples=bootstrap_samples),
                    **paired,
                }
                rows.append(row)
    results = pd.DataFrame(rows)
    top = pd.concat(selected_rows, ignore_index=True, sort=False)
    top = top[top.selection.ne("ALL_ELIGIBLE")].reset_index(drop=True)
    top_columns = [
        "candidate_id",
        "candidate_family",
        "candidate_version",
        "config_hash",
        "git_commit",
        "danger_variant",
        "evaluation_scope",
        "selection",
        "run_id",
        "ticker",
        "decision_date",
        "input_signature",
        "candidate_score",
        "candidate_rank",
        "candidate_rank_pct",
        "eligible_count",
        "v4_score",
        "v5_TCS",
        "v5_TS",
        "extension_state",
        "extension_threshold_set_id",
        "extension_maturity_score",
        "trigger_state_canonical",
        "trigger_distance_atr",
        "danger_state",
        "setup_type",
        "market_regime",
        *[
            column
            for column in top.columns
            if column.startswith(("forward_return_", "MFE_", "MAE_"))
        ],
    ]
    top = top[list(dict.fromkeys(top_columns))]
    walk_forward = results[results.evaluation_scope.isin({"calibration", "validation", "holdout"})]
    return results, top, walk_forward.reset_index(drop=True)


def interaction_analysis(
    frame: pd.DataFrame, *, bootstrap_samples: int = 300
) -> pd.DataFrame:
    work = frame.copy()
    work["v4_band_label"] = pd.cut(
        work.v4_score,
        [-np.inf, 6.0, 7.0, 8.0, np.inf],
        labels=["LT_6", "6_TO_7", "7_TO_8", "GE_8"],
        right=False,
    ).astype("object")
    work["ts_band_label"] = pd.cut(
        work.v5_TS,
        [-np.inf, 6.0, 7.0, 8.0, np.inf],
        labels=["LT_6", "6_TO_7", "7_TO_8", "GE_8"],
        right=False,
    ).astype("object")
    studies = (
        ("V4_BAND_X_EXTENSION", ["v4_band_label", "extension_state"]),
        ("V4_BAND_X_TRIGGER", ["v4_band_label", "trigger_state_canonical"]),
        (
            "V4_BAND_X_EXTENSION_X_TRIGGER",
            ["v4_band_label", "extension_state", "trigger_state_canonical"],
        ),
        ("TS_BAND_X_EXTENSION", ["ts_band_label", "extension_state"]),
        ("TS_BAND_X_TRIGGER", ["ts_band_label", "trigger_state_canonical"]),
        ("SETUP_TYPE_X_TRIGGER", ["setup_type", "trigger_state_canonical"]),
        ("SETUP_TYPE_X_EXTENSION", ["setup_type", "extension_state"]),
        ("REGIME_X_TRIGGER", ["market_regime", "trigger_state_canonical"]),
        ("REGIME_X_EXTENSION", ["market_regime", "extension_state"]),
    )
    rows: list[dict[str, Any]] = []
    for study, fields in studies:
        for keys, group in work.groupby(fields, dropna=False, observed=True):
            values = keys if isinstance(keys, tuple) else (keys,)
            rows.append(
                {
                    "interaction": study,
                    **dict(zip(fields, values, strict=True)),
                    **research_metrics(group, bootstrap_samples=bootstrap_samples),
                }
            )
    return pd.DataFrame(rows)


def action_transitions(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["v4_canonical_action"] = work.v4_action.map(normalize_action)
    policies = {
        "M1A_V4_FILTER_EXTREME": lambda row: "FILTERED"
        if row.extension_state == "EXTREME"
        else row.v4_canonical_action,
        "M1B_V4_FILTER_EXTENDED_EXTREME": lambda row: "FILTERED"
        if row.extension_state in {"EXTENDED", "EXTREME"}
        else row.v4_canonical_action,
        "M1C_V4_EXTENSION_SECONDARY": lambda row: row.v4_canonical_action,
        "M2_V4_TRIGGER": _trigger_overlay_action,
        "M3_V4_EXTENSION_TRIGGER": lambda row: "FILTERED"
        if row.extension_state == "EXTREME"
        else _trigger_overlay_action(row),
    }
    rows: list[dict[str, Any]] = []
    total = len(work)
    for candidate_id, policy in policies.items():
        candidate_actions = work.apply(policy, axis=1)
        candidate_frame = work.assign(candidate_action=candidate_actions)
        for (before, after), group in candidate_frame.groupby(
            ["v4_canonical_action", "candidate_action"], dropna=False
        ):
            rows.append(
                {
                    "candidate_id": candidate_id,
                    "from_action": before,
                    "to_action": after,
                    "action_changed": before != after,
                    "count": len(group),
                    "percentage": len(group) / total if total else None,
                    **research_metrics(group),
                }
            )
    return pd.DataFrame(rows)


def normalize_action(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text.upper() in CANONICAL_ACTIONS:
        return text.upper()
    if not text or text in {"no data", "none", "nan"}:
        return "NO_TRADE"
    if "filter" in text:
        return "FILTERED"
    if "avoid" in text or "exit risk" in text or "overheated" in text:
        return "AVOID"
    if "defensive" in text or "reduce" in text or "trail" in text:
        return "DEFENSIVE"
    if "buyable" in text or "breakout buy" in text or text.startswith("entry candidate"):
        return "ENTER"
    if "wait" in text or "not ready" in text or "failed" in text or "risk-off" in text:
        return "WAIT_FOR_TRIGGER"
    if "watch" in text or "good chart" in text or "momentum" in text:
        return "WATCH"
    if "no clear trade" in text or "no qualified setup" in text:
        return "NO_TRADE"
    return "WATCH"


def coverage_report(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "dimension": "TOTAL",
            "value": "ALL",
            "observations": len(frame),
            "runs": frame.run_id.nunique(),
            "independent_dates": frame.decision_date.nunique(),
            **outcome_metrics(frame),
        }
    ]
    for field in (
        "decision_date",
        "market_regime",
        "setup_type",
        "sector",
        "stage",
        "trigger_state_canonical",
        "extension_state",
    ):
        values = frame[field].astype("object").where(frame[field].notna(), "MISSING")
        for value, group in frame.assign(_coverage_value=values).groupby(
            "_coverage_value", dropna=False
        ):
            rows.append(
                {
                    "dimension": field,
                    "value": value,
                    "observations": len(group),
                    "runs": group.run_id.nunique(),
                    "independent_dates": group.decision_date.nunique(),
                    **outcome_metrics(group),
                }
            )
    return pd.DataFrame(rows)


def research_metrics(
    frame: pd.DataFrame, *, bootstrap_samples: int = 300
) -> dict[str, Any]:
    result = {
        "independent_dates": int(frame.decision_date.nunique())
        if "decision_date" in frame
        else 0,
        **outcome_metrics(frame),
    }
    for horizon in HORIZONS:
        ci = cluster_mean_ci(
            frame,
            f"forward_return_{horizon}d",
            samples=bootstrap_samples,
            seed=20260823 + horizon,
        )
        result[f"bootstrap_clusters_{horizon}d"] = ci["bootstrap_clusters"]
        result[f"mean_return_{horizon}d_ci_low"] = ci["bootstrap_mean_ci_low"]
        result[f"mean_return_{horizon}d_ci_high"] = ci["bootstrap_mean_ci_high"]
    return result


def determine_verdict(
    results: pd.DataFrame,
    *,
    independent_dates: int,
    minimum_dates: int,
) -> str:
    if independent_dates < minimum_dates:
        return "CONTINUE SHADOW"
    holdout = results[
        (results.evaluation_scope == "holdout")
        & (results.selection == "TOP_20_PCT")
        & (results.danger_variant == "NO_DANGER_EXCLUSION")
    ].copy()
    baseline = holdout[holdout.candidate_id == "M0_V4"]
    if baseline.empty:
        return "INSUFFICIENT DATA"
    candidates = holdout[
        holdout.candidate_id.isin(
            {
                "M1A_V4_FILTER_EXTREME",
                "M1B_V4_FILTER_EXTENDED_EXTREME",
                "M1C_V4_EXTENSION_SECONDARY",
                "M2_V4_TRIGGER",
                "M3_V4_EXTENSION_TRIGGER",
                "M4_TS_GATE_6.0_V4",
                "M4_TS_GATE_6.5_V4",
                "M4_TS_GATE_7.0_V4",
            }
        )
    ]
    qualified = candidates[
        candidates.mean_return_5d.gt(float(baseline.mean_return_5d.iloc[0]))
        & candidates.mean_return_10d.gt(float(baseline.mean_return_10d.iloc[0]))
        & candidates.paired_delta_5d_ci_low.gt(0)
        & candidates.mean_MAE_5d.ge(float(baseline.mean_MAE_5d.iloc[0]) - 0.5)
        & candidates.coverage.ge(0.50)
    ]
    if qualified.empty:
        return "V4 REMAINS BEST"
    winner = qualified.sort_values(["paired_mean_delta_5d", "mean_return_10d"], ascending=False)
    candidate = str(winner.iloc[0].candidate_id)
    if candidate.startswith("M1"):
        return "PROMOTE V4+EXTENSION TO EXTENDED SHADOW"
    if candidate.startswith("M2"):
        return "PROMOTE V4+TRIGGER TO EXTENDED SHADOW"
    if candidate.startswith("M3"):
        return "PROMOTE V4+EXTENSION+TRIGGER TO EXTENDED SHADOW"
    return "PROMOTE TS-GATE+V4 TO EXTENDED SHADOW"


def _trigger_overlay_action(row: pd.Series) -> str:
    state = row.trigger_state_canonical
    if state in {"INVALIDATED", "TOO_FAR_BELOW", "EXTENDED_BEYOND_TRIGGER"}:
        return "FILTERED"
    if state in {"APPROACHING", "NEAR", "AT_TRIGGER"}:
        return "WAIT_FOR_TRIGGER"
    if state == "FRESHLY_TRIGGERED":
        return "ENTER" if row.v4_canonical_action in {"ENTER", "WATCH"} else "WATCH"
    if state == "BEYOND_TRIGGER":
        return "WATCH"
    return row.v4_canonical_action


def _selection_turnover(
    scoped: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    candidate_id: str,
    danger_variant: str,
    selection: str,
    selector: Any,
) -> float | None:
    if candidate_id == "M0_V4":
        return 0.0
    baseline = scoped[
        (scoped.candidate_id == "M0_V4") & (scoped.danger_variant == danger_variant)
    ]
    if baseline.empty:
        baseline = scoped[
            (scoped.candidate_id == "M0_V4")
            & (scoped.danger_variant == "NO_DANGER_EXCLUSION")
        ]
    baseline = baseline[selector(baseline)]
    left = set(zip(selected.run_id, selected.ticker, strict=False))
    right = set(zip(baseline.run_id, baseline.ticker, strict=False))
    union = left | right
    return len(left ^ right) / len(union) if union else None


def _paired_selection_delta(
    scoped: pd.DataFrame,
    selected: pd.DataFrame,
    *,
    candidate_id: str,
    danger_variant: str,
    selector: Any,
) -> dict[str, Any]:
    baseline = scoped[
        (scoped.candidate_id == "M0_V4") & (scoped.danger_variant == danger_variant)
    ]
    if baseline.empty:
        baseline = scoped[
            (scoped.candidate_id == "M0_V4")
            & (scoped.danger_variant == "NO_DANGER_EXCLUSION")
        ]
    baseline = baseline[selector(baseline)]
    output: dict[str, Any] = {}
    for horizon in (5, 10):
        column = f"forward_return_{horizon}d"
        candidate_dates = selected.groupby("decision_date")[column].mean()
        baseline_dates = baseline.groupby("decision_date")[column].mean()
        paired = pd.concat(
            [candidate_dates.rename("candidate"), baseline_dates.rename("baseline")], axis=1
        ).dropna()
        deltas = paired.candidate - paired.baseline
        output[f"paired_dates_{horizon}d"] = len(deltas)
        output[f"paired_mean_delta_{horizon}d"] = deltas.mean() if len(deltas) else None
        low, high = _bootstrap_series_ci(deltas, seed=20260823 + horizon)
        output[f"paired_delta_{horizon}d_ci_low"] = low
        output[f"paired_delta_{horizon}d_ci_high"] = high
    return output


def _rank_correlation_vs_v4(
    scoped: pd.DataFrame, eligible: pd.DataFrame, danger_variant: str
) -> float | None:
    baseline = scoped[
        (scoped.candidate_id == "M0_V4") & (scoped.danger_variant == danger_variant)
    ][["run_id", "ticker", "candidate_rank"]].rename(columns={"candidate_rank": "v4_rank"})
    if baseline.empty:
        baseline = scoped[
            (scoped.candidate_id == "M0_V4")
            & (scoped.danger_variant == "NO_DANGER_EXCLUSION")
        ][["run_id", "ticker", "candidate_rank"]].rename(columns={"candidate_rank": "v4_rank"})
    pair = eligible[["run_id", "ticker", "candidate_rank"]].merge(
        baseline, on=["run_id", "ticker"]
    )
    if len(pair) < 3:
        return None
    return float(pair.candidate_rank.rank().corr(pair.v4_rank.rank()))


def _bootstrap_series_ci(
    values: pd.Series, *, samples: int = 300, seed: int
) -> tuple[float | None, float | None]:
    clean = pd.to_numeric(values, errors="coerce").dropna().to_numpy()
    if len(clean) < 2:
        return None, None
    rng = np.random.default_rng(seed)
    indexes = rng.integers(0, len(clean), size=(samples, len(clean)))
    means = clean[indexes].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def _diagnostic_bucket(frame: pd.DataFrame, field: str) -> pd.DataFrame:
    result = frame.copy()
    values = result[field]
    if pd.api.types.is_bool_dtype(values) or set(values.dropna().unique()) <= {0, 1}:
        result["_diagnostic_value"] = values.map({True: "TRUE", False: "FALSE"}).fillna(
            "MISSING"
        )
    elif pd.api.types.is_numeric_dtype(values):
        numeric = pd.to_numeric(values, errors="coerce")
        if numeric.nunique() < 4:
            result["_diagnostic_value"] = numeric.astype("object").where(
                numeric.notna(), "MISSING"
            )
        else:
            ranks = numeric.rank(method="average", pct=True)
            result["_diagnostic_value"] = pd.cut(
                ranks,
                [-np.inf, 0.25, 0.50, 0.75, np.inf],
                labels=["Q1", "Q2", "Q3", "Q4"],
            ).astype("object")
            result["_diagnostic_value"] = result._diagnostic_value.where(
                numeric.notna(), "MISSING"
            )
    else:
        result["_diagnostic_value"] = values.astype("object").where(
            values.notna(), "MISSING"
        )
    return result


def _trigger_extension(value: Any) -> float | None:
    number = _number(value)
    return max(0.0, -number) if number is not None else None


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None and pd.notna(value) else None
    except (TypeError, ValueError):
        return None


def _mapping(config: Mapping[str, Any], key: str) -> dict[str, Any]:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value
