from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCatalystSource,
    CeriChangeEvent,
    CeriCompany,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriGuidanceEvent,
    CeriIngestionRun,
    CeriPriceResponseFeature,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import RawCompanyRow
from app.services.calculation_identity import CalculationIdentity
from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.ceri.alert_service import CeriAlertService
from app.services.ceri.artifact_lineage import CeriArtifactOwnership
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService
from app.services.ceri.change_detection_service import CeriChangeDetectionService
from app.services.ceri.change_semantics import select_prior_comparison
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.enums import CeriDataset
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ceri.evidence_eligibility import eligible_snapshot_select
from app.services.ceri.feature_flags import ceri_flags
from app.services.ceri.freshness_service import ticker_feed_freshness_from_runs
from app.services.ceri.guidance_normalizer import guidance_eligibility_reason
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.pit_eligibility import (
    eligible_source_record_ids,
    referenced_sources_are_eligible,
)
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService
from app.services.ceri.upcoming_earnings_authority import (
    UpcomingEarningsSelection,
    select_upcoming_earnings,
)
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.contextual_calculation_identity import (
    CERI_CONTEXT_COMPATIBILITY,
    CERI_IBMI_COMPATIBILITY,
    build_contextual_result_identity,
    build_ibmi_feature_identity,
    consumer_context_identity,
    contextual_compatibility,
    expected_ibmi_identity,
    ibmi_contextual_compatibility,
    identity_metadata,
    pipeline_id_for_cutoff,
)
from app.services.contextual_consumer_eligibility import (
    CONTEXTUAL_ELIGIBILITY_KEY,
    IBMI_SHORT_PRESSURE_TO_CERI,
    IBMI_VOLATILITY_TO_CERI,
    contextual_decision_input,
)
from app.services.ib_market_intelligence.calculations import options_event_premium_score
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.market_calculation_context_service import standalone_market_context
from app.services.market_clock_service import MarketCalculationCutoff, MarketClockService
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
    source_identity: CalculationIdentity


