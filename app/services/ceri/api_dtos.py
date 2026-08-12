from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class CeriOpportunityDto:
    score: float | None
    rated: bool
    coverage_pct: float | None
    minimum_required_coverage_pct: float | None
    unrated_reason: str | None
    reweighted: bool


@dataclass(frozen=True, slots=True)
class CeriRiskDto:
    score: float | None
    dominant_reason: str | None


@dataclass(frozen=True, slots=True)
class CeriConfidenceDto:
    label: str
    score: float | None
    coverage_pct: float | None
    gates: list[str]
    caps: list[str]


@dataclass(frozen=True, slots=True)
class CeriRevisionSummaryDto:
    value: float | None
    unit: str | None
    available: bool
    reason: str | None
    upward_count: int | None
    downward_count: int | None
    breadth: float | None


@dataclass(frozen=True, slots=True)
class CeriGuidanceSummaryDto:
    status: str
    selected: dict[str, Any] | None = None
    reason: str | None = None
    rejected: list[dict[str, Any]] | None = None


@dataclass(frozen=True, slots=True)
class CeriEventSummaryDto:
    status: str
    selected: dict[str, Any] | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CeriFreshnessDto:
    age_days: int | None
    status: str
    timestamp_quality: str | None = None


@dataclass(frozen=True, slots=True)
class CeriDashboardRowDto:
    id: int
    run_id: int | None
    source_run_id_text: str | None
    company_id: int
    ticker: str
    as_of_session: str
    cutoff_at: str
    opportunity_score: float | None
    opportunity: CeriOpportunityDto
    event_risk_score: float | None
    event_risk: CeriRiskDto
    data_confidence: str
    confidence: CeriConfidenceDto
    coverage_pct: float | None
    posture: str
    earnings_proximity_risk: float | None
    alignment_flags: dict[str, Any] | None
    alignment_context: dict[str, Any] | None
    evidence_lineage: dict[str, Any] | None
    top_positive_contributors: list[dict[str, Any]] | None
    top_negative_contributors: list[dict[str, Any]] | None
    ledgers: dict[str, Any]
    guidance: dict[str, Any]
    revision_evidence: dict[str, Any]
    next_event: dict[str, Any]
    freshness: dict[str, Any]
    reasons: list[str] | None
    warnings: list[str] | None
    config_version: str
    config_hash: str
    calculation_version: str
    evidence_hash: str
    hash_schema_version: str | None
    invalidated_by_purge: bool
    purge_invalidation: Any

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CeriTickerDetailDto:
    ticker: str
    latest: dict[str, Any]
    revision_features: list[dict[str, Any]]
    revision_history: list[dict[str, Any]]
    earnings_surprise_history: list[dict[str, Any]]
    guidance: dict[str, Any]
    source_freshness: dict[str, Any]
    events: list[dict[str, Any]]
    alerts: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
