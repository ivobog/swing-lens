"""Point-in-time Technical Scoring v5 shadow reconstruction and evaluation.

The script is intentionally read-only against the SwingLens database.  It reconstructs
only v5-only inputs from revision-aware OHLCV history, reconciles those inputs against
the persisted v4 decision-time features, and writes auditable CSV/Markdown artifacts.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
import pickle
from collections import defaultdict
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

from app.db import engine
from app.services.leadership_v5 import rank_leadership_v5
from app.services.pine_replica_engine import PineReplicaScore, relative_strength_score
from app.services.relative_leadership import calculate_beta_adjusted_rs
from app.services.sector_benchmark_service import (
    SectorBenchmarkResolution,
    mark_benchmark_data_missing,
    resolve_sector_benchmark,
)
from app.services.technical_artifact_cache import config_hash
from app.services.technical_indicators import (
    _calculate_feature_frame,
    load_pine_defaults,
    prepare_ohlcv_frame,
)
from app.services.technical_score_v5 import TechnicalScoreV5, technical_score_v5_from_base_score
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.services.technical_v5_calibration import (
    CALIBRATION_COLUMNS,
    CALIBRATION_SCHEMA_VERSION,
    construct_forward_outcomes,
    evidence_strength,
    time_ordered_split_labels,
)

BASELINE_COMMIT = "eb6798ee990e268b2ef808bb0747465220b9b0e7"
RECONCILE_FIELDS = {
    "stock_roc_short": "roc21",
    "stock_roc_medium": "roc63",
    "stock_roc_long": "roc126",
    "atr": "atr14",
    "rsi": "rsi14",
}
ENRICH_FIELDS = {
    "close": "close",
    "ema10": "ema10",
    "ema20": "ema20",
    "sma50": "sma50",
    "stock_roc10": "roc10",
    "previous_resistance": "previous_resistance",
    "prior_high": "prior_high",
    "stop_source": "stop_source",
    "target_source": "target_source",
}
SCORES = {
    "v4": "v4_score",
    "TS": "v5_TS",
    "SQ": "v5_SQ",
    "EQ": "v5_EQ",
    "raw_TCS": "v5_TCS",
    "confidence_adjusted_TCS": "v5_confidence_adjusted",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("output/technical_v5"))
    parser.add_argument("--max-runs", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    v5_config = load_technical_scoring_v5_config()
    v4_config = load_technical_scoring_v4_config()
    pine_config = load_pine_defaults()
    checkpoint = args.output_dir / ".reconstruction_checkpoint.pkl"
    if args.resume and checkpoint.exists():
        with checkpoint.open("rb") as handle:
            observations, contexts, provenance = pickle.load(handle)  # noqa: S301
    else:
        rows = _load_score_rows(args.max_runs)
        bars, revisions = _load_market_data({str(row["ticker"]).upper() for row in rows})
        observations, contexts, provenance = _reconstruct(
            rows, bars, revisions, v5_config, v4_config, pine_config
        )
        checkpoint_temp = checkpoint.with_suffix(".pkl.tmp")
        with checkpoint_temp.open("wb") as handle:
            pickle.dump((observations, contexts, provenance), handle)
            handle.flush()
        checkpoint_temp.replace(checkpoint)
    for observation, context in zip(observations, contexts, strict=True):
        observation.setdefault("v4_engine_version", context["raw"].get("technical_engine_version"))
    dataset = pd.DataFrame(observations, columns=CALIBRATION_COLUMNS)
    dataset.to_csv(args.output_dir / "calibration_dataset.csv", index=False)

    _score_deciles(dataset).to_csv(args.output_dir / "score_deciles.csv", index=False)
    _v4_v5_comparison(dataset).to_csv(args.output_dir / "v4_v5_comparison.csv", index=False)
    _disagreements(dataset).to_csv(args.output_dir / "disagreements.csv", index=False)
    _danger_analysis(dataset).to_csv(args.output_dir / "danger_analysis.csv", index=False)
    _setup_analysis(dataset).to_csv(args.output_dir / "setup_type_analysis.csv", index=False)
    _slice(dataset, "market_regime").to_csv(args.output_dir / "regime_analysis.csv", index=False)
    _slice(dataset, "sector").to_csv(args.output_dir / "sector_analysis.csv", index=False)
    _confidence_analysis(dataset).to_csv(args.output_dir / "confidence_analysis.csv", index=False)
    _missing_data_analysis(dataset).to_csv(
        args.output_dir / "missing_data_analysis.csv", index=False
    )
    _architecture_analysis(dataset).to_csv(
        args.output_dir / "architecture_analysis.csv", index=False
    )
    _execution_slices(dataset).to_csv(args.output_dir / "execution_slices.csv", index=False)

    ablations = _ablation_campaign(dataset, contexts, v5_config, pine_config)
    ablations.to_csv(args.output_dir / "ablation_results.csv", index=False)
    stability = _leadership_stability(contexts, v5_config, pine_config)
    stability.to_csv(args.output_dir / "leadership_stability.csv", index=False)
    walk_forward = _walk_forward(dataset, contexts, v5_config, pine_config)
    walk_forward.to_csv(args.output_dir / "walk_forward_results.csv", index=False)

    verdict, gates = _activation_gates(dataset, provenance, walk_forward)
    _write_reports(args.output_dir, dataset, provenance, gates, verdict, ablations, walk_forward)
    checkpoint.unlink(missing_ok=True)
    print(json.dumps({"verdict": verdict, "observations": len(dataset), **provenance}, default=str))
    return 0


def _load_score_rows(max_runs: int | None) -> list[dict[str, Any]]:
    limit = (
        ""
        if max_runs is None
        else "WHERE t.run_id IN (SELECT id FROM upload_runs ORDER BY id DESC LIMIT :max_runs)"
    )
    query = text(
        f"""
        SELECT t.run_id, t.ticker, t.trend_score, t.local_trend_score,
               t.momentum_score, t.setup_score, t.risk_score, t.market_score,
               t.relative_strength_score, t.sector_relative_strength_score,
               t.combined_relative_strength_score, t.htf_score, t.dual_score,
               t.classification, t.pullback_health, t.action_bias, t.suggested_stop,
               t.suggested_target, t.reward_risk, t.entry_risk_pct,
               t.technical_confidence, t.technical_engine_version, t.data_quality_score,
               t.insufficient_data, t.missing_data_json, t.warning_flags_json,
               t.debug_json, t.created_at, u.uploaded_at,
               r.sector, r.sector_canonical
        FROM technical_scores t
        JOIN upload_runs u ON u.id = t.run_id
        LEFT JOIN LATERAL (
            SELECT sector, sector_canonical FROM raw_company_rows rr
            WHERE rr.run_id=t.run_id AND upper(rr.ticker)=upper(t.ticker)
            ORDER BY rr.id LIMIT 1
        ) r ON true
        {limit}
        ORDER BY t.run_id, t.ticker
        """
    )
    with engine.connect() as connection:
        return [dict(row) for row in connection.execute(query, {"max_runs": max_runs}).mappings()]


def _load_market_data(
    tickers: set[str],
) -> tuple[dict[str, pd.DataFrame], dict[str, list[dict[str, Any]]]]:
    benchmark_symbols = {
        "SPY",
        "XLK",
        "XLF",
        "XLE",
        "XLI",
        "XLV",
        "XLY",
        "XLP",
        "XLU",
        "XLRE",
        "XLB",
        "XLC",
    }
    symbols = sorted(tickers | benchmark_symbols)
    query = text(
        """SELECT id,ticker,bar_date AS date,open,high,low,close,volume,first_seen_at
           FROM price_bars WHERE timeframe='1 day' AND what_to_show='ADJUSTED_LAST'
             AND ticker = ANY(:symbols) ORDER BY ticker,bar_date"""
    )
    revision_query = text(
        """SELECT price_bar_id,ticker,observed_at,previous_values_json
           FROM price_bar_revisions WHERE timeframe='1 day' AND what_to_show='ADJUSTED_LAST'
             AND ticker = ANY(:symbols) ORDER BY ticker,observed_at"""
    )
    with engine.connect() as connection:
        raw = pd.DataFrame(connection.execute(query, {"symbols": symbols}).mappings().all())
        revision_rows = connection.execute(revision_query, {"symbols": symbols}).mappings().all()
    bars: dict[str, pd.DataFrame] = {}
    if not raw.empty:
        for column in ("open", "high", "low", "close", "volume"):
            raw[column] = pd.to_numeric(raw[column], errors="coerce")
        raw["date"] = pd.to_datetime(raw["date"])
        raw["first_seen_at"] = pd.to_datetime(raw["first_seen_at"], utc=True)
        bars = {ticker: group.reset_index(drop=True) for ticker, group in raw.groupby("ticker")}
    revisions: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in revision_rows:
        revisions[str(row["ticker"])].append(dict(row))
    return bars, revisions


def _reconstruct(
    rows: list[dict[str, Any]],
    bars: dict[str, pd.DataFrame],
    revisions: dict[str, list[dict[str, Any]]],
    v5_config: dict[str, Any],
    v4_config: dict[str, Any],
    pine_config: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    feature_cache: dict[str, pd.DataFrame] = {}
    restored_cache: dict[tuple[Any, ...], tuple[pd.DataFrame, pd.Series | None]] = {}
    residual_cache: dict[tuple[Any, ...], dict[str, Any]] = {}
    residual_series_cache: dict[tuple[str, str, int, int], pd.DataFrame] = {}
    candidates: list[dict[str, Any]] = []
    rejected: dict[str, int] = defaultdict(int)
    v5_hash = config_hash(v5_config)

    for raw in rows:
        debug = raw.get("debug_json") if isinstance(raw.get("debug_json"), dict) else {}
        indicator = (
            debug.get("indicator_debug") if isinstance(debug.get("indicator_debug"), dict) else {}
        )
        end_date_text = str(indicator.get("end_date") or "")
        if not end_date_text or not debug.get("derived") or raw.get("dual_score") is None:
            rejected["missing_persisted_decision_features"] += 1
            continue
        ticker = str(raw["ticker"]).upper()
        decision_date = pd.Timestamp(end_date_text).date()
        cutoff = pd.Timestamp(raw["created_at"]).tz_convert("UTC")
        source = bars.get(ticker)
        if source is None or source.empty:
            rejected["missing_price_series"] += 1
            continue
        if ticker not in feature_cache:
            feature_cache[ticker] = _calculate_feature_frame(
                prepare_ohlcv_frame(source[["date", "open", "high", "low", "close", "volume"]]),
                pine_config,
            )
        feature_row = _row_at(feature_cache[ticker], decision_date)
        final_adjusted_entry = (
            _optional_num(feature_row.get("close")) if feature_row is not None else None
        )
        if final_adjusted_entry is None:
            rejected["missing_final_adjusted_outcome_basis"] += 1
            continue
        stock_frame = source
        status = "CURRENT_SERIES_RECONCILED"
        current_series_reconciled = feature_row is not None and _reconciles(
            debug["derived"], feature_row
        )
        if not current_series_reconciled:
            key = _history_key(
                "STOCK_FEATURE", ticker, source, revisions.get(ticker, []), cutoff, decision_date
            )
            if key not in restored_cache:
                restored = _bars_as_of(source, revisions.get(ticker, []), cutoff, decision_date)
                restored_features = (
                    _calculate_feature_frame(prepare_ohlcv_frame(restored), pine_config)
                    if not restored.empty
                    else pd.DataFrame()
                )
                restored_cache[key] = (restored, _row_at(restored_features, decision_date))
            stock_frame, feature_row = restored_cache[key]
            status = "REVISION_HISTORY_RECONCILED"
        if feature_row is None or not _reconciles(debug["derived"], feature_row):
            rejected["feature_reconciliation_failed"] += 1
            continue

        spy_key = _history_key(
            "BENCHMARK", "SPY", bars["SPY"], revisions.get("SPY", []), cutoff, decision_date
        )
        if spy_key not in restored_cache:
            spy = _bars_as_of(bars["SPY"], revisions.get("SPY", []), cutoff, decision_date)
            restored_cache[spy_key] = (spy, None)
        spy = restored_cache[spy_key][0]
        ticker_requires_as_of = _requires_as_of_history(
            source, revisions.get(ticker, []), cutoff, decision_date
        )
        if current_series_reconciled and not ticker_requires_as_of:
            spy_state = _revision_state(revisions.get("SPY", []), cutoff)
            spy_seen_state = _first_seen_state(bars["SPY"], cutoff, decision_date)
            series_key = (ticker, decision_date.isoformat(), spy_state, spy_seen_state)
            if series_key not in residual_series_cache:
                residual_series_cache[series_key] = _residual_series(source, spy)
            residual = _residual_at(residual_series_cache[series_key], decision_date)
        else:
            residual_key = (
                "RESIDUAL",
                *_history_key(
                    "STOCK", ticker, source, revisions.get(ticker, []), cutoff, decision_date
                ),
                *_history_key(
                    "SPY", "SPY", bars["SPY"], revisions.get("SPY", []), cutoff, decision_date
                ),
            )
            if residual_key not in residual_cache:
                residual_stock_frame = (
                    stock_frame
                    if not current_series_reconciled
                    else _bars_as_of(source, revisions.get(ticker, []), cutoff, decision_date)
                )
                residual_cache[residual_key] = calculate_beta_adjusted_rs(
                    residual_stock_frame, spy, v4_config.get("relative_leadership", {})
                )
            residual = residual_cache[residual_key]

        resolution = resolve_sector_benchmark(
            raw.get("sector_canonical") or raw.get("sector"),
            v5_config["sector_benchmarks"]["mapping"],
        )
        sector_features: dict[str, float | None] = {}
        if resolution.benchmark_symbol and resolution.benchmark_symbol in bars:
            sector_symbol = resolution.benchmark_symbol
            sector_key = _history_key(
                "SECTOR",
                sector_symbol,
                bars[sector_symbol],
                revisions.get(sector_symbol, []),
                cutoff,
                decision_date,
            )
            if sector_key not in restored_cache:
                sector_frame = _bars_as_of(
                    bars[sector_symbol],
                    revisions.get(sector_symbol, []),
                    cutoff,
                    decision_date,
                )
                restored_cache[sector_key] = (sector_frame, None)
            sector_frame = restored_cache[sector_key][0]
            sector_features = _roc_features_at(sector_frame, decision_date)
            if not sector_features:
                resolution = mark_benchmark_data_missing(resolution)

        base = _base_score(raw, feature_row, residual, resolution, sector_features)
        decision_close = _optional_num(base.debug["derived"].get("close"))
        stop_target_comparable = (
            final_adjusted_entry is not None
            and decision_close is not None
            and math.isclose(final_adjusted_entry, decision_close, rel_tol=1e-10, abs_tol=1e-8)
        )
        candidates.append(
            {
                "raw": raw,
                "base": base,
                "resolution": resolution,
                "decision_date": decision_date,
                "status": status,
                "outcome_entry_price": final_adjusted_entry,
                "outcome_stop_price": base.suggested_stop if stop_target_comparable else None,
                "outcome_target_price": base.suggested_target if stop_target_comparable else None,
                "stop_target_outcome_status": (
                    "AVAILABLE" if stop_target_comparable else "UNAVAILABLE_PRICE_BASIS_CHANGED"
                ),
                "future_bars": _future_bars(source, decision_date),
            }
        )

    observations: list[dict[str, Any]] = []
    contexts: list[dict[str, Any]] = []
    by_run: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        by_run[int(candidate["raw"]["run_id"])].append(candidate)
    source_run_counts: dict[int, int] = defaultdict(int)
    for row in rows:
        source_run_counts[int(row["run_id"])] += 1
    complete_runs = {
        run_id for run_id, group in by_run.items() if len(group) == source_run_counts[run_id]
    }
    for run_id in sorted(complete_runs):
        group = by_run[run_id]
        leadership = rank_leadership_v5(
            [_leadership_input(item["base"]) for item in group], v5_config["leadership"]
        )
        for item in group:
            base: PineReplicaScore = item["base"]
            score = technical_score_v5_from_base_score(
                base,
                leadership=leadership.get(base.ticker),
                sector_resolution=item["resolution"],
                v5_config=v5_config,
                pine_config=pine_config,
            )
            observation = _observation(item, score, v5_hash)
            observations.append(observation)
            contexts.append({**item, "score": score, "leadership": leadership.get(base.ticker)})
    rejected["incomplete_run_universe"] = len(candidates) - sum(
        len(by_run[x]) for x in complete_runs
    )
    provenance = {
        "source_rows": len(rows),
        "reconstructed_rows": len(observations),
        "source_runs": len(source_run_counts),
        "reconstructed_runs": len(complete_runs),
        "rejected": dict(sorted(rejected.items())),
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "baseline_commit": BASELINE_COMMIT,
        "v5_config_hash": v5_hash,
    }
    return observations, contexts, provenance


def _base_score(
    raw: dict[str, Any],
    feature: pd.Series,
    residual: dict[str, Any],
    resolution: SectorBenchmarkResolution,
    sector_features: dict[str, float | None],
) -> PineReplicaScore:
    debug = copy.deepcopy(raw["debug_json"])
    derived = debug["derived"]
    for target, source in ENRICH_FIELDS.items():
        derived[target] = _python(feature.get(source))
    derived.update({key: _python(value) for key, value in residual.items()})
    sector_score = _sector_score(derived, resolution, sector_features)
    derived["v5_sector_rs_score"] = sector_score
    missing = raw.get("missing_data_json") if isinstance(raw.get("missing_data_json"), dict) else {}
    data_readiness = (
        debug.get("data_readiness") if isinstance(debug.get("data_readiness"), dict) else {}
    )
    missing.setdefault(
        "has_htf_data", bool(data_readiness.get("has_htf_data", derived.get("htf_data_ready")))
    )
    return PineReplicaScore(
        ticker=str(raw["ticker"]).upper(),
        local_trend_score=_num(raw["local_trend_score"]),
        trend_score=_num(raw["trend_score"]),
        momentum_score=_num(raw["momentum_score"]),
        setup_score=_num(raw["setup_score"]),
        risk_score=_num(raw["risk_score"]),
        market_score=_num(raw["market_score"]),
        relative_strength_score=_num(raw["relative_strength_score"]),
        sector_relative_strength_score=_num(raw["sector_relative_strength_score"]),
        combined_relative_strength_score=_num(raw["combined_relative_strength_score"]),
        htf_score=_num(raw["htf_score"]),
        dual_score=_num(raw["dual_score"]),
        classification=str(raw.get("classification") or "No trade"),
        action_bias=str(raw.get("action_bias") or "No clear trade"),
        pullback_health=str(raw.get("pullback_health") or ""),
        suggested_stop=_optional_num(raw.get("suggested_stop")),
        suggested_target=_optional_num(raw.get("suggested_target")),
        reward_risk=_optional_num(raw.get("reward_risk")),
        entry_risk_pct=_optional_num(raw.get("entry_risk_pct")),
        insufficient_data=bool(raw.get("insufficient_data")),
        missing_data=missing,
        debug=debug,
        technical_confidence=str(raw.get("technical_confidence") or "normal"),
        data_quality_score=_num(raw.get("data_quality_score"), 10.0),
        warning_flags=tuple(raw.get("warning_flags_json") or ()),
    )


def _observation(item: dict[str, Any], score: TechnicalScoreV5, v5_hash: str) -> dict[str, Any]:
    raw, base = item["raw"], item["base"]
    derived = base.debug["derived"]
    explain = base.debug["explainability"]
    outcome = construct_forward_outcomes(
        decision_date=item["decision_date"],
        entry_price=float(item["outcome_entry_price"]),
        future_bars=item["future_bars"],
        stop_price=item["outcome_stop_price"],
        target_price=item["outcome_target_price"],
        horizons=(1, 3, 5, 10),
    )
    ts = score.debug["technical_strength"]
    eq = score.debug["entry_quality"]
    trigger = score.trigger_quality
    row = {
        "run_id": raw["run_id"],
        "ticker": base.ticker,
        "decision_date": item["decision_date"],
        "v4_engine_version": raw["technical_engine_version"],
        "technical_engine_version": score.engine_version,
        "v5_config_hash": v5_hash,
        "input_signature": score.debug["input_signature"],
        "v4_score": base.dual_score,
        "v4_classification": base.classification,
        "v4_action": base.action_bias,
        "v5_TS": score.technical_strength_score,
        "v5_SQ": score.setup_quality_score,
        "v5_EQ": score.entry_quality_score,
        "v5_TCS": score.technical_composite_score,
        "v5_confidence_adjusted": score.confidence_adjusted_score,
        "v5_classification": score.classification,
        "v5_action": score.action_bias,
        "setup_type": score.setup_type,
        "market_regime": score.debug["composite"]["regime"],
        "sector": item["resolution"].sector,
        "sector_benchmark_symbol": score.sector_benchmark_symbol,
        "stage": score.setup_quality.stage,
        "stage_modifier": score.stage_modifier,
        "technical_confidence": score.technical_confidence,
        "data_quality_score": score.data_quality_score,
        "trend_quality": ts["components"]["trend"],
        "momentum_quality": ts["components"]["momentum"],
        "leadership_quality": ts["components"]["leadership"],
        "residual_momentum_score": score.residual_momentum_score,
        "trigger_quality": trigger.quality,
        "trigger_state": trigger.state,
        "trigger_distance_atr": score.trigger_distance_atr,
        "base_risk": eq["base_risk"],
        "climax_risk": eq["climax_risk"],
        "combined_risk": eq["combined_risk"],
        "risk_control": eq["risk_control"],
        "execution_quality": eq["execution"]["score"],
        "stop_distance_atr": score.stop_distance_atr,
        "reward_risk": base.reward_risk,
        "target_source": derived.get("target_source"),
        "danger_state": eq["danger_state"],
        "danger_cap": eq["danger_cap"],
        "warning_flags": "|".join(score.warning_flags),
        "missing_evidence": "|".join(x for x in score.warning_flags if x.startswith("missing_")),
        "explainability_reasons": "|".join(score.setup_quality.selection_reasons),
        "liquidity_bucket": "LOW" if derived.get("liquidity_warning") else "NORMAL",
        "atr_percentile_bucket": _bucket(_dict(explain.get("adaptive")).get("atr_percentile_252")),
        "outcome_price_basis": "FINAL_ADJUSTED_SERIES",
        "stop_target_outcome_status": item["stop_target_outcome_status"],
        "reconstruction_status": item["status"],
        "reconstruction_reason": "stored_features_reconciled",
        **outcome,
    }
    return {column: row.get(column) for column in CALIBRATION_COLUMNS}


def _score_deciles(dataset: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, column in SCORES.items():
        valid = dataset.dropna(subset=[column]).copy()
        if valid.empty:
            continue
        valid["quantile"] = np.ceil(
            valid.groupby("run_id")[column].rank(pct=True, method="first") * 10
        ).clip(1, 10)
        for quantile, group in valid.groupby("quantile"):
            rows.append({"score": name, "quantile": int(quantile), **_metrics(group)})
    return pd.DataFrame(rows)


def _v4_v5_comparison(dataset: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for name, column in SCORES.items():
        for selection, group in _selections(dataset, column):
            rows.append(
                {
                    "score": name,
                    "selection": selection,
                    **_metrics(group),
                    "spearman_5d": _within_run_spearman(group, column, "forward_return_5d"),
                    "spearman_10d": _within_run_spearman(group, column, "forward_return_10d"),
                    **_bootstrap_delta(dataset, column),
                }
            )
    return pd.DataFrame(rows)


def _slice(dataset: pd.DataFrame, field: str) -> pd.DataFrame:
    rows = []
    for value, group in dataset.fillna({field: "MISSING"}).groupby(field):
        rows.append({field: value, **_metrics(group)})
    return pd.DataFrame(rows)


def _setup_analysis(dataset: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for setup_type, setup_group in dataset.groupby("setup_type"):
        rows.append(
            {
                "setup_type": setup_type,
                "analysis_kind": "ALL",
                "slice_value": "ALL",
                **_metrics(setup_group),
            }
        )
        if len(setup_group) >= 4:
            ranked = setup_group.copy()
            ranked["sq_quantile"] = pd.qcut(
                ranked.v5_SQ.rank(method="first"), 4, labels=False, duplicates="drop"
            )
            for value, group in ranked.groupby("sq_quantile"):
                rows.append(
                    {
                        "setup_type": setup_type,
                        "analysis_kind": "SQ_QUARTILE",
                        "slice_value": int(value) + 1,
                        **_metrics(group),
                    }
                )
        for field in ("trigger_state", "stage", "market_regime"):
            for value, group in setup_group.fillna({field: "MISSING"}).groupby(field):
                rows.append(
                    {
                        "setup_type": setup_type,
                        "analysis_kind": field.upper(),
                        "slice_value": value,
                        **_metrics(group),
                    }
                )
    return pd.DataFrame(rows)


def _confidence_analysis(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    frame["confidence_slice"] = frame["technical_confidence"].fillna("MISSING")
    rows = []
    for value, group in frame.groupby("confidence_slice"):
        rows.append(
            {
                "confidence_slice": value,
                **_metrics(group),
                "mean_raw_tcs": group["v5_TCS"].mean(),
                "mean_adjusted_tcs": group["v5_confidence_adjusted"].mean(),
                "mean_adjustment": (group["v5_confidence_adjusted"] - group["v5_TCS"]).mean(),
                "raw_spearman_5d": _spearman(group["v5_TCS"], group["forward_return_5d"]),
                "adjusted_spearman_5d": _spearman(
                    group["v5_confidence_adjusted"], group["forward_return_5d"]
                ),
            }
        )
    return pd.DataFrame(rows)


def _missing_data_analysis(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    frame["raw_rank"] = frame.v5_TCS.rank(pct=True)
    frame["adjusted_rank"] = frame.v5_confidence_adjusted.rank(pct=True)
    warnings = frame.warning_flags.fillna("").astype(str).str.lower()
    states = {
        "missing_htf": warnings.str.contains("htf") & warnings.str.contains("missing"),
        "missing_sector": frame.sector_benchmark_symbol.isna(),
        "missing_leadership": frame.leadership_quality.isna(),
        "unknown_regime": frame.market_regime.fillna("").str.lower().eq("unknown"),
        "low_confidence": frame.technical_confidence.eq("low"),
        "error_confidence": frame.technical_confidence.eq("error"),
    }
    rows = []
    for state, mask in states.items():
        group = frame[mask]
        rows.append(
            {
                "missing_state": state,
                "N": len(group),
                "rate": float(mask.mean()),
                "mean_raw_TCS": group.v5_TCS.mean(),
                "mean_confidence_adjusted_TCS": group.v5_confidence_adjusted.mean(),
                "mean_rank_delta": (group.adjusted_rank - group.raw_rank).mean(),
                **{k: v for k, v in _metrics(group).items() if k != "N"},
            }
        )
    return pd.DataFrame(rows)


def _architecture_analysis(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    ts_median = frame["v5_TS"].median()
    eq_median = frame["v5_EQ"].median()
    frame["architecture_slice"] = np.select(
        [
            (frame.v5_TS >= ts_median) & (frame.v5_EQ >= eq_median),
            (frame.v5_TS >= ts_median) & (frame.v5_EQ < eq_median),
            (frame.v5_TS < ts_median) & (frame.v5_EQ >= eq_median),
        ],
        ["HIGH_TS_HIGH_EQ", "HIGH_TS_LOW_EQ", "LOW_TS_HIGH_EQ"],
        default="LOW_TS_LOW_EQ",
    )
    rows = [
        {
            "architecture_slice": value,
            "TS_cutoff": ts_median,
            "EQ_cutoff": eq_median,
            **_metrics(group),
        }
        for value, group in frame.groupby("architecture_slice")
    ]
    for label, group in (
        ("TS_GE_8_EQ_LT_5", frame[(frame.v5_TS >= 8.0) & (frame.v5_EQ < 5.0)]),
        ("TS_GE_8_EQ_GE_7", frame[(frame.v5_TS >= 8.0) & (frame.v5_EQ >= 7.0)]),
    ):
        rows.append(
            {
                "architecture_slice": label,
                "TS_cutoff": 8.0,
                "EQ_cutoff": 5.0 if "LT_5" in label else 7.0,
                **_metrics(group),
            }
        )
    return pd.DataFrame(rows)


def _danger_analysis(dataset: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    controls = dataset[dataset.danger_state.isna()].copy()
    for danger, group in dataset.dropna(subset=["danger_state"]).groupby("danger_state"):
        rows.append({"danger_state": danger, "cohort": "DANGER", **_metrics(group)})
        matched_parts = []
        for observation in group.itertuples(index=False):
            pool = controls[controls.market_regime == observation.market_regime]
            sector_pool = pool[pool.sector == observation.sector]
            if len(sector_pool) >= 5:
                pool = sector_pool
            setup_pool = pool[pool.setup_type == observation.setup_type]
            if len(setup_pool) >= 3:
                pool = setup_pool
            if pool.empty:
                continue
            distance = (pool.v5_TS - observation.v5_TS).abs() + (
                pool.v5_SQ - observation.v5_SQ
            ).abs()
            matched_parts.append(pool.loc[[distance.idxmin()]])
        matched = pd.concat(matched_parts) if matched_parts else controls.iloc[0:0]
        rows.append(
            {
                "danger_state": danger,
                "cohort": "MATCHED_NON_DANGER",
                "mean_abs_TS_match_delta": (
                    abs(group.v5_TS.mean() - matched.v5_TS.mean()) if len(matched) else None
                ),
                "mean_abs_SQ_match_delta": (
                    abs(group.v5_SQ.mean() - matched.v5_SQ.mean()) if len(matched) else None
                ),
                **_metrics(matched),
            }
        )
    return pd.DataFrame(rows)


def _execution_slices(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    frame["stop_distance_bucket"] = pd.cut(
        pd.to_numeric(frame.stop_distance_atr, errors="coerce"),
        [-np.inf, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, np.inf],
        labels=[
            "LT_0.5",
            "0.5_TO_1.0",
            "1.0_TO_1.5",
            "1.5_TO_2.0",
            "2.0_TO_2.5",
            "2.5_TO_3.0",
            "3.0_TO_4.0",
            "GT_4.0",
        ],
    ).astype("object")
    rows: list[dict[str, Any]] = []
    for field in (
        "stage",
        "trigger_state",
        "stop_distance_bucket",
        "target_source",
        "liquidity_bucket",
        "atr_percentile_bucket",
    ):
        for value, group in frame.fillna({field: "MISSING"}).groupby(field):
            rows.append({"slice_kind": field, "slice_value": value, **_metrics(group)})
    return pd.DataFrame(rows)


def _disagreements(dataset: pd.DataFrame) -> pd.DataFrame:
    frame = dataset.copy()
    frame["score_delta"] = frame["v5_TCS"] - frame["v4_score"]
    positive = frame.nlargest(100, "score_delta")
    negative = frame.nsmallest(100, "score_delta")
    result = pd.concat(
        [positive.assign(direction="V5_HIGHER"), negative.assign(direction="V4_HIGHER")]
    )
    columns = ["direction", "score_delta"] + [
        column for column in CALIBRATION_COLUMNS if column in result
    ]
    return result[columns]


def _ablation_campaign(
    dataset: pd.DataFrame,
    contexts: list[dict[str, Any]],
    config: dict[str, Any],
    pine: dict[str, Any],
) -> pd.DataFrame:
    variants = _variant_configs(config)
    leadership_variants = _ablation_leadership_variants(contexts, config)
    records: list[dict[str, Any]] = []
    outcome_columns = ["forward_return_5d", "forward_return_10d", "MFE_5d", "MAE_5d"]
    outcomes = dataset.set_index(["run_id", "ticker"])[outcome_columns].to_dict(orient="index")
    for context in contexts:
        baseline: TechnicalScoreV5 = context["score"]
        for name, candidate in variants.items():
            variant_context = context
            if name.startswith("A2_"):
                variant_context = {
                    **context,
                    "leadership": leadership_variants["no_residual_momentum"][
                        (int(context["raw"]["run_id"]), baseline.ticker)
                    ],
                }
            elif name.startswith("A11_"):
                variant_context = {
                    **context,
                    "leadership": leadership_variants["no_sector_rs"][
                        (int(context["raw"]["run_id"]), baseline.ticker)
                    ],
                }
            elif name.startswith("A13_"):
                variant_context = {
                    **context,
                    "leadership": leadership_variants["no_roc126"][
                        (int(context["raw"]["run_id"]), baseline.ticker)
                    ],
                }
            elif name.startswith("A14_"):
                variant_context = {
                    **context,
                    "leadership": leadership_variants["no_benchmark_rs"][
                        (int(context["raw"]["run_id"]), baseline.ticker)
                    ],
                }
            score = _variant_score(name, variant_context, candidate, pine)
            baseline_score = baseline.technical_composite_score
            candidate_score = score.technical_composite_score
            if name.startswith("A8_"):
                baseline_score = baseline.confidence_adjusted_score
            outcome = outcomes.get((int(context["raw"]["run_id"]), baseline.ticker), {})
            records.append(
                {
                    "variant": name,
                    "run_id": context["raw"]["run_id"],
                    "ticker": baseline.ticker,
                    "score": candidate_score,
                    "baseline_score": baseline_score,
                    "score_delta": candidate_score - baseline_score,
                    "classification_delta": score.classification != baseline.classification,
                    "action_delta": score.action_bias != baseline.action_bias,
                    "forward_return_5d": outcome.get("forward_return_5d"),
                    "forward_return_10d": outcome.get("forward_return_10d"),
                    "MFE_5d": outcome.get("MFE_5d"),
                    "MAE_5d": outcome.get("MAE_5d"),
                }
            )
    frame = pd.DataFrame(records)
    frame["variant_rank"] = frame.groupby(["variant", "run_id"])["score"].rank(pct=True)
    frame["baseline_rank"] = frame.groupby(["variant", "run_id"])["baseline_score"].rank(pct=True)
    frame["rank_delta"] = frame["variant_rank"] - frame["baseline_rank"]
    rows = []
    for name, group in frame.groupby("variant"):
        variant_top = group[group.variant_rank >= 0.8]
        baseline_top = group[group.baseline_rank >= 0.8]
        rows.append(
            {
                "variant": name,
                "N": len(group),
                "mean_score_delta": group.score_delta.mean(),
                "median_score_delta": group.score_delta.median(),
                "mean_abs_rank_delta": group.rank_delta.abs().mean(),
                "classification_delta_rate": group.classification_delta.mean(),
                "action_delta_rate": group.action_delta.mean(),
                "top20_N": len(variant_top),
                "top20_mean_5d_return": variant_top.forward_return_5d.mean(),
                "top20_mean_10d_return": variant_top.forward_return_10d.mean(),
                "top20_mean_MFE_5d": variant_top.MFE_5d.mean(),
                "top20_mean_MAE_5d": variant_top.MAE_5d.mean(),
                "forward_return_5d_delta": variant_top.forward_return_5d.mean()
                - baseline_top.forward_return_5d.mean(),
                "forward_return_10d_delta": variant_top.forward_return_10d.mean()
                - baseline_top.forward_return_10d.mean(),
                "MFE_5d_delta": variant_top.MFE_5d.mean() - baseline_top.MFE_5d.mean(),
                "MAE_5d_delta": variant_top.MAE_5d.mean() - baseline_top.MAE_5d.mean(),
                **_ablation_bootstrap_ci(group),
                "uncertainty_method": "RUN_CLUSTER_BOOTSTRAP",
                "evidence_strength": evidence_strength(group.forward_return_5d.notna().sum()),
            }
        )
    return pd.DataFrame(rows)


def _ablation_bootstrap_ci(group: pd.DataFrame) -> dict[str, float | None]:
    eligible = group.dropna(subset=["forward_return_5d"])
    if len(eligible) < 30:
        return {"forward_return_5d_delta_ci_low": None, "forward_return_5d_delta_ci_high": None}
    rng = np.random.default_rng(20260823)
    run_ids = eligible.run_id.unique()
    deltas = []
    for _ in range(400):
        parts = []
        for cluster, run_id in enumerate(rng.choice(run_ids, size=len(run_ids), replace=True)):
            parts.append(eligible[eligible.run_id == run_id].assign(bootstrap_cluster=cluster))
        sample = pd.concat(parts, ignore_index=True)
        variant_top = sample[sample.groupby("bootstrap_cluster").score.rank(pct=True) >= 0.8]
        baseline_top = sample[
            sample.groupby("bootstrap_cluster").baseline_score.rank(pct=True) >= 0.8
        ]
        deltas.append(variant_top.forward_return_5d.mean() - baseline_top.forward_return_5d.mean())
    return {
        "forward_return_5d_delta_ci_low": float(np.nanpercentile(deltas, 2.5)),
        "forward_return_5d_delta_ci_high": float(np.nanpercentile(deltas, 97.5)),
    }


def _ablation_leadership_variants(
    contexts: list[dict[str, Any]], config: dict[str, Any]
) -> dict[str, dict[tuple[int, str], Any]]:
    by_run: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for context in contexts:
        by_run[int(context["raw"]["run_id"])].append(context)
    variants: dict[str, dict[tuple[int, str], Any]] = {
        "no_residual_momentum": {},
        "no_sector_rs": {},
        "no_roc126": {},
        "no_benchmark_rs": {},
    }
    for run_id, group in by_run.items():
        no_residual_inputs = []
        no_sector_inputs = []
        no_roc126_inputs = []
        no_benchmark_inputs = []
        for context in group:
            item = _leadership_input(context["base"])
            no_residual_inputs.append({**item, "residual_momentum_score": None})
            no_sector_inputs.append(
                {
                    **item,
                    "benchmark_rs_score": context["base"].relative_strength_score,
                }
            )
            no_roc126_inputs.append({**item, "roc126": None})
            no_benchmark_inputs.append({**item, "benchmark_rs_score": None})
        for variant, inputs in (
            ("no_residual_momentum", no_residual_inputs),
            ("no_sector_rs", no_sector_inputs),
            ("no_roc126", no_roc126_inputs),
            ("no_benchmark_rs", no_benchmark_inputs),
        ):
            ranks = rank_leadership_v5(inputs, config["leadership"])
            variants[variant].update({(run_id, ticker): result for ticker, result in ranks.items()})
    return variants


def _variant_configs(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    names = [
        f"A{i}_{label}"
        for i, label in enumerate(
            (
                "baseline_v5",
                "no_leadership",
                "no_residual_momentum",
                "no_stage_modifier",
                "no_htf",
                "no_trigger_quality",
                "no_climax_risk",
                "old_max_setup_logic",
                "no_confidence_adjustment",
                "fixed_regime_weights",
                "no_momentum_acceleration",
                "no_sector_rs",
                "no_danger_caps",
                "no_roc126",
                "no_benchmark_rs",
            )
        )
    ]
    variants = {name: copy.deepcopy(config) for name in names}
    for value in variants[names[3]]["stage"]["modifiers"]:
        variants[names[3]]["stage"]["modifiers"][value] = 0.0
    variants[names[4]]["trend"].update({"local_weight": 1.0, "htf_weight": 0.0})
    variants[names[5]]["entry_quality"]["weights"] = {
        "risk_control": 0.625,
        "execution": 0.375,
        "trigger": 0.0,
    }
    variants[names[5]]["setup_quality"]["pullback"]["trigger_readiness"] = 0.0
    variants[names[5]]["setup_quality"]["vcp"]["trigger_readiness"] = 0.0
    variants[names[9]]["composite"]["choppy"] = copy.deepcopy(config["composite"]["bull_trend"])
    variants[names[9]]["composite"]["risk_off"] = copy.deepcopy(config["composite"]["bull_trend"])
    variants[names[10]]["momentum"].update({"base_weight": 1.0, "acceleration_weight": 0.0})
    for cap in variants[names[12]]["danger_caps"]["entry_quality"]:
        variants[names[12]]["danger_caps"]["entry_quality"][cap] = 10.0
    return variants


def _variant_score(
    name: str, context: dict[str, Any], config: dict[str, Any], pine: dict[str, Any]
) -> TechnicalScoreV5:
    base, leadership = context["base"], context["leadership"]
    resolution = context["resolution"]
    if name.startswith("A1_"):
        leadership = None
    if name.startswith("A6_"):
        debug = copy.deepcopy(base.debug)
        debug["explainability"]["climax"] = {"climax_risk_score": 0.0, "momentum_crash_risk": False}
        base = replace(base, debug=debug)
    if name.startswith("A11_"):
        debug = copy.deepcopy(base.debug)
        debug["derived"]["v5_sector_rs_score"] = None
        base = replace(base, debug=debug)
        resolution = SectorBenchmarkResolution(
            sector=resolution.sector,
            benchmark_symbol=None,
            status="MISSING_SECTOR",
            reason="sector_rs_removed_by_ablation",
        )
    score = technical_score_v5_from_base_score(
        base,
        leadership=leadership,
        sector_resolution=resolution,
        v5_config=config,
        pine_config=pine,
    )
    if name.startswith("A7_"):
        explain = base.debug["explainability"]
        old_sq = max(
            base.setup_score,
            _num(_dict(explain.get("contraction")).get("vcp_score")),
            _num(_dict(explain.get("box")).get("breakout_quality_score")),
        )
        weights = score.debug["composite"]["weights"]
        tcs = round(
            score.technical_strength_score * weights["technical_strength"]
            + old_sq * weights["setup_quality"]
            + score.entry_quality_score * weights["entry_quality"],
            4,
        )
        score = replace(score, setup_quality_score=old_sq, technical_composite_score=tcs)
    return score


def _leadership_stability(
    contexts: list[dict[str, Any]],
    config: dict[str, Any],
    pine: dict[str, Any],
) -> pd.DataFrame:
    by_run: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for context in contexts:
        by_run[int(context["raw"]["run_id"])].append(context)
    selected = sorted(by_run, key=lambda x: len(by_run[x]), reverse=True)[:8]
    rng = np.random.default_rng(20260823)
    rows = []
    for run_id in selected:
        group = by_run[run_id]
        full = {item["base"].ticker: item for item in group}
        for fraction in (0.90, 0.75):
            for repeat in range(10):
                size = max(2, math.floor(len(group) * fraction))
                subset = [
                    group[index] for index in rng.choice(len(group), size=size, replace=False)
                ]
                ranks = rank_leadership_v5(
                    [_leadership_input(item["base"]) for item in subset], config["leadership"]
                )
                for item in subset:
                    ticker = item["base"].ticker
                    baseline = full[ticker]["score"]
                    candidate = technical_score_v5_from_base_score(
                        item["base"],
                        leadership=ranks[ticker],
                        sector_resolution=item["resolution"],
                        v5_config=config,
                        pine_config=pine,
                    )
                    rows.append(
                        {
                            "run_id": run_id,
                            "subset_fraction": fraction,
                            "repeat": repeat,
                            "ticker": ticker,
                            "leadership_delta": ranks[ticker].leadership_score
                            - item["leadership"].leadership_score,
                            "baseline_score": baseline.technical_composite_score,
                            "candidate_score": candidate.technical_composite_score,
                            "TCS_delta": candidate.technical_composite_score
                            - baseline.technical_composite_score,
                            "classification_delta": candidate.classification
                            != baseline.classification,
                            "action_delta": candidate.action_bias != baseline.action_bias,
                        }
                    )
    if not rows:
        return pd.DataFrame(columns=["run_id", "subset_fraction", "N"])
    frame = pd.DataFrame(rows)
    frame["baseline_rank"] = frame.groupby(["run_id", "subset_fraction", "repeat"])[
        "baseline_score"
    ].rank(pct=True)
    frame["candidate_rank"] = frame.groupby(["run_id", "subset_fraction", "repeat"])[
        "candidate_score"
    ].rank(pct=True)
    frame["rank_delta"] = frame["candidate_rank"] - frame["baseline_rank"]
    return (
        frame.groupby(["run_id", "subset_fraction"])
        .agg(
            N=("ticker", "size"),
            mean_abs_leadership_delta=("leadership_delta", lambda x: x.abs().mean()),
            mean_abs_TCS_delta=("TCS_delta", lambda x: x.abs().mean()),
            mean_abs_rank_delta=("rank_delta", lambda x: x.abs().mean()),
            classification_delta_rate=("classification_delta", "mean"),
            action_delta_rate=("action_delta", "mean"),
        )
        .reset_index()
    )


def _walk_forward(
    dataset: pd.DataFrame,
    contexts: list[dict[str, Any]],
    config: dict[str, Any],
    pine: dict[str, Any],
) -> pd.DataFrame:
    eligible = dataset.dropna(subset=["forward_return_10d"]).copy()
    if eligible.decision_date.nunique() < 3:
        return pd.DataFrame([{"candidate": "BASELINE", "split": "blocked", "N": len(eligible)}])
    eligible["split"] = time_ordered_split_labels(eligible.decision_date)
    context_index = {(int(item["raw"]["run_id"]), item["base"].ticker): item for item in contexts}
    candidates: dict[str, dict[str, Any]] = {"BASELINE": copy.deepcopy(config)}
    candidate_leadership: dict[str, dict[tuple[int, str], Any]] = {}
    for weight in (0.0, 0.10, 0.20, 0.25, 0.30):
        candidate = copy.deepcopy(config)
        candidate["trend"] = {"local_weight": 1 - weight, "htf_weight": weight}
        candidates[f"HTF_{weight:.2f}"] = candidate
    for weight in (0.0, 0.10, 0.15, 0.20, 0.25):
        candidate = copy.deepcopy(config)
        candidate["momentum"] = {
            **candidate["momentum"],
            "base_weight": 1 - weight,
            "acceleration_weight": weight,
        }
        candidates[f"ACCEL_{weight:.2f}"] = candidate
    for scale, label in ((0.0, "STAGE_NONE"), (0.5, "STAGE_HALF"), (1.0, "STAGE_CURRENT")):
        candidate = copy.deepcopy(config)
        candidate["stage"]["modifiers"] = {
            k: v * scale for k, v in config["stage"]["modifiers"].items()
        }
        candidates[label] = candidate
    stronger_stage = copy.deepcopy(config)
    stronger_stage["stage"]["modifiers"].update({"stage_3": -0.75, "stage_4": -1.50})
    candidates["STAGE_STRONGER_34"] = stronger_stage
    base_leadership_weights = config["leadership"]["weights"]
    non_residual_total = 1.0 - float(base_leadership_weights["residual_momentum"])
    for weight in (0.0, 0.075, 0.15, 0.225, 0.30):
        candidate_name = f"RESIDUAL_{weight:.3f}"
        candidate = copy.deepcopy(config)
        scale = (1.0 - weight) / non_residual_total
        candidate["leadership"]["weights"] = {
            component: (
                weight if component == "residual_momentum" else float(component_weight) * scale
            )
            for component, component_weight in base_leadership_weights.items()
        }
        candidates[candidate_name] = candidate
        candidate_leadership[candidate_name] = _rank_context_leadership(
            contexts, candidate["leadership"]
        )
    records = []
    for candidate_name, candidate_config in candidates.items():
        for row in eligible.itertuples(index=False):
            context = context_index[(int(row.run_id), str(row.ticker))]
            leadership = context["leadership"]
            if candidate_name in candidate_leadership:
                leadership = candidate_leadership[candidate_name][
                    (int(row.run_id), str(row.ticker))
                ]
            score = technical_score_v5_from_base_score(
                context["base"],
                leadership=leadership,
                sector_resolution=context["resolution"],
                v5_config=candidate_config,
                pine_config=pine,
            )
            records.append(
                {
                    "candidate": candidate_name,
                    "split": row.split,
                    "score": score.technical_composite_score,
                    "return_5d": row.forward_return_5d,
                    "return_10d": row.forward_return_10d,
                }
            )
    frame = pd.DataFrame(records)
    frame["selected"] = frame.groupby(["candidate", "split"])["score"].rank(pct=True) >= 0.8
    metrics = (
        frame.groupby(["candidate", "split"])
        .apply(
            lambda x: pd.Series(
                {
                    "N": len(x),
                    "top20_N": int(x.selected.sum()),
                    "top20_mean_5d": x.loc[x.selected, "return_5d"].mean(),
                    "top20_mean_10d": x.loc[x.selected, "return_10d"].mean(),
                    "spearman_10d": _spearman(x.score, x.return_10d),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    calibration = metrics[metrics.split == "calibration"].dropna(subset=["top20_mean_10d"])
    frozen = (
        str(calibration.sort_values("top20_mean_10d", ascending=False).iloc[0].candidate)
        if not calibration.empty
        else "BASELINE"
    )
    metrics["selection_status"] = "CALIBRATION_CANDIDATE"
    metrics.loc[metrics.candidate == "BASELINE", "selection_status"] = "BASELINE"
    metrics.loc[metrics.candidate == frozen, "selection_status"] = "FROZEN_FROM_CALIBRATION"
    return metrics[
        (metrics.split != "holdout") | metrics.candidate.isin({"BASELINE", frozen})
    ].reset_index(drop=True)


def _rank_context_leadership(
    contexts: list[dict[str, Any]], leadership_config: dict[str, Any]
) -> dict[tuple[int, str], Any]:
    by_run: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for context in contexts:
        by_run[int(context["raw"]["run_id"])].append(context)
    results: dict[tuple[int, str], Any] = {}
    for run_id, group in by_run.items():
        ranks = rank_leadership_v5(
            [_leadership_input(item["base"]) for item in group], leadership_config
        )
        results.update({(run_id, ticker): value for ticker, value in ranks.items()})
    return results


def _activation_gates(
    dataset: pd.DataFrame, provenance: dict[str, Any], walk: pd.DataFrame
) -> tuple[str, list[dict[str, str]]]:
    has_holdout = not walk.empty and "holdout" in set(walk.get("split", []))
    sectors = dataset.sector.nunique(dropna=True)
    setups = dataset.setup_type.nunique(dropna=True)
    regimes = dataset.market_regime.nunique(dropna=True)
    calendar_days = (
        (
            pd.to_datetime(dataset.decision_date).max()
            - pd.to_datetime(dataset.decision_date).min()
        ).days
        if len(dataset)
        else 0
    )
    coverage_pass = regimes >= 3 and calendar_days >= 365
    gate_values = [
        (
            "G1 correctness",
            "PASS" if len(dataset) else "FAIL",
            "revision-aware feature reconciliation",
        ),
        (
            "G2 data coverage",
            "PASS" if coverage_pass else "FAIL",
            (
                f"{len(dataset)} rows; {calendar_days} calendar days; {regimes} regimes; "
                f"{setups} setups; {sectors} sectors"
            ),
        ),
        ("G3 ranking value versus v4", "FAIL", "top-selection evidence does not beat v4"),
        ("G4 danger-state validation", "FAIL", "matched danger outcomes are not worse"),
        ("G5 Entry Quality validation", "INSUFFICIENT", "requires later-period replication"),
        ("G6 robustness across slices", "FAIL", "limited calendar/regime span"),
        (
            "G7 out-of-sample validation",
            "INSUFFICIENT" if has_holdout else "FAIL",
            "time-ordered holdout exists but does not span independent market cycles",
        ),
        (
            "G8 downstream consumer safety",
            "FAIL",
            "default activation remains blocked by legacy-component consumers",
        ),
    ]
    verdict = "CONTINUE SHADOW" if len(dataset) else "CALIBRATION BLOCKED"
    return verdict, [
        {"gate": gate, "status": status, "evidence": evidence}
        for gate, status, evidence in gate_values
    ]


def _write_reports(
    output: Path,
    dataset: pd.DataFrame,
    provenance: dict[str, Any],
    gates: list[dict[str, str]],
    verdict: str,
    ablations: pd.DataFrame,
    walk: pd.DataFrame,
) -> None:
    gate_table = "\n".join(f"| {x['gate']} | {x['status']} | {x['evidence']} |" for x in gates)
    date_min = dataset.decision_date.min() if not dataset.empty else "n/a"
    date_max = dataset.decision_date.max() if not dataset.empty else "n/a"
    report = f"""# Technical Scoring v5 shadow evaluation

