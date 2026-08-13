from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriChangeEvent,
    CeriCompany,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import RawCompanyRow
from app.services.ceri.alert_service import CeriAlertService
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService
from app.services.ceri.change_detection_service import CeriChangeDetectionService
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.enums import CeriDataset
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ceri.feature_flags import ceri_flags
from app.services.ceri.guidance_normalizer import guidance_eligibility_reason
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService
from app.services.ib_market_intelligence.calculations import options_event_premium_score
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.settings import get_settings


@dataclass(frozen=True)
class CeriRunCaptureResult:
    score_snapshots: int = 0
    change_events: int = 0
    alerts: int = 0
    unrated: int = 0
    quarantined: int = 0
    conflicted: int = 0
    stale: int = 0
    failed: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "score_snapshots": self.score_snapshots,
            "change_events": self.change_events,
            "alerts": self.alerts,
            "unrated": self.unrated,
            "quarantined": self.quarantined,
            "conflicted": self.conflicted,
            "stale": self.stale,
            "failed": self.failed,
            "skipped": self.skipped,
        }


@dataclass(frozen=True)
class _VolatilityRiskFeature:
    id: int
    components: dict[str, Any]


@dataclass(frozen=True)
class _ShortPressureContextFeature:
    id: int
    classification: str


class CeriRunCaptureService:
    def __init__(
        self,
        *,
        snapshot_service: CeriSnapshotService | None = None,
        change_detection: CeriChangeDetectionService | None = None,
        alert_service: CeriAlertService | None = None,
    ) -> None:
        self.snapshot_service = snapshot_service or CeriSnapshotService()
        self.change_detection = change_detection or CeriChangeDetectionService()
        self.alert_service = alert_service or CeriAlertService(alerts_enabled=ceri_flags().alerts)
        self.opportunity = CeriOpportunityScoreService(config=self.snapshot_service.config)
        self.risk = CeriEventRiskService(config=self.snapshot_service.config)
        self.confidence = CeriConfidenceService(config=self.snapshot_service.config)
        self.surprise = CeriSurpriseFeatureService(config=self.snapshot_service.config)
        self.catalysts = CeriCatalystFeatureService(config=self.snapshot_service.config)
        self.price_response = CeriPriceResponseService(config=self.snapshot_service.config)

    def capture_run(
        self,
        db: Session,
        run_id: int,
        *,
        tickers: set[str] | None = None,
        force: bool = False,
        cutoff_at: datetime | None = None,
    ) -> CeriRunCaptureResult:
        if not force and not ceri_flags().run_capture:
            return CeriRunCaptureResult(skipped=1)
        rows = _raw_rows_for_run(db, run_id)
        if tickers is not None:
            requested = {ticker.upper() for ticker in tickers}
            rows = [row for row in rows if str(row.ticker).upper() in requested]
        if not rows:
            return CeriRunCaptureResult(skipped=1)
        cutoff_at = cutoff_at or _utcnow()
        companies_by_ticker = _companies_for_tickers(
            db, {str(row.ticker).upper() for row in rows}
        )
        company_ids = {company.id for company in companies_by_ticker.values()}
        features_by_company = _revision_features_for_companies(
            db, company_ids, cutoff_at.date()
        )
        features_by_company = {
            company_id: [
                feature
                for feature in features
                if feature.calculation_version
                == self.snapshot_service.config.engine.calculation_version
                and feature.config_hash == self.snapshot_service.config.config_hash
            ]
            for company_id, features in features_by_company.items()
        }
        existing_snapshot_company_ids = _existing_snapshot_company_ids(
            db,
            run_id,
            company_ids,
            self.snapshot_service.config,
        )
        counts = {
            "score_snapshots": 0,
            "change_events": 0,
            "alerts": 0,
            "unrated": 0,
            "quarantined": _quarantined_count(db),
            "conflicted": 0,
            "stale": 0,
            "failed": 0,
            "skipped": 0,
        }
        for row in rows:
            try:
                company = companies_by_ticker.get(str(row.ticker).upper())
                if company is None:
                    counts["unrated"] += 1
                    continue
                if company.id in existing_snapshot_company_ids:
                    counts["skipped"] += 1
                    continue
                features = features_by_company.get(company.id, [])
                if not features:
                    counts["unrated"] += 1
                    continue
                catalyst_features = _catalyst_features_for_company(
                    db,
                    company.id,
                    cutoff_at.date(),
                    self.catalysts,
                )
                company_conflicted = sum(
                    _is_conflict_warning(feature.warnings_json) for feature in features
                )
                company_stale = sum(
                    "estimate_data_stale" in (feature.warnings_json or []) for feature in features
                )
                counts["conflicted"] += company_conflicted
                counts["stale"] += company_stale
                earnings = _scalars(
                    db,
                    select(CeriEarningsActual).where(CeriEarningsActual.company_id == company.id),
                )
                estimates = _scalars(
                    db,
                    select(CeriEstimateSnapshot).where(
                        CeriEstimateSnapshot.company_id == company.id
                    ),
                )
                surprise_summary = self.surprise.summarize(earnings, estimates)
                price_result, price_feature = _price_response_for_company(
                    db,
                    company_id=company.id,
                    ticker=row.ticker,
                    as_of_session=cutoff_at.date(),
                    service=self.price_response,
                )
                confidence = self.confidence.calculate(
                    as_of_session=cutoff_at.date(),
                    revision_features=features,
                    dataset_freshness_days=_confidence_freshness_days(
                        db,
                        company_id=company.id,
                        cutoff_at=cutoff_at,
                        revision_features=features,
                        earnings=earnings,
                    ),
                    conflict_penalty=float(company_conflicted),
                )
                opportunity = self.opportunity.calculate(
                    revision_features=features,
                    surprise_summary=surprise_summary,
                    guidance_events=_guidance_for_company(db, company.id, cutoff_at.date()),
                    catalyst_features=catalyst_features,
                    price_response_quality=(
                        price_result.quality if price_result is not None else None
                    ),
                    price_response_parent_event_id=(
                        price_feature.event_id if price_feature is not None else None
                    ),
                    conflict_penalty=min(3.0, float(company_conflicted)),
                    as_of_session=cutoff_at.date(),
                )
                volatility_feature = _point_in_time_volatility_feature(db, row.ticker, cutoff_at)
                short_pressure_feature = _point_in_time_short_pressure_feature(
                    db, row.ticker, cutoff_at
                )
                volatility_config = load_ib_market_intelligence_config().section("volatility")
                volatility_risk = (
                    options_event_premium_score(
                        volatility_feature,
                        maximum=float(volatility_config.get("ceri_risk_max_contribution", 1.5)),
                    )
                    if volatility_feature is not None
                    else 0.0
                )
                risk = self.risk.calculate(
                    as_of_session=cutoff_at.date(),
                    next_earnings_session=row.upcoming_earnings_date,
                    catalyst_features=catalyst_features,
                    stale=bool(company_stale),
                    conflict_penalty=min(3.0, float(company_conflicted)),
                    options_event_premium_score=volatility_risk,
                    short_pressure_classification=(
                        short_pressure_feature.classification
                        if short_pressure_feature is not None
                        else None
                    ),
                )
                guidance_rows = _guidance_for_company(db, company.id, cutoff_at.date())
                catalyst_lineage = _catalyst_lineage(db, company.id, cutoff_at.date())
                evidence_lineage = {
                    "revision_feature_ids": [feature.id for feature in features if feature.id],
                    "revision_pairs": [
                        {
                            "feature_id": feature.id,
                            "metric": feature.metric,
                            "period_slot": feature.period_slot,
                            "window_days": feature.window_days,
                            "current_snapshot_id": feature.current_snapshot_id,
                            "baseline_snapshot_id": feature.baseline_snapshot_id,
                            "baseline_origin": feature.baseline_origin,
                            "available": feature.pct_change is not None,
                            "unavailable_reason": feature.unavailable_reason,
                        }
                        for feature in features
                    ],
                    "revision_source_ids": _source_ids(features),
                    "earnings_ids": [item.id for item in earnings if item.id],
                    "earnings_source_ids": [item.source_record_id for item in earnings],
                    "guidance_ids": [item.id for item in guidance_rows if item.id],
                    "guidance_selected_ids": sorted(
                        evidence_id
                        for component in opportunity.components
                        if component.name == "guidance"
                        for evidence_id in component.evidence_ids
                    ),
                    "guidance_rejected": [
                        {"id": item.id, "reason": guidance_eligibility_reason(item)}
                        for item in guidance_rows
                        if item.accepted_for_scoring is not True
                    ],
                    "guidance_source_ids": [item.source_record_id for item in guidance_rows],
                    "catalyst_event_ids": catalyst_lineage["event_ids"],
                    "catalyst_revision_ids": catalyst_lineage["revision_ids"],
                    "catalyst_selected_event_ids": list(risk.selected_event_ids),
                    "catalyst_rejected_event_ids": list(risk.rejected_event_ids),
                    "catalyst_rejected": list(risk.rejected_events),
                    "catalyst_source_ids": catalyst_lineage["source_ids"],
                    "price_response_feature_ids": [price_feature.id]
                    if price_feature is not None and price_feature.id
                    else [],
                    "price_bar_ids": list(price_result.price_bar_ids)
                    if price_result is not None
                    else [],
                    "ib_volatility_feature_ids": [volatility_feature.id]
                    if volatility_feature is not None
                    else [],
                    "ib_short_pressure_feature_ids": [short_pressure_feature.id]
                    if short_pressure_feature is not None
                    else [],
                    "warnings": sorted(
                        set(
                            warning
                            for feature in features
                            for warning in (feature.warnings_json or [])
                        )
                    ),
                }
                source_ids = sorted(
                    set(
                        _source_ids(features)
                        + [item.source_record_id for item in earnings]
                        + [item.source_record_id for item in guidance_rows]
                        + catalyst_lineage["source_ids"]
                    )
                )
                snapshot = self.snapshot_service.build_snapshot(
                    run_id=run_id,
                    source_run_id_text=str(run_id),
                    company_id=company.id,
                    ticker=row.ticker,
                    as_of_session=cutoff_at.date(),
                    cutoff_at=cutoff_at,
                    opportunity=opportunity,
                    event_risk=risk,
                    confidence=confidence,
                    source_ids=source_ids,
                    alignment_inputs={
                        "fundamentals": bool(row.raw_json.get("fundamental_score")),
                        "technicals": bool(row.raw_json.get("technical_score")),
                        "sector": bool(row.sector),
                        "regime": bool(row.raw_json.get("market_regime")),
                        "lifecycle": bool(row.raw_json.get("lifecycle_state")),
                    },
                    alignment_context=_alignment_context(db, row, run_id),
                    evidence_lineage=evidence_lineage,
                )
                self.snapshot_service.persist_snapshot(db, snapshot)
                counts["score_snapshots"] += 1
                if not getattr(opportunity, "rated", opportunity.score is not None):
                    counts["unrated"] += 1
                prior = _prior_snapshot(db, company.id, snapshot)
                changes = self.change_detection.detect_score_changes(
                    db,
                    current=snapshot,
                    prior=prior,
                    scope=f"run:{run_id}",
                )
                counts["change_events"] += changes.changes
                if changes.changes:
                    new_changes = _latest_changes(db, company.id, changes.changes)
                    alerts = self.alert_service.rebuild_alerts(
                        db,
                        changes=new_changes,
                        ticker_by_company={company.id: row.ticker},
                    )
                    counts["alerts"] += alerts.alerts
            except Exception:
                counts["failed"] += 1
        return CeriRunCaptureResult(**counts)


