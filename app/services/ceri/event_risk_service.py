from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from app.services.ceri.catalyst_feature_service import CatalystFeature
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.effective_session_service import CeriEffectiveSessionService


@dataclass(frozen=True)
class EarningsProximity:
    days_until_earnings: int | None
    level: str
    risk_score: float


@dataclass(frozen=True)
class EventRiskResult:
    score: float
    earnings_proximity: EarningsProximity
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]


class CeriEventRiskService:
    def __init__(self, config: CeriConfig | None = None) -> None:
        self.config = config or load_ceri_config()
        self.sessions = CeriEffectiveSessionService(self.config.engine.timezone)

    def calculate(
        self,
        *,
        as_of_session: date,
        next_earnings_session: date | None = None,
        catalyst_features: list[CatalystFeature] | None = None,
        stale: bool = False,
        conflict_penalty: float = 0.0,
        options_event_premium_score: float = 0.0,
        short_pressure_classification: str | None = None,
    ) -> EventRiskResult:
        catalyst_features = catalyst_features or []
        proximity = self.earnings_proximity(as_of_session, next_earnings_session)
        catalyst_risk = sum(feature.binary_risk_score for feature in catalyst_features)
        options_event_premium_score = max(0.0, min(1.5, float(options_event_premium_score)))
        score = min(
            10.0,
            proximity.risk_score
            + catalyst_risk
            + conflict_penalty
            + options_event_premium_score,
        )
        warnings: list[str] = []
        reasons = [f"earnings_proximity:{proximity.level}"]
        if catalyst_risk:
            reasons.append("binary_catalyst_risk")
        if conflict_penalty:
            reasons.append("conflict_penalty")
        if options_event_premium_score:
            reasons.append("ibkr_options_event_premium")
        if short_pressure_classification:
            reasons.append(
                f"ibkr_short_pressure_context:{short_pressure_classification.lower()}"
            )
        if stale:
            warnings.append("data_stale")
            score = min(10.0, score + 1.0)
        return EventRiskResult(
            score=score,
            earnings_proximity=proximity,
            reasons=tuple(reasons),
            warnings=tuple(warnings),
        )

    def earnings_proximity(
        self,
        as_of_session: date,
        next_earnings_session: date | None,
    ) -> EarningsProximity:
        if next_earnings_session is None:
            return EarningsProximity(days_until_earnings=None, level="unknown", risk_score=1.0)
        as_of_session = self.sessions.next_trading_session(as_of_session)
        next_earnings_session = self.sessions.next_trading_session(next_earnings_session)
        days = _trading_sessions_until(as_of_session, next_earnings_session, self.sessions)
        if days < 0:
            return EarningsProximity(days_until_earnings=days, level="clear", risk_score=0.0)
        if days <= int(self.config.event_risk["earnings_block_trading_days"]):
            return EarningsProximity(days_until_earnings=days, level="blocked", risk_score=5.0)
        if days <= int(self.config.event_risk["earnings_high_risk_trading_days"]):
            return EarningsProximity(days_until_earnings=days, level="high", risk_score=3.0)
        if days <= 10:
            return EarningsProximity(days_until_earnings=days, level="medium", risk_score=1.5)
        return EarningsProximity(days_until_earnings=days, level="clear", risk_score=0.0)


def _trading_sessions_until(
    start: date,
    end: date,
    sessions: CeriEffectiveSessionService,
) -> int:
    if end <= start:
        return (end - start).days
    count = 0
    cursor = start
    while cursor < end:
        cursor = sessions.next_trading_session(cursor + timedelta(days=1))
        count += 1
    return count
