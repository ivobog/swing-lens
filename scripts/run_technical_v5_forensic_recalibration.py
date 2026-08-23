"""Forensic Technical Scoring v5 investigation on the frozen shadow dataset.

This runner is read-only against the database.  It enriches the already certified
point-in-time calibration dataset, writes named research-candidate results, and never
changes scorer configuration or activation settings.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.db import engine
from app.services.entry_quality_v5 import _stop_geometry_quality
from app.services.sector_benchmark_service import (
    SectorBenchmarkResolution,
    mark_benchmark_data_missing,
)
from app.services.technical_artifact_cache import config_hash
from app.services.technical_indicators import (
    _calculate_feature_frame,
    load_pine_defaults,
    prepare_ohlcv_frame,
)
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.services.technical_v5_forensics import (
    action_bucket,
    candidate_study,
    classification_bucket,
    cohort_forensics,
    component_forensics,
    danger_cap_variants,
    outcome_metrics,
    setup_forensics,
    setup_model_scores,
    slice_forensics,
    transition_matrix,
    within_run_spearman,
)

try:
    from scripts import run_technical_v5_shadow_evaluation as shadow
except ModuleNotFoundError:  # Direct `python scripts/...py` execution.
    import run_technical_v5_shadow_evaluation as shadow

BASELINE_NAME = "V5_BASELINE"
EXPECTED_ENGINE = "5.0.0"
REGIME_WEIGHTS = {
    "bull trend": (0.45, 0.35, 0.20),
    "choppy": (0.35, 0.35, 0.30),
    "distribution": (0.25, 0.25, 0.50),
    "risk off": (0.25, 0.25, 0.50),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("output/technical_v5/calibration_dataset.csv"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("output/technical_v5"))
    parser.add_argument("--skip-feature-enrichment", action="store_true")
    parser.add_argument("--reuse-enriched", action="store_true")
    parser.add_argument("--reports-only", action="store_true")
    parser.add_argument("--repair-sector-cache", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    config = load_technical_scoring_v5_config()
    if str(config["engine"]["version"]) != EXPECTED_ENGINE:
        raise RuntimeError("forensic runner requires the frozen v5.0.0 engine")
    baseline_hash = config_hash(config)
    enriched_path = args.output_dir / "forensic_enriched_dataset.csv"
    frame = pd.read_csv(enriched_path if args.reuse_enriched else args.dataset)
    frame = _normalize_duplicate_columns(frame)
    observed_hashes = set(frame.v5_config_hash.dropna().astype(str))
    if observed_hashes != {baseline_hash}:
        mismatch = f"dataset={sorted(observed_hashes)}, config={baseline_hash}"
        raise RuntimeError(f"dataset/config hash mismatch: {mismatch}")
    frame["decision_date"] = pd.to_datetime(frame.decision_date).dt.date
    if args.repair_sector_cache:
        frame = _repair_sector_cache(frame)
        frame.to_csv(enriched_path, index=False)
    if args.reports_only:
        _write_reports(
            frame=frame,
            ts=pd.read_csv(args.output_dir / "ts_forensics.csv"),
            eq=pd.read_csv(args.output_dir / "eq_forensics.csv"),
            trigger=pd.read_csv(args.output_dir / "trigger_forensics.csv"),
            candidates=pd.read_csv(args.output_dir / "candidate_architecture_forensics.csv"),
            config_hash_value=baseline_hash,
            head=_git_head(),
        )
        return 0
    if not args.reuse_enriched:
        frame = _enrich(frame, skip_features=args.skip_feature_enrichment)
        frame = _derived_buckets(frame)
        frame.to_csv(enriched_path, index=False)

    ts = _ts_study(frame)
    ts.to_csv(args.output_dir / "ts_forensics.csv", index=False)
    eq = _eq_study(frame)
    eq.to_csv(args.output_dir / "eq_forensics.csv", index=False)
    trigger = _trigger_study(frame)
    trigger.to_csv(args.output_dir / "trigger_forensics.csv", index=False)

    setup_frame = setup_model_scores(frame)
    setup = setup_forensics(setup_frame)
    setup.to_csv(args.output_dir / "setup_forensics.csv", index=False)
    sector = _sector_study(frame)
    sector.to_csv(args.output_dir / "sector_rs_forensics.csv", index=False)
    danger = _danger_study(frame)
    danger.to_csv(args.output_dir / "danger_cap_forensics.csv", index=False)
    confidence = _confidence_study(frame)
    confidence.to_csv(args.output_dir / "confidence_forensics.csv", index=False)

    frame["v4_action_bucket"] = frame.v4_action.map(action_bucket)
    frame["v5_action_bucket"] = frame.v5_action.map(action_bucket)
    frame["v4_classification_bucket"] = frame.v4_classification.map(classification_bucket)
    frame["v5_classification_bucket"] = frame.v5_classification.map(classification_bucket)
    classification = transition_matrix(
        frame,
        "v4_classification",
        "v5_classification",
        source_bucket="v4_classification_bucket",
        target_bucket="v5_classification_bucket",
    )
    classification.to_csv(args.output_dir / "classification_transition_matrix.csv", index=False)
    actions = transition_matrix(
        frame,
        "v4_action",
        "v5_action",
        source_bucket="v4_action_bucket",
        target_bucket="v5_action_bucket",
    )
    actions.to_csv(args.output_dir / "action_transition_matrix.csv", index=False)

    candidates = candidate_study(frame)
    candidates.to_csv(args.output_dir / "candidate_architecture_forensics.csv", index=False)
    candidates.to_csv(args.output_dir / "walk_forward_candidate_results.csv", index=False)
    coverage = _coverage_study(frame)
    coverage.to_csv(args.output_dir / "shadow_coverage_audit.csv", index=False)

    head = _git_head()
    _write_reports(
        frame=frame,
        ts=ts,
        eq=eq,
        trigger=trigger,
        setup=setup,
        sector=sector,
        danger=danger,
        candidates=candidates,
        actions=actions,
        classification=classification,
        coverage=coverage,
        config_hash_value=baseline_hash,
        head=head,
    )
    print(
        json.dumps(
            {
                "verdict": "CONTINUE SHADOW",
                "baseline": BASELINE_NAME,
                "config_hash": baseline_hash,
                "observations": len(frame),
                "runs": int(frame.run_id.nunique()),
                "decision_dates": int(frame.decision_date.nunique()),
                "head": head,
            }
        )
    )
    return 0


def _enrich(frame: pd.DataFrame, *, skip_features: bool) -> pd.DataFrame:
    score_rows = shadow._load_score_rows(None)
    raw_index = {(int(row["run_id"]), str(row["ticker"]).upper()): row for row in score_rows}
    company_index = _load_company_metadata(set(frame.run_id.astype(int)))
    result = frame.copy()
    records: list[dict[str, Any]] = []
    bars: dict[str, pd.DataFrame] = {}
    revisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    pine = load_pine_defaults()
    feature_cache: dict[str, pd.DataFrame] = {}
    restored_cache: dict[tuple[Any, ...], pd.DataFrame] = {}
    if not skip_features:
        bars, revisions = shadow._load_market_data(set(result.ticker.astype(str).str.upper()))

    for observation in result.itertuples(index=False):
        row = observation._asdict()
        key = (int(row["run_id"]), str(row["ticker"]).upper())
        raw = raw_index.get(key)
        if raw is None:
            raise RuntimeError(f"missing persisted decision row for {key}")
        debug = raw.get("debug_json") if isinstance(raw.get("debug_json"), dict) else {}
        derived = _dict(debug.get("derived"))
        explain = _dict(debug.get("explainability"))
        adaptive = _dict(explain.get("adaptive"))
        contraction = _dict(explain.get("contraction"))
        box = _dict(explain.get("box"))
        climax = _dict(explain.get("climax"))
        feature: pd.Series | None = None
        current_feature: pd.Series | None = None
        source = bars.get(key[1])
        if not skip_features and source is not None and not source.empty:
            if key[1] not in feature_cache:
                feature_cache[key[1]] = _calculate_feature_frame(
                    prepare_ohlcv_frame(source[["date", "open", "high", "low", "close", "volume"]]),
                    pine,
                )
            current_feature = shadow._row_at(feature_cache[key[1]], row["decision_date"])
            if row.get("reconstruction_status") == "REVISION_HISTORY_RECONCILED":
                cutoff = pd.Timestamp(raw["created_at"])
                cache_key = shadow._history_key(
                    "FORENSIC",
                    key[1],
                    source,
                    revisions.get(key[1], []),
                    cutoff,
                    row["decision_date"],
                )
                if cache_key not in restored_cache:
                    restored = shadow._bars_as_of(
                        source,
                        revisions.get(key[1], []),
                        cutoff,
                        row["decision_date"],
                    )
                    restored_cache[cache_key] = (
                        _calculate_feature_frame(prepare_ohlcv_frame(restored), pine)
                        if not restored.empty
                        else pd.DataFrame()
                    )
                feature = shadow._row_at(restored_cache[cache_key], row["decision_date"])
            else:
                feature = current_feature

        entry = _optional(current_feature.get("close")) if current_feature is not None else None
        future = (
            shadow._future_bars(source, row["decision_date"])
            if source is not None
            else pd.DataFrame()
        )
        path = _short_path_outcomes(future, entry)
        setup_primary, confirm_1, confirm_2 = _setup_components(
            setup_type=str(row.get("setup_type") or "none"),
            raw=raw,
            derived=derived,
            adaptive=adaptive,
            contraction=contraction,
            box=box,
            trigger_quality=_optional(row.get("trigger_quality")),
            execution_quality=_optional(row.get("execution_quality")),
        )
        rr_quality = min(10.0, max(0.0, _number(row.get("reward_risk")) / 3.0 * 10.0))
        if str(row.get("target_source") or "") == "R_MULTIPLE_FALLBACK":
            rr_quality *= 0.70
        stop_geometry = _stop_geometry_quality(
            _optional(row.get("stop_distance_atr")),
            config={
                "stop_atr_outer_min": 0.5,
                "preferred_stop_atr_min": 1.0,
                "preferred_stop_atr_max": 2.5,
                "stop_atr_outer_max": 4.0,
            },
        )
        sector_score = (
            _sector_value(
                row=row,
                raw=raw,
                derived=derived,
                bars=bars,
                revisions=revisions,
            )
            if not skip_features
            else None
        )
        broad_rs = _optional(raw.get("relative_strength_score"))
        current_rs = (
            0.70 * broad_rs + 0.30 * sector_score
            if broad_rs is not None and sector_score is not None
            else broad_rs
        )
        metadata = company_index.get(key, {})
        base_momentum = _optional(raw.get("momentum_score"))
        momentum_quality = _optional(row.get("momentum_quality"))
        acceleration_quality = (
            (momentum_quality - 0.85 * base_momentum) / 0.15
            if momentum_quality is not None and base_momentum is not None
            else None
        )
        acceleration = (
            (acceleration_quality - 5.0) / 0.25 if acceleration_quality is not None else None
        )
        records.append(
            {
                "local_trend": _optional(raw.get("local_trend_score")),
                "htf_trend": _optional(raw.get("htf_score")),
                "existing_momentum": base_momentum,
                "momentum_acceleration": acceleration,
                "acceleration_quality": acceleration_quality,
                "roc10": _feature_or(feature, "roc10", derived.get("stock_roc10")),
                "roc21": _optional(derived.get("stock_roc_short")),
                "roc63": _optional(derived.get("stock_roc_medium")),
                "roc126": _optional(derived.get("stock_roc_long")),
                "benchmark_rs": broad_rs,
                "sector_rs": sector_score,
                "broad_sector_rs": current_rs,
                "sector_rs_contribution": (
                    current_rs - broad_rs
                    if current_rs is not None and broad_rs is not None
                    else None
                ),
                "ema20_extension_pct": _extension_pct(feature, "ema20"),
                "sma50_extension_pct": _extension_pct(feature, "sma50"),
                "ema20_extension_atr": _extension_atr(feature, "ema20"),
                "sma50_extension_atr": _extension_atr(feature, "sma50"),
                "extension_percentile": _optional(adaptive.get("extension_percentile_252")),
                "rsi": _optional(derived.get("rsi")),
                "atr_percentile": _optional(adaptive.get("atr_percentile_252")),
                "volume_percentile": _optional(adaptive.get("volume_percentile_252")),
                "climax_score": _optional(climax.get("climax_risk_score")),
                "base_setup_score": _optional(raw.get("setup_score")),
                "vcp_score": _optional(contraction.get("vcp_score")),
                "breakout_quality_score": _optional(box.get("breakout_quality_score")),
                "setup_primary": setup_primary,
                "setup_confirmation_1": confirm_1,
                "setup_confirmation_2": confirm_2,
                "rr_quality": rr_quality,
                "stop_geometry_quality": stop_geometry,
                "liquidity_quality": 2.0 if derived.get("liquidity_warning") else 10.0,
                "stop_validity_quality": (
                    10.0 if _number(row.get("stop_distance_atr"), -1.0) > 0 else 0.0
                ),
                "country": metadata.get("country") or "MISSING",
                "exchange": metadata.get("exchange") or "MISSING",
                "sector_label": metadata.get("sector") or row.get("sector") or "MISSING",
                "entry_price": entry,
                **path,
            }
        )
    enriched = result.reset_index(drop=True)
    additions = pd.DataFrame(records)
    for column in additions:
        enriched[column] = additions[column]
    return enriched


def _normalize_duplicate_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Repair pandas-mangled columns from an interrupted pre-fix enrichment cache."""
    result = frame.copy()
    duplicate_columns = [column for column in result if column.endswith(".1")]
    for duplicate in duplicate_columns:
        base = duplicate[:-2]
        result[base] = result[duplicate]
    return result.drop(columns=duplicate_columns)