def _raw_rows_for_run(db: Session, run_id: int) -> list[RawCompanyRow]:
    return _scalars(
        db,
        select(RawCompanyRow).where(RawCompanyRow.run_id == run_id),
    )


def _company_for_ticker(db: Session, ticker: str) -> CeriCompany | None:
    return _maybe_scalar(
        db,
        select(CeriCompany).where(CeriCompany.ticker == ticker.upper()),
    )


def _companies_for_tickers(
    db: Session, tickers: set[str]
) -> dict[str, CeriCompany]:
    if not tickers:
        return {}
    companies = _scalars(
        db,
        select(CeriCompany).where(CeriCompany.ticker.in_(sorted(tickers))),
    )
    return {company.ticker.upper(): company for company in companies}


def _revision_features(
    db: Session,
    company_id: int,
    as_of_session,
) -> list[CeriRevisionFeature]:
    return _scalars(
        db,
        select(CeriRevisionFeature)
        .where(CeriRevisionFeature.company_id == company_id)
        .where(CeriRevisionFeature.as_of_session == as_of_session),
    )


def _revision_features_for_companies(
    db: Session,
    company_ids: set[int],
    as_of_session,
) -> dict[int, list[CeriRevisionFeature]]:
    if not company_ids:
        return {}
    features = _scalars(
        db,
        select(CeriRevisionFeature)
        .where(CeriRevisionFeature.company_id.in_(sorted(company_ids)))
        .where(CeriRevisionFeature.as_of_session == as_of_session),
    )
    grouped: dict[int, list[CeriRevisionFeature]] = {}
    for feature in features:
        grouped.setdefault(feature.company_id, []).append(feature)
    return grouped