@dataclass(frozen=True)
class _ShortPressureContextFeature:
    id: int
    classification: str
    source_identity: CalculationIdentity


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
        market_cutoff: MarketCalculationCutoff | None = None,
        effective_configuration=None,
        expected_calculation_identity=None,
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> CeriRunCaptureResult:
        from copy import deepcopy

        from app.services.configuration_delivery import current_delivery
        from app.services.entrypoint_authority import EntryPointAuthorityError

        if isinstance(db, Session) and (
            (market_cutoff is None and cutoff_at is None)
            or (effective_configuration is None and current_delivery() is None)
        ):
            raise EntryPointAuthorityError(
                "CERI_EXPLICIT_CALCULATION_AUTHORITY_REQUIRED",
                "CERI capture requires an explicit cutoff and frozen configuration.",
            )

        from app.services.contextual_effective_configuration import resolve_ceri_configuration

        settings = deepcopy(get_settings())
        ibmi_config = load_ib_market_intelligence_config()
        consumer = {
            "run_capture": ceri_flags(settings).run_capture,
            "revision_feature_config_hash": self.snapshot_service.config.config_hash,
            "ibmi_enabled": bool(getattr(settings, "ib_market_intelligence_enabled", False)),
            "volatility_enabled": bool(
                getattr(settings, "ib_volatility_intelligence_enabled", False)
            ),
            "short_pressure_enabled": bool(getattr(settings, "ib_short_pressure_enabled", False)),
            "volatility": {
                "ceri_risk_max_contribution": float(
                    ibmi_config.section("volatility").get("ceri_risk_max_contribution", 1.5)
                )
            },
        }
        from app.services.effective_configuration import (
            ConfigurationSource,
            ConfigurationSourceKind,
        )

        native_sources = dict(getattr(settings, "_contextual_configuration_sources", ()))
        consumer_sources = {
            f"consumer.{key}": ConfigurationSource(
                ConfigurationSourceKind(native_sources.get(field, "SETTINGS_MODEL")), field
            )
            for key, field in (
                ("ibmi_enabled", "ib_market_intelligence_enabled"),
                ("volatility_enabled", "ib_volatility_intelligence_enabled"),
                ("short_pressure_enabled", "ib_short_pressure_enabled"),
            )
        }
        consumer_sources["consumer.run_capture"] = ConfigurationSource(
            ConfigurationSourceKind.SETTINGS_MODEL, "ceri-effective-master-and-run-capture-flags"
        )
        consumer_sources["consumer.revision_feature_config_hash"] = ConfigurationSource(
            ConfigurationSourceKind.REQUEST, "ceri-native-feature-config-filter"
        )
        cap_key = "volatility.ceri_risk_max_contribution"
        consumer_sources[f"consumer.{cap_key}"] = dict(
            getattr(ibmi_config, "_configuration_sources", ())
        ).get(
            cap_key,
            ConfigurationSource(
                ConfigurationSourceKind.CODE_DEFAULT, "ceri-options-risk-default-cap"
            ),
        )
        frozen = effective_configuration or resolve_ceri_configuration(
            self.snapshot_service.config,
            consumer=consumer,
            consumer_sources=consumer_sources,
        )
        frozen.require_family("contextual.ceri")
        if frozen.values["consumer"].get("revision_feature_config_hash") is None:
            raise ValueError("CERI_CAPTURE_INPUT_SELECTION_REQUIRED")
        if expected_calculation_identity is not None:
            frozen.require_retry_identity(expected_calculation_identity)
        self.snapshot_service.effective_configuration = frozen
        self.snapshot_service.config = frozen.ceri_config()
        for service in (
            self.opportunity,
            self.risk,
            self.confidence,
            self.surprise,
            self.catalysts,
            self.price_response,
        ):
            service.config = self.snapshot_service.config
        consumer = frozen.values["consumer"]
        settings.ib_market_intelligence_enabled = consumer["ibmi_enabled"]
        settings.ib_volatility_intelligence_enabled = consumer["volatility_enabled"]
        settings.ib_short_pressure_enabled = consumer["short_pressure_enabled"]
        if not force and not consumer["run_capture"]:
            return CeriRunCaptureResult(skipped=1)
        rows = _raw_rows_for_run(db, run_id)
        if tickers is not None:
            requested = {ticker.upper() for ticker in tickers}
            rows = [row for row in rows if str(row.ticker).upper() in requested]
        if not rows:
            return CeriRunCaptureResult(skipped=1)
        if market_cutoff is None:
            market_cutoff = standalone_market_context(
                reason="STANDALONE_CERI_CAPTURE", cutoff_at=cutoff_at
            )
        elif cutoff_at is not None and cutoff_at != market_cutoff.cutoff_at:
            raise ValueError("cutoff_at conflicts with the frozen market cutoff")
        cutoff_at = market_cutoff.cutoff_at
        as_of_session = market_cutoff.latest_completed_session
        if progress_callback is not None:
            progress_callback("PREPARING_COMPANIES", 0, len(rows))
        companies_by_ticker = _companies_for_tickers(db, {str(row.ticker).upper() for row in rows})
        if progress_callback is not None:
            progress_callback("PREPARING_PROVIDER_CHECKS", 0, len(rows))
        provider_checks_by_ticker = _provider_checks_for_tickers(
            db,
            {str(row.ticker).upper() for row in rows},
            cutoff_at,
        )
        if progress_callback is not None:
            progress_callback("PREPARING_FEATURES", 0, len(rows))
        company_ids = {company.id for company in companies_by_ticker.values()}
        features_by_company = _revision_features_for_companies(
            db,
            company_ids,
            as_of_session,
            cutoff_at,
            calculation_context_id=market_cutoff.context_id,
        )
        features_by_company = {
            company_id: [
                feature
                for feature in features
                if feature.calculation_version
                == self.snapshot_service.config.engine.calculation_version
                and feature.config_hash == consumer["revision_feature_config_hash"]
            ]
            for company_id, features in features_by_company.items()
        }
        if progress_callback is not None:
            progress_callback("PREPARING_IBMI_CONTEXT", 0, len(rows))
        pipeline_id = pipeline_id_for_cutoff(db, run_id=run_id, market_cutoff=market_cutoff)
        ibmi_candidates = _preload_ibmi_context(db, rows, cutoff_at, settings=settings)
        if progress_callback is not None:
            progress_callback("PREPARING_EXISTING_SNAPSHOTS", 0, len(rows))
        existing_snapshot_company_ids = _existing_snapshot_company_ids(
            db,
            run_id,
            company_ids,
            self.snapshot_service.config,
            market_cutoff,
            pipeline_id,
        )
        if progress_callback is not None:
            progress_callback("CAPTURE_INPUTS_PREPARED", 0, len(rows))
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
        captured_snapshots = []
        from app.services.source_mutation_authority import prefetched_source_scope

        if progress_callback is not None:
            progress_callback("PREPARING_EARNINGS", 0, len(rows))
        all_earnings = _capture_earnings_rows(db, company_ids)
        earnings_by_company: dict[int, list[CeriEarningsActual]] = {}
        for item in all_earnings:
            earnings_by_company.setdefault(item.company_id, []).append(item)
        if progress_callback is not None:
            progress_callback("PREPARING_SOURCE_BUNDLE", 0, len(rows))
        initial_bundle = (
            _capture_source_bundle(
                db,
                company_ids,
                run_id,
                market_cutoff,
                enrichment=True,
                earnings_rows=all_earnings,
            )
            if isinstance(db, Session)
            else None
        )
        if progress_callback is not None:
            progress_callback("PREPARING_UPCOMING_EARNINGS", 0, len(rows))
        initial_scope = (
            prefetched_source_scope(db, initial_bundle)
            if initial_bundle is not None
            else nullcontext()
        )
        with initial_scope:
            upcoming_earnings_by_company = _upcoming_earnings_for_companies(
                db,
                company_ids=company_ids,
                earnings=all_earnings,
                as_of_session=as_of_session,
                cutoff_at=cutoff_at,
                config=self.snapshot_service.config,
            )
            for row_index, row in enumerate(rows, start=1):
                if progress_callback is not None:
                    progress_callback(str(row.ticker), row_index - 1, len(rows))
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
                        as_of_session,
                        cutoff_at,
                        self.catalysts,
                    )
                    company_conflicted = sum(
                        _is_conflict_warning(feature.warnings_json) for feature in features
                    )
                    feature_stale = any(
                        "estimate_data_stale" in (feature.warnings_json or [])
                        for feature in features
                    )
                    dataset_freshness_days = _provider_feed_freshness_days(
                        provider_checks_by_ticker.get(str(row.ticker).upper(), []),
                        ticker=str(row.ticker),
                        cutoff_at=cutoff_at,
                        config=self.snapshot_service.config,
                    )
                    estimate_age = dataset_freshness_days.get(CeriDataset.ESTIMATES.value)
                    company_stale = feature_stale or (
                        estimate_age is not None
                        and estimate_age
                        > self.snapshot_service.config.datasets[
                            CeriDataset.ESTIMATES
                        ].max_stale_days
                    )
                    counts["conflicted"] += company_conflicted
                    counts["stale"] += int(company_stale)
                    earnings = _eligible_source_backed_rows(
                        db,
                        [
                            item
                            for item in earnings_by_company.get(company.id, [])
                            if item.report_session is not None
                            and item.report_session <= as_of_session
                        ],
                        cutoff_at,
                    )
                    upcoming_earnings = upcoming_earnings_by_company[company.id]
                    estimates = _eligible_source_backed_rows(
                        db,
                        _scalars(
                            db,
                            select(CeriEstimateSnapshot).where(
                                CeriEstimateSnapshot.company_id == company.id,
                                CeriEstimateSnapshot.effective_session <= as_of_session,
                            ),
                        ),
                        cutoff_at,
                        scalar_fields=("source_record_id", "conversion_source_record_id"),
                    )
                    surprise_summary = self.surprise.summarize(earnings, estimates)
                    price_result, price_feature = _price_response_for_company(
                        db,
                        company_id=company.id,
                        ticker=row.ticker,
                        as_of_session=as_of_session,
                        cutoff_at=cutoff_at,
                        calculation_context_id=market_cutoff.context_id,
                        calendar_version=market_cutoff.calendar_version,
                        service=self.price_response,
                    )
                    contextual_permissions = {}
                    volatility_feature = _point_in_time_volatility_feature(
                        db,
                        row.ticker,
                        cutoff_at,
                        as_of_session=as_of_session,
                        market_cutoff=market_cutoff,
                        ibmi_config=ibmi_config,
                        candidates=ibmi_candidates,
                        decisions=contextual_permissions,
                        settings=settings,
                    )
                    short_pressure_feature = _point_in_time_short_pressure_feature(
                        db,
                        row.ticker,
                        cutoff_at,
                        as_of_session=as_of_session,
                        market_cutoff=market_cutoff,
                        ibmi_config=ibmi_config,
                        candidates=ibmi_candidates,
                        decisions=contextual_permissions,
                        settings=settings,
                    )
                    confidence = self.confidence.calculate(
                        as_of_session=as_of_session,
                        revision_features=features,
                        dataset_freshness_days=dataset_freshness_days,
                        conflict_penalty=float(company_conflicted),
                    )
                    opportunity = self.opportunity.calculate(
                        revision_features=features,
                        surprise_summary=surprise_summary,
                        guidance_events=_guidance_for_company(
                            db, company.id, as_of_session, cutoff_at
                        ),
                        catalyst_features=catalyst_features,
                        price_response_quality=(
                            price_result.quality if price_result is not None else None
                        ),
                        price_response_parent_event_id=(
                            price_feature.event_id if price_feature is not None else None
                        ),
                        price_response_parent_type=(
                            price_feature.event_type if price_feature is not None else None
                        ),
                        price_response_unavailable_reason=(
                            price_result.unavailable_reason
                            if price_result is not None
                            else "NO_ACCEPTED_EVENT"
                        ),
                        conflict_penalty=min(3.0, float(company_conflicted)),
                        as_of_session=as_of_session,
                    )
                    volatility_config = consumer["volatility"]
                    volatility_risk = (
                        options_event_premium_score(
                            volatility_feature,
                            maximum=float(volatility_config.get("ceri_risk_max_contribution", 1.5)),
                        )
                        if volatility_feature is not None
                        else None
                    )
                    risk = self.risk.calculate(
                        as_of_session=as_of_session,
                        next_earnings_session=(
                            upcoming_earnings.selected.earnings_session
                            if upcoming_earnings.selected is not None
                            else None
                        ),
                        catalyst_features=catalyst_features,
                        stale=company_stale,
                        conflict_penalty=min(3.0, float(company_conflicted)),
                        options_event_premium_score=volatility_risk,
                        short_pressure_classification=(
                            short_pressure_feature.classification
                            if short_pressure_feature is not None
                            else None
                        ),
                    )
                    guidance_rows = _guidance_for_company(db, company.id, as_of_session, cutoff_at)
                    catalyst_lineage = _catalyst_lineage(db, company.id, as_of_session, cutoff_at)
                    evidence_lineage = {
                        CONTEXTUAL_ELIGIBILITY_KEY: contextual_permissions,
                        "ib_context_selected_feature_ids": sorted(
                            permission["source_feature_id"]
                            for permission in contextual_permissions.values()
                            if permission["source_feature_id"] is not None
                            and permission["decision"]["producer_evidence_id"] is not None
                        ),
                        "historical_view_mode": "AS_KNOWN",
                        "temporal_lineage": {
                            "calculation_context_id": market_cutoff.context_id,
                            "calculation_cutoff_at": CanonicalEvidenceSerializer.canonicalize(
                                market_cutoff.cutoff_at
                            ),
                            "input_as_of_session": as_of_session.isoformat(),
                            "calendar_version": market_cutoff.calendar_version,
                        },
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
                        "upcoming_earnings_authority": upcoming_earnings.evidence(),
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
                    ceri_base_identity = consumer_context_identity(
                        market_cutoff=market_cutoff,
                        run_id=run_id,
                        pipeline_id=pipeline_id,
                        ticker=row.ticker,
                        company_id=company.id,
                    )
                    ibmi_sources = []
                    if volatility_feature is not None:
                        ibmi_sources.append(
                            (
                                "IBMI-volatility",
                                volatility_feature,
                                volatility_feature.source_identity,
                            )
                        )
                    if short_pressure_feature is not None:
                        ibmi_sources.append(
                            (
                                "IBMI-short-pressure",
                                short_pressure_feature,
                                short_pressure_feature.source_identity,
                            )
                        )
                    ceri_identity = build_contextual_result_identity(
                        base=ceri_base_identity,
                        namespace="ceri-context",
                        config_hash=self.snapshot_service.config.config_hash,
                        calculation_version=self.snapshot_service.config.engine.calculation_version,
                        engine_version=self.snapshot_service.config.engine.calculation_version,
                        source_artifacts=ibmi_sources,
                        source_payload=evidence_lineage,
                        company_id=company.id,
                    )
                    ceri_identity = self.snapshot_service.effective_configuration.bind(
                        ceri_identity
                    )
                    evidence_lineage.update(
                        identity_metadata(ceri_identity, policy=CERI_CONTEXT_COMPATIBILITY.name)
                    )
                    source_ids = sorted(
                        set(
                            _source_ids(features)
                            + [item.source_record_id for item in earnings]
                            + (
                                [upcoming_earnings.selected.source_record_id]
                                if upcoming_earnings.selected is not None
                                else []
                            )
                            + [item.source_record_id for item in guidance_rows]
                            + catalyst_lineage["source_ids"]
                        )
                    )
                    snapshot = self.snapshot_service.build_snapshot(
                        run_id=run_id,
                        source_run_id_text=str(run_id),
                        company_id=company.id,
                        ticker=row.ticker,
                        as_of_session=as_of_session,
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
                        alignment_context=_alignment_context(
                            db,
                            row,
                            run_id,
                            upcoming_earnings=upcoming_earnings,
                        ),
                        evidence_lineage=evidence_lineage,
                    )
                    snapshot.calculation_context_id = market_cutoff.context_id
                    snapshot.calendar_version = market_cutoff.calendar_version
                    if not isinstance(db, Session):
                        self.snapshot_service.persist_snapshot(db, snapshot)
                    counts["score_snapshots"] += 1
                    if not getattr(opportunity, "rated", opportunity.score is not None):
                        counts["unrated"] += 1
                    if isinstance(db, Session):
                        captured_snapshots.append(snapshot)
                    else:
                        prior, comparison_state = _prior_snapshot(db, company.id, snapshot)
                        changes = self.change_detection.detect_score_changes(
                            db,
                            current=snapshot,
                            prior=prior,
                            scope=f"run:{run_id}",
                            comparison_state=comparison_state,
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
                    if isinstance(db, Session):
                        # Financial writer rejection must leave the semantic root
                        # failed and atomic, not become a successful partial capture.
                        db.rollback()
                        raise
                    counts["failed"] += 1
        if captured_snapshots:
            from app.services.source_mutation_authority import prefetched_source_scope

            try:
                persistence_checkpoint = max(len(rows) - 1, 0)
                if progress_callback is not None:
                    progress_callback(
                        "PERSISTING_SOURCE_BUNDLE",
                        persistence_checkpoint,
                        len(rows),
                    )
                # Native source enrichment completes before freezing its SQL
                # witness. No earlier witness survives a source-body mutation.
                bundle = _capture_source_bundle(db, company_ids, run_id, market_cutoff)
                if progress_callback is not None:
                    progress_callback(
                        "PERSISTING_SNAPSHOTS",
                        persistence_checkpoint,
                        len(rows),
                    )
                with prefetched_source_scope(db, bundle):
                    for snapshot in captured_snapshots:
                        self.snapshot_service.persist_snapshot(db, snapshot)
                        if progress_callback is not None:
                            progress_callback(
                                f"PERSISTING_SNAPSHOT:{snapshot.ticker}",
                                persistence_checkpoint,
                                len(rows),
                            )
                if progress_callback is not None:
                    progress_callback(
                        "PERSISTING_COMPARISONS",
                        persistence_checkpoint,
                        len(rows),
                    )
                _capture_score_comparisons(
                    db, self, captured_snapshots, counts, run_id, market_cutoff
                )
                if progress_callback is not None:
                    progress_callback(str(rows[-1].ticker), len(rows), len(rows))
            except Exception:
                db.rollback()
                raise
        return CeriRunCaptureResult(**counts)


def _capture_earnings_rows(db, company_ids) -> list[CeriEarningsActual]:
    """Load the capture-wide earnings authority population in one statement."""
    return _scalars(
        db,
        select(CeriEarningsActual).where(CeriEarningsActual.company_id.in_(company_ids)),
    )


def _raw_rows_for_run(db: Session, run_id: int) -> list[RawCompanyRow]:
    return _scalars(
        db,
        select(RawCompanyRow).where(RawCompanyRow.run_id == run_id),
    )


def _capture_source_bundle(
    db,
    company_ids,
    run_id,
    market_cutoff,
    *,
    enrichment=False,
    earnings_rows: list[CeriEarningsActual] | None = None,
):
    """Lock the admitted source population once using existing bundle rules."""
    from app.services.source_mutation_authority import PrefetchedSourceBodies

    bundle = PrefetchedSourceBodies(db)
    sources = set()
    for model in (
        CeriRevisionFeature,
        CeriEstimateSnapshot,
        CeriEarningsActual,
        CeriGuidanceEvent,
        CeriCatalystEvent,
        CeriPriceResponseFeature,
    ):
        statement = select(model).where(model.company_id.in_(company_ids))
        if model is CeriEarningsActual and earnings_rows is not None:
            rows = earnings_rows
        else:
            rows = (
                db.scalars(statement).all()
                if enrichment and model in {CeriEarningsActual, CeriPriceResponseFeature}
                else bundle.load(model, statement)
            )
        for row in rows:
            for field in (
                "source_record_id",
                "current_source_record_id",
                "baseline_source_record_id",
                "provider_retrospective_source_record_id",
                "conversion_source_record_id",
            ):
                source_id = getattr(row, field, None)
                if source_id is not None:
                    sources.add(source_id)
            sources.update(getattr(row, "source_observation_ids_json", None) or [])
    events = select(CeriCatalystEvent.id).where(CeriCatalystEvent.company_id.in_(company_ids))
    revisions = bundle.load(
        CeriCatalystEventRevision,
        select(CeriCatalystEventRevision).where(
            CeriCatalystEventRevision.catalyst_event_id.in_(events)
        ),
    )
    attachments = bundle.load(
        CeriCatalystSource,
        select(CeriCatalystSource).where(
            CeriCatalystSource.catalyst_revision_id.in_([row.id for row in revisions])
        ),
    )
    sources.update(
        row.source_record_id
        for row in [*revisions, *attachments]
        if row.source_record_id is not None
    )
    bundle.load(CeriSourceRecord, select(CeriSourceRecord).where(CeriSourceRecord.id.in_(sources)))
    _capture_delivery_bundle(db, bundle, run_id, market_cutoff)
    bundle.seal()
    return bundle


def _capture_delivery_bundle(db, bundle, run_id, market_cutoff):
    from app.models.tables import (
        EffectiveConfigurationRecord,
        ExecutionConfigurationAnchor,
        ExecutionConfigurationBinding,
        MarketCalculationContext,
        PipelineRun,
        UploadRun,
    )
    from app.services.configuration_delivery import current_delivery
    from app.services.domain_write_fence import current_domain_write_ownership

    delivery = current_delivery()
    if delivery is not None:
        bundle.load(
            EffectiveConfigurationRecord,
            select(EffectiveConfigurationRecord).where(
                EffectiveConfigurationRecord.resolution_hash.in_(
                    [config.snapshot.resolution_hash for config in delivery.configurations.values()]
                )
            ),
        )
        bundle.load(
            ExecutionConfigurationAnchor,
            select(ExecutionConfigurationAnchor).where(
                ExecutionConfigurationAnchor.anchor_id == delivery.anchor["anchor_id"]
            ),
        )
        keys = []
        ownership = current_domain_write_ownership()
        if ownership is not None:
            keys.append("job:" + str(ownership.job_id))
        if market_cutoff.context_id is not None:
            contexts = bundle.load(
                MarketCalculationContext,
                select(MarketCalculationContext).where(
                    MarketCalculationContext.id == market_cutoff.context_id
                ),
            )
            pipeline_ids = {row.pipeline_run_id for row in contexts if row.pipeline_run_id}
            keys.extend("pipeline:" + str(value) for value in pipeline_ids)
            bundle.load(PipelineRun, select(PipelineRun).where(PipelineRun.id.in_(pipeline_ids)))
        bundle.load(
            ExecutionConfigurationBinding,
            select(ExecutionConfigurationBinding).where(
                ExecutionConfigurationBinding.binding_key.in_(keys)
            ),
        )
        bundle.load(UploadRun, select(UploadRun).where(UploadRun.id == run_id))


def _capture_score_comparisons(
    db: Session,
    service: CeriRunCaptureService,
    snapshots: list[CeriScoreSnapshot],
    counts: dict[str, int],
    run_id: int,
    market_cutoff: MarketCalculationCutoff,
):
    """New scores and retained predecessors share one exact locked witness."""
    from app.models.tables import CoreCalculationEvidence
    from app.services.source_mutation_authority import (
        PrefetchedSourceBodies,
        prefetched_source_scope,
    )

    bundle = PrefetchedSourceBodies(db)
    scores = bundle.load(
        CeriScoreSnapshot,
        select(CeriScoreSnapshot).where(
            CeriScoreSnapshot.company_id.in_({score.company_id for score in snapshots})
        ),
    )
    bundle.load(
        CoreCalculationEvidence,
        select(CoreCalculationEvidence).where(
            CoreCalculationEvidence.id.in_({score.evidence_id for score in scores})
        ),
    )
    _capture_delivery_bundle(db, bundle, run_id, market_cutoff)
    bundle.seal()
    scores_by_company: dict[int, list[CeriScoreSnapshot]] = {}
    for score in scores:
        scores_by_company.setdefault(score.company_id, []).append(score)
    new_change_ids: set[int] = set()
    ticker_by_company = {snapshot.company_id: snapshot.ticker for snapshot in snapshots}
    with prefetched_source_scope(db, bundle):
        for snapshot in snapshots:
            prior, comparison_state = _prior_snapshot_from_candidates(
                snapshot,
                scores_by_company.get(snapshot.company_id, []),
            )
            changes = service.change_detection.detect_score_changes(
                db,
                current=snapshot,
                prior=prior,
                scope=f"run:{run_id}",
                comparison_state=comparison_state,
            )
            counts["change_events"] += changes.changes
            if changes.changes:
                new_change_ids.update(changes.change_ids)
    if new_change_ids:
        alert_bundle, new_changes = _capture_alert_bundle(
            db,
            change_ids=new_change_ids,
            scores=scores,
            run_id=run_id,
            market_cutoff=market_cutoff,
        )
        alerts = service.alert_service.rebuild_alerts(
            db,
            changes=new_changes,
            ticker_by_company=ticker_by_company,
            _source_bundle=alert_bundle,
        )
        counts["alerts"] += alerts.alerts


def _capture_alert_bundle(db, *, change_ids, scores, run_id, market_cutoff):
    """Retain exact alert inputs for the semantic writer to finish and seal."""
    from app.models.tables import CoreCalculationEvidence
    from app.services.source_mutation_authority import PrefetchedSourceBodies

    bundle = PrefetchedSourceBodies(db)
    retained_changes = bundle.load(
        CeriChangeEvent,
        select(CeriChangeEvent).where(CeriChangeEvent.id.in_(change_ids)),
    )
    if {change.id for change in retained_changes} != set(change_ids):
        raise ValueError("CERI_CHANGE_SOURCE_SET_MISMATCH")
    score_ids = {
        value
        for change in retained_changes
        for value in (change.from_snapshot_id, change.to_snapshot_id)
        if value is not None
    }
    retained_scores = bundle.load(
        CeriScoreSnapshot,
        select(CeriScoreSnapshot).where(CeriScoreSnapshot.id.in_(score_ids)),
    )
    bundle.load(
        CoreCalculationEvidence,
        select(CoreCalculationEvidence).where(
            CoreCalculationEvidence.id.in_(
                {score.evidence_id for score in retained_scores if score.evidence_id is not None}
            )
        ),
    )
    bundle.load(
        CeriCompany,
        select(CeriCompany).where(
            CeriCompany.id.in_({change.company_id for change in retained_changes})
        ),
    )
    _capture_delivery_bundle(db, bundle, run_id, market_cutoff)
    return bundle, retained_changes


def _company_for_ticker(db: Session, ticker: str) -> CeriCompany | None:
    return _maybe_scalar(
        db,
        select(CeriCompany).where(CeriCompany.ticker == ticker.upper()),
    )


def _companies_for_tickers(db: Session, tickers: set[str]) -> dict[str, CeriCompany]:
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
    cutoff_at: datetime,
    *,
    calculation_context_id: int | None,
) -> dict[int, list[CeriRevisionFeature]]:
    if not company_ids:
        return {}
    features = _scalars(
        db,
        select(CeriRevisionFeature)
        .where(CeriRevisionFeature.company_id.in_(sorted(company_ids)))
        .where(CeriRevisionFeature.as_of_session == as_of_session)
        .where(
            CeriRevisionFeature.calculation_context_id == calculation_context_id
            if calculation_context_id is not None
            else CeriRevisionFeature.ownership_mode != CeriArtifactOwnership.PIPELINE.value
        ),
    )
    eligible = _eligible_source_backed_rows(
        db,
        [
            feature
            for feature in features
            if feature.known_at is None or feature.known_at <= cutoff_at
        ],
        cutoff_at,
        scalar_fields=(
            "current_source_record_id",
            "baseline_source_record_id",
            "provider_retrospective_source_record_id",
        ),
        collection_fields=("source_observation_ids_json",),
        allow_unreferenced=True,
    )
    grouped: dict[int, list[CeriRevisionFeature]] = {}
    for feature in eligible:
        grouped.setdefault(feature.company_id, []).append(feature)
    return grouped


def _guidance_for_company(
    db: Session,
    company_id: int,
    as_of_session,
    cutoff_at: datetime,
) -> list[CeriGuidanceEvent]:
    return _eligible_source_backed_rows(
        db,
        _scalars(
            db,
            select(CeriGuidanceEvent)
            .where(CeriGuidanceEvent.company_id == company_id)
            .where(CeriGuidanceEvent.effective_session <= as_of_session),
        ),
        cutoff_at,
    )


def _catalyst_features_for_company(
    db: Session,
    company_id: int,
    as_of_session,
    cutoff_at: datetime,
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
    revisions = _eligible_source_backed_rows(
        db,
        _scalars(
            db,
            select(CeriCatalystEventRevision)
            .join(
                CeriCatalystEvent,
                CeriCatalystEvent.id == CeriCatalystEventRevision.catalyst_event_id,
            )
            .where(CeriCatalystEvent.company_id == company_id),
        ),
        cutoff_at,
    )
    revisions = _latest_eligible_catalyst_revisions(revisions, as_of_session)
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


def _catalyst_lineage(
    db: Session, company_id: int, as_of_session, cutoff_at: datetime
) -> dict[str, list[int]]:
    events = [
        event
        for event in _scalars(
            db,
            select(CeriCatalystEvent).where(CeriCatalystEvent.company_id == company_id),
        )
    ]
    event_ids = {event.id for event in events if event.id is not None}
    revisions = (
        _latest_eligible_catalyst_revisions(
            _eligible_source_backed_rows(
                db,
                _scalars(
                    db,
                    select(CeriCatalystEventRevision).where(
                        CeriCatalystEventRevision.catalyst_event_id.in_(event_ids),
                    ),
                ),
                cutoff_at,
            ),
            as_of_session,
        )
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
    cutoff_at: datetime,
    calculation_context_id: int | None,
    calendar_version: str,
    service: CeriPriceResponseService,
):
    candidates: list[tuple[str, int | None, datetime | None, object]] = []
    earnings = [
        row
        for row in _eligible_source_backed_rows(
            db,
            _scalars(
                db,
                select(CeriEarningsActual).where(CeriEarningsActual.company_id == company_id),
            ),
            cutoff_at,
        )
        if row.report_session is not None
        and row.report_session <= as_of_session
        and row.actual_value is not None
        and (row.event_kind or "REPORTED").upper() == "REPORTED"
    ]
    for event in earnings:
        candidates.append(("EARNINGS", event.id, event.report_at, event.report_session))
    guidance = [
        row
        for row in _eligible_source_backed_rows(
            db,
            _scalars(
                db,
                select(CeriGuidanceEvent).where(CeriGuidanceEvent.company_id == company_id),
            ),
            cutoff_at,
        )
        if (row.effective_session is None or row.effective_session <= as_of_session)
        and row.accepted_for_scoring is True
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
            for revision in _latest_eligible_catalyst_revisions(
                _eligible_source_backed_rows(
                    db,
                    _scalars(
                        db,
                        select(CeriCatalystEventRevision).where(
                            CeriCatalystEventRevision.catalyst_event_id.in_(company_event_ids),
                        ),
                    ),
                    cutoff_at,
                ),
                as_of_session,
            )
            if revision.issuer_relevance is True
            and str(revision.review_state or "").upper() != "REJECTED"
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
        result = service.unavailable(
            company_id=company_id,
            event_type="NONE",
            reason="NO_ACCEPTED_EVENT",
            cutoff_at=cutoff_at,
        )
        feature = service.persist(
            db,
            result=result,
            company_id=company_id,
            ticker=ticker,
            event_id=None,
            event_effective_at=None,
            event_effective_session=None,
            feature_as_of_session=as_of_session,
            cutoff_at=cutoff_at,
            calculation_context_id=calculation_context_id,
            calendar_version=calendar_version,
            ownership_mode=(
                CeriArtifactOwnership.PIPELINE.value
                if calculation_context_id is not None
                else CeriArtifactOwnership.STANDALONE.value
            ),
        )
        return result, feature
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
        feature_as_of_session=as_of_session,
        cutoff_at=cutoff_at,
    )
    feature = service.persist(
        db,
        result=result,
        company_id=company_id,
        ticker=ticker,
        event_id=event_id,
        event_effective_at=event_at,
        event_effective_session=event_session,
        feature_as_of_session=as_of_session,
        cutoff_at=cutoff_at,
        calculation_context_id=calculation_context_id,
        calendar_version=calendar_version,
        ownership_mode=(
            CeriArtifactOwnership.PIPELINE.value
            if calculation_context_id is not None
            else CeriArtifactOwnership.STANDALONE.value
        ),
    )
    return result, feature


def _alignment_context(
    db: Session,
    row: RawCompanyRow,
    run_id: int,
    *,
    upcoming_earnings: UpcomingEarningsSelection | None = None,
) -> dict[str, Any]:
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
            upcoming_earnings.evidence()
            if upcoming_earnings is not None
            else {
                "selection_reason": "SOURCE_BACKED_UPCOMING_EARNINGS_NOT_PROVIDED",
                "selected_exact_value": None,
            }
        ),
    }
    return context


