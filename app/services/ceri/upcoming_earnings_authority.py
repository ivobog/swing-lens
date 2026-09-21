from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime

from app.models.ceri_tables import CeriEarningsActual, CeriSourceRecord
from app.services.ceri.config import CeriConfig
from app.services.ceri.pit_eligibility import (
    source_record_historical_eligibility_at,
    source_record_known_at,
)


@dataclass(frozen=True)
class UpcomingEarningsCandidate:
    earnings_actual_id: int
    source_record_id: int
    provider: str
    provider_record_id: str
    content_hash: str
    supersedes_id: int | None
    earnings_session: date
    external_effective_at: datetime | None
    published_at: datetime | None
    observed_at: datetime | None
    source_timestamp: datetime | None
    possession_at: datetime
    historical_eligibility_at: datetime

    def evidence(self, cutoff_at: datetime) -> dict[str, object]:
        payload = asdict(self)
        payload.update(
            {
                "earnings_session": self.earnings_session.isoformat(),
                "external_effective_at": _iso(self.external_effective_at),
                "published_at": _iso(self.published_at),
                "observed_at": _iso(self.observed_at),
                "source_timestamp": _iso(self.source_timestamp),
                "possession_at": self.possession_at.isoformat(),
                "historical_eligibility_at": self.historical_eligibility_at.isoformat(),
                "calculation_cutoff_at": cutoff_at.isoformat(),
                "temporally_eligible": self.historical_eligibility_at <= cutoff_at,
            }
        )
        return payload


@dataclass(frozen=True)
class UpcomingEarningsSelection:
    selected: UpcomingEarningsCandidate | None
    candidates: tuple[UpcomingEarningsCandidate, ...]
    policy: str
    reason: str
    cutoff_at: datetime

    def evidence(self) -> dict[str, object]:
        return {
            "selection_policy": self.policy,
            "selection_reason": self.reason,
            "selected_source_record_id": (
                self.selected.source_record_id if self.selected is not None else None
            ),
            "selected_provider": self.selected.provider if self.selected is not None else None,
            "selected_exact_value": (
                self.selected.earnings_session.isoformat()
                if self.selected is not None
                else None
            ),
            "calculation_cutoff_at": self.cutoff_at.isoformat(),
            "candidates": [candidate.evidence(self.cutoff_at) for candidate in self.candidates],
        }


def select_upcoming_earnings(
    *,
    company_id: int,
    as_of_session: date,
    cutoff_at: datetime,
    earnings: list[CeriEarningsActual],
    source_records: dict[int, CeriSourceRecord],
    config: CeriConfig,
) -> UpcomingEarningsSelection:
    """Select one source-backed upcoming date under frozen provider policy.

    Only facts whose external and local-possession boundaries are both at or
    before the cutoff become candidates. Source rows and normalized earnings
    rows are immutable, so the returned evidence pins the exact value/version.
    """

    policy = "provider_priority_then_latest_possession_then_next_session_then_source_id"
    candidates: list[UpcomingEarningsCandidate] = []
    for row in earnings:
        source = source_records.get(int(row.source_record_id))
        eligibility_at = (
            source_record_historical_eligibility_at(source) if source is not None else None
        )
        possession_at = source_record_known_at(source) if source is not None else None
        if (
            row.company_id != company_id
            or (row.event_kind or "").upper() != "UPCOMING"
            or row.actual_value is not None
            or row.report_session is None
            or row.report_session < as_of_session
            or source is None
            or source.dataset != "earnings"
            or source.quarantine_reason is not None
            or eligibility_at is None
            or possession_at is None
            or eligibility_at > cutoff_at
        ):
            continue
        candidates.append(
            UpcomingEarningsCandidate(
                earnings_actual_id=int(row.id),
                source_record_id=int(source.id),
                provider=source.provider,
                provider_record_id=source.provider_record_id,
                content_hash=source.content_hash,
                supersedes_id=source.supersedes_id,
                earnings_session=row.report_session,
                external_effective_at=row.report_at,
                published_at=source.published_at,
                observed_at=source.observed_at,
                source_timestamp=source.source_timestamp,
                possession_at=possession_at,
                historical_eligibility_at=eligibility_at,
            )
        )
    candidates.sort(key=lambda item: item.source_record_id)
    if not candidates:
        return UpcomingEarningsSelection(
            selected=None,
            candidates=(),
            policy=policy,
            reason="NO_ELIGIBLE_SOURCE_BACKED_UPCOMING_EARNINGS",
            cutoff_at=cutoff_at,
        )
    provider_priority = {
        provider.value: index for index, provider in enumerate(config.providers.priority)
    }
    selected = min(
        candidates,
        key=lambda item: (
            provider_priority.get(item.provider, len(provider_priority)),
            -item.possession_at.timestamp(),
            item.earnings_session,
            -item.source_record_id,
        ),
    )
    reason = "SINGLE_ELIGIBLE_SOURCE" if len(candidates) == 1 else policy.upper()
    return UpcomingEarningsSelection(
        selected=selected,
        candidates=tuple(candidates),
        policy=policy,
        reason=reason,
        cutoff_at=cutoff_at,
    )


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