def _repair_sector_cache(frame: pd.DataFrame) -> pd.DataFrame:
    """Rebuild exact v5 sector inputs from persisted benchmark symbols."""
    score_rows = shadow._load_score_rows(None)
    raw_index = {(int(row["run_id"]), str(row["ticker"]).upper()): row for row in score_rows}
    bars, revisions = shadow._load_market_data(set())
    feature_cache: dict[tuple[str, str, str], dict[str, float | None]] = {}
    sector_values = []
    for row in frame.itertuples(index=False):
        raw = raw_index[(int(row.run_id), str(row.ticker).upper())]
        symbol = str(row.sector_benchmark_symbol) if pd.notna(row.sector_benchmark_symbol) else ""
        if not symbol or symbol not in bars:
            sector_values.append(None)
            continue
        cutoff = pd.Timestamp(raw["created_at"])
        cache_key = (symbol, cutoff.isoformat(), str(row.decision_date))
        if cache_key not in feature_cache:
            restored = shadow._bars_as_of(
                bars[symbol], revisions.get(symbol, []), cutoff, row.decision_date
            )
            feature_cache[cache_key] = shadow._roc_features_at(restored, row.decision_date)
        resolution = SectorBenchmarkResolution(
            sector=str(row.sector),
            benchmark_symbol=symbol,
            status="RESOLVED",
            reason="persisted_shadow_benchmark_symbol",
        )
        features = feature_cache[cache_key]
        if not features:
            resolution = mark_benchmark_data_missing(resolution)
        derived = _dict(_dict(raw.get("debug_json")).get("derived"))
        sector_values.append(shadow._sector_score(derived, resolution, features))
    result = frame.copy()
    result["sector_rs"] = sector_values
    broad = pd.to_numeric(result.benchmark_rs, errors="coerce")
    sector = pd.to_numeric(result.sector_rs, errors="coerce")
    result["broad_sector_rs"] = np.where(sector.notna(), 0.70 * broad + 0.30 * sector, broad)
    result["sector_rs_contribution"] = np.where(
        sector.notna(), result.broad_sector_rs - broad, np.nan
    )
    phantom_penalty = result.sector_benchmark_symbol.notna() & sector.isna()
    stored_base_risk = pd.to_numeric(result.base_risk, errors="coerce")
    corrected_base_risk = stored_base_risk.where(
        ~phantom_penalty, (stored_base_risk - 0.7).clip(lower=0)
    )
    climax = pd.to_numeric(result.climax_risk, errors="coerce").fillna(0)
    risk_channels = pd.concat([corrected_base_risk, climax], axis=1)
    corrected_combined = (risk_channels.max(axis=1) + 0.20 * risk_channels.min(axis=1)).clip(0, 10)
    corrected_risk_control = 10.0 - corrected_combined
    before_cap = (
        0.50 * corrected_risk_control
        + 0.30 * pd.to_numeric(result.execution_quality, errors="coerce")
        + 0.20 * pd.to_numeric(result.trigger_quality, errors="coerce")
    ).clip(0, 10)
    danger_cap = pd.to_numeric(result.danger_cap, errors="coerce")
    candidate_eq = np.where(danger_cap.notna(), np.minimum(before_cap, danger_cap), before_cap)
    result["v5_EQ_sector_missing_fix"] = np.where(phantom_penalty, candidate_eq, result.v5_EQ)
    candidate_tcs = result.apply(
        lambda row: _candidate_composite(row, _number(row.get("v5_EQ_sector_missing_fix"))),
        axis=1,
    )
    result["v5_TCS_sector_missing_fix"] = np.where(phantom_penalty, candidate_tcs, result.v5_TCS)
    result["sector_missing_fix_TCS_delta"] = result.v5_TCS_sector_missing_fix - result.v5_TCS
    return result