def _revision_is_known_by(revision: CeriCatalystEventRevision, as_of_session) -> bool:
    if revision.effective_session is not None:
        return revision.effective_session <= as_of_session
    if revision.announced_at is not None:
        return revision.announced_at.date() <= as_of_session
    return False


def _latest_eligible_catalyst_revisions(
    revisions: list[CeriCatalystEventRevision], as_of_session
) -> list[CeriCatalystEventRevision]:
    eligible = [
        revision for revision in revisions if _revision_is_known_by(revision, as_of_session)
    ]
    latest: dict[int, CeriCatalystEventRevision] = {}
    for revision in eligible:
        current = latest.get(revision.catalyst_event_id)
        if current is None or (revision.revision_number, revision.id or 0) > (
            current.revision_number,
            current.id or 0,
        ):
            latest[revision.catalyst_event_id] = revision
    return list(latest.values())


def _eligible_source_backed_rows(
    db: Session,
    rows: list[Any],
    cutoff_at: datetime,
    *,
    scalar_fields: tuple[str, ...] = ("source_record_id",),
    collection_fields: tuple[str, ...] = (),
    allow_unreferenced: bool = False,
) -> list[Any]:
    source_ids = {
        int(value)
        for row in rows
        for field in scalar_fields
        if (value := getattr(row, field, None)) is not None
    }
    source_ids.update(
        int(value)
        for row in rows
        for field in collection_fields
        for value in (getattr(row, field, None) or [])
    )
    from app.services.source_mutation_authority import prefetched_source_rows

    sources = prefetched_source_rows(db, CeriSourceRecord, source_ids)
    if sources is None:
        sources = (
            _scalars(
                db,
                select(CeriSourceRecord).where(CeriSourceRecord.id.in_(sorted(source_ids))),
            )
            if source_ids
            else []
        )
    if not isinstance(db, Session) and not sources:
        # Legacy unit adapters do not model the source-record graph.  Real
        # pipeline sessions and explicit PIT fixtures always enforce it.
        return rows
    eligible_ids = eligible_source_record_ids(sources, cutoff_at)
    return [
        row
        for row in rows
        if referenced_sources_are_eligible(
            row,
            eligible_ids,
            scalar_fields=scalar_fields,
            collection_fields=collection_fields,
            allow_unreferenced=allow_unreferenced,
        )
    ]


