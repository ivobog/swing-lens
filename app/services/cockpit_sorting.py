from typing import Protocol


class CockpitSortable(Protocol):
    sort_bucket: int | None
    final_score: object
    ticker: str


def cockpit_sort_key(decision: CockpitSortable) -> tuple[int, float, str]:
    return (
        decision.sort_bucket if decision.sort_bucket is not None else 999,
        -float(decision.final_score or 0),
        decision.ticker,
    )