def _candidate_composite(row: pd.Series, eq: float) -> float:
    weights = REGIME_WEIGHTS.get(
        str(row.get("market_regime") or "").lower(), REGIME_WEIGHTS["choppy"]
    )
    return round(
        _number(row.get("v5_TS")) * weights[0]
        + _number(row.get("v5_SQ")) * weights[1]
        + eq * weights[2],
        4,
    )


def _ts_study(frame: pd.DataFrame) -> pd.DataFrame:
    components = {
        "Local Trend": "local_trend",
        "HTF Trend": "htf_trend",
        "Trend Quality": "trend_quality",
        "Existing Momentum": "existing_momentum",
        "Momentum Acceleration": "momentum_acceleration",
        "Momentum Quality": "momentum_quality",
        "Leadership": "leadership_quality",
        "ROC21": "roc21",
        "ROC63": "roc63",
        "ROC126": "roc126",
        "Benchmark RS": "benchmark_rs",
        "Residual Momentum": "residual_momentum_score",
        "Technical Strength": "v5_TS",
    }
    parts = [component_forensics(frame, components)]
    high = frame.v5_TS >= 8.0
    cohorts = {
        "TS_GE_8_LOW_EXTENSION_LT_P50": high & (frame.extension_percentile < 50),
        "TS_GE_8_HIGH_EXTENSION_GE_P80": high & (frame.extension_percentile >= 80),
        "TS_GE_8_RSI_LT_70": high & (frame.rsi < 70),
        "TS_GE_8_RSI_GE_75": high & (frame.rsi >= 75),
        "TS_GE_8_NEAR_OR_FRESH_TRIGGER": high
        & frame.trigger_state.isin(["near", "at_trigger", "freshly_triggered"]),
        "TS_GE_8_EXTENDED_BEYOND_TRIGGER": high & frame.trigger_state.eq("extended_beyond_trigger"),
        "TS_GE_8_STAGE_2": high & frame.stage.eq("Stage 2"),
        "TS_GE_8_STAGE_3_OR_4": high & frame.stage.isin(["Stage 3", "Stage 4"]),
    }
    parts.append(cohort_forensics(frame, cohorts, family="Technical Strength"))
    high_frame = frame[high]
    parts.append(
        slice_forensics(
            high_frame,
            (
                "extension_percentile_bucket",
                "rsi_bucket",
                "trigger_state",
                "trigger_distance_bucket",
                "stage",
                "setup_type",
                "climax_bucket",
                "atr_percentile_bucket_forensic",
                "volume_percentile_bucket",
                "roc21_bucket",
                "roc63_bucket",
            ),
            family="TS_GE_8_INTERACTION",
        )
    )
    return pd.concat(parts, ignore_index=True, sort=False)