def _upcoming_earnings_for_companies(
    db: Session,
    *,
    company_ids: set[int],
    earnings: list[CeriEarningsActual],
    as_of_session,
    cutoff_at: datetime,
    config,
) -> dict[int, UpcomingEarningsSelection]:
    source_ids = {int(row.source_record_id) for row in earnings if row.source_record_id is not None}
    from app.services.source_mutation_authority import prefetched_source_rows

    sources = prefetched_source_rows(db, CeriSourceRecord, source_ids)
    if sources is None:
        sources = (
            _scalars(
                db,
                select(CeriSourceRecord).where(CeriSourceRecord.id.in_(sorted(source_ids))),
            )
            if source_ids
            else []
        )
    source_by_id = {int(source.id): source for source in sources if source.id is not None}
    return {
        company_id: select_upcoming_earnings(
            company_id=company_id,
            as_of_session=as_of_session,
            cutoff_at=cutoff_at,
            earnings=earnings,
            source_records=source_by_id,
            config=config,
        )
        for company_id in company_ids
    }


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
    market_cutoff: MarketCalculationCutoff | None = None,
    pipeline_id: int | None = None,
) -> set[int]:
    if not company_ids:
        return set()
    rows = _scalars(
        db,
        select(CeriScoreSnapshot)
        .where(CeriScoreSnapshot.run_id == run_id)
        .where(CeriScoreSnapshot.company_id.in_(sorted(company_ids)))
        .where(CeriScoreSnapshot.config_hash == config.config_hash)
        .where(CeriScoreSnapshot.calculation_version == config.engine.calculation_version),
    )
    if market_cutoff is None:
        return {getattr(row, "company_id", row) for row in rows}
    compatible: set[int] = set()
    for row in rows:
        identity = calculation_identity_from_debug(row.evidence_lineage_json)
        if identity is None:
            raise ValueError(
                "CALCULATION_IDENTITY_REJECTED: consumer=CERI "
                "producer=legacy CeriScoreSnapshot reason=LEGACY_UNKNOWN"
            )
        frozen = getattr(config, "_effective_configuration", None)
        if frozen is not None and identity.configuration != frozen.bind(identity).configuration:
            # A new native input-selection policy is a new calculation, even
            # when the compatibility row's historical raw config hash is equal.
            continue
        expected = consumer_context_identity(
            market_cutoff=market_cutoff,
            run_id=run_id,
            pipeline_id=pipeline_id,
            ticker=row.ticker,
            company_id=row.company_id,
        )
        result = contextual_compatibility(expected, identity, policy=CERI_CONTEXT_COMPATIBILITY)
        if not result.accepted:
            raise ValueError(
                "CALCULATION_IDENTITY_REJECTED: consumer=CERI "
                f"producer=CeriScoreSnapshot policy={result.policy} "
                f"result={result.status.value} details={'; '.join(result.diagnostics)}"
            )
        compatible.add(row.company_id)
    return compatible