def _guidance_for_company(
    db: Session,
    company_id: int,
    as_of_session,
) -> list[CeriGuidanceEvent]:
    return _scalars(
        db,
        select(CeriGuidanceEvent)
        .where(CeriGuidanceEvent.company_id == company_id)
        .where(CeriGuidanceEvent.effective_session <= as_of_session),
    )


def _catalyst_features_for_company(
    db: Session,
    company_id: int,
    as_of_session,
    service: CeriCatalystFeatureService,
):
    events = [
        event
        for event in _scalars(
            db,
            select(CeriCatalystEvent).where(CeriCatalystEvent.company_id == company_id),
        )
        if event.company_id == company_id
    ]
    event_by_id = {event.id: event for event in events}
    revisions = _scalars(
        db,
        select(CeriCatalystEventRevision)
        .join(
            CeriCatalystEvent,
            CeriCatalystEvent.id == CeriCatalystEventRevision.catalyst_event_id,
        )
        .where(CeriCatalystEvent.company_id == company_id)
        .where(CeriCatalystEventRevision.is_current.is_(True)),
    )
    return [
        service.calculate(
            event=event_by_id[revision.catalyst_event_id],
            revision=revision,
            as_of_session=as_of_session,
        )
        for revision in revisions
        if revision.catalyst_event_id in event_by_id
        and _revision_is_known_by(revision, as_of_session)
    ]