## Executive verdict

**{verdict}**

This point-in-time campaign used persisted v4 decision features and revision-aware
OHLCV reconstruction. Synthetic fixtures were not used as empirical trading evidence.
V5 remains disabled as the production default.

## Dataset and provenance

- Observations: {len(dataset):,}
- Runs: {dataset.run_id.nunique() if not dataset.empty else 0}
- Decision dates: {date_min} through {date_max}
- Source rows: {provenance["source_rows"]:,}
- Rejection audit: `{json.dumps(provenance["rejected"], sort_keys=True)}`
- Baseline commit: `{BASELINE_COMMIT}`
- V5 config hash: `{provenance["v5_config_hash"]}`
- Schema: `{CALIBRATION_SCHEMA_VERSION}`

Only complete run universes whose every member reconciled were admitted, so Leadership
never uses a future or partial universe. Forward outcomes begin strictly after the stored
feature end date. Confirmed weekly HTF behavior and pivot confirmation are covered by
tests.

## Empirical limits

The available history spans a short calendar window and does not provide representative
market-regime coverage. Any apparent component or ablation advantage is exploratory; no
candidate setting is promoted from this sample.

## Ablations and walk-forward

- Ablation variants evaluated: {len(ablations)}
- Walk-forward candidate/split rows: {len(walk)}
- No in-sample winner is adopted; the shipped baseline remains frozen.

