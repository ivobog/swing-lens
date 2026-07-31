from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.models.tables import FirstEvent
from app.services.winner_probability.config import SAME_BAR_CONSERVATIVE_STOP_FIRST


@dataclass(frozen=True)
class TargetStopEvaluation:
    target_hit: bool
    stop_hit: bool
    first_event: str
    event_session: date | None
    same_bar_conflict: bool
    primary_winner: bool
    optimistic_winner: bool
    conservative_winner: bool


class TargetStopService:
    def evaluate(
        self,
        *,
        bars: list,
        entry_price: Decimal,
        target_pct: Decimal,
        stop_pct: Decimal,
        same_bar_conflict_policy: str = SAME_BAR_CONSERVATIVE_STOP_FIRST,
    ) -> TargetStopEvaluation:
        target_price = entry_price * (Decimal("1") + target_pct / Decimal("100"))
        stop_price = entry_price * (Decimal("1") - stop_pct / Decimal("100"))
        target_hit_any = False
        stop_hit_any = False

        for bar in sorted(bars, key=lambda row: row.bar_date):
            target_hit = Decimal(str(bar.high)) >= target_price
            stop_hit = Decimal(str(bar.low)) <= stop_price
            target_hit_any = target_hit_any or target_hit
            stop_hit_any = stop_hit_any or stop_hit
            if target_hit and stop_hit:
                conservative = False
                optimistic = True
                return TargetStopEvaluation(
                    target_hit=True,
                    stop_hit=True,
                    first_event=FirstEvent.SAME_BAR_CONFLICT,
                    event_session=bar.bar_date,
                    same_bar_conflict=True,
                    primary_winner=(
                        conservative
                        if same_bar_conflict_policy == SAME_BAR_CONSERVATIVE_STOP_FIRST
                        else optimistic
                    ),
                    optimistic_winner=optimistic,
                    conservative_winner=conservative,
                )
            if target_hit:
                return TargetStopEvaluation(
                    target_hit=True,
                    stop_hit=stop_hit_any,
                    first_event=FirstEvent.TARGET_FIRST,
                    event_session=bar.bar_date,
                    same_bar_conflict=False,
                    primary_winner=True,
                    optimistic_winner=True,
                    conservative_winner=True,
                )
            if stop_hit:
                return TargetStopEvaluation(
                    target_hit=target_hit_any,
                    stop_hit=True,
                    first_event=FirstEvent.STOP_FIRST,
                    event_session=bar.bar_date,
                    same_bar_conflict=False,
                    primary_winner=False,
                    optimistic_winner=False,
                    conservative_winner=False,
                )

        return TargetStopEvaluation(
            target_hit=target_hit_any,
            stop_hit=stop_hit_any,
            first_event=FirstEvent.NEITHER,
            event_session=None,
            same_bar_conflict=False,
            primary_winner=False,
            optimistic_winner=False,
            conservative_winner=False,
        )
