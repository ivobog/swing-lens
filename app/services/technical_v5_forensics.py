from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import Any

import numpy as np
import pandas as pd

HORIZONS = (1, 3, 5, 10)


def outcome_metrics(frame: pd.DataFrame) -> dict[str, Any]:
    """Return the common forensic outcome contract without imputing missing outcomes."""
    result: dict[str, Any] = {"N": int(len(frame))}
    for horizon in HORIZONS:
        returns = _series(frame, f"forward_return_{horizon}d")
        mfe = _series(frame, f"MFE_{horizon}d")
        mae = _series(frame, f"MAE_{horizon}d")
        result.update(
            {
                f"N_{horizon}d": int(returns.notna().sum()),
                f"mean_return_{horizon}d": returns.mean(),
                f"median_return_{horizon}d": returns.median(),
                f"mean_MFE_{horizon}d": mfe.mean(),
                f"median_MFE_{horizon}d": mfe.median(),
                f"mean_MAE_{horizon}d": mae.mean(),
                f"median_MAE_{horizon}d": mae.median(),
                f"hit_rate_{horizon}d": (returns > 0).where(returns.notna()).mean(),
            }
        )
    first_hit = frame.get("first_hit_10d", pd.Series(index=frame.index, dtype="object"))
    defensible = first_hit.isin(["TARGET", "STOP"])
    result["target_before_stop_rate_10d"] = (
        first_hit[defensible].eq("TARGET").mean() if defensible.any() else None
    )
    result["mean_time_to_target"] = _series(frame, "time_to_target").mean()
    result["mean_time_to_MFE_5d"] = _series(frame, "time_to_MFE_5d").mean()
    result["mean_time_to_MFE_10d"] = _series(frame, "time_to_MFE_10d").mean()
    return result


def component_forensics(
    frame: pd.DataFrame,
    components: Mapping[str, str],
    *,
    clusters: str = "decision_date",
    bootstrap_samples: int = 300,
) -> pd.DataFrame:
    """Evaluate components overall, in top selections, and in run-relative deciles."""
    rows: list[dict[str, Any]] = []
    for component, column in components.items():
        valid = frame.dropna(subset=[column]).copy()
        if valid.empty:
            rows.append({"component": component, "analysis_kind": "OVERALL", "N": 0})
            continue
        valid["rank_pct"] = valid.groupby("run_id")[column].rank(pct=True, method="average")
        valid["decile"] = np.ceil(valid["rank_pct"] * 10).clip(1, 10).astype(int)
        decile_means = valid.groupby("decile", observed=True).agg(
            mean_5d=("forward_return_5d", "mean"), mean_10d=("forward_return_10d", "mean")
        )
        overall = {
            "component": component,
            "component_column": column,
            "analysis_kind": "OVERALL",
            "slice_value": "ALL",
            **outcome_metrics(valid),
            "spearman_5d": within_run_spearman(valid, column, "forward_return_5d"),
            "spearman_10d": within_run_spearman(valid, column, "forward_return_10d"),
            "decile_monotonicity_5d": spearman_series(
                pd.Series(decile_means.index, index=decile_means.index), decile_means.mean_5d
            ),
            "decile_monotonicity_10d": spearman_series(
                pd.Series(decile_means.index, index=decile_means.index), decile_means.mean_10d
            ),
        }
        overall.update(
            cluster_mean_ci(
                valid,
                "forward_return_5d",
                clusters=clusters,
                samples=bootstrap_samples,
            )
        )
        rows.append(overall)
        for label, selected in (
            ("TOP_10_PCT", valid[valid.rank_pct >= 0.90]),
            ("TOP_20_PCT", valid[valid.rank_pct >= 0.80]),
        ):
            record = {
                "component": component,
                "component_column": column,
                "analysis_kind": label,
                "slice_value": label,
                **outcome_metrics(selected),
            }
            record.update(
                cluster_mean_ci(
                    selected,
                    "forward_return_5d",
                    clusters=clusters,
                    samples=bootstrap_samples,
                )
            )
            rows.append(record)
        for decile, group in valid.groupby("decile", observed=True):
            record = {
                "component": component,
                "component_column": column,
                "analysis_kind": "DECILE",
                "slice_value": int(decile),
                **outcome_metrics(group),
            }
            record.update(
                cluster_mean_ci(
                    group,
                    "forward_return_5d",
                    clusters=clusters,
                    samples=bootstrap_samples,
                )
            )
            rows.append(record)
    return pd.DataFrame(rows)


