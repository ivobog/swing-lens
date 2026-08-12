from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriRevisionFeature,
)
from app.models.tables import RawCompanyRow
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import HistoricalViewMode
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService


@dataclass(frozen=True)
class CeriFeatureRebuildRequest:
    company_ids: tuple[int, ...] | None = None
    ticker: str | None = None
    as_of_session: date | None = None
    from_session: date | None = None
    to_session: date | None = None
    run_id: int | None = None
    mode: str = "AS_KNOWN"


@dataclass(frozen=True)
class CeriFeatureRebuildResult:
    features: int = 0
    features_deduplicated: int = 0
    earnings_updated: int = 0
    processed_companies: int = 0
    warnings: int = 0
    failed: int = 0
    errors: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["feature_count"] = self.features
        result["warnings"] = self.warnings
        result["errors"] = list(self.errors)
        return result


class CeriFeatureRebuildService:
    """Rebuilds durable normalized CERI feature rows from canonical evidence."""

    def __init__(
        self,
        *,
        config: CeriConfig | None = None,
        revisions: CeriRevisionFeatureService | None = None,
        surprise: CeriSurpriseFeatureService | None = None,
        catalysts: CeriCatalystFeatureService | None = None,
        confidence: CeriConfidenceService | None = None,
        price_response: CeriPriceResponseService | None = None,
    ) -> None:
        self.config = config or load_ceri_config()
        self.revisions = revisions or CeriRevisionFeatureService(config=self.config)
        self.surprise = surprise or CeriSurpriseFeatureService(config=self.config)
        self.catalysts = catalysts or CeriCatalystFeatureService(config=self.config)
        self.confidence = confidence or CeriConfidenceService(config=self.config)
        self.price_response = price_response or CeriPriceResponseService(config=self.config)

    def rebuild(
        self, db: Session, request: CeriFeatureRebuildRequest, *, processing_run: Any | None = None
    ) -> CeriFeatureRebuildResult:
        companies = self._companies(db, request)
        cutoff = request.as_of_session or request.to_session or date.today()
        cutoff_at = datetime.combine(cutoff, time(23, 59, 59), tzinfo=UTC)
        try:
            mode = HistoricalViewMode(request.mode)
        except ValueError:
            mode = HistoricalViewMode.AS_KNOWN
        features = deduped = earnings_updated = warnings = failed = 0
        errors: list[dict[str, Any]] = []
        for company in companies:
            try:
                company_features: list[CeriRevisionFeature] = []
                for metric in (metric.value for metric in self.config.metrics.required):
                    for period_slot in self.config.metrics.period_types:
                        calculated = self.revisions.calculate_windows(
                            db,
                            company_id=company.id,
                            metric=metric,
                            period_slot=period_slot.value,
                            cutoff_at=cutoff_at,
                            mode=mode,
                        )
                        self._add_acceleration(calculated, calculated)
                        for feature in calculated:
                            if (
                                request.from_session
                                and feature.as_of_session < request.from_session
                            ):
                                continue
                            existing = self._existing_feature(db, feature)
                            if existing is not None:
                                company_features.append(existing)
                                _copy_revision_derived(existing, feature)
                                deduped += 1
                                continue
                            db.add(feature)
                            db.flush()
                            company_features.append(feature)
                            features += 1
                            warnings += len(feature.warnings_json or [])
                estimates = [
                    row for row in _load(db, CeriEstimateSnapshot) if row.company_id == company.id
                ]
                earnings = [
                    row
                    for row in _load(db, CeriEarningsActual)
                    if row.company_id == company.id
                    and (row.report_at is None or row.report_at <= cutoff_at)
                ]
                if earnings:
                    self.surprise.summarize(earnings, estimates)
                    for row in earnings:
                        if row.consensus_snapshot_id is not None:
                            earnings_updated += 1
                    self._upsert_derived(
                        db,
                        company_id=company.id,
                        family="earnings_surprise",
                        key="latest",
                        as_of_session=cutoff,
                        value={
                            "features": [
                                {
                                    "earnings_actual_id": item.earnings_actual_id,
                                    "consensus_snapshot_id": item.consensus_snapshot_id,
                                    "surprise_absolute": str(item.surprise_absolute)
                                    if item.surprise_absolute is not None
                                    else None,
                                    "surprise_pct": str(item.surprise_pct)
                                    if item.surprise_pct is not None
                                    else None,
                                    "direction": item.direction,
                                    "warnings": item.warnings,
                                }
                                for item in self.surprise.summarize(earnings, estimates).features
                            ]
                        },
                        source_ids=[
                            row.source_record_id
                            for row in earnings
                            if row.source_record_id is not None
                        ],
                    )
                guidance_rows = [
                    row
                    for row in _load(db, CeriGuidanceEvent)
                    if row.company_id == company.id
                    and (row.effective_session is None or row.effective_session <= cutoff)
                ]
                if request.from_session:
                    guidance_rows = [
                        row
                        for row in guidance_rows
                        if (
                            row.effective_session is None
                            or row.effective_session >= request.from_session
                        )
                    ]
                if guidance_rows:
                    latest_guidance = max(
                        guidance_rows,
                        key=lambda row: (row.effective_session or date.min, row.id or 0),
                    )
                    self._upsert_derived(
                        db,
                        company_id=company.id,
                        family="guidance",
                        key="latest",
                        as_of_session=cutoff,
                        value={
                            "guidance_id": latest_guidance.id,
                            "action": latest_guidance.action,
                            "confidence": latest_guidance.confidence,
                            "metric": latest_guidance.metric,
                            "period_type": latest_guidance.period_type,
                            "low_value": str(latest_guidance.low_value)
                            if latest_guidance.low_value is not None
                            else None,
                            "high_value": str(latest_guidance.high_value)
                            if latest_guidance.high_value is not None
                            else None,
                            "point_value": str(latest_guidance.point_value)
                            if latest_guidance.point_value is not None
                            else None,
                        },
                        source_ids=[row.source_record_id for row in guidance_rows],
                    )
                event_rows = _current_catalysts(db, company.id, cutoff)
                catalyst_values = []
                catalyst_source_ids: list[int] = []
                for event, revision in event_rows:
                    value = self.catalysts.calculate(
                        event=event, revision=revision, as_of_session=cutoff
                    )
                    catalyst_values.append(_json_safe(asdict(value)))
                    if revision.source_record_id is not None:
                        catalyst_source_ids.append(revision.source_record_id)
                if catalyst_values:
                    self._upsert_derived(
                        db,
                        company_id=company.id,
                        family="catalysts",
                        key="current",
                        as_of_session=cutoff,
                        value={"items": catalyst_values},
                        source_ids=catalyst_source_ids,
                    )
                confidence = self.confidence.calculate(
                    as_of_session=cutoff,
                    revision_features=company_features,
                )
                self._upsert_derived(
                    db,
                    company_id=company.id,
                    family="confidence",
                    key="score",
                    as_of_session=cutoff,
                    value=_json_safe(asdict(confidence)),
                    source_ids=[
                        source_id
                        for feature in company_features
                        for source_id in (feature.source_observation_ids_json or [])
                    ],
                )
                self._rebuild_price_response(db, company.id, company.ticker, cutoff)
                if processing_run is not None:
                    processing_run.checkpoint_json = {
                        "company_id": company.id,
                        "as_of_session": cutoff.isoformat(),
                    }
            except Exception as exc:
                failed += 1
                errors.append(
                    {"company_id": company.id, "error": str(exc).replace("\n", " ")[:500]}
                )
        return CeriFeatureRebuildResult(
            features, deduped, earnings_updated, len(companies), warnings, failed, tuple(errors)
        )

    def _upsert_derived(
        self,
        db: Session,
        *,
        company_id: int,
        family: str,
        key: str,
        as_of_session: date,
        value: dict[str, Any],
        source_ids: list[int],
    ) -> CeriDerivedFeature:
        existing = next(
            (
                row
                for row in _load(db, CeriDerivedFeature)
                if row.company_id == company_id
                and row.feature_family == family
                and row.feature_key == key
                and row.as_of_session == as_of_session
                and row.config_hash == self.config.config_hash
                and row.calculation_version == self.config.engine.calculation_version
            ),
            None,
        )
        evidence = {
            "company_id": company_id,
            "family": family,
            "key": key,
            "as_of_session": as_of_session.isoformat(),
            "value": value,
            "source_ids": sorted(set(source_ids)),
            "config_hash": self.config.config_hash,
            "calculation_version": self.config.engine.calculation_version,
        }
        if existing is None:
            existing = CeriDerivedFeature(
                company_id=company_id,
                feature_family=family,
                feature_key=key,
                as_of_session=as_of_session,
                config_version=self.config.engine.config_version,
                config_hash=self.config.config_hash,
                calculation_version=self.config.engine.calculation_version,
                evidence_hash=_stable_hash(evidence),
            )
            db.add(existing)
        existing.value_json = value
        existing.source_ids_json = sorted(set(source_ids))
        existing.evidence_hash = _stable_hash(evidence)
        db.flush()
        return existing

    def _rebuild_price_response(
        self, db: Session, company_id: int, ticker: str, cutoff: date
    ) -> None:
        event = _latest_price_event(db, company_id, cutoff)
        if event is None:
            return
        result = self.price_response.calculate(
            db,
            company_id=company_id,
            ticker=ticker,
            event_type=event[0],
            event_id=event[1],
            event_effective_at=event[2],
            event_effective_session=event[3],
        )
        self.price_response.persist(
            db,
            result=result,
            company_id=company_id,
            ticker=ticker,
            event_id=event[1],
            event_effective_at=event[2],
            event_effective_session=event[3],
        )

    def _companies(self, db: Session, request: CeriFeatureRebuildRequest) -> list[CeriCompany]:
        requested_ids = set(request.company_ids or ())
        companies = _load(db, CeriCompany)
        if request.run_id is not None:
            tickers = {
                row.ticker.upper()
                for row in _load(db, RawCompanyRow)
                if row.run_id == request.run_id
            }
            companies = [company for company in companies if company.ticker.upper() in tickers]
        if request.ticker:
            companies = [
                company for company in companies if company.ticker.upper() == request.ticker.upper()
            ]
        if requested_ids:
            companies = [company for company in companies if company.id in requested_ids]
        return sorted(companies, key=lambda company: (company.ticker.upper(), company.id or 0))

    def _existing_feature(
        self, db: Session, feature: CeriRevisionFeature
    ) -> CeriRevisionFeature | None:
        for existing in _load(db, CeriRevisionFeature):
            if (
                existing.company_id == feature.company_id
                and existing.metric == feature.metric
                and existing.period_key == feature.period_key
                and existing.as_of_session == feature.as_of_session
                and existing.window_days == feature.window_days
                and existing.config_hash == feature.config_hash
                and existing.calculation_version == feature.calculation_version
            ):
                return existing
        return None

    def _add_acceleration(
        self, calculated: list[CeriRevisionFeature], all_features: list[CeriRevisionFeature]
    ) -> None:
        if len(calculated) < 2:
            return
        recent = min(calculated, key=lambda feature: feature.window_days)
        longer = max(calculated, key=lambda feature: feature.window_days)
        if recent is not longer:
            # The feature may already be persisted; mutating it only changes a
            # derived value and its evidence hash, never the source evidence.
            self.revisions.with_acceleration(recent, longer)


