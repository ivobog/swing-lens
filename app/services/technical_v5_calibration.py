from collections.abc import Iterable
from typing import Any

HISTORICAL_COMPARISON_COLUMNS = (
    "ticker",
    "decision_date",
    "v4_score",
    "v5_TS",
    "v5_SQ",
    "v5_EQ",
    "v5_TCS",
    "classification",
    "setup_type",
    "market_regime",
    "sector",
    "forward_return_5d",
    "forward_return_10d",
    "MFE_5d",
    "MAE_5d",
    "MFE_10d",
    "MAE_10d",
)

SUPPORTED_ABLATIONS = (
    "leadership",
    "residual_momentum",
    "stage_modifier",
    "htf",
    "trigger_quality",
    "climax_risk",
    "old_max_setup",
)


def historical_comparison_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return a stable calibration schema without tuning or imputing outcomes."""

    return [{column: row.get(column) for column in HISTORICAL_COMPARISON_COLUMNS} for row in rows]


def component_ablation_record(
    *,
    ticker: str,
    baseline_tcs: float,
    variants: dict[str, float],
) -> dict[str, Any]:
    unsupported = sorted(set(variants) - set(SUPPORTED_ABLATIONS))
    if unsupported:
        raise ValueError(f"Unsupported v5 ablations: {', '.join(unsupported)}")
    return {
        "ticker": ticker.upper(),
        "baseline_tcs": round(float(baseline_tcs), 4),
        "ablations": {
            name: {
                "score": round(float(score), 4),
                "delta": round(float(score) - float(baseline_tcs), 4),
            }
            for name, score in sorted(variants.items())
        },
    }