def _catalyst_lineage(db: Session, company_id: int, as_of_session) -> dict[str, list[int]]:
    events = [
        event
        for event in _scalars(
            db,
            select(CeriCatalystEvent).where(CeriCatalystEvent.company_id == company_id),
        )
    ]
    event_ids = {event.id for event in events if event.id is not None}
    revisions = (
        [
            revision
            for revision in _scalars(
                db,
                select(CeriCatalystEventRevision).where(
                    CeriCatalystEventRevision.catalyst_event_id.in_(event_ids),
                    CeriCatalystEventRevision.is_current.is_(True),
                ),
            )
            if _revision_is_known_by(revision, as_of_session)
        ]
        if event_ids
        else []
    )
    return {
        "event_ids": sorted(event_ids),
        "revision_ids": sorted(revision.id for revision in revisions if revision.id),
        "source_ids": sorted(
            revision.source_record_id for revision in revisions if revision.source_record_id
        ),
    }


def _price_response_for_company(
    db: Session,
    *,
    company_id: int,
    ticker: str,
    as_of_session,
    service: CeriPriceResponseService,
):
    candidates: list[tuple[str, int | None, datetime | None, object]] = []
    earnings = [
        row
        for row in _scalars(
            db,
            select(CeriEarningsActual).where(CeriEarningsActual.company_id == company_id),
        )
        if row.report_session is not None and row.report_session <= as_of_session
    ]
    for event in earnings:
        candidates.append(("EARNINGS", event.id, event.report_at, event.report_session))
    guidance = [
        row
        for row in _scalars(
            db,
            select(CeriGuidanceEvent).where(CeriGuidanceEvent.company_id == company_id),
        )
        if row.effective_session is None or row.effective_session <= as_of_session
    ]
    for event in guidance:
        candidates.append(("GUIDANCE", event.id, event.effective_at, event.effective_session))
    company_event_ids = set(
        _scalars(
            db,
            select(CeriCatalystEvent.id).where(CeriCatalystEvent.company_id == company_id),
        )
    )
    catalysts = (
        [
            revision
            for revision in _scalars(
                db,
                select(CeriCatalystEventRevision).where(
                    CeriCatalystEventRevision.catalyst_event_id.in_(company_event_ids),
                    CeriCatalystEventRevision.is_current.is_(True),
                ),
            )
            if _revision_is_known_by(revision, as_of_session)
        ]
        if company_event_ids
        else []
    )
    for event in catalysts:
        candidates.append(
            (
                "CATALYST",
                event.id,
                event.announced_at,
                event.effective_session or event.expected_date,
            )
        )
    if not candidates:
        return None, None
    event_type, event_id, event_at, event_session = max(
        candidates,
        key=lambda item: (
            item[3] or datetime.min.date(),
            item[2] or datetime.min.replace(tzinfo=UTC),
            item[1] or 0,
        ),
    )
    result = service.calculate(
        db,
        company_id=company_id,
        ticker=ticker,
        event_type=event_type,
        event_id=event_id,
        event_effective_at=event_at,
        event_effective_session=event_session,
    )
    feature = service.persist(
        db,
        result=result,
        company_id=company_id,
        ticker=ticker,
        event_id=event_id,
        event_effective_at=event_at,
        event_effective_session=event_session,
    )
    return result, feature


