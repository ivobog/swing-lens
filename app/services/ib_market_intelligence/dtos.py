from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.services.ib_market_intelligence.enums import AvailabilityStatus


@dataclass(frozen=True)
class HistoricalMetricBarDTO:
    ticker: str
    ib_conid: int | None
    session_date: date
    timeframe: str
    metric_type: str
    open_value: float | None
    high_value: float | None
    low_value: float | None
    close_value: float | None
    requested_range: str
    source_semantic_type: str
    availability_status: str = AvailabilityStatus.AVAILABLE
    warning_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class FeatureResult:
    module: str
    classification: str
    score: float | None
    confidence: str
    freshness_status: str
    coverage_status: str
    components: dict[str, Any]
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    evidence_hashes: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveSnapshotDTO:
    ticker: str
    ib_conid: int | None
    effective_session: date
    observed_at: datetime
    snapshot_type: str
    values: dict[str, Any]
    availability_status: str
    capability_reason: str | None = None
    warning_flags: tuple[str, ...] = ()
    source_request: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HistogramLevel:
    price: float
    activity_count: float


@dataclass(frozen=True)
class HistogramCapture:
    valid_levels: tuple[HistogramLevel, ...]
    raw_bins: tuple[dict[str, Any], ...]
    malformed_bin_count: int = 0


@dataclass(frozen=True)
class FlexExecutionDTO:
    external_execution_id: str | None
    trade_time: datetime
    symbol: str
    conid: int | None
    side: str
    quantity: Decimal
    price: Decimal
    exchange: str | None
    commission: Decimal
    fees: Decimal
    currency: str | None
    asset_class: str | None
    order_reference: str | None
    account_hash: str | None
    account_masked_label: str | None
    broker_realized_pnl: Decimal | None
    raw_record_hash: str