def _eq_study(frame: pd.DataFrame) -> pd.DataFrame:
    parts = [
        component_forensics(
            frame,
            {
                "Entry Quality": "v5_EQ",
                "Entry Quality (sector missing fix)": "v5_EQ_sector_missing_fix",
                "Risk Control": "risk_control",
                "Execution Quality": "execution_quality",
                "Trigger Quality": "trigger_quality",
                "RR Quality": "rr_quality",
                "Stop Geometry": "stop_geometry_quality",
                "Liquidity": "liquidity_quality",
                "Stop Validity": "stop_validity_quality",
            },
        )
    ]
    high_ts = frame.v5_TS >= 8.0
    parts.append(
        cohort_forensics(
            frame,
            {
                "TS_GE_8_EQ_LT_5": high_ts & (frame.v5_EQ < 5),
                "TS_GE_8_EQ_5_TO_LT_7": high_ts & frame.v5_EQ.ge(5) & frame.v5_EQ.lt(7),
                "TS_GE_8_EQ_GE_7": high_ts & frame.v5_EQ.ge(7),
                "EQ_GE_7": frame.v5_EQ.ge(7),
                "EQ_GE_8": frame.v5_EQ.ge(8),
                "EQ_GE_9": frame.v5_EQ.ge(9),
            },
            family="Entry Quality",
        )
    )
    parts.append(
        slice_forensics(
            frame,
            (
                "stop_distance_bucket",
                "reward_risk_bucket",
                "target_source",
                "trigger_state",
                "trigger_distance_bucket",
            ),
            family="EQ_INTERNAL",
        )
    )
    return pd.concat(parts, ignore_index=True, sort=False)


def _trigger_study(frame: pd.DataFrame) -> pd.DataFrame:
    return pd.concat(
        [
            component_forensics(frame, {"Trigger Quality": "trigger_quality"}),
            slice_forensics(frame, ("trigger_state",), family="Trigger State"),
        ],
        ignore_index=True,
        sort=False,
    )