def _prior_snapshot(
    db: Session,
    company_id: int,
    current: CeriScoreSnapshot,
) -> tuple[CeriScoreSnapshot | None, str]:
    snapshots = _scalars(
        db,
        eligible_snapshot_select(name="effective_prior_dispositions").where(
            CeriScoreSnapshot.company_id == company_id,
        ),
    )
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot is not current
        and snapshot.id != current.id
        and snapshot.as_of_session <= current.as_of_session
    ]
    prior, state, _excluded = select_prior_comparison(current, candidates)
    return prior, state.value


def _prior_snapshot_from_candidates(
    current: CeriScoreSnapshot,
    snapshots: list[CeriScoreSnapshot],
) -> tuple[CeriScoreSnapshot | None, str]:
    candidates = [
        snapshot
        for snapshot in snapshots
        if snapshot is not current
        and snapshot.id != current.id
        and snapshot.as_of_session <= current.as_of_session
    ]
    prior, state, _excluded = select_prior_comparison(current, candidates)
    return prior, state.value


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
    *,
    as_of_session=None,
    market_cutoff: MarketCalculationCutoff | None = None,
    ibmi_config=None,
    candidates=None,
    decisions=None,
    settings=None,
) -> _VolatilityRiskFeature | None:
    if decisions is not None:
        decisions["ibmi_volatility"] = contextual_decision_input(None, IBMI_VOLATILITY_TO_CERI)[1]
    settings = settings or get_settings()
    if not (
        getattr(settings, "ib_market_intelligence_enabled", False)
        and getattr(settings, "ib_volatility_intelligence_enabled", False)
    ):
        return None
    rows = (
        candidates
        if candidates is not None
        else _scalars(
            db,
            select(IBIntelligenceFeature)
            .options(selectinload(IBIntelligenceFeature.calculation_evidence))
            .where(IBIntelligenceFeature.ticker == ticker.upper())
            .where(IBIntelligenceFeature.module == "VOLATILITY")
            .where(IBIntelligenceFeature.calculated_at <= cutoff_at)
            .where(
                IBIntelligenceFeature.as_of_session
                <= MarketClockService()
                .cutoff_for(cutoff_at, reason="CERI_IB_FEATURE_SELECTION")
                .latest_completed_session
            )
            .order_by(
                IBIntelligenceFeature.as_of_session.desc(),
                IBIntelligenceFeature.calculated_at.desc(),
            ),
        )
    )
    ibmi_config = ibmi_config or load_ib_market_intelligence_config()
    session = (
        as_of_session
        or MarketClockService()
        .cutoff_for(cutoff_at, reason="CERI_IB_FEATURE_IDENTITY")
        .latest_completed_session
    )
    market_cutoff = market_cutoff or MarketClockService().cutoff_for(
        cutoff_at, reason="CERI_IB_FEATURE_IDENTITY"
    )
    base = consumer_context_identity(
        market_cutoff=market_cutoff,
        run_id=None,
        pipeline_id=None,
        ticker=ticker,
        globally_reusable=True,
    )
    expected = expected_ibmi_identity(
        context=base,
        ticker=ticker,
        config_hash=ibmi_config.config_hash,
        calculation_version=ibmi_config.calculation_version,
        source_version=ibmi_config.source_version,
        module="volatility",
        effective_configuration=ibmi_config._effective_configurations["volatility"],
    )
    for row in rows:
        if row.ticker.upper() != ticker.upper() or row.module != "VOLATILITY":
            continue
        if row.as_of_session != session:
            continue
        identity = build_ibmi_feature_identity(row)
        candidate_expected = expected
        if row.evidence_id is None:
            # An unsealed matching current candidate remains the selected,
            # blocked input. It cannot authorize fallback to an older READY row.
            candidate_expected = expected_ibmi_identity(
                context=base,
                ticker=ticker,
                config_hash=ibmi_config.config_hash,
                calculation_version=ibmi_config.calculation_version,
                source_version=ibmi_config.source_version,
                module="volatility",
            )
        if ibmi_contextual_compatibility(
            expected=candidate_expected, actual=identity, policy=CERI_IBMI_COMPATIBILITY
        ).accepted:
            usable, permission = contextual_decision_input(row, IBMI_VOLATILITY_TO_CERI)
            if decisions is not None:
                decisions["ibmi_volatility"] = permission
            if usable is None:
                return None
            return _VolatilityRiskFeature(
                id=row.id,
                components=dict(usable.components_json or {}),
                source_identity=identity,
            )
    return None