def cohort_forensics(
    frame: pd.DataFrame,
    cohorts: Mapping[str, pd.Series],
    *,
    family: str,
) -> pd.DataFrame:
    rows = []
    for label, mask in cohorts.items():
        group = frame[mask.fillna(False)]
        record = {
            "component": family,
            "analysis_kind": "COHORT",
            "slice_value": label,
            **outcome_metrics(group),
        }
        record.update(cluster_mean_ci(group, "forward_return_5d"))
        rows.append(record)
    return pd.DataFrame(rows)


def slice_forensics(
    frame: pd.DataFrame,
    fields: Iterable[str],
    *,
    family: str,
) -> pd.DataFrame:
    rows = []
    for field in fields:
        if field not in frame:
            continue
        values = frame[field].astype("object").where(frame[field].notna(), "MISSING")
        for value, group in frame.assign(_slice=values).groupby("_slice", dropna=False):
            rows.append(
                {
                    "component": family,
                    "analysis_kind": f"SLICE_{field.upper()}",
                    "slice_value": value,
                    **outcome_metrics(group),
                }
            )
    return pd.DataFrame(rows)


def transition_matrix(
    frame: pd.DataFrame,
    source: str,
    target: str,
    *,
    source_bucket: str | None = None,
    target_bucket: str | None = None,
) -> pd.DataFrame:
    total = len(frame)
    grouped = frame.groupby([source, target], dropna=False)
    rows = []
    for (before, after), group in grouped:
        record = {
            "from_value": before,
            "to_value": after,
            "count": len(group),
            "percentage": len(group) / total if total else None,
            **outcome_metrics(group),
        }
        if source_bucket and target_bucket:
            record.update(
                {
                    "from_decision_bucket": group[source_bucket].iloc[0],
                    "to_decision_bucket": group[target_bucket].iloc[0],
                    "true_decision_change": bool(
                        group[source_bucket].iloc[0] != group[target_bucket].iloc[0]
                    ),
                }
            )
        rows.append(record)
    return pd.DataFrame(rows).sort_values("count", ascending=False).reset_index(drop=True)


