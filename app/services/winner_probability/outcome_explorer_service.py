from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import WinnerPredictionSnapshot
from app.services.winner_probability.api_service import WinnerProbabilityApiService
from app.services.winner_probability.dtos import WinnerProbabilityApiQuery

ALLOWED_SEGMENTS = frozenset(
    {
        "setup_family",
        "setup_classification",
        "ranking_profile",
        "market_regime",
        "market_risk_state",
        "sector_state",
        "earnings_risk_level",
        "technical_data_quality",
    }
)


@dataclass(frozen=True)
class OutcomeExplorerQuery:
    segment_by: str = "setup_family"
    min_sample: int = 10
    api_query: WinnerProbabilityApiQuery = field(default_factory=WinnerProbabilityApiQuery)

    def __post_init__(self) -> None:
        if self.segment_by not in ALLOWED_SEGMENTS:
            raise ValueError(f"Unsupported segment_by value: {self.segment_by}")
        if self.min_sample < 0:
            raise ValueError("min_sample must be non-negative")


class OutcomeExplorerService:
    def __init__(self, *, api_service: WinnerProbabilityApiService | None = None) -> None:
        self.api_service = api_service or WinnerProbabilityApiService()

    def explorer_table(
        self,
        db: Session,
        *,
        query: OutcomeExplorerQuery,
    ) -> dict[str, Any]:
        run_ids = list(
            db.scalars(select(WinnerPredictionSnapshot.run_id).distinct().limit(500))
        )
        groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for run_id in run_ids:
            payload = self.api_service.get_run_evidence(
                db,
                run_id=run_id,
                query=query.api_query,
            )
            for row in payload["items"]:
                segment_value = row["prediction"].get(query.segment_by) or "Unknown"
                groups[str(segment_value)].append(row)
        segments = [
            _segment_payload(query.segment_by, segment_value, rows, query.min_sample)
            for segment_value, rows in groups.items()
        ]
        segments.sort(key=lambda row: (-row["sample_n"], row["segment_value"]))
        return {
            "segment_by": query.segment_by,
            "min_sample": query.min_sample,
            "segments": segments,
        }


def _segment_payload(
    segment_by: str,
    segment_value: str,
    rows: list[dict[str, Any]],
    min_sample: int,
) -> dict[str, Any]:
    estimates = [row["estimate"] for row in rows if row.get("estimate")]
    sample_n = len(estimates)
    suppressed = sample_n < min_sample
    probabilities = [
        estimate["point_probability"]
        for estimate in estimates
        if estimate.get("point_probability") is not None
    ]
    lower_bounds = [
        estimate["lower_bound"]
        for estimate in estimates
        if estimate.get("lower_bound") is not None
    ]
    return {
        "segment": segment_by,
        "segment_value": segment_value,
        "sample_n": sample_n,
        "suppressed": suppressed,
        "mean_probability": None if suppressed else _mean(probabilities),
        "mean_lower_bound": None if suppressed else _mean(lower_bounds),
        "evidence_grade_counts": _counts(
            estimate.get("evidence_grade") for estimate in estimates
        ),
    }


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        if value is None:
            continue
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))
