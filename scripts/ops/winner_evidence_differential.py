from __future__ import annotations

import argparse
import json
import math
import statistics
import tracemalloc
from dataclasses import asdict
from decimal import Decimal
from time import perf_counter
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.db import SessionLocal, engine
from app.models.tables import WinnerOutcomeDefinition, WinnerPredictionSnapshot
from app.services.winner_probability.cohort_definition import CohortDefinitionService
from app.services.winner_probability.cohort_statistics import CohortStatisticsService
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.evidence_manifest_service import (
    _hash_payload,
    _manifest_payload,
)
from app.services.winner_probability.evidence_service import EvidenceService


class QueryProbe:
    def __init__(self) -> None:
        self.count = 0
        self.db_seconds = 0.0
        self.native_base_queries = 0
        self.compatibility_base_queries = 0
        self._started: list[float] = []

    def before(self, _conn, _cursor, statement, _parameters, _context, _executemany) -> None:
        self.count += 1
        self._started.append(perf_counter())
        normalized = str(statement).lower()
        if (
            "winner_prediction_snapshots" in normalized
            and "winner_forward_outcomes" in normalized
            and "winner_target_stop_outcomes" in normalized
        ):
            self.native_base_queries += 1
        if (
            "winner_training_eligibility_decisions" in normalized
            and "winner_training_outcome_replays" in normalized
        ):
            self.compatibility_base_queries += 1

    def after(self, *_args) -> None:
        if self._started:
            self.db_seconds += max(0.0, perf_counter() - self._started.pop())


def evaluate_predictions(
    db: Session,
    *,
    service: EvidenceService,
    predictions: list[WinnerPredictionSnapshot],
    outcome_definition: WinnerOutcomeDefinition,
    config,
) -> tuple[list[dict[str, Any]], list[float], int]:
    cohort_service = CohortDefinitionService()
    statistics_service = CohortStatisticsService()
    results: list[dict[str, Any]] = []
    ticker_seconds: list[float] = []
    loaded_rows = 0
    for prediction in predictions:
        started = perf_counter()
        keys = cohort_service.cohort_keys_for_prediction(prediction, config)
        broadest = keys[-1]
        funnel = service.diagnostic_funnel(
            db,
            prediction=prediction,
            outcome_definition=outcome_definition,
            cohort_key=broadest,
            training_cutoff_at=prediction.source_data_cutoff_at,
            config=config,
        )
        loaded_rows += funnel.stages[0].before_count if funnel.stages else 0
        calculated: dict[str, tuple[Any, tuple[Any, ...], Any]] = {}
        for level, key in zip(config.cohort.hierarchy, keys, strict=True):
            evidence = service.filter_for_cohort(funnel.evidence, key)
            calculated[level.level] = (
                key,
                evidence,
                statistics_service.calculate(evidence, config),
            )
        selected = None
        for level in config.cohort.hierarchy:
            key, evidence, cohort_statistics = calculated[level.level]
            if (
                cohort_statistics.effective_n >= Decimal(level.min_effective_n)
                and cohort_statistics.interval_width
                <= Decimal(str(config.cohort.max_interval_width))
                and cohort_statistics.evidence_grade != "Insufficient"
            ):
                selected = (key, evidence, cohort_statistics)
                break
        fallback = calculated[config.cohort.hierarchy[-1].level]
        final_key, final_evidence, final_statistics = selected or fallback
        results.append(
            {
                "prediction_id": int(prediction.id),
                "ticker": prediction.ticker,
                "cutoff": prediction.source_data_cutoff_at.isoformat(),
                "funnel": [asdict(stage) for stage in funnel.stages],
                "members": [_member_identity(row) for row in final_evidence],
                "manifest_hash": _hash_payload(_manifest_payload(final_evidence)),
                "selected_cohort": final_key.key if selected is not None else None,
                "statistics": _statistics_payload(final_statistics),
                "final_estimate": _estimate_payload(selected, fallback),
            }
        )
        ticker_seconds.append(max(0.0, perf_counter() - started))
    return results, ticker_seconds, loaded_rows