def _load(db: Session, model: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(select(model))
    return list(result.all() if hasattr(result, "all") else result)


def _copy_revision_derived(target: CeriRevisionFeature, source: CeriRevisionFeature) -> None:
    target.actual_elapsed_days = source.actual_elapsed_days
    target.absolute_change = source.absolute_change
    target.pct_change = source.pct_change
    target.pct_change_unit = source.pct_change_unit
    target.period_slot = source.period_slot
    target.upward_count = source.upward_count
    target.downward_count = source.downward_count
    target.net_breadth = source.net_breadth
    target.dispersion = source.dispersion
    target.acceleration = source.acceleration
    target.acceleration_unit = source.acceleration_unit
    target.baseline_origin = source.baseline_origin
    target.revision_confidence_score = source.revision_confidence_score
    target.revision_confidence_label = source.revision_confidence_label
    target.warnings_json = source.warnings_json
    target.evidence_hash = source.evidence_hash


def _current_catalysts(
    db: Session, company_id: int, cutoff: date
) -> list[tuple[CeriCatalystEvent, CeriCatalystEventRevision]]:
    events = {
        event.id: event for event in _load(db, CeriCatalystEvent) if event.company_id == company_id
    }
    revisions = [
        revision
        for revision in _load(db, CeriCatalystEventRevision)
        if revision.is_current
        and revision.catalyst_event_id in events
        and (revision.effective_session is None or revision.effective_session <= cutoff)
    ]
    return [(events[revision.catalyst_event_id], revision) for revision in revisions]


def _latest_price_event(
    db: Session, company_id: int, cutoff: date
) -> tuple[str, int | None, datetime | None, date | None] | None:
    candidates: list[tuple[str, int | None, datetime | None, date | None]] = []
    earnings = [
        row
        for row in _load(db, CeriEarningsActual)
        if row.company_id == company_id
        and row.report_session is not None
        and row.report_session <= cutoff
    ]
    if earnings:
        row = max(earnings, key=lambda item: (item.report_session, item.id or 0))
        candidates.append(("EARNINGS", row.id, row.report_at, row.report_session))
    guidance = [
        row
        for row in _load(db, CeriGuidanceEvent)
        if row.company_id == company_id
        and (row.effective_session is None or row.effective_session <= cutoff)
    ]
    if guidance:
        row = max(guidance, key=lambda item: (item.effective_session or date.min, item.id or 0))
        candidates.append(("GUIDANCE", row.id, row.effective_at, row.effective_session))
    for _event, revision in _current_catalysts(db, company_id, cutoff):
        candidates.append(
            (
                "CATALYST",
                revision.id,
                revision.announced_at,
                revision.effective_session or revision.expected_date,
            )
        )
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item[3] or date.min,
            item[2] or datetime.min.replace(tzinfo=UTC),
            item[1] or 0,
        ),
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return value.value
    return value


def _stable_hash(value: Any) -> str:
    import hashlib
    import json

    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()
