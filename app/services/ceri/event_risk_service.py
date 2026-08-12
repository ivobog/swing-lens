from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.services.ceri.catalyst_feature_service import CatalystFeature
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.effective_session_service import CeriEffectiveSessionService


@dataclass(frozen=True)
class EarningsProximity:
    days_until_earnings: int | None
    level: str
    risk_score: float


@dataclass(frozen=True)
class EventRiskLedgerEntry:
    component: str
    score: float
    event_ids: tuple[int, ...] = ()
    reason: str | None = None


@dataclass(frozen=True)
class EventRiskResult:
    score: float
    earnings_proximity: EarningsProximity
    dominant_component: str
    ledger: tuple[EventRiskLedgerEntry, ...]
    selected_event_ids: tuple[int, ...]
    rejected_event_ids: tuple[int, ...]
    penalties: tuple[dict[str, float], ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    rejected_events: tuple[dict[str, Any], ...] = ()


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
        eligible = [
            feature
            for feature in catalyst_features
            if feature.selected and feature.binary_eligible and feature.binary_risk_score > 0
        ]
        deduped: dict[str, CatalystFeature] = {}
        for feature in eligible:
            key = feature.dedup_key or f"event:{feature.catalyst_event_id}"
            current = deduped.get(key)
            if current is None or feature.binary_risk_score > current.binary_risk_score:
                deduped[key] = feature
        by_component: dict[str, list[CatalystFeature]] = {}
        for feature in deduped.values():
            by_component.setdefault(
                feature.risk_component or "other_event_risk", []
            ).append(feature)
        ledger = [
            EventRiskLedgerEntry(
                component="earnings_proximity_risk",
                score=proximity.risk_score,
                reason=f"earnings_proximity:{proximity.level}",
            )
        ]
        for component, features in sorted(by_component.items()):
            score = max(feature.binary_risk_score for feature in features)
            ledger.append(
                EventRiskLedgerEntry(
                    component=component,
                    score=score,
                    event_ids=tuple(
                        sorted(
                            feature.catalyst_event_id
                            for feature in features
                            if feature.catalyst_event_id is not None
                        )
                    ),
                    reason="dominant_deduplicated_event_risk",
                )
            )
        dominant = max(ledger, key=lambda entry: entry.score)
        options_event_premium_score = max(0.0, min(1.5, float(options_event_premium_score)))
        penalties: list[dict[str, float]] = []
        if conflict_penalty:
            penalties.append({"name": "conflict_penalty", "value": max(0.0, conflict_penalty)})
        if options_event_premium_score:
            penalties.append(
                {"name": "ibkr_options_event_premium", "value": options_event_premium_score}
            )
        if stale:
            penalties.append(
                {
                    "name": "staleness_penalty",
                    "value": float(self.config.event_risk.get("staleness_penalty", 1.0)),
                }
            )
        penalty_cap = float(self.config.event_risk.get("secondary_penalty_cap", 2.0))
        applied_penalty = min(penalty_cap, sum(item["value"] for item in penalties))
        score = min(10.0, dominant.score + applied_penalty)
        warnings: list[str] = []
        reasons = [f"dominant_risk:{dominant.component}"]
        if next_earnings_session is None:
            warnings.append("earnings_date_unavailable")
        if eligible:
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
        rejected_ids = tuple(
            sorted(
                feature.catalyst_event_id
                for feature in catalyst_features
                if feature.catalyst_event_id is not None
                and (not feature.selected or not feature.binary_eligible)
            )
        )
        rejected_events = tuple(
            {
                "event_id": feature.catalyst_event_id,
                "revision_id": feature.catalyst_revision_id,
                "reason": feature.rejection_reason
                or (
                    "ISSUER_RELEVANCE_UNVERIFIED"
                    if feature.issuer_relevance is not True
                    else "LIFECYCLE_OR_TAXONOMY_BINARY_INELIGIBLE"
                ),
            }
            for feature in catalyst_features
            if feature.catalyst_event_id in rejected_ids
        )
        return EventRiskResult(
            score=score,
            earnings_proximity=proximity,
            dominant_component=dominant.component,
            ledger=tuple(ledger),
            selected_event_ids=tuple(
                sorted(
                    feature.catalyst_event_id
                    for feature in deduped.values()
                    if feature.catalyst_event_id is not None
                )
            ),
            rejected_event_ids=rejected_ids,
            penalties=tuple(penalties),
            reasons=tuple(reasons),
            warnings=tuple(warnings),
            rejected_events=rejected_events,
        )

    def earnings_proximity(
        self,
        as_of_session: date,
        next_earnings_session: date | None,
    ) -> EarningsProximity:
        if next_earnings_session is None:
            return EarningsProximity(days_until_earnings=None, level="unknown", risk_score=0.0)
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