def _alignment_context(db: Session, row: RawCompanyRow, run_id: int) -> dict[str, Any]:
    context: dict[str, Any] = {
        "fundamentals": {
            "score": row.raw_json.get("fundamental_score"),
            "source": "raw_company_row",
        },
        "technicals": {
            "score": row.raw_json.get("technical_score"),
            "source": "raw_company_row",
        },
        "sector": {
            "identity": row.sector,
            "state": row.raw_json.get("sector_state"),
            "source": "raw_company_row",
        },
        "regime": {
            "label": row.raw_json.get("market_regime"),
            "score": row.raw_json.get("market_regime_score"),
            "source_run_id": run_id,
        },
        "lifecycle": {
            "state": row.raw_json.get("lifecycle_state"),
            "actionability": row.raw_json.get("lifecycle_actionability"),
            "source_run_id": run_id,
        },
        "earnings_clearance": (
            row.upcoming_earnings_date.isoformat()
            if row.upcoming_earnings_date is not None
            else None
        ),
    }
    return context


def _revision_is_known_by(revision: CeriCatalystEventRevision, as_of_session) -> bool:
    if revision.effective_session is not None:
        return revision.effective_session <= as_of_session
    if revision.announced_at is not None:
        return revision.announced_at.date() <= as_of_session
    return False


def _existing_snapshot(
    db: Session,
    run_id: int,
    company_id: int,
    config,
) -> CeriScoreSnapshot | None:
    return _maybe_scalar(
        db,
        select(CeriScoreSnapshot)
        .where(CeriScoreSnapshot.run_id == run_id)
        .where(CeriScoreSnapshot.company_id == company_id)
        .where(CeriScoreSnapshot.config_hash == config.config_hash)
        .where(CeriScoreSnapshot.calculation_version == config.engine.calculation_version),
    )


def _existing_snapshot_company_ids(
    db: Session,
    run_id: int,
    company_ids: set[int],
    config,
) -> set[int]:
    if not company_ids:
        return set()
    return set(
        _scalars(
            db,
            select(CeriScoreSnapshot.company_id)
            .where(CeriScoreSnapshot.run_id == run_id)
            .where(CeriScoreSnapshot.company_id.in_(sorted(company_ids)))
            .where(CeriScoreSnapshot.config_hash == config.config_hash)
            .where(
                CeriScoreSnapshot.calculation_version
                == config.engine.calculation_version
            ),
        )
    )


def _prior_snapshot(
    db: Session,
    company_id: int,
    current: CeriScoreSnapshot,
) -> CeriScoreSnapshot | None:
    snapshots = _scalars(
        db,
        select(CeriScoreSnapshot).where(CeriScoreSnapshot.company_id == company_id),
    )
    prior = [
        snapshot
        for snapshot in snapshots
        if snapshot is not current
        and snapshot.id != current.id
        and snapshot.as_of_session <= current.as_of_session
    ]
    if not prior:
        return None
    return sorted(prior, key=lambda snapshot: (snapshot.as_of_session, snapshot.id or 0))[-1]


def _latest_changes(db: Session, company_id: int, limit: int):
    changes = _scalars(
        db,
        select(CeriChangeEvent).where(CeriChangeEvent.company_id == company_id),
    )
    scoped = [change for change in changes if getattr(change, "company_id", None) == company_id]
    scoped.sort(key=lambda change: (change.created_at or datetime.min, change.id or 0))
    return scoped[-limit:]