def _point_in_time_short_pressure_feature(
    db: Session,
    ticker: str,
    cutoff_at: datetime,
    *,
    as_of_session=None,
    market_cutoff: MarketCalculationCutoff | None = None,
    ibmi_config=None,
    candidates=None,
    decisions=None,
    settings=None,
) -> _ShortPressureContextFeature | None:
    if decisions is not None:
        decisions["ibmi_short_pressure"] = contextual_decision_input(
            None,
            IBMI_SHORT_PRESSURE_TO_CERI,
        )[1]
    settings = settings or get_settings()
    if not (
        getattr(settings, "ib_market_intelligence_enabled", False)
        and getattr(settings, "ib_short_pressure_enabled", False)
    ):
        return None
    rows = (
        candidates
        if candidates is not None
        else _scalars(
            db,
            select(IBIntelligenceFeature)
            .options(selectinload(IBIntelligenceFeature.calculation_evidence))
            .where(IBIntelligenceFeature.ticker == ticker.upper())
            .where(IBIntelligenceFeature.module == "SHORT_PRESSURE")
            .where(IBIntelligenceFeature.calculated_at <= cutoff_at)
            .where(
                IBIntelligenceFeature.as_of_session
                <= MarketClockService()
                .cutoff_for(cutoff_at, reason="CERI_IB_FEATURE_SELECTION")
                .latest_completed_session
            )
            .order_by(
                IBIntelligenceFeature.as_of_session.desc(),
                IBIntelligenceFeature.calculated_at.desc(),
            ),
        )
    )
    ibmi_config = ibmi_config or load_ib_market_intelligence_config()
    session = (
        as_of_session
        or MarketClockService()
        .cutoff_for(cutoff_at, reason="CERI_IB_FEATURE_IDENTITY")
        .latest_completed_session
    )
    market_cutoff = market_cutoff or MarketClockService().cutoff_for(
        cutoff_at, reason="CERI_IB_FEATURE_IDENTITY"
    )
    base = consumer_context_identity(
        market_cutoff=market_cutoff,
        run_id=None,
        pipeline_id=None,
        ticker=ticker,
        globally_reusable=True,
    )
    expected = expected_ibmi_identity(
        context=base,
        ticker=ticker,
        config_hash=ibmi_config.config_hash,
        calculation_version=ibmi_config.calculation_version,
        source_version=ibmi_config.source_version,
        module="short_pressure",
        effective_configuration=ibmi_config._effective_configurations["short_pressure"],
    )
    for row in rows:
        if row.ticker.upper() != ticker.upper() or row.module != "SHORT_PRESSURE":
            continue
        if row.as_of_session != session:
            continue
        identity = build_ibmi_feature_identity(row)
        candidate_expected = expected
        if row.evidence_id is None:
            candidate_expected = expected_ibmi_identity(
                context=base,
                ticker=ticker,
                config_hash=ibmi_config.config_hash,
                calculation_version=ibmi_config.calculation_version,
                source_version=ibmi_config.source_version,
                module="short_pressure",
            )
        if ibmi_contextual_compatibility(
            expected=candidate_expected, actual=identity, policy=CERI_IBMI_COMPATIBILITY
        ).accepted:
            usable, permission = contextual_decision_input(row, IBMI_SHORT_PRESSURE_TO_CERI)
            if decisions is not None:
                decisions["ibmi_short_pressure"] = permission
            if usable is None:
                return None
            return _ShortPressureContextFeature(
                id=row.id,
                classification=usable.classification,
                source_identity=identity,
            )
    return None


