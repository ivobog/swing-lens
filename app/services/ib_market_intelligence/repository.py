from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import (
    IBHistoricalMetricBar,
    IBHistoricalMetricRevision,
    IBIntelligenceFeature,
    IBMarketIntelligenceSnapshot,
)
from app.services.ib_market_intelligence.config import IBMarketIntelligenceConfig
from app.services.ib_market_intelligence.dtos import (
    FeatureResult,
    HistoricalMetricBarDTO,
    LiveSnapshotDTO,
)
from app.services.ib_market_intelligence.evidence_hash import evidence_hash


def persist_historical_metric_bar(
    db: Session,
    dto: HistoricalMetricBarDTO,
    *,
    intelligence_run_id: int | None = None,
    observed_at: datetime | None = None,
) -> tuple[IBHistoricalMetricBar, str]:
    observed_at = observed_at or datetime.now(UTC)
    values = {
        "open_value": dto.open_value,
        "high_value": dto.high_value,
        "low_value": dto.low_value,
        "close_value": dto.close_value,
        "availability_status": str(dto.availability_status),
        "warning_flags": list(dto.warning_flags),
    }
    digest = evidence_hash(
        {
            "ticker": dto.ticker,
            "session_date": dto.session_date,
            "timeframe": dto.timeframe,
            "metric_type": dto.metric_type,
            **values,
        }
    )
    existing = db.scalar(
        select(IBHistoricalMetricBar)
        .where(IBHistoricalMetricBar.ticker == dto.ticker)
        .where(IBHistoricalMetricBar.session_date == dto.session_date)
        .where(IBHistoricalMetricBar.timeframe == dto.timeframe)
        .where(IBHistoricalMetricBar.metric_type == dto.metric_type)
    )
    if existing is None:
        row = IBHistoricalMetricBar(
            intelligence_run_id=intelligence_run_id,
            ticker=dto.ticker,
            ib_conid=dto.ib_conid,
            session_date=dto.session_date,
            effective_session=dto.session_date,
            timeframe=dto.timeframe,
            metric_type=dto.metric_type,
            open_value=_decimal(dto.open_value),
            high_value=_decimal(dto.high_value),
            low_value=_decimal(dto.low_value),
            close_value=_decimal(dto.close_value),
            source="IBKR",
            source_semantic_type=dto.source_semantic_type,
            requested_range=dto.requested_range,
            availability_status=str(dto.availability_status),
            capability_reason=None,
            data_hash=digest,
            first_seen_at=observed_at,
            last_seen_at=observed_at,
            warning_flags_json=list(dto.warning_flags),
        )
        db.add(row)
        db.flush()
        return row, "INSERTED"
    existing.last_seen_at = observed_at
    existing.intelligence_run_id = intelligence_run_id or existing.intelligence_run_id
    if existing.data_hash == digest:
        db.flush()
        return existing, "UNCHANGED"
    previous = _bar_values(existing)
    revision_number = existing.revision_count + 1
    db.add(
        IBHistoricalMetricRevision(
            metric_bar_id=existing.id,
            revision_number=revision_number,
            previous_data_hash=existing.data_hash,
            new_data_hash=digest,
            previous_values_json=previous,
            new_values_json=values,
            observed_at=observed_at,
        )
    )
    existing.ib_conid = dto.ib_conid
    existing.open_value = _decimal(dto.open_value)
    existing.high_value = _decimal(dto.high_value)
    existing.low_value = _decimal(dto.low_value)
    existing.close_value = _decimal(dto.close_value)
    existing.availability_status = str(dto.availability_status)
    existing.warning_flags_json = list(dto.warning_flags)
    existing.data_hash = digest
    existing.revision_count = revision_number
    existing.revised_at = observed_at
    db.flush()
    return existing, "REVISED"