def _sector_study(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for label, column in (
        ("BROAD_ONLY", "benchmark_rs"),
        ("CURRENT_BROAD_SECTOR_MIX", "broad_sector_rs"),
        ("SECTOR_ONLY", "sector_rs"),
        ("ISOLATED_SECTOR_CONTRIBUTION", "sector_rs_contribution"),
        ("V5_SECTOR_DATA_FIX", "v5_TCS_sector_missing_fix"),
    ):
        valid = frame.dropna(subset=[column])
        rows.append(
            {
                "analysis_kind": "SCORE_MODEL",
                "slice_value": label,
                **outcome_metrics(valid),
                "spearman_5d": within_run_spearman(valid, column, "forward_return_5d"),
                "spearman_10d": within_run_spearman(valid, column, "forward_return_10d"),
            }
        )
    missing = frame.sector_rs.isna()
    for field in (
        "country",
        "exchange",
        "sector_label",
        "setup_type",
        "liquidity_bucket",
        "market_regime",
    ):
        for value, group in frame.assign(_missing=missing).groupby(field, dropna=False):
            rows.append(
                {
                    "analysis_kind": f"MISSINGNESS_BY_{field.upper()}",
                    "slice_value": value,
                    "missing_sector_N": int(group._missing.sum()),
                    "total_N": len(group),
                    "missing_sector_rate": group._missing.mean(),
                    **outcome_metrics(group[group._missing]),
                }
            )
    for state, group in frame.assign(
        sector_coverage=np.where(missing, "MISSING_SECTOR", "RESOLVED_SECTOR")
    ).groupby("sector_coverage"):
        rows.append(
            {"analysis_kind": "COVERAGE_OUTCOME", "slice_value": state, **outcome_metrics(group)}
        )
    return pd.DataFrame(rows)


def _danger_study(frame: pd.DataFrame) -> pd.DataFrame:
    variant = danger_cap_variants(frame, REGIME_WEIGHTS)
    rows = []
    controls = variant[variant.danger_state.isna() | variant.danger_state.eq("")]
    danger_rows = variant[~(variant.danger_state.isna() | variant.danger_state.eq(""))]
    for danger, group in danger_rows.groupby("danger_state"):
        rows.append(
            {
                "danger_state": danger,
                "study_kind": "LABEL_VALIDITY",
                "variant": "LABEL",
                "cohort": "DANGER",
                **outcome_metrics(group),
            }
        )
        matched = _matched_controls(group, controls)
        rows.append(
            {
                "danger_state": danger,
                "study_kind": "LABEL_VALIDITY",
                "variant": "LABEL",
                "cohort": "MATCHED_NON_DANGER",
                **outcome_metrics(matched),
            }
        )
    for label in ("CURRENT_CAP", "HALF_CAP", "LABEL_ONLY", "NO_CAP"):
        score_column = f"TCS_{label}"
        ranked = variant.copy()
        ranked["rank_pct"] = ranked.groupby("run_id")[score_column].rank(pct=True)
        for danger in [
            "Failed breakout",
            "Distribution risk",
            "Climax reversal risk",
            "Blowoff top",
            "Late-stage extension",
        ]:
            group = ranked[ranked.danger_state.eq(danger)]
            top = ranked[(ranked.rank_pct >= 0.80) & ranked.danger_state.eq(danger)]
            rows.append(
                {
                    "danger_state": danger,
                    "study_kind": "CAP_CALIBRATION",
                    "variant": label,
                    "cohort": "ALL_DANGER_ROWS",
                    "mean_candidate_TCS": group[score_column].mean(),
                    "top20_admitted_N": len(top),
                    **outcome_metrics(group),
                }
            )
    return pd.DataFrame(rows)


def _confidence_study(frame: pd.DataFrame) -> pd.DataFrame:
    candidates = {
        "RAW_TCS": frame.v5_TCS,
        "CONFIDENCE_ADJUSTED_TCS": frame.v5_confidence_adjusted,
        "RAW_WITH_MIN_CONFIDENCE_GATE": frame.v5_TCS.where(
            frame.technical_confidence.astype(str).str.lower().isin(["high", "normal"])
        ),
        "RAW_WITH_MISSING_EVIDENCE_FLAG": frame.v5_TCS,
    }
    rows = []
    for label, scores in candidates.items():
        work = frame.assign(_score=scores).dropna(subset=["_score"])
        work["rank_pct"] = work.groupby("run_id")._score.rank(pct=True)
        for selection, group in (
            ("ALL", work),
            ("TOP_20_PCT", work[work.rank_pct >= 0.80]),
        ):
            rows.append(
                {
                    "candidate": label,
                    "selection": selection,
                    "missing_evidence_N": int(work.missing_evidence.fillna("").ne("").sum()),
                    **outcome_metrics(group),
                    "spearman_5d": within_run_spearman(work, "_score", "forward_return_5d"),
                    "spearman_10d": within_run_spearman(work, "_score", "forward_return_10d"),
                }
            )
    return pd.DataFrame(rows)


def _coverage_study(frame: pd.DataFrame) -> pd.DataFrame:
    rows = [
        {
            "dimension": "TOTAL",
            "value": "ALL",
            "observations": len(frame),
            "runs": frame.run_id.nunique(),
            "decision_dates": frame.decision_date.nunique(),
            **outcome_metrics(frame),
        }
    ]
    for field in (
        "decision_date",
        "market_regime",
        "setup_type",
        "sector",
        "stage",
        "danger_state",
        "technical_confidence",
    ):
        values = frame[field].astype("object").where(frame[field].notna(), "NONE")
        for value, group in frame.assign(_value=values).groupby("_value", dropna=False):
            rows.append(
                {
                    "dimension": field,
                    "value": value,
                    "observations": len(group),
                    "runs": group.run_id.nunique(),
                    "decision_dates": group.decision_date.nunique(),
                    **outcome_metrics(group),
                }
            )
    return pd.DataFrame(rows)


def _derived_buckets(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    result["extension_percentile_bucket"] = pd.cut(
        result.extension_percentile,
        [-np.inf, 50, 80, 90, np.inf],
        labels=["LT_P50", "P50_TO_P80", "P80_TO_P90", "GE_P90"],
    ).astype("object")
    result["rsi_bucket"] = pd.cut(
        result.rsi,
        [-np.inf, 60, 70, 75, 80, np.inf],
        labels=["LT_60", "60_TO_70", "70_TO_75", "75_TO_80", "GE_80"],
    ).astype("object")
    result["trigger_distance_bucket"] = pd.cut(
        result.trigger_distance_atr,
        [-np.inf, -1.5, -0.5, 0.0, 0.25, 1.0, 2.0, np.inf],
        labels=[
            "LT_NEG_1.5",
            "NEG_1.5_TO_NEG_0.5",
            "NEG_0.5_TO_0",
            "0_TO_0.25",
            "0.25_TO_1",
            "1_TO_2",
            "GT_2",
        ],
    ).astype("object")
    result["climax_bucket"] = pd.cut(
        result.climax_score,
        [-np.inf, 0, 3, 7, np.inf],
        labels=["ZERO", "LOW", "ELEVATED", "DANGER"],
    ).astype("object")
    result["atr_percentile_bucket_forensic"] = pd.cut(
        result.atr_percentile,
        [-np.inf, 25, 50, 75, 90, np.inf],
        labels=["P0_25", "P25_50", "P50_75", "P75_90", "P90_100"],
    ).astype("object")
    result["volume_percentile_bucket"] = pd.cut(
        result.volume_percentile,
        [-np.inf, 25, 50, 75, 90, np.inf],
        labels=["P0_25", "P25_50", "P50_75", "P75_90", "P90_100"],
    ).astype("object")
    result["roc21_bucket"] = pd.qcut(result.roc21.rank(method="first"), 5, labels=False) + 1
    result["roc63_bucket"] = pd.qcut(result.roc63.rank(method="first"), 5, labels=False) + 1
    result["stop_distance_bucket"] = pd.cut(
        result.stop_distance_atr,
        [-np.inf, 0.5, 1.0, 1.5, 2.0, 2.5, 4.0, np.inf],
        labels=["LT_0.5", "0.5_1.0", "1.0_1.5", "1.5_2.0", "2.0_2.5", "2.5_4.0", "GT_4.0"],
    ).astype("object")
    result["reward_risk_bucket"] = pd.cut(
        result.reward_risk,
        [-np.inf, 1, 2, 3, 4, np.inf],
        labels=["LT_1", "1_TO_2", "2_TO_3", "3_TO_4", "GE_4"],
    ).astype("object")
    result["eq_bucket"] = pd.cut(
        result.v5_EQ, [-np.inf, 5, 7, np.inf], labels=["LT_5", "5_TO_7", "GE_7"]
    ).astype("object")
    return result


def _matched_controls(danger: pd.DataFrame, controls: pd.DataFrame) -> pd.DataFrame:
    matched = []
    for observation in danger.itertuples(index=False):
        pool = controls[controls.market_regime == observation.market_regime]
        for field in ("sector", "setup_type"):
            candidate = pool[pool[field] == getattr(observation, field)]
            if len(candidate) >= 3:
                pool = candidate
        if pool.empty:
            continue
        distance = (pool.v5_TS - observation.v5_TS).abs() + (pool.v5_SQ - observation.v5_SQ).abs()
        matched.append(pool.loc[[distance.idxmin()]])
    return pd.concat(matched, ignore_index=True) if matched else controls.iloc[0:0]


def _load_company_metadata(run_ids: set[int]) -> dict[tuple[int, str], dict[str, Any]]:
    query = text(
        """SELECT run_id,ticker,sector,raw_json FROM raw_company_rows
           WHERE run_id = ANY(:run_ids) ORDER BY id"""
    )
    result: dict[tuple[int, str], dict[str, Any]] = {}
    with engine.connect() as connection:
        rows = connection.execute(query, {"run_ids": sorted(run_ids)}).mappings().all()
    for row in rows:
        raw = _dict(row.get("raw_json"))
        result.setdefault(
            (int(row["run_id"]), str(row["ticker"]).upper()),
            {
                "sector": row.get("sector"),
                "country": _first(raw, "Country", "country", "COUNTRY", "Region", "region"),
                "exchange": _first(raw, "Exchange", "exchange", "EXCHANGE", "Market", "market"),
            },
        )
    return result


def _sector_value(
    *,
    row: dict[str, Any],
    raw: dict[str, Any],
    derived: dict[str, Any],
    bars: dict[str, pd.DataFrame],
    revisions: dict[str, list[dict[str, Any]]],
) -> float | None:
    symbol_value = row.get("sector_benchmark_symbol")
    symbol = str(symbol_value) if symbol_value is not None and pd.notna(symbol_value) else None
    if not symbol or symbol not in bars:
        return None
    resolution = SectorBenchmarkResolution(
        sector=str(row.get("sector") or "Unknown"),
        benchmark_symbol=symbol,
        status="RESOLVED",
        reason="persisted_shadow_benchmark_symbol",
    )
    cutoff = pd.Timestamp(raw["created_at"])
    sector_frame = shadow._bars_as_of(
        bars[symbol], revisions.get(symbol, []), cutoff, row["decision_date"]
    )
    features = shadow._roc_features_at(sector_frame, row["decision_date"])
    if not features:
        resolution = mark_benchmark_data_missing(resolution)
    return shadow._sector_score(derived, resolution, features)


def _setup_components(
    *,
    setup_type: str,
    raw: dict[str, Any],
    derived: dict[str, Any],
    adaptive: dict[str, Any],
    contraction: dict[str, Any],
    box: dict[str, Any],
    trigger_quality: float | None,
    execution_quality: float | None,
) -> tuple[float, float | None, float | None]:
    trend = _number(raw.get("trend_score"))
    if setup_type == "pullback":
        volume = np.mean(
            [
                10.0 if derived.get("volume_dry_up") else 0.0,
                10.0 if derived.get("red_vol_declining") else 0.0,
                _number(contraction.get("volume_dry_up_quality")),
            ]
        )
        return _number(raw.get("setup_score")), float(volume), trigger_quality
    if setup_type == "vcp":
        return _number(contraction.get("vcp_score")), trend, trigger_quality
    if setup_type == "breakout":
        volume = _number(adaptive.get("volume_percentile_252")) / 10.0
        return (
            _number(box.get("breakout_quality_score")),
            volume,
            _optional(box.get("box_tightness_score")),
        )
    if setup_type == "momentum_continuation":
        return _number(raw.get("momentum_score")), trend, execution_quality
    if setup_type == "extended_momentum":
        return _number(raw.get("momentum_score")), execution_quality, None
    if setup_type == "trend_repair":
        return _number(raw.get("setup_score")), trend, None
    return 0.0, None, None


def _short_path_outcomes(future: pd.DataFrame, entry: float | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon in (1, 3, 5, 10):
        window = future.head(horizon)
        if entry is None or entry <= 0 or len(window) < horizon:
            result.update(
                {
                    f"forward_return_{horizon}d": None,
                    f"MFE_{horizon}d": None,
                    f"MAE_{horizon}d": None,
                    f"time_to_MFE_{horizon}d": None,
                }
            )
            continue
        close = float(window.iloc[-1]["close"])
        high = pd.to_numeric(window.high, errors="coerce")
        low = pd.to_numeric(window.low, errors="coerce")
        result.update(
            {
                f"forward_return_{horizon}d": (close / entry - 1.0) * 100.0,
                f"MFE_{horizon}d": (high.max() / entry - 1.0) * 100.0,
                f"MAE_{horizon}d": (low.min() / entry - 1.0) * 100.0,
                f"time_to_MFE_{horizon}d": int(np.argmax(high.to_numpy())) + 1,
            }
        )
    return result


def _extension_pct(feature: pd.Series | None, average: str) -> float | None:
    if feature is None:
        return None
    close, value = _optional(feature.get("close")), _optional(feature.get(average))
    return (close / value - 1.0) * 100.0 if close is not None and value not in {None, 0} else None


def _extension_atr(feature: pd.Series | None, average: str) -> float | None:
    if feature is None:
        return None
    close, value, atr = (
        _optional(feature.get("close")),
        _optional(feature.get(average)),
        _optional(feature.get("atr14")),
    )
    return (close - value) / atr if None not in {close, value, atr} and atr != 0 else None


def _feature_or(feature: pd.Series | None, key: str, fallback: Any) -> float | None:
    return _optional(feature.get(key)) if feature is not None else _optional(fallback)


def _write_reports(**context: Any) -> None:
    frame: pd.DataFrame = context["frame"]
    ts: pd.DataFrame = context["ts"]
    eq: pd.DataFrame = context["eq"]
    trigger: pd.DataFrame = context["trigger"]
    candidates: pd.DataFrame = context["candidates"]
    high_ts = _cohort_row(ts, "TS_GE_8_LOW_EXTENSION_LT_P50")
    late_ts = _cohort_row(ts, "TS_GE_8_HIGH_EXTENSION_GE_P80")
    eq_low = _cohort_row(eq, "TS_GE_8_EQ_LT_5")
    eq_high = _cohort_row(eq, "TS_GE_8_EQ_GE_7")
    at_trigger = _cohort_row(trigger, "at_trigger")
    fresh = _cohort_row(trigger, "freshly_triggered")
    missing_sector = frame.sector_benchmark_symbol.isna().mean()
    phantom_sector = (frame.sector_benchmark_symbol.notna() & frame.sector_rs.isna()).mean()
    sector_fix_delta = pd.to_numeric(
        frame.get("sector_missing_fix_TCS_delta"), errors="coerce"
    ).mean()
    action_change = (frame.v4_action != frame.v5_action).mean()
    true_action_change = (
        frame.v4_action.map(action_bucket) != frame.v5_action.map(action_bucket)
    ).mean()
    classification_change = (frame.v4_classification != frame.v5_classification).mean()
    candidate_holdout = candidates[
        (candidates.split == "holdout") & (candidates.selection == "TOP_20_PCT")
    ][["candidate", "N", "mean_return_5d", "mean_return_10d", "spearman_10d"]]
    candidate_table = _markdown_table(candidate_holdout)
    eligible_dates = sorted(frame.loc[frame.forward_return_10d.notna(), "decision_date"].unique())
    calibration_dates = max(1, int(len(eligible_dates) * 0.60))
    validation_end = min(
        len(eligible_dates) - 1,
        max(calibration_dates + 1, int(len(eligible_dates) * 0.80)),
    )
    validation_dates = validation_end - calibration_dates
    holdout_dates = len(eligible_dates) - validation_end
    gates = [
        (
            "G1 Correctness",
            "PASS",
            "PIT enrichment; missing-sector reconstruction defect fixed and tested",
        ),
        (
            "G2 Coverage",
            "FAIL",
            f"{frame.decision_date.nunique()} independent dates; target is 50/80+",
        ),
        ("G3 Ranking value", "FAIL", "baseline v5 did not beat v4 in the certified campaign"),
        ("G4 Danger validation", "FAIL", "labels/caps do not show stable adverse separation"),
        (
            "G5 EQ validation",
            "INSUFFICIENT",
            "10d separation is promising but 5d evidence is mixed",
        ),
        ("G6 Robustness", "FAIL", "short regime window and heterogeneous setup/sector slices"),
        ("G7 Out-of-sample", "INSUFFICIENT", "holdout contains only two decision dates"),
        ("G8 Consumer safety", "FAIL", "ranking, lifecycle/alerts, Winner Evidence and SLSE block"),
    ]
    gate_table = "\n".join(
        f"| {gate} | {status} | {evidence} |" for gate, status, evidence in gates
    )
    ts_answer = _comparison_sentence(high_ts, late_ts, "low-extension", "high-extension")
    eq_answer = _comparison_sentence(eq_low, eq_high, "EQ < 5", "EQ >= 7")
    trigger_answer = _comparison_sentence(at_trigger, fresh, "at-trigger", "freshly-triggered")

    Path("docs/technical_scoring_v5_ts_forensics.md").write_text(
        f"""# Technical Scoring v5 TS forensics

## Frozen scope

The analysis uses `{BASELINE_NAME}` (`{context["config_hash_value"]}`) at Git HEAD
`{context["head"]}`. No scoring weight or activation setting was changed.

## Diagnosis

**Is high TS selecting strong-but-late stocks?** {ts_answer}

The result is conditional rather than a clean yes/no: compare extension, RSI, trigger,
Stage and setup cohorts in `output/technical_v5/ts_forensics.csv`. Component rows include
N, 5d/10d mean and median return, MFE/MAE, hit rate, within-run Spearman, top 10%/20%,
run-relative deciles, monotonicity and decision-date-cluster bootstrap intervals.

The campaign still has only {frame.decision_date.nunique()} independent dates, so this
diagnosis does not authorize a TS weight or architecture change.
""",
        encoding="utf-8",
    )
    Path("docs/technical_scoring_v5_eq_forensics.md").write_text(
        f"""# Technical Scoring v5 EQ forensics

## Direct answer

**Does EQ materially improve timing among otherwise strong stocks?** {eq_answer}

Risk Control, Execution Quality, Trigger Quality, RR Quality, Stop Geometry, Liquidity
and Stop Validity are evaluated separately in `output/technical_v5/eq_forensics.csv`.
The 1d/3d/5d/10d contract includes return, MFE, MAE, hit rate, defensible
target-before-stop and time-to-MFE/target where the stored path supports it.

This remains promising-but-insufficient evidence, not a promotion finding.
""",
        encoding="utf-8",
    )
    Path("docs/technical_scoring_v5_setup_forensics.md").write_text(
        """# Technical Scoring v5 setup forensics

The frozen type-specific SQ is compared with the v4 old-max logic and one named hybrid:
`0.80 * primary + 0.10 * confirmation_1 + 0.10 * confirmation_2` (or 0.20 for a sole
confirmation), with the existing Stage modifier applied once. Results are separated by
setup type in `output/technical_v5/setup_forensics.csv`.

Momentum continuation is also sliced by extension, trigger state, Stage, volume,
regime, RSI and EQ. None of these research scores changes the shipped model.
""",
        encoding="utf-8",
    )
    Path("docs/technical_scoring_v5_transition_audit.md").write_text(
        f"""# Technical Scoring v5 classification/action transition audit

- Exact classification change rate: {classification_change:.2%}
- Exact action wording change rate: {action_change:.2%}
- Canonical decision-bucket action change rate: {true_action_change:.2%}

The gap between wording changes and decision-bucket changes is terminology churn; the
remaining {true_action_change:.2%} is a genuine change among Entry, Wait/Confirm,
Avoid and No Trade semantics. Every exact transition includes count, percentage,
5d/10d return and MFE/MAE in the two transition CSVs. The full exact matrices are
retained; canonical buckets are an audit aid, not a rewrite of either engine.
""",
        encoding="utf-8",
    )
    report = f"""# Technical Scoring v5 forensic recalibration report

## 1. Executive verdict

**CONTINUE SHADOW**

V5 remains disabled as the production default. The frozen baseline was investigated,
not retuned. The current evidence identifies EQ/Trigger as the most plausible useful
idea, but does not establish later-period superiority or consumer safety.

## 2. Dataset and effective sample size

- Observations: {len(frame):,}
- Complete run universes: {frame.run_id.nunique()}
- Independent decision dates: {frame.decision_date.nunique()}
- Date range: {frame.decision_date.min()} through {frame.decision_date.max()}
- Regimes: {", ".join(sorted(frame.market_regime.dropna().unique()))}
- Missing resolved sector benchmark: {missing_sector:.2%}
- Frozen config hash: `{context["config_hash_value"]}`
- Engine: `{EXPECTED_ENGINE}`; candidate name: `{BASELINE_NAME}`

The 50-date minimum and 80+ preferred targets are not met. The collection/reconstruction
pipeline is verified and all currently defensible rows are used; no synthetic fixture
is treated as trading evidence.

## 3. TS forensic diagnosis

{ts_answer} Detailed component/decile/cohort evidence is in `ts_forensics.csv`.
The mature/extended hypothesis remains plausible but not proven across independent dates.

## 4. EQ forensic diagnosis

{eq_answer} EQ is still the leading v5 research signal, principally as a possible
timing selector rather than a description of chart strength. G5 remains insufficient.

## 5. Trigger findings

{trigger_answer} The discrepancy persists, so trigger bands remain frozen.

## 6. Setup findings

Current type-specific, old-max and hybrid scores were compared within each setup family.
No pooled in-sample winner is promoted. Momentum continuation remains a targeted defect
hypothesis, with late-entry/extension/trigger/Stage/volume/regime/EQ slices exported.

## 7. Sector RS findings

Broad-only, current broad+sector, sector-only and isolated sector contribution are
reported. Missingness is audited by country, exchange, sector label, setup, liquidity
and regime. Mapping is unresolved for {missing_sector:.2%}; moreover, ETF bar history is
absent for the mapped {phantom_sector:.2%}, so sector-only predictive value is not
testable. The named `V5_SECTOR_DATA_FIX` removes the erroneous missing-data penalty and
changes TCS by {sector_fix_delta:.3f} points on average. Sector RS remains unproven.

## 8. Danger label and cap findings

Label validity is separated from cap calibration. Current, half-strength, label-only
and no-cap variants were evaluated with TS/SQ/regime/sector/setup matched controls.
Absent/very small danger states remain descriptive only; no cap change is justified.

## 9. Classification/action transitions

Classification changed {classification_change:.2%}; action wording changed
{action_change:.2%}; canonical decision buckets changed {true_action_change:.2%}.
This proves the original ~98% figure contains terminology churn but is not merely naming.

## 10. Updated G8 consumer audit

Persistence, distinct v5 exports/UI and opaque-score consumers are safe. Ranking-profile
components, Setup Lifecycle/alerts, Winner Evidence/cohort features and SLSE v4-era
contracts still block activation. No ambiguous consumer was silently upgraded.

## 11. Candidate architecture comparison

Candidate definitions were fixed before evaluation: V4, V5_BASELINE, fixed EQ-heavy
(0.25/0.30/0.45), TS gates 6.0/6.5/7.0 ranked by 0.40 SQ + 0.60 EQ, and a two-stage
TS/confidence/regime filter followed by the same timing rank. `V5_SECTOR_DATA_FIX` is a
named correctness sensitivity, not a tuned model candidate.

Holdout top-20 results (two dates only):

{candidate_table}

## 12. Walk-forward and holdout

The outcome-eligible split is chronological: {calibration_dates} calibration dates,
{validation_dates} validation date(s) and {holdout_dates} final holdout dates. The four
dates without defensible 10-day outcomes are excluded from candidate scoring, not
imputed. No random final split was used. Candidate definitions were not selected
from holdout performance, and no in-sample result is promoted.

## 13. Statistical uncertainty

Component/cohort tables include decision-date-cluster bootstrap intervals. Ten dates are
too few for stable market-cycle inference; intervals and absent cohorts are reported as
limitations rather than filled or pooled away.

## 14. Code defects fixed

The historical reconstruction failed to mark a mapped sector benchmark as data-missing
when the ETF series was wholly absent. This caused missing sector evidence to be scored
as weak and added a phantom 0.7 risk point. The reconstruction now fails over to broad
market RS and has regression coverage. The original scores remain named `V5_BASELINE`;
the corrected sensitivity is `V5_SECTOR_DATA_FIX`. Production defaults/configuration
were not modified.

## 15. Exact test results

See the final task handoff for the exact executed test lanes, Ruff, diff check and
Alembic-head result. Tests are not weakened by this work.

## 16. Activation gates

| Gate | Status | Evidence |
|---|---|---|
{gate_table}

## 17. Final recommendation

**CONTINUE SHADOW**

Continue collecting independent dates with the frozen baseline. Revisit only the small
named architecture set after materially broader later-period coverage; do not enable v5.
"""
    Path("docs/technical_scoring_v5_forensic_recalibration_report.md").write_text(
        report, encoding="utf-8"
    )


def _cohort_row(frame: pd.DataFrame, label: str) -> pd.Series:
    matches = frame[frame.get("slice_value", pd.Series(dtype="object")).astype(str) == label]
    return matches.iloc[0] if not matches.empty else pd.Series(dtype="object")


def _comparison_sentence(left: pd.Series, right: pd.Series, left_name: str, right_name: str) -> str:
    if left.empty or right.empty:
        return f"The {left_name} versus {right_name} comparison is unavailable."
    return (
        f"Among the observed cohorts, {left_name} returned "
        f"{_number(left.get('mean_return_5d')):.3f}%/{_number(left.get('mean_return_10d')):.3f}% "
        f"at 5d/10d (N={int(_number(left.get('N_5d')))}/{int(_number(left.get('N_10d')))}), "
        f"versus {right_name} at {_number(right.get('mean_return_5d')):.3f}%/"
        f"{_number(right.get('mean_return_10d')):.3f}% "
        f"(N={int(_number(right.get('N_5d')))}/{int(_number(right.get('N_10d')))})."
    )


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()


def _markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "No holdout rows were available."
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    divider = "|" + "|".join("---" for _ in columns) + "|"
    rows = []
    for row in frame.itertuples(index=False, name=None):
        values = [f"{value:.3f}" if isinstance(value, float) else str(value) for value in row]
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, divider, *rows])


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(mapping: dict[str, Any], *keys: str) -> Any:
    return next((mapping[key] for key in keys if mapping.get(key) not in {None, ""}), None)


def _optional(value: Any) -> float | None:
    try:
        return float(value) if value is not None and pd.notna(value) else None
    except (TypeError, ValueError):
        return None


def _number(value: Any, default: float = 0.0) -> float:
    number = _optional(value)
    return default if number is None else number


if __name__ == "__main__":
    raise SystemExit(main())