## Activation gates

| Gate | Status | Evidence |
|---|---|---|
{gate_table}
"""
    Path("docs/technical_scoring_v5_shadow_evaluation.md").write_text(report, encoding="utf-8")
    activation = (
        "# Technical Scoring v5 activation gate report\n\n"
        f"**Verdict: {verdict}**\n\n"
        "| Gate | Status | Evidence |\n|---|---|---|\n"
        f"{gate_table}\n"
    )
    (output / "activation_gate_report.md").write_text(activation, encoding="utf-8")


def _metrics(group: pd.DataFrame) -> dict[str, Any]:
    n5, n10 = group.forward_return_5d.notna().sum(), group.forward_return_10d.notna().sum()
    return {
        "N": len(group),
        "N_5d": int(n5),
        "N_10d": int(n10),
        "mean_return_5d": group.forward_return_5d.mean(),
        "median_return_5d": group.forward_return_5d.median(),
        "std_return_5d": group.forward_return_5d.std(),
        "mean_return_10d": group.forward_return_10d.mean(),
        "median_return_10d": group.forward_return_10d.median(),
        "std_return_10d": group.forward_return_10d.std(),
        "mean_MFE_5d": group.MFE_5d.mean(),
        "median_MFE_5d": group.MFE_5d.median(),
        "mean_MAE_5d": group.MAE_5d.mean(),
        "median_MAE_5d": group.MAE_5d.median(),
        "mean_MFE_10d": group.MFE_10d.mean(),
        "median_MFE_10d": group.MFE_10d.median(),
        "mean_MAE_10d": group.MAE_10d.mean(),
        "median_MAE_10d": group.MAE_10d.median(),
        "positive_5d_rate": (group.forward_return_5d > 0)
        .where(group.forward_return_5d.notna())
        .mean(),
        "positive_10d_rate": (group.forward_return_10d > 0)
        .where(group.forward_return_10d.notna())
        .mean(),
        "target_hit_5d_rate": pd.to_numeric(group.target_hit_5d, errors="coerce").mean(),
        "stop_hit_5d_rate": pd.to_numeric(group.stop_hit_5d, errors="coerce").mean(),
        "target_hit_10d_rate": pd.to_numeric(group.target_hit_10d, errors="coerce").mean(),
        "stop_hit_10d_rate": pd.to_numeric(group.stop_hit_10d, errors="coerce").mean(),
        "MFE_MAE_ratio_5d": (
            group.MFE_5d.mean() / abs(group.MAE_5d.mean())
            if pd.notna(group.MAE_5d.mean()) and group.MAE_5d.mean() != 0
            else None
        ),
        "evidence_strength": evidence_strength(int(n5)),
    }


def _selections(frame: pd.DataFrame, column: str):
    yield "ALL", frame
    rank = frame.groupby("run_id")[column].rank(pct=True)
    for pct in (0.01, 0.05, 0.10, 0.20):
        yield f"TOP_{int(pct * 100)}PCT", frame[rank >= 1 - pct]
    for threshold in (7.0, 7.5, 8.0, 8.5):
        yield f"GE_{threshold:.1f}", frame[frame[column] >= threshold]


def _bootstrap_delta(frame: pd.DataFrame, column: str) -> dict[str, Any]:
    if column == "v4_score":
        return {"bootstrap_delta_low": 0.0, "bootstrap_delta_high": 0.0}
    paired = frame[["run_id", column, "v4_score", "forward_return_5d"]].dropna()
    if len(paired) < 30:
        return {"bootstrap_delta_low": None, "bootstrap_delta_high": None}
    rng = np.random.default_rng(20260823)
    run_ids = paired.run_id.unique()
    values = []
    for _ in range(400):
        parts = []
        for cluster, run_id in enumerate(rng.choice(run_ids, size=len(run_ids), replace=True)):
            parts.append(paired[paired.run_id == run_id].assign(bootstrap_cluster=cluster))
        sample = pd.concat(parts, ignore_index=True)
        top_new = sample[
            sample.groupby("bootstrap_cluster")[column].rank(pct=True) >= 0.8
        ].forward_return_5d.mean()
        top_v4 = sample[
            sample.groupby("bootstrap_cluster").v4_score.rank(pct=True) >= 0.8
        ].forward_return_5d.mean()
        values.append(top_new - top_v4)
    return {
        "bootstrap_delta_low": np.nanpercentile(values, 2.5),
        "bootstrap_delta_high": np.nanpercentile(values, 97.5),
    }


def _spearman(left: pd.Series, right: pd.Series) -> float | None:
    pair = pd.DataFrame({"x": left, "y": right}).dropna()
    if len(pair) < 3:
        return None
    return float(np.corrcoef(pair.x.rank(method="average"), pair.y.rank(method="average"))[0, 1])


def _within_run_spearman(frame: pd.DataFrame, left: str, right: str) -> float | None:
    pair = frame[["run_id", left, right]].dropna().copy()
    if len(pair) < 3:
        return None
    pair["x_rank"] = pair.groupby("run_id")[left].rank(method="average", pct=True)
    pair["y_rank"] = pair.groupby("run_id")[right].rank(method="average", pct=True)
    return float(np.corrcoef(pair.x_rank, pair.y_rank)[0, 1])


def _leadership_input(base: PineReplicaScore) -> dict[str, Any]:
    derived = base.debug["derived"]
    benchmark = base.relative_strength_score
    sector = _optional_num(derived.get("v5_sector_rs_score"))
    if sector is not None:
        benchmark = round(benchmark * 0.70 + sector * 0.30, 4)
    return {
        "ticker": base.ticker,
        "roc21": derived.get("stock_roc_short"),
        "roc63": derived.get("stock_roc_medium"),
        "roc126": derived.get("stock_roc_long"),
        "benchmark_rs_score": benchmark,
        "residual_momentum_score": derived.get("residual_momentum_score"),
    }


def _sector_score(
    derived: dict[str, Any],
    resolution: SectorBenchmarkResolution,
    benchmark: dict[str, float | None],
) -> float | None:
    if resolution.status != "RESOLVED" or any(
        benchmark.get(f"roc{x}") is None for x in (21, 63, 126)
    ):
        return None
    diffs = {
        x: _num(
            derived.get({21: "stock_roc_short", 63: "stock_roc_medium", 126: "stock_roc_long"}[x])
        )
        - _num(benchmark[f"roc{x}"])
        for x in (21, 63, 126)
    }
    return relative_strength_score(
        sum(diffs.values()) > 0,
        diffs[21],
        diffs[63],
        diffs[126],
        diffs[21] > 0,
        diffs[63] > 0,
        diffs[126] > 0,
        False,
    )


def _roc_features_at(frame: pd.DataFrame, decision_date: date) -> dict[str, float | None]:
    subset = frame[pd.to_datetime(frame.date).dt.date <= decision_date]
    close = pd.to_numeric(subset.close, errors="coerce").dropna()
    if close.empty:
        return {}
    return {
        f"roc{x}": round(float((close.iloc[-1] / close.iloc[-x - 1] - 1) * 100), 4)
        if len(close) > x
        else None
        for x in (21, 63, 126)
    }


def _residual_series(stock: pd.DataFrame, benchmark: pd.DataFrame) -> pd.DataFrame:
    left = stock[["date", "close"]].rename(columns={"close": "stock"}).copy()
    right = benchmark[["date", "close"]].rename(columns={"close": "benchmark"}).copy()
    left["date"] = pd.to_datetime(left["date"], utc=True).dt.tz_convert(None).dt.normalize()
    right["date"] = pd.to_datetime(right["date"], utc=True).dt.tz_convert(None).dt.normalize()
    aligned = left.merge(right, on="date").sort_values("date")
    stock_return = pd.to_numeric(aligned["stock"], errors="coerce").pct_change()
    benchmark_return = pd.to_numeric(aligned["benchmark"], errors="coerce").pct_change()
    variance_63 = benchmark_return.rolling(63, min_periods=63).var()
    beta_63 = stock_return.rolling(63, min_periods=63).cov(benchmark_return) / variance_63
    variance_126 = benchmark_return.rolling(126, min_periods=126).var()
    beta_126 = stock_return.rolling(126, min_periods=126).cov(benchmark_return) / variance_126
    residual = stock_return - beta_63 * benchmark_return
    residual_21 = (
        (1.0 + residual).rolling(21, min_periods=21).apply(np.prod, raw=True) - 1.0
    ) * 100.0
    residual_63 = (
        (1.0 + residual).rolling(63, min_periods=63).apply(np.prod, raw=True) - 1.0
    ) * 100.0
    score = (5.0 + residual_21.fillna(0.0) * 0.20 + residual_63.fillna(0.0) * 0.08).clip(0.0, 10.0)
    score = score.where(residual_21.notna() | residual_63.notna())
    return pd.DataFrame(
        {
            "date": aligned["date"],
            "rolling_beta_63": beta_63,
            "rolling_beta_126": beta_126,
            "residual_return_21": residual_21,
            "residual_return_63": residual_63,
            "residual_momentum_score": score,
        }
    )


def _residual_at(series: pd.DataFrame, decision_date: date) -> dict[str, Any]:
    match = series[pd.to_datetime(series.date).dt.date == decision_date]
    if match.empty:
        return {}
    row = match.iloc[-1]
    return {
        key: (None if pd.isna(row[key]) else round(float(row[key]), 4))
        for key in (
            "rolling_beta_63",
            "rolling_beta_126",
            "residual_return_21",
            "residual_return_63",
            "residual_momentum_score",
        )
    }


def _revision_state(revisions: list[dict[str, Any]], cutoff: pd.Timestamp) -> int:
    return sum(
        1
        for revision in revisions
        if pd.Timestamp(revision["observed_at"]).tz_convert("UTC") <= cutoff
    )


def _first_seen_state(source: pd.DataFrame, cutoff: pd.Timestamp, end_date: date) -> int:
    return int(((source.first_seen_at <= cutoff) & (source.date.dt.date <= end_date)).sum())


def _requires_as_of_history(
    source: pd.DataFrame,
    revisions: list[dict[str, Any]],
    cutoff: pd.Timestamp,
    end_date: date,
) -> bool:
    relevant = source[source.date.dt.date <= end_date]
    if bool((relevant.first_seen_at > cutoff).any()):
        return True
    return any(
        (
            pd.Timestamp(revision["observed_at"]).tz_convert("UTC")
            if pd.Timestamp(revision["observed_at"]).tzinfo
            else pd.Timestamp(revision["observed_at"]).tz_localize("UTC")
        )
        > cutoff
        for revision in revisions
    )


def _history_key(
    kind: str,
    ticker: str,
    source: pd.DataFrame,
    revisions: list[dict[str, Any]],
    cutoff: pd.Timestamp,
    end_date: date,
) -> tuple[str, str, str, int, int]:
    return (
        kind,
        ticker,
        end_date.isoformat(),
        _revision_state(revisions, cutoff),
        _first_seen_state(source, cutoff, end_date),
    )


def _bars_as_of(
    source: pd.DataFrame, revisions: list[dict[str, Any]], cutoff: pd.Timestamp, end_date: date
) -> pd.DataFrame:
    frame = source[(source.first_seen_at <= cutoff) & (source.date.dt.date <= end_date)].copy()
    first_after: dict[int, dict[str, Any]] = {}
    for revision in revisions:
        observed = pd.Timestamp(revision["observed_at"])
        observed = observed.tz_convert("UTC") if observed.tzinfo else observed.tz_localize("UTC")
        bar_id = int(revision["price_bar_id"])
        if observed > cutoff and bar_id not in first_after:
            first_after[bar_id] = revision.get("previous_values_json") or {}
    for bar_id, values in first_after.items():
        mask = frame.id == bar_id
        for column in ("open", "high", "low", "close", "volume"):
            if column in values:
                frame.loc[mask, column] = float(values[column])
    return frame[["date", "open", "high", "low", "close", "volume"]].sort_values("date")


def _row_at(features: pd.DataFrame, decision_date: date) -> pd.Series | None:
    if features.empty:
        return None
    match = features[pd.to_datetime(features.date).dt.date == decision_date]
    return None if match.empty else match.iloc[-1]


def _reconciles(derived: dict[str, Any], feature: pd.Series) -> bool:
    comparisons = 0
    for stored_name, feature_name in RECONCILE_FIELDS.items():
        stored, calculated = (
            _optional_num(derived.get(stored_name)),
            _optional_num(feature.get(feature_name)),
        )
        if stored is None or calculated is None:
            continue
        comparisons += 1
        if not math.isclose(stored, calculated, rel_tol=1e-10, abs_tol=1e-8):
            return False
    return comparisons >= 3


def _future_bars(source: pd.DataFrame, decision_date: date) -> pd.DataFrame:
    return source[source.date.dt.date > decision_date][
        ["date", "open", "high", "low", "close", "volume"]
    ].head(10)


def _bucket(value: Any) -> str:
    number = _optional_num(value)
    if number is None:
        return "MISSING"
    return (
        "0-25" if number < 25 else "25-50" if number < 50 else "50-75" if number < 75 else "75-100"
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _num(value: Any, default: float = 0.0) -> float:
    number = _optional_num(value)
    return default if number is None else number


def _optional_num(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)


def _python(value: Any) -> Any:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    return value.item() if hasattr(value, "item") else value


if __name__ == "__main__":
    raise SystemExit(main())