def persist_live_snapshot(
    db: Session, dto: LiveSnapshotDTO, *, intelligence_run_id: int | None = None
) -> tuple[IBMarketIntelligenceSnapshot, bool]:
    digest = evidence_hash(
        {
            "ticker": dto.ticker,
            "ib_conid": dto.ib_conid,
            "effective_session": dto.effective_session,
            "observed_at": dto.observed_at,
            "snapshot_type": dto.snapshot_type,
            "values": dto.values,
            "availability_status": str(dto.availability_status),
            "source_request": dto.source_request,
        }
    )
    existing = db.scalar(
        select(IBMarketIntelligenceSnapshot).where(
            IBMarketIntelligenceSnapshot.evidence_hash == digest
        )
    )
    if existing is not None:
        return existing, False
    row = IBMarketIntelligenceSnapshot(
        intelligence_run_id=intelligence_run_id,
        ticker=dto.ticker,
        ib_conid=dto.ib_conid,
        effective_session=dto.effective_session,
        observed_at=dto.observed_at,
        snapshot_type=dto.snapshot_type,
        values_json=dto.values,
        availability_status=str(dto.availability_status),
        capability_reason=dto.capability_reason,
        evidence_hash=digest,
        source_request_json=dto.source_request,
        warning_flags_json=list(dto.warning_flags),
    )
    db.add(row)
    db.flush()
    return row, True


def persist_feature(
    db: Session,
    *,
    ticker: str,
    ib_conid: int | None,
    as_of_session: date,
    feature: FeatureResult,
    config: IBMarketIntelligenceConfig,
    intelligence_run_id: int | None = None,
    calculated_at: datetime | None = None,
) -> tuple[IBIntelligenceFeature, bool]:
    input_signature = evidence_hash(
        {
            "module": feature.module,
            "components": feature.components,
            "evidence_hashes": feature.evidence_hashes,
            "classification": feature.classification,
        }
    )
    existing = db.scalar(
        select(IBIntelligenceFeature)
        .where(IBIntelligenceFeature.ticker == ticker.upper())
        .where(IBIntelligenceFeature.as_of_session == as_of_session)
        .where(IBIntelligenceFeature.module == feature.module)
        .where(IBIntelligenceFeature.calculation_version == config.calculation_version)
        .where(IBIntelligenceFeature.config_hash == config.config_hash)
    )
    if existing is not None:
        if existing.input_signature != input_signature:
            raise ValueError(
                "Immutable feature identity already exists with different input; bump calculation "
                "version or configuration before rebuilding."
            )
        return existing, False
    row = IBIntelligenceFeature(
        intelligence_run_id=intelligence_run_id,
        ticker=ticker.upper(),
        ib_conid=ib_conid,
        as_of_session=as_of_session,
        calculated_at=calculated_at or datetime.now(UTC),
        module=feature.module,
        classification=feature.classification,
        score=_decimal(feature.score),
        confidence=str(feature.confidence),
        freshness_status=str(feature.freshness_status),
        coverage_status=str(feature.coverage_status),
        components_json=feature.components,
        reasons_json=list(feature.reasons),
        warnings_json=list(feature.warnings),
        source_evidence_hashes_json=list(feature.evidence_hashes),
        source_version=config.source_version,
        calculation_version=config.calculation_version,
        config_hash=config.config_hash,
        input_signature=input_signature,
    )
    db.add(row)
    db.flush()
    return row, True


def _decimal(value: Any) -> Decimal | None:
    return Decimal(str(value)) if value is not None else None


def _bar_values(row: IBHistoricalMetricBar) -> dict[str, Any]:
    return {
        "open_value": str(row.open_value) if row.open_value is not None else None,
        "high_value": str(row.high_value) if row.high_value is not None else None,
        "low_value": str(row.low_value) if row.low_value is not None else None,
        "close_value": str(row.close_value) if row.close_value is not None else None,
        "availability_status": row.availability_status,
        "warning_flags": row.warning_flags_json,
    }