def _point_in_time_volatility_feature(
    db: Session,
    ticker: str,
    cutoff_at: datetime,
) -> _VolatilityRiskFeature | None:
    settings = get_settings()
    if not (
        getattr(settings, "ib_market_intelligence_enabled", False)
        and getattr(settings, "ib_volatility_intelligence_enabled", False)
    ):
        return None
    row = _maybe_scalar(
        db,
        select(IBIntelligenceFeature)
        .where(IBIntelligenceFeature.ticker == ticker.upper())
        .where(IBIntelligenceFeature.module == "VOLATILITY")
        .where(IBIntelligenceFeature.calculated_at <= cutoff_at)
        .where(IBIntelligenceFeature.as_of_session <= cutoff_at.date())
        .order_by(
            IBIntelligenceFeature.as_of_session.desc(),
            IBIntelligenceFeature.calculated_at.desc(),
        ),
    )
    if row is None or row.coverage_status != "AVAILABLE":
        return None
    return _VolatilityRiskFeature(id=row.id, components=dict(row.components_json or {}))


def _point_in_time_short_pressure_feature(
    db: Session,
    ticker: str,
    cutoff_at: datetime,
) -> _ShortPressureContextFeature | None:
    settings = get_settings()
    if not (
        getattr(settings, "ib_market_intelligence_enabled", False)
        and getattr(settings, "ib_short_pressure_enabled", False)
    ):
        return None
    row = _maybe_scalar(
        db,
        select(IBIntelligenceFeature)
        .where(IBIntelligenceFeature.ticker == ticker.upper())
        .where(IBIntelligenceFeature.module == "SHORT_PRESSURE")
        .where(IBIntelligenceFeature.calculated_at <= cutoff_at)
        .where(IBIntelligenceFeature.as_of_session <= cutoff_at.date())
        .order_by(
            IBIntelligenceFeature.as_of_session.desc(),
            IBIntelligenceFeature.calculated_at.desc(),
        ),
    )
    if row is None or row.coverage_status != "AVAILABLE":
        return None
    return _ShortPressureContextFeature(
        id=row.id,
        classification=row.classification,
    )


def _source_ids(features: list[CeriRevisionFeature]) -> list[int]:
    ids: set[int] = set()
    for feature in features:
        ids.update(feature.source_observation_ids_json or [])
    return sorted(ids)


def _confidence_freshness_days(
    db: Session,
    *,
    company_id: int,
    cutoff_at: datetime,
    revision_features: list[CeriRevisionFeature],
    earnings: list[CeriEarningsActual],
) -> dict[str, int | None]:
    source_ids = set(_source_ids(revision_features))
    source_ids.update(row.source_record_id for row in earnings if row.source_record_id is not None)
    guidance = _guidance_for_company(db, company_id, cutoff_at.date())
    source_ids.update(row.source_record_id for row in guidance if row.source_record_id is not None)
    company_event_ids = {
        row.id
        for row in _scalars(
            db, select(CeriCatalystEvent).where(CeriCatalystEvent.company_id == company_id)
        )
    }
    catalyst_source_ids = (
        {
            row.source_record_id
            for row in _scalars(
                db,
                select(CeriCatalystEventRevision).where(
                    CeriCatalystEventRevision.source_record_id.is_not(None),
                    CeriCatalystEventRevision.catalyst_event_id.in_(company_event_ids),
                ),
            )
            if row.source_record_id is not None
        }
        if company_event_ids
        else set()
    )
    source_ids.update(catalyst_source_ids)
    records = (
        _scalars(
            db,
            select(CeriSourceRecord).where(CeriSourceRecord.id.in_(source_ids)),
        )
        if source_ids
        else []
    )
    result: dict[str, int | None] = {}
    for dataset in CeriDataset:
        stamps = [
            row.retrieved_at or row.observed_at or row.published_at or row.ingested_at
            for row in records
            if row.dataset == dataset.value
        ]
        stamps = [stamp for stamp in stamps if stamp is not None and stamp <= cutoff_at]
        result[dataset.value] = (
            max(0, (cutoff_at.date() - max(stamps).date()).days) if stamps else None
        )
    return result


def _quarantined_count(db: Session) -> int:
    return len([row for row in getattr(db, "added", []) if getattr(row, "quarantine_reason", None)])


def _is_conflict_warning(warnings: list[str] | None) -> bool:
    return any("conflict" in str(value).lower() for value in (warnings or []))


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


def _scalars(db: Session, statement):
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _utcnow() -> datetime:
    return datetime.now(UTC)
