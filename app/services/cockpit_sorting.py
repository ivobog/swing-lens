from typing import Protocol


class CockpitSortable(Protocol):
    sort_bucket: int
    final_score: float
    ticker: str


def cockpit_sort_key(decision: CockpitSortable) -> tuple[int, float, str]:
    return (
        decision.sort_bucket,
        -decision.final_score,
        decision.ticker,
    )
