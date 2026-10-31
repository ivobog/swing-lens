"""market-rotation-v1: percent returns and percentage-point acceleration.

Pure functions. Absolute returns never affect the four-quadrant classification.
This transparent method does not implement proprietary JdK RRG or Technical V4/V5.
"""

import math
from dataclasses import dataclass
from datetime import date

from app.services.us_market_calendar import (
    CALENDAR_VERSION,
    is_us_trading_day,
    subtract_us_trading_sessions,
)

ALGORITHM_VERSION = "market-rotation-v1"
SESSION_OFFSETS = (0, 5, 21, 42, 63)
METRIC_NAMES = (
    "rs_5_pct",
    "rs_21_pct",
    "rs_63_pct",
    "rs_prior_21_pct",
    "relative_momentum_pp",
    "etf_return_63_pct",
    "spy_return_63_pct",
)


@dataclass(frozen=True)
class RotationMetrics:
    rs_5_pct: float
    rs_21_pct: float
    rs_63_pct: float
    rs_prior_21_pct: float
    relative_momentum_pp: float
    etf_return_63_pct: float
    spy_return_63_pct: float


def required_sessions(target: date, *, calendar_version: str = CALENDAR_VERSION) -> dict[int, date]:
    if not is_us_trading_day(target, calendar_version=calendar_version):
        raise ValueError("TARGET_MUST_BE_US_SESSION")
    return {
        offset: subtract_us_trading_sessions(target, offset, calendar_version=calendar_version)
        for offset in SESSION_OFFSETS
    }


def evaluate_pair(etf: dict[int, float], spy: dict[int, float]) -> RotationMetrics:
    try:
        for series in (etf, spy):
            for offset in SESSION_OFFSETS:
                value = series[offset]
                if isinstance(value, bool) or not math.isfinite(value) or value <= 0:
                    raise ValueError("INVALID_CLOSE")
        q = {offset: etf[offset] / spy[offset] for offset in SESSION_OFFSETS}
        if any(not math.isfinite(value) or value <= 0 for value in q.values()):
            raise ValueError("NONFINITE_METRICS")
        rs5 = 100 * (q[0] / q[5] - 1)
        rs21 = 100 * (q[0] / q[21] - 1)
        rs63 = 100 * (q[0] / q[63] - 1)
        prior21 = 100 * (q[21] / q[42] - 1)
        values = (
            rs5,
            rs21,
            rs63,
            prior21,
            rs21 - prior21,
            100 * (etf[0] / etf[63] - 1),
            100 * (spy[0] / spy[63] - 1),
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("NONFINITE_METRICS")
        return RotationMetrics(*values)
    except (OverflowError, ZeroDivisionError, KeyError, TypeError) as exc:
        raise ValueError("INVALID_OR_NONFINITE_ANCHORS") from exc


def quadrant(rs63: float, momentum: float) -> str:
    if not math.isfinite(rs63) or not math.isfinite(momentum):
        raise ValueError("NONFINITE_METRICS")
    if rs63 >= 0:
        return "Leading" if momentum >= 0 else "Weakening"
    return "Improving" if momentum >= 0 else "Lagging"


def rank_sectors(rows: list[dict]) -> None:
    eligible = [row for row in rows if row["quality_status"] == "ELIGIBLE"]
    for rank, row in enumerate(
        sorted(
            eligible,
            key=lambda row: (
                -row["rs_63_pct"],
                -row["rs_21_pct"],
                -row["rs_5_pct"],
                row["etf_symbol"],
            ),
        ),
        1,
    ):
        row["rank"] = rank