def _preload_ibmi_context(db: Session, rows, cutoff_at: datetime, *, settings=None):
    settings = settings or get_settings()
    if not getattr(settings, "ib_market_intelligence_enabled", False) or not (
        getattr(settings, "ib_volatility_intelligence_enabled", False)
        or getattr(settings, "ib_short_pressure_enabled", False)
    ):
        return ()
    return tuple(
        _scalars(
            db,
            select(IBIntelligenceFeature)
            .options(
                selectinload(IBIntelligenceFeature.calculation_evidence),
            )
            .where(
                IBIntelligenceFeature.ticker.in_({str(row.ticker).upper() for row in rows}),
                IBIntelligenceFeature.module.in_(("VOLATILITY", "SHORT_PRESSURE")),
                IBIntelligenceFeature.calculated_at <= cutoff_at,
            )
            .order_by(
                IBIntelligenceFeature.as_of_session.desc(),
                IBIntelligenceFeature.calculated_at.desc(),
            ),
        )
    )


def _source_ids(features: list[CeriRevisionFeature]) -> list[int]:
    ids: set[int] = set()
    for feature in features:
        ids.update(feature.source_observation_ids_json or [])
    return sorted(ids)


def _provider_feed_freshness_days(
    runs: list[CeriIngestionRun],
    *,
    ticker: str,
    cutoff_at: datetime,
    config,
) -> dict[str, int | None]:
    if not hasattr(config, "datasets") or not hasattr(config.engine, "timezone"):
        # Lightweight test/dry-run snapshot adapters may intentionally expose
        # only version identity.  In that case freshness is unavailable rather
        # than fabricated.
        return {}
    states = ticker_feed_freshness_from_runs(
        runs,
        ticker=ticker,
        cutoff_at=cutoff_at,
        max_stale_days={
            dataset.value: policy.max_stale_days for dataset, policy in config.datasets.items()
        },
        timezone_name=config.engine.timezone,
    )
    return {dataset: state.age_days for dataset, state in states.items()}