def run(run_id: int | None, limit: int) -> dict[str, Any]:
    config = load_winner_probability_config()
    with SessionLocal() as db:
        outcome_definition = db.scalar(
            select(WinnerOutcomeDefinition)
            .where(
                WinnerOutcomeDefinition.definition_id
                == config.primary_outcome_definition.id
            )
            .where(
                WinnerOutcomeDefinition.calculation_version
                == config.engine.calculation_version
            )
            .where(WinnerOutcomeDefinition.is_active.is_(True))
        )
        if outcome_definition is None:
            raise RuntimeError("active primary Winner outcome definition was not found")
        statement = (
            select(WinnerPredictionSnapshot)
            .where(WinnerPredictionSnapshot.eligibility_status == "ELIGIBLE")
            .where(WinnerPredictionSnapshot.superseded_at.is_(None))
            .distinct(WinnerPredictionSnapshot.source_data_cutoff_at)
            .order_by(
                WinnerPredictionSnapshot.source_data_cutoff_at.desc(),
                WinnerPredictionSnapshot.id,
            )
            .limit(limit)
        )
        if run_id is not None:
            statement = statement.where(WinnerPredictionSnapshot.run_id == run_id)
        predictions = list(db.scalars(statement))
        if not predictions:
            raise RuntimeError("no eligible Winner predictions matched the certification scope")

        old_probe = QueryProbe()
        event.listen(engine, "before_cursor_execute", old_probe.before)
        event.listen(engine, "after_cursor_execute", old_probe.after)
        tracemalloc.start()
        old_started = perf_counter()
        try:
            old_results, old_ticker_seconds, old_rows = evaluate_predictions(
                db,
                service=EvidenceService(),
                predictions=predictions,
                outcome_definition=outcome_definition,
                config=config,
            )
            old_wall = perf_counter() - old_started
            old_peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
            event.remove(engine, "before_cursor_execute", old_probe.before)
            event.remove(engine, "after_cursor_execute", old_probe.after)
            db.rollback()

        optimized_probe = QueryProbe()
        optimized_service = EvidenceService()
        event.listen(engine, "before_cursor_execute", optimized_probe.before)
        event.listen(engine, "after_cursor_execute", optimized_probe.after)
        tracemalloc.start()
        optimized_started = perf_counter()
        try:
            optimized_service.prepare_run_candidate_universe(
                db,
                outcome_definition=outcome_definition,
                config=config,
            )
            optimized_results, optimized_ticker_seconds, optimized_rows = evaluate_predictions(
                db,
                service=optimized_service,
                predictions=predictions,
                outcome_definition=outcome_definition,
                config=config,
            )
            optimized_wall = perf_counter() - optimized_started
            optimized_peak = tracemalloc.get_traced_memory()[1]
            reuse_metrics = optimized_service.run_candidate_metrics()
        finally:
            tracemalloc.stop()
            event.remove(engine, "before_cursor_execute", optimized_probe.before)
            event.remove(engine, "after_cursor_execute", optimized_probe.after)
            db.rollback()

    mismatches = [
        {
            "prediction_id": old["prediction_id"],
            "old": old,
            "optimized": optimized,
        }
        for old, optimized in zip(old_results, optimized_results, strict=True)
        if not _semantically_equivalent(old, optimized)
    ]
    return {
        "verdict": "EQUIVALENT" if not mismatches else "MISMATCH",
        "prediction_count": len(predictions),
        "distinct_cutoffs": len({row["cutoff"] for row in old_results}),
        "mismatches": mismatches,
        "before": _performance_payload(
            old_probe,
            old_wall,
            old_ticker_seconds,
            old_rows,
            old_peak,
        ),
        "optimized": {
            **_performance_payload(
                optimized_probe,
                optimized_wall,
                optimized_ticker_seconds,
                optimized_rows,
                optimized_peak,
            ),
            **reuse_metrics,
        },
    }


def _member_identity(row) -> dict[str, Any]:
    return {
        "prediction_id": int(row.prediction.id),
        "forward_outcome_id": int(row.forward_outcome.id),
        "target_stop_outcome_id": int(row.target_stop_outcome.id),
        "eligibility_decision_id": row.eligibility_decision_id,
        "temporal_validity_decision_id": row.temporal_validity_decision_id,
        "outcome_replay_id": row.outcome_replay_id,
        "episode_id": row.prediction.episode_id,
        "evidence_origin": row.evidence_origin,
        "inclusion_weight": str(row.inclusion_weight),
    }


def _semantically_equivalent(old: dict[str, Any], optimized: dict[str, Any]) -> bool:
    # Safe SQL prefilters deliberately change diagnostic candidate counts.
    # The required equivalence boundary is the final membership and estimate.
    semantic_keys = {
        "prediction_id",
        "ticker",
        "cutoff",
        "members",
        "manifest_hash",
        "selected_cohort",
        "statistics",
        "final_estimate",
    }
    return {key: old[key] for key in semantic_keys} == {
        key: optimized[key] for key in semantic_keys
    }


def _statistics_payload(value) -> dict[str, Any]:
    return {key: str(item) for key, item in asdict(value).items()}


def _estimate_payload(selected, fallback) -> dict[str, Any]:
    _key, _evidence, statistics_value = selected or fallback
    return {
        "status": "estimated" if selected is not None else "insufficient",
        "point_probability": (
            str(statistics_value.posterior_probability) if selected is not None else None
        ),
        "lower_bound": str(statistics_value.lower_bound),
        "upper_bound": str(statistics_value.upper_bound),
        "sample_n": statistics_value.sample_n,
        "effective_n": str(statistics_value.effective_n),
        "wins": str(statistics_value.wins),
        "evidence_grade": (
            statistics_value.evidence_grade if selected is not None else "Insufficient"
        ),
    }


def _performance_payload(
    probe: QueryProbe,
    wall_seconds: float,
    ticker_seconds: list[float],
    loaded_rows: int,
    peak_bytes: int,
) -> dict[str, Any]:
    ordered = sorted(ticker_seconds)
    p95_index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1))
    return {
        "sql_query_count": probe.count,
        "native_base_queries": probe.native_base_queries,
        "compatibility_base_queries": probe.compatibility_base_queries,
        "candidate_rows_seen": loaded_rows,
        "wall_seconds": round(wall_seconds, 6),
        "db_seconds": round(probe.db_seconds, 6),
        "ticker_median_seconds": round(statistics.median(ticker_seconds), 6),
        "ticker_p95_seconds": round(ordered[p95_index], 6),
        "python_peak_bytes": peak_bytes,
        "manifest_persistence_seconds": None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only old-vs-run-scoped Winner evidence certification"
    )
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--limit", type=int, default=10)
    args = parser.parse_args()
    report = run(args.run_id, max(1, args.limit))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "EQUIVALENT" else 1


if __name__ == "__main__":
    raise SystemExit(main())
