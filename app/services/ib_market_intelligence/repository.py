from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
from app.observability.transaction_metrics import publish_after_commit
from app.services.ib_market_intelligence.config import IBMarketIntelligenceConfig
from app.services.ib_market_intelligence.dtos import (
    FeatureResult,
    HistoricalMetricBarDTO,
    LiveSnapshotDTO,
)
from app.services.ib_market_intelligence.evidence_hash import evidence_hash
from app.services.market_clock_service import CALENDAR_VERSION
from app.services.us_market_calendar import us_market_session


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


def project_historical_metric_rows_as_of(
    db: Session,
    rows: list[IBHistoricalMetricBar] | tuple[IBHistoricalMetricBar, ...],
    *,
    as_of: datetime,
) -> list[IBHistoricalMetricBar]:
    """Project mutable IB metric rows to the values SwingLens knew at ``as_of``.

    ``IBHistoricalMetricBar`` is a current projection.  The first revision after
    the boundary carries the last values that were eligible at the boundary.  If
    that proof is missing, the row is excluded instead of exposing current data.
    """

    materialized = list(rows)
    revised_ids = [
        int(row.id)
        for row in materialized
        if row.id is not None
        and _aware(row.revised_at) is not None
        and _aware(row.revised_at) > as_of
    ]
    revisions = (
        list(
            db.scalars(
                select(IBHistoricalMetricRevision)
                .where(IBHistoricalMetricRevision.metric_bar_id.in_(revised_ids))
                .order_by(
                    IBHistoricalMetricRevision.metric_bar_id,
                    IBHistoricalMetricRevision.revision_number,
                    IBHistoricalMetricRevision.id,
                )
            )
        )
        if revised_ids
        else []
    )
    by_bar: dict[int, list[IBHistoricalMetricRevision]] = {}
    for revision in revisions:
        by_bar.setdefault(int(revision.metric_bar_id), []).append(revision)

    projected: list[IBHistoricalMetricBar] = []
    for row in materialized:
        first_seen = _aware(row.first_seen_at)
        if first_seen is None or first_seen > as_of:
            continue
        revised_at = _aware(row.revised_at)
        if revised_at is None or revised_at <= as_of:
            projected.append(row)
            continue
        history = by_bar.get(int(row.id or 0), [])
        first_after = next(
            (
                item
                for item in history
                if _aware(item.observed_at) and _aware(item.observed_at) > as_of
            ),
            None,
        )
        if first_after is None:
            continue
        prior_revisions = [
            item
            for item in history
            if _aware(item.observed_at) is not None and _aware(item.observed_at) <= as_of
        ]
        values = dict(first_after.previous_values_json or {})
        projected.append(
            IBHistoricalMetricBar(
                id=row.id,
                intelligence_run_id=row.intelligence_run_id,
                ticker=row.ticker,
                ib_conid=row.ib_conid,
                session_date=row.session_date,
                effective_session=row.effective_session,
                timeframe=row.timeframe,
                metric_type=row.metric_type,
                open_value=_decimal(values.get("open_value")),
                high_value=_decimal(values.get("high_value")),
                low_value=_decimal(values.get("low_value")),
                close_value=_decimal(values.get("close_value")),
                source=row.source,
                source_semantic_type=row.source_semantic_type,
                requested_range=row.requested_range,
                availability_status=str(
                    values.get("availability_status") or row.availability_status
                ),
                capability_reason=row.capability_reason,
                data_hash=first_after.previous_data_hash,
                revision_count=max(0, int(first_after.revision_number) - 1),
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                revised_at=(
                    prior_revisions[-1].observed_at if prior_revisions else None
                ),
                warning_flags_json=list(values.get("warning_flags") or []),
            )
        )
    return projected


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
    calculation_cutoff_at: datetime | None = None,
) -> tuple[IBIntelligenceFeature, bool]:
    input_signature = evidence_hash(
        {
            "module": feature.module,
            "components": feature.components,
            "evidence_hashes": feature.evidence_hashes,
            "classification": feature.classification,
            "score": feature.score,
            "confidence": str(feature.confidence),
            "freshness_status": str(feature.freshness_status),
            "coverage_status": str(feature.coverage_status),
            "reasons": feature.reasons,
            "warnings": feature.warnings,
        }
    )
    existing = db.scalar(
        select(IBIntelligenceFeature)
        .where(IBIntelligenceFeature.ticker == ticker.upper())
        .where(IBIntelligenceFeature.as_of_session == as_of_session)
        .where(IBIntelligenceFeature.module == feature.module)
        .where(IBIntelligenceFeature.calculation_version == config.calculation_version)
        .where(IBIntelligenceFeature.config_hash == config.config_hash)
        .where(IBIntelligenceFeature.input_signature == input_signature)
    )
    if existing is not None:
        return existing, False
    row = IBIntelligenceFeature(
        intelligence_run_id=intelligence_run_id,
        ticker=ticker.upper(),
        ib_conid=ib_conid,
        as_of_session=as_of_session,
        calculated_at=calculated_at or datetime.now(UTC),
        calculation_cutoff_at=calculation_cutoff_at
        or (
            us_market_session(as_of_session).close_at + timedelta(minutes=15)
            if us_market_session(as_of_session) is not None
            else None
        ),
        calendar_version=CALENDAR_VERSION,
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
    if str(feature.freshness_status) == "STALE":
        publish_after_commit(
            db, "increment", "swinglens_ibmi_stale_features_total", module=feature.module
        )
    if str(feature.coverage_status) in {"FAILED", "UNAVAILABLE"}:
        publish_after_commit(
            db,
            "increment",
            "swinglens_ibmi_calculation_unavailable_total",
            module=feature.module,
        )
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


def _aware(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value
