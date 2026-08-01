from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.services.ceri.catalyst_feature_service import CatalystFeature
from app.services.ceri.config import CeriConfig, load_ceri_config


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

    def calculate(
        self,
        *,
        as_of_session: date,
        next_earnings_session: date | None = None,
        catalyst_features: list[CatalystFeature] | None = None,
        stale: bool = False,
        conflict_penalty: float = 0.0,
    ) -> EventRiskResult:
        catalyst_features = catalyst_features or []
        proximity = self.earnings_proximity(as_of_session, next_earnings_session)
        catalyst_risk = sum(feature.binary_risk_score for feature in catalyst_features)
        score = min(10.0, proximity.risk_score + catalyst_risk + conflict_penalty)
        warnings: list[str] = []
        reasons = [f"earnings_proximity:{proximity.level}"]
        if catalyst_risk:
            reasons.append("binary_catalyst_risk")
        if conflict_penalty:
            reasons.append("conflict_penalty")
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
        days = (next_earnings_session - as_of_session).days
        if days < 0:
            return EarningsProximity(days_until_earnings=days, level="clear", risk_score=0.0)
        if days <= int(self.config.event_risk["earnings_block_trading_days"]):
            return EarningsProximity(days_until_earnings=days, level="blocked", risk_score=5.0)
        if days <= int(self.config.event_risk["earnings_high_risk_trading_days"]):
            return EarningsProximity(days_until_earnings=days, level="high", risk_score=3.0)
        if days <= 10:
            return EarningsProximity(days_until_earnings=days, level="medium", risk_score=1.5)
        return EarningsProximity(days_until_earnings=days, level="clear", risk_score=0.0)
