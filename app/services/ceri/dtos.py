from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services.ceri.enums import (
    CatalystCategory,
    CatalystDirection,
    CatalystStatus,
    CeriChangeType,
    CeriConfidenceLabel,
    CeriDataset,
    CeriMetric,
    CeriPeriodType,
    CeriProviderCapability,
    DateConfidence,
    GuidanceAction,
    HistoricalViewMode,
)


class CeriFilterError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    capabilities: frozenset[CeriProviderCapability]
    datasets: frozenset[CeriDataset]

    def supports(self, capability: CeriProviderCapability) -> bool:
        return capability in self.capabilities


@dataclass(frozen=True)
class ProviderHealth:
    provider: str
    healthy: bool
    checked_at: datetime | None = None
    quota_status: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class ProviderCompany:
    provider: str
    provider_company_id: str | None
    ticker: str
    exchange: str | None = None
    cik: str | None = None
    name: str | None = None
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompanyQuery:
    ticker: str | None = None
    exchange: str | None = None
    provider_company_id: str | None = None
    cik: str | None = None

    def __post_init__(self) -> None:
        if not any((self.ticker, self.provider_company_id, self.cik)):
            raise ValueError("CompanyQuery requires ticker, provider_company_id, or cik")


@dataclass(frozen=True)
class EstimateRequest:
    company_id: int | None
    ticker: str
    metrics: tuple[CeriMetric, ...]
    period_types: tuple[CeriPeriodType, ...]
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class EarningsRequest:
    company_id: int | None
    ticker: str
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class GuidanceRequest:
    company_id: int | None
    ticker: str
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class CatalystRequest:
    company_id: int | None
    ticker: str
    categories: tuple[CatalystCategory, ...] = ()
    start: date | None = None
    end: date | None = None


@dataclass(frozen=True)
class RawProviderRecord:
    provider: str
    dataset: CeriDataset
    provider_record_id: str
    payload: dict[str, Any]
    published_at: datetime | None
    observed_at: datetime | None
    source_url: str | None = None
    export_policy: str = "exportable"
    retrieved_at: datetime | None = None


@dataclass(frozen=True)
class NormalizedEstimateRecord:
    source_record_id: int
    company_id: int
    metric: CeriMetric
    period_type: CeriPeriodType
    fiscal_period_end: date
    consensus: Decimal | None
    high: Decimal | None
    low: Decimal | None
    analyst_count: int | None
    currency: str
    scale: Decimal
    effective_at: datetime | None
    effective_session: date | None
    quality_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class NormalizedGuidanceRecord:
    source_record_id: int
    company_id: int
    action: GuidanceAction
    metric: CeriMetric
    period_type: CeriPeriodType
    low: Decimal | None
    high: Decimal | None
    confidence: CeriConfidenceLabel


@dataclass(frozen=True)
class NormalizedCatalystRecord:
    source_record_id: int
    company_id: int
    category: CatalystCategory
    subtype: str
    subject_key: str
    status: CatalystStatus
    direction: CatalystDirection
    materiality: float | None
    confidence: CeriConfidenceLabel
    date_confidence: DateConfidence
    announced_at: datetime | None = None
    expected_date: date | None = None
    effective_session: date | None = None
    canonical_text: str | None = None
    conflict_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class RevisionFeature:
    company_id: int
    metric: CeriMetric
    period_key: str
    as_of_session: date
    window_days: int
    baseline_snapshot_id: int | None
    current_snapshot_id: int | None
    absolute_change: Decimal | None
    pct_change: Decimal | None
    upward_count: int | None
    downward_count: int | None
    net_breadth: Decimal | None
    dispersion: Decimal | None
    acceleration: Decimal | None
    confidence: CeriConfidenceLabel
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScoreComponent:
    name: str
    value: float | None
    weight: float
    contribution: float | None
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CeriScoreSnapshotPayload:
    ticker: str
    as_of_session: date
    opportunity_score: float | None
    event_risk_score: float | None
    confidence: CeriConfidenceLabel
    coverage_pct: float
    posture: str
    components: tuple[ScoreComponent, ...]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    config_hash: str
    evidence_hash: str


@dataclass(frozen=True)
class CeriFilters:
    opportunity_min: float | None = None
    risk_max: float | None = None
    confidence: CeriConfidenceLabel | None = None
    eps_revision_30d_min: float | None = None
    breadth_min: float | None = None
    guidance: GuidanceAction | None = None
    event_category: CatalystCategory | None = None
    event_before: date | None = None
    event_after: date | None = None
    changed_since: date | None = None
    mode: HistoricalViewMode = HistoricalViewMode.AS_KNOWN
    page: int = 1
    page_size: int = 50
    sort: str = "opportunity_score"
    allowed_sorts: tuple[str, ...] = field(
        default=(
            "opportunity_score",
            "event_risk_score",
            "confidence",
            "eps_revision_30d",
            "ticker",
        ),
        repr=False,
    )

    def __post_init__(self) -> None:
        _optional_score(self.opportunity_min, "opportunity_min")
        _optional_score(self.risk_max, "risk_max")
        if self.breadth_min is not None and not -1 <= self.breadth_min <= 1:
            raise CeriFilterError("breadth_min must be between -1 and 1")
        if self.page <= 0:
            raise CeriFilterError("page must be positive")
        if self.page_size <= 0 or self.page_size > 500:
            raise CeriFilterError("page_size must be between 1 and 500")
        if self.event_before and self.event_after and self.event_after > self.event_before:
            raise CeriFilterError("event_after must be on or before event_before")
        if self.sort not in self.allowed_sorts:
            raise CeriFilterError("sort is not supported")

    def as_query_params(self) -> dict[str, Any]:
        return {
            key: (value.value if hasattr(value, "value") else value)
            for key, value in asdict(self).items()
            if value is not None and key != "allowed_sorts"
        }


@dataclass(frozen=True)
class ExportFieldPolicy:
    field: str
    policy: str
    reason: str | None = None


@dataclass(frozen=True)
class CeriExportRow:
    ticker: str
    as_of_session: date
    cutoff_at: datetime
    opportunity_score: float | None
    event_risk_score: float | None
    confidence: CeriConfidenceLabel
    warnings: tuple[str, ...]
    evidence_references: tuple[str, ...]


@dataclass(frozen=True)
class ChangeEventPayload:
    ticker: str
    change_type: CeriChangeType
    severity: str
    created_at: datetime
    delta: dict[str, Any]
    dedup_key: str


def _optional_score(value: float | None, field_name: str) -> None:
    if value is not None and not 0 <= value <= 10:
        raise CeriFilterError(f"{field_name} must be between 0 and 10")
