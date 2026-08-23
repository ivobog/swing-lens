from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class LeadershipV5Result:
    ticker: str
    leadership_score: float | None
    percentiles: dict[str, float | None]
    weighted_components: dict[str, float]
    missing_components: tuple[str, ...]
    scope: str
    universe_size: int
    available_component_count: int
    leadership_tags: tuple[str, ...]


def rank_leadership_v5(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, LeadershipV5Result]:
    if not rows:
        return {}
    frame = pd.DataFrame(rows).copy()
    if "ticker" not in frame:
        return {}
    frame["ticker"] = frame["ticker"].astype(str).str.upper()
    weights = {key: float(value) for key, value in config.get("weights", {}).items()}
    columns = {
        "roc21": "roc21",
        "roc63": "roc63",
        "roc126": "roc126",
        "benchmark_rs": "benchmark_rs_score",
        "residual_momentum": "residual_momentum_score",
    }
    percentile_columns: dict[str, str] = {}
    for component, source in columns.items():
        target = f"p_{component}"
        percentile_columns[component] = target
        numeric = (
            pd.to_numeric(frame[source], errors="coerce")
            if source in frame
            else pd.Series(index=frame.index, dtype=float)
        )
        frame[target] = numeric.rank(pct=True, method="average") * 100.0

    scope = str(config.get("scope") or "run_universe")
    universe_size = len(frame)
    threshold = float(config.get("leadership_min_percentile", 70.0)) / 10.0
    renormalize = bool(config.get("renormalize_missing", True))
    results: dict[str, LeadershipV5Result] = {}
    for record in frame.to_dict(orient="records"):
        percentiles = {
            component: _optional_float(record.get(column))
            for component, column in percentile_columns.items()
        }
        available = [name for name, value in percentiles.items() if value is not None]
        missing = tuple(name for name in weights if name not in available)
        denominator = sum(weights[name] for name in available) if renormalize else 1.0
        weighted = {
            name: round((percentiles[name] / 10.0) * weights[name] / denominator, 4)
            for name in available
            if denominator > 0
        }
        score = round(sum(weighted.values()), 4) if weighted else None
        tags = ("rs_leader",) if score is not None and score >= threshold else ()
        ticker = str(record["ticker"]).upper()
        results[ticker] = LeadershipV5Result(
            ticker=ticker,
            leadership_score=score,
            percentiles=percentiles,
            weighted_components=weighted,
            missing_components=missing,
            scope=scope,
            universe_size=universe_size,
            available_component_count=len(available),
            leadership_tags=tags,
        )
    return results


def _optional_float(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return round(float(value), 4)