def _provider_checks_for_tickers(
    db: Session,
    tickers: set[str],
    cutoff_at: datetime,
) -> dict[str, list[CeriIngestionRun]]:
    if not tickers:
        return {}
    ticker_expression = func.upper(CeriIngestionRun.scope_json["ticker"].astext)
    runs = _scalars(
        db,
        select(CeriIngestionRun).where(
            CeriIngestionRun.status == "COMPLETED",
            CeriIngestionRun.completed_at.is_not(None),
            CeriIngestionRun.completed_at <= cutoff_at,
            ticker_expression.in_(sorted(tickers)),
        ),
    )
    grouped: dict[str, list[CeriIngestionRun]] = {}
    for run in runs:
        ticker = str((run.scope_json or {}).get("ticker") or "").upper()
        grouped.setdefault(ticker, []).append(run)
    return grouped


def _quarantined_count(db: Session) -> int:
    return len([row for row in getattr(db, "added", []) if getattr(row, "quarantine_reason", None)])


def _is_conflict_warning(warnings: list[str] | None) -> bool:
    return any("conflict" in str(value).lower() for value in (warnings or []))


def _maybe_scalar(db: Session, statement):
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    return None


_LARGE_CERI_TABLES = frozenset(
    {
        "ceri_source_records",
        "ceri_estimate_snapshots",
        "ceri_earnings_actuals",
        "ceri_guidance_events",
        "ceri_catalyst_events",
        "ceri_catalyst_event_revisions",
        "ceri_catalyst_sources",
        "ceri_revision_features",
        "ceri_derived_features",
        "ceri_price_response_features",
        "ceri_score_snapshots",
        "ceri_change_events",
        "ceri_alert_events",
    }
)


def _scalars(db: Session, statement):
    if isinstance(db, Session):
        descriptions = getattr(statement, "column_descriptions", ())
        entity = descriptions[0].get("entity") if descriptions else None
        table = getattr(entity, "__tablename__", None)
        if table in _LARGE_CERI_TABLES and not getattr(statement, "_where_criteria", ()):
            raise ValueError(f"CERI_CAPTURE_UNSCOPED_READ_FORBIDDEN:{table}")
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _utcnow() -> datetime:
    return datetime.now(UTC)