def action_bucket(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "buyable" in text or text.startswith("entry candidate"):
        return "ENTRY"
    if "avoid" in text or "exit risk" in text:
        return "AVOID"
    if "no clear trade" in text or "no qualified setup" in text:
        return "NO_TRADE"
    return "WAIT_CONFIRM"


def classification_bucket(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("failed", "distribution", "climax", "blowoff")):
        return "DANGER_AVOID"
    if any(token in text for token in ("clean bull", "breakout", "contraction")):
        return "ENTRY_SETUP"
    if "no trade" in text:
        return "NO_TRADE"
    return "WAIT_MANAGE"


def setup_model_scores(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["SQ_CURRENT_TYPE_SPECIFIC"] = _series(result, "v5_SQ")
    result["SQ_OLD_MAX"] = result[["base_setup_score", "vcp_score", "breakout_quality_score"]].max(
        axis=1, skipna=True
    )

    def hybrid(row: pd.Series) -> float:
        primary = _number(row.get("setup_primary"), 0.0)
        secondary = [
            _number(row.get("setup_confirmation_1"), 0.0),
            _number(row.get("setup_confirmation_2"), 0.0),
        ]
        present = int(pd.notna(row.get("setup_confirmation_1"))) + int(
            pd.notna(row.get("setup_confirmation_2"))
        )
        if str(row.get("setup_type")) == "none":
            return 0.0
        score = 0.8 * primary
        if present >= 2:
            score += 0.1 * secondary[0] + 0.1 * secondary[1]
        elif present == 1:
            score += 0.2 * secondary[0]
        score += _number(row.get("stage_modifier"), 0.0)
        return max(0.0, min(10.0, round(score, 4)))

    result["SQ_HYBRID_PRIMARY_CONFIRMATION"] = result.apply(hybrid, axis=1)
    return result


def setup_forensics(frame: pd.DataFrame) -> pd.DataFrame:
    models = {
        "CURRENT_TYPE_SPECIFIC": "SQ_CURRENT_TYPE_SPECIFIC",
        "OLD_MAX": "SQ_OLD_MAX",
        "HYBRID_PRIMARY_CONFIRMATION": "SQ_HYBRID_PRIMARY_CONFIRMATION",
    }
    rows = []
    for setup_type, setup_group in frame.groupby("setup_type", dropna=False):
        for model, column in models.items():
            valid = setup_group.dropna(subset=[column]).copy()
            valid["rank_pct"] = valid.groupby("run_id")[column].rank(pct=True, method="average")
            for selection, group in (
                ("ALL", valid),
                ("TOP_20_PCT_WITHIN_SETUP", valid[valid.rank_pct >= 0.80]),
            ):
                rows.append(
                    {
                        "setup_type": setup_type,
                        "setup_model": model,
                        "analysis_kind": selection,
                        **outcome_metrics(group),
                        "spearman_5d": within_run_spearman(valid, column, "forward_return_5d"),
                        "spearman_10d": within_run_spearman(valid, column, "forward_return_10d"),
                    }
                )
    momentum = frame[frame.setup_type.eq("momentum_continuation")]
    for field in (
        "extension_percentile_bucket",
        "trigger_state",
        "stage",
        "volume_percentile_bucket",
        "market_regime",
        "eq_bucket",
        "rsi_bucket",
    ):
        if field not in momentum:
            continue
        for value, group in momentum.groupby(field, dropna=False):
            rows.append(
                {
                    "setup_type": "momentum_continuation",
                    "setup_model": "DIAGNOSTIC",
                    "analysis_kind": field.upper(),
                    "slice_value": value,
                    **outcome_metrics(group),
                }
            )
    return pd.DataFrame(rows)


def danger_cap_variants(
    frame: pd.DataFrame, regime_weights: Mapping[str, Sequence[float]]
) -> pd.DataFrame:
    result = frame.copy()
    before_cap = (
        0.50 * _series(result, "risk_control")
        + 0.30 * _series(result, "execution_quality")
        + 0.20 * _series(result, "trigger_quality")
    ).clip(0, 10)
    current_cap = _series(result, "danger_cap")
    half_cap = current_cap + 0.5 * (10.0 - current_cap)
    result["EQ_CURRENT_CAP"] = _series(result, "v5_EQ")
    result["EQ_HALF_CAP"] = np.where(
        current_cap.notna(), np.minimum(before_cap, half_cap), before_cap
    )
    result["EQ_LABEL_ONLY"] = before_cap
    result["EQ_NO_CAP"] = before_cap
    for label, eq_column in (
        ("CURRENT_CAP", "EQ_CURRENT_CAP"),
        ("HALF_CAP", "EQ_HALF_CAP"),
        ("LABEL_ONLY", "EQ_LABEL_ONLY"),
        ("NO_CAP", "EQ_NO_CAP"),
    ):
        result[f"TCS_{label}"] = result.apply(
            lambda row, column=eq_column: _composite(
                row,
                _number(row.get(column)),
                regime_weights,
            ),
            axis=1,
        )
    return result


def candidate_scores(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a small, named, hypothesis-driven architecture set."""
    records = []
    for row in frame.itertuples(index=False):
        base = row._asdict()
        common = {
            "run_id": base["run_id"],
            "ticker": base["ticker"],
            "decision_date": base["decision_date"],
        }
        ts = _number(base.get("v5_TS"))
        sq = _number(base.get("v5_SQ"))
        eq = _number(base.get("v5_EQ"))
        values = {
            "V4": _number(base.get("v4_score")),
            "V5_BASELINE": _number(base.get("v5_TCS")),
            "V5_EQ_HEAVY": 0.25 * ts + 0.30 * sq + 0.45 * eq,
        }
        if pd.notna(base.get("v5_TCS_sector_missing_fix")):
            values["V5_SECTOR_DATA_FIX"] = _number(base.get("v5_TCS_sector_missing_fix"))
        timing_rank = 0.40 * sq + 0.60 * eq
        for threshold in (6.0, 6.5, 7.0):
            values[f"V5_STRENGTH_GATE_{threshold:.1f}"] = timing_rank if ts >= threshold else np.nan
        confidence = str(base.get("technical_confidence") or "").lower()
        regime = str(base.get("market_regime") or "").lower()
        values["V5_TWO_STAGE"] = (
            timing_rank
            if ts >= 6.5 and confidence in {"high", "normal"} and regime not in {"", "unknown"}
            else np.nan
        )
        for candidate, score in values.items():
            records.append({**common, "candidate": candidate, "score": score})
    scores = pd.DataFrame(records)
    outcome_columns = [
        column
        for column in frame.columns
        if column.startswith(("forward_return_", "MFE_", "MAE_", "first_hit_", "time_to_"))
    ]
    return scores.merge(frame[["run_id", "ticker", *outcome_columns]], on=["run_id", "ticker"])


def candidate_study(frame: pd.DataFrame) -> pd.DataFrame:
    eligible_frame = frame.dropna(subset=["forward_return_10d"]).copy()
    scores = candidate_scores(eligible_frame)
    date_labels = time_split_labels(eligible_frame.decision_date)
    split_map = dict(zip(eligible_frame.decision_date.astype(str), date_labels, strict=False))
    scores["split"] = scores.decision_date.astype(str).map(split_map)
    rows = []
    for (candidate, split), group in scores.groupby(["candidate", "split"]):
        eligible = group.dropna(subset=["score"]).copy()
        eligible["rank_pct"] = eligible.groupby("run_id")["score"].rank(pct=True, method="average")
        for selection, selected in (
            ("ALL_ELIGIBLE", eligible),
            ("TOP_10_PCT", eligible[eligible.rank_pct >= 0.90]),
            ("TOP_20_PCT", eligible[eligible.rank_pct >= 0.80]),
        ):
            record = {
                "candidate": candidate,
                "split": split,
                "selection": selection,
                "eligible_N": len(eligible),
                **outcome_metrics(selected),
                "spearman_5d": within_run_spearman(eligible, "score", "forward_return_5d"),
                "spearman_10d": within_run_spearman(eligible, "score", "forward_return_10d"),
            }
            record.update(cluster_mean_ci(selected, "forward_return_5d"))
            rows.append(record)
    return pd.DataFrame(rows)


def time_split_labels(values: Iterable[Any]) -> list[str]:
    dates = [pd.Timestamp(value).date() for value in values]
    unique = sorted(set(dates))
    if len(unique) < 3:
        return ["blocked"] * len(dates)
    calibration_end = max(1, int(len(unique) * 0.60))
    validation_end = min(len(unique) - 1, max(calibration_end + 1, int(len(unique) * 0.80)))
    calibration = set(unique[:calibration_end])
    validation = set(unique[calibration_end:validation_end])
    return [
        "calibration"
        if value in calibration
        else "validation"
        if value in validation
        else "holdout"
        for value in dates
    ]


def cluster_mean_ci(
    frame: pd.DataFrame,
    column: str,
    *,
    clusters: str = "decision_date",
    samples: int = 300,
    seed: int = 20260823,
) -> dict[str, float | int | None]:
    valid = (
        frame[[clusters, column]].dropna()
        if clusters in frame and column in frame
        else pd.DataFrame()
    )
    if valid.empty or valid[clusters].nunique() < 2:
        return {
            "bootstrap_clusters": int(valid[clusters].nunique()) if not valid.empty else 0,
            "bootstrap_mean_ci_low": None,
            "bootstrap_mean_ci_high": None,
        }
    cluster_stats = valid.groupby(clusters)[column].agg(["sum", "count"])
    rng = np.random.default_rng(seed)
    cluster_count = len(cluster_stats)
    sampled = rng.integers(0, cluster_count, size=(samples, cluster_count))
    sums = cluster_stats["sum"].to_numpy()[sampled].sum(axis=1)
    counts = cluster_stats["count"].to_numpy()[sampled].sum(axis=1)
    means = sums / counts
    return {
        "bootstrap_clusters": int(cluster_count),
        "bootstrap_mean_ci_low": float(np.nanpercentile(means, 2.5)),
        "bootstrap_mean_ci_high": float(np.nanpercentile(means, 97.5)),
    }


def within_run_spearman(frame: pd.DataFrame, left: str, right: str) -> float | None:
    if left not in frame or right not in frame:
        return None
    pair = frame[["run_id", left, right]].dropna().copy()
    if len(pair) < 3:
        return None
    pair["x_rank"] = pair.groupby("run_id")[left].rank(method="average", pct=True)
    pair["y_rank"] = pair.groupby("run_id")[right].rank(method="average", pct=True)
    return spearman_series(pair.x_rank, pair.y_rank)


def spearman_series(left: pd.Series, right: pd.Series) -> float | None:
    pair = pd.DataFrame({"x": left, "y": right}).dropna()
    if len(pair) < 3 or pair.x.nunique() < 2 or pair.y.nunique() < 2:
        return None
    return float(pair.x.rank(method="average").corr(pair.y.rank(method="average")))


def _composite(
    row: pd.Series,
    eq: float,
    regime_weights: Mapping[str, Sequence[float]],
) -> float:
    regime = str(row.get("market_regime") or "").lower()
    weights = regime_weights.get(regime, regime_weights["choppy"])
    return round(
        _number(row.get("v5_TS")) * weights[0]
        + _number(row.get("v5_SQ")) * weights[1]
        + eq * weights[2],
        4,
    )


def _series(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame:
        return pd.Series(np.nan, index=frame.index, dtype="float64")
    return pd.to_numeric(frame[column], errors="coerce")


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if pd.notna(value) else default
    except (TypeError, ValueError):
        return default
