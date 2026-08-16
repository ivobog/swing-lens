from __future__ import annotations

from collections.abc import Iterable
from contextlib import nullcontext
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from time import perf_counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, load_only

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriEstimateSnapshot,
    CeriFeatureBuildState,
    CeriGuidanceEvent,
    CeriPriceResponseFeature,
    CeriRevisionFeature,
    CeriSourceRecord,
)
from app.models.tables import PriceBar, RawCompanyRow
from app.services.ceri.capability_matrix_service import CeriCapabilityMatrixService
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.config import CeriConfig, load_ceri_config
from app.services.ceri.enums import HistoricalViewMode
from app.services.ceri.point_in_time_query import CeriPointInTimeQuery
from app.services.ceri.price_response_service import CeriPriceResponseService
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService
from app.services.ceri.surprise_feature_service import CeriSurpriseFeatureService

FEATURE_REBUILD_IMPL_VERSION = "batch-prefetch-v1"


@dataclass(frozen=True)
class CeriFeatureRebuildRequest:
    company_ids: tuple[int, ...] | None = None
    ticker: str | None = None
    tickers: tuple[str, ...] | None = None
    as_of_session: date | None = None
    from_session: date | None = None
    to_session: date | None = None
    run_id: int | None = None
    mode: str = "AS_KNOWN"


@dataclass(frozen=True)
class CeriFeatureRebuildResult:
    features: int = 0
    features_inserted: int = 0
    features_updated: int = 0
    features_deduplicated: int = 0
    earnings_updated: int = 0
    processed_companies: int = 0
    companies_rebuilt: int = 0
    companies_skipped_unchanged: int = 0
    warnings: int = 0
    failed: int = 0
    errors: tuple[dict[str, Any], ...] = ()
    short_circuited_families: tuple[str, ...] = ()
    family_runtime_ms: dict[str, int] | None = None
    family_query_counts: dict[str, int] | None = None
    batch_total_ms: int = 0
    load_context_ms: int = 0
    persistence_ms: int = 0
    sql_select_count: int = 0
    sql_write_count: int = 0
    rows_loaded: dict[str, int] | None = None
    feature_rebuild_impl_version: str = FEATURE_REBUILD_IMPL_VERSION

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["feature_count"] = self.features
        result["warnings"] = self.warnings
        result["errors"] = list(self.errors)
        return result


@dataclass(frozen=True)
class RevisionFeatureKey:
    company_id: int
    metric: str
    period_key: str
    as_of_session: date
    window_days: int
    config_hash: str
    calculation_version: str


@dataclass(frozen=True)
class DerivedFeatureKey:
    company_id: int
    feature_family: str
    feature_key: str
    as_of_session: date
    config_hash: str
    calculation_version: str


@dataclass(frozen=True)
class BuildStateKey:
    company_id: int
    as_of_session: date
    historical_view_mode: str
    config_hash: str
    calculation_version: str


@dataclass
class CeriFeatureBatchContext:
    companies: list[CeriCompany]
    cutoff: date
    cutoff_at: datetime
    mode: HistoricalViewMode
    estimates_by_company: dict[int, list[CeriEstimateSnapshot]]
    earnings_by_company: dict[int, list[CeriEarningsActual]]
    guidance_by_company: dict[int, list[CeriGuidanceEvent]]
    catalyst_events_by_company: dict[int, list[CeriCatalystEvent]]
    catalyst_revisions_by_event: dict[int, list[CeriCatalystEventRevision]]
    source_records_by_id: dict[int, CeriSourceRecord]
    existing_revision_features: dict[RevisionFeatureKey, CeriRevisionFeature]
    existing_derived_features: dict[DerivedFeatureKey, CeriDerivedFeature]
    existing_price_features_by_company: dict[int, list[CeriPriceResponseFeature]]
    feature_build_state: dict[BuildStateKey, CeriFeatureBuildState]
    bars_by_ticker: dict[str, list[PriceBar]]
    capabilities: dict[int, Any]
    point_in_time_query: CeriPointInTimeQuery
    load_context_ms: int
    select_count: int
    rows_loaded: dict[str, int]
    write_count: int = 0


class CeriFeatureRebuildService:
    """Batch-prefetched, deterministic CERI feature rebuild engine."""

    def __init__(self, *, config: CeriConfig | None = None,
                 revisions: CeriRevisionFeatureService | None = None,
                 surprise: CeriSurpriseFeatureService | None = None,
                 catalysts: CeriCatalystFeatureService | None = None,
                 confidence: CeriConfidenceService | None = None,
                 price_response: CeriPriceResponseService | None = None) -> None:
        self.config = config or load_ceri_config()
        self.revisions = revisions or CeriRevisionFeatureService(config=self.config)
        self.surprise = surprise or CeriSurpriseFeatureService(config=self.config)
        self.catalysts = catalysts or CeriCatalystFeatureService(config=self.config)
        self.confidence = confidence or CeriConfidenceService(config=self.config)
        self.price_response = price_response or CeriPriceResponseService(config=self.config)

    def prepare_batch(self, db: Session,
                      request: CeriFeatureRebuildRequest) -> CeriFeatureBatchContext:
        started = perf_counter()
        select_count = 0
        rows_loaded: dict[str, int] = {}
        companies = self._companies(db, request)
        select_count += 1 + int(request.run_id is not None)
        rows_loaded["companies"] = len(companies)
        company_ids = [company.id for company in companies]
        cutoff = request.as_of_session or request.to_session or date.today()
        cutoff_at = datetime.combine(cutoff, time(23, 59, 59), tzinfo=UTC)
        try:
            mode = HistoricalViewMode(request.mode)
        except ValueError:
            mode = HistoricalViewMode.AS_KNOWN

        def load(model: Any, statement: Any) -> list[Any]:
            nonlocal select_count
            select_count += 1
            rows = _scalars(db, statement)
            rows_loaded[model.__tablename__] = len(rows)
            return rows

        if not company_ids:
            pit = CeriPointInTimeQuery(config=self.config, snapshots=[], source_records={})
            return CeriFeatureBatchContext(
                [], cutoff, cutoff_at, mode, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, {}, pit,
                int((perf_counter() - started) * 1000), select_count, rows_loaded,
            )

        estimates = load(CeriEstimateSnapshot, select(CeriEstimateSnapshot).where(
            CeriEstimateSnapshot.company_id.in_(company_ids)))
        earnings = load(CeriEarningsActual, select(CeriEarningsActual).where(
            CeriEarningsActual.company_id.in_(company_ids)))
        guidance = load(CeriGuidanceEvent, select(CeriGuidanceEvent).where(
            CeriGuidanceEvent.company_id.in_(company_ids)))
        events = load(CeriCatalystEvent, select(CeriCatalystEvent).where(
            CeriCatalystEvent.company_id.in_(company_ids)))
        event_ids = [row.id for row in events if row.id is not None]
        revisions = load(
            CeriCatalystEventRevision,
            select(CeriCatalystEventRevision).where(
                CeriCatalystEventRevision.catalyst_event_id.in_(event_ids)),
        ) if event_ids else []
        rows_loaded.setdefault(CeriCatalystEventRevision.__tablename__, 0)

        source_ids = {
            source_id for row in [*estimates, *earnings, *guidance, *revisions]
            for source_id in (getattr(row, "source_record_id", None),
                              getattr(row, "conversion_source_record_id", None))
            if source_id is not None
        }
        sources = load(
            CeriSourceRecord,
            select(CeriSourceRecord)
            .options(load_only(
                CeriSourceRecord.id,
                CeriSourceRecord.provider,
                CeriSourceRecord.dataset,
                CeriSourceRecord.provider_record_id,
                CeriSourceRecord.published_at,
                CeriSourceRecord.observed_at,
                CeriSourceRecord.source_timestamp,
                CeriSourceRecord.retrieved_at,
                CeriSourceRecord.content_hash,
                CeriSourceRecord.normalized_hash,
                CeriSourceRecord.idempotency_key,
                CeriSourceRecord.supersedes_id,
            ))
            .where(CeriSourceRecord.id.in_(source_ids)),
        ) if source_ids else []
        rows_loaded.setdefault(CeriSourceRecord.__tablename__, 0)
        revision_features = load(CeriRevisionFeature, select(CeriRevisionFeature).where(
            CeriRevisionFeature.company_id.in_(company_ids),
            CeriRevisionFeature.as_of_session == cutoff,
            CeriRevisionFeature.config_hash == self.config.config_hash,
            CeriRevisionFeature.calculation_version == self.config.engine.calculation_version,
        ))
        derived_features = load(CeriDerivedFeature, select(CeriDerivedFeature).where(
            CeriDerivedFeature.company_id.in_(company_ids),
            CeriDerivedFeature.as_of_session == cutoff,
            CeriDerivedFeature.config_hash == self.config.config_hash,
            CeriDerivedFeature.calculation_version == self.config.engine.calculation_version,
        ))
        price_features = load(CeriPriceResponseFeature, select(CeriPriceResponseFeature).where(
            CeriPriceResponseFeature.company_id.in_(company_ids),
            CeriPriceResponseFeature.config_hash == self.config.config_hash,
            CeriPriceResponseFeature.calculation_version == self.config.engine.calculation_version,
        ))
        states = load(CeriFeatureBuildState, select(CeriFeatureBuildState).where(
            CeriFeatureBuildState.company_id.in_(company_ids),
            CeriFeatureBuildState.as_of_session == cutoff,
            CeriFeatureBuildState.historical_view_mode == mode.value,
            CeriFeatureBuildState.config_hash == self.config.config_hash,
            CeriFeatureBuildState.calculation_version == self.config.engine.calculation_version,
        ))
        requested_tickers = {company.ticker.upper() for company in companies}
        requested_tickers.add(self.config.price_response.benchmark.upper())
        bars = load(PriceBar, select(PriceBar)
                    .where(PriceBar.ticker.in_(requested_tickers))
                    .where(func.lower(PriceBar.timeframe).in_(("1d", "1 day", "day", "daily")))
                    .where(func.lower(PriceBar.source).in_(("ib", "ibkr", "interactive_brokers")))
                    .where(PriceBar.close.is_not(None))
                    .order_by(PriceBar.ticker, PriceBar.bar_date))
        bars = [row for row in bars if row.ticker.upper() in requested_tickers
                and row.timeframe.lower() in {"1d", "1 day", "day", "daily"}
                and row.source.lower() in {"ib", "ibkr", "interactive_brokers"}
                and row.close is not None]
        rows_loaded[PriceBar.__tablename__] = len(bars)

        estimates_by_company = _group_by(estimates, "company_id")
        earnings_by_company = _group_by(earnings, "company_id")
        guidance_by_company = _group_by(guidance, "company_id")
        events_by_company = _group_by(events, "company_id")
        revisions_by_event = _group_by(revisions, "catalyst_event_id")
        sources_by_id = {row.id: row for row in sources if row.id is not None}
        bars_by_ticker: dict[str, list[PriceBar]] = {}
        for bar in bars:
            bars_by_ticker.setdefault(bar.ticker.upper(), []).append(bar)
        for ticker_bars in bars_by_ticker.values():
            ticker_bars.sort(key=lambda row: row.bar_date)

        slots: dict[int, set[tuple[str, str]]] = {}
        for row in estimates:
            slot = row.canonical_period_slot or row.period_type
            if row.company_id is not None and row.metric and slot:
                slots.setdefault(row.company_id, set()).add((row.metric, slot))
        eligible_event_ids = {row.catalyst_event_id for row in revisions
                              if row.is_current and row.issuer_relevance is True}
        capabilities = CeriCapabilityMatrixService().build(
            company_ids=company_ids,
            estimates_by_company=slots,
            earnings_company_ids={row.company_id for row in earnings
                                  if row.actual_value is not None
                                  and row.event_kind in (None, "REPORTED")},
            guidance_company_ids={row.company_id for row in guidance
                                  if row.accepted_for_scoring is True},
            catalyst_company_ids={row.company_id for row in events
                                  if row.id in eligible_event_ids},
        )
        pit = CeriPointInTimeQuery(config=self.config, snapshots=estimates,
                                   source_records=sources_by_id)
        return CeriFeatureBatchContext(
            companies, cutoff, cutoff_at, mode, estimates_by_company,
            earnings_by_company, guidance_by_company, events_by_company,
            revisions_by_event, sources_by_id,
            {_revision_key(row): row for row in revision_features},
            {_derived_key(row): row for row in derived_features},
            _group_by(price_features, "company_id"),
            {_state_key(row): row for row in states}, bars_by_ticker, capabilities, pit,
            int((perf_counter() - started) * 1000), select_count, rows_loaded,
        )

    def rebuild(self, db: Session, request: CeriFeatureRebuildRequest, *,
                processing_run: Any | None = None,
                batch_context: CeriFeatureBatchContext | None = None) -> CeriFeatureRebuildResult:
        own_context = batch_context is None
        context = batch_context or self.prepare_batch(db, request)
        results = [self.rebuild_from_context(
            db, company=company, request=request, context=context,
            processing_run=processing_run,
        ) for company in self._context_companies(context, request)]
        merged = _merge_results(results)
        if own_context:
            return _replace_result(
                merged, load_context_ms=context.load_context_ms,
                sql_select_count=context.select_count, rows_loaded=dict(context.rows_loaded),
                family_query_counts={
                    "estimates": 1,
                    "earnings": 1,
                    "guidance": 1,
                    "catalyst_events": 1,
                    "catalyst_revisions": int(
                        "ceri_catalyst_event_revisions" in context.rows_loaded
                    ),
                    "price_bars": 1,
                },
                batch_total_ms=merged.batch_total_ms + context.load_context_ms,
            )
        return merged

    def rebuild_from_context(self, db: Session, *, company: CeriCompany,
                             request: CeriFeatureRebuildRequest,
                             context: CeriFeatureBatchContext,
                             processing_run: Any | None = None) -> CeriFeatureRebuildResult:
        started = perf_counter()
        input_hash = self._input_fingerprint(company, context, request)
        key = BuildStateKey(company.id, context.cutoff, context.mode.value,
                            self.config.config_hash,
                            self.config.engine.calculation_version)
        state = context.feature_build_state.get(key)
        output_hash, output_count = self._existing_output_fingerprint(company.id, context)
        if (state is not None and state.input_evidence_hash == input_hash
                and state.output_evidence_hash == output_hash
                and state.output_feature_count == output_count):
            return CeriFeatureRebuildResult(
                processed_companies=1, companies_skipped_unchanged=1,
                batch_total_ms=int((perf_counter() - started) * 1000))
        nested = getattr(db, "begin_nested", None)
        savepoint = nested() if callable(nested) else nullcontext()
        try:
            with savepoint:
                return self._rebuild_company(
                    db, company=company, request=request, context=context,
                    processing_run=processing_run, input_hash=input_hash, started=started)
        except Exception as exc:
            return CeriFeatureRebuildResult(
                processed_companies=1, failed=1,
                errors=({"company_id": company.id, "error": _safe_error(exc)},),
                batch_total_ms=int((perf_counter() - started) * 1000))

    def _rebuild_company(self, db: Session, *, company: CeriCompany,
                         request: CeriFeatureRebuildRequest,
                         context: CeriFeatureBatchContext, processing_run: Any | None,
                         input_hash: str, started: float) -> CeriFeatureRebuildResult:
        timings = {name: 0 for name in (
            "revisions", "earnings_surprise", "guidance", "catalysts",
            "confidence", "price_response")}
        short_circuited: set[str] = set()
        capability = context.capabilities[company.id]
        revision_rows: list[CeriRevisionFeature] = []
        family_started = perf_counter()
        revision_service = (CeriRevisionFeatureService(
            config=self.config, query=context.point_in_time_query)
            if isinstance(self.revisions, CeriRevisionFeatureService) else self.revisions)
        if not capability.revision_slots:
            short_circuited.add("revisions")
        for metric in (metric.value for metric in self.config.metrics.required):
            for period_slot in self.config.metrics.period_types:
                if (metric, period_slot.value) not in capability.revision_slots:
                    continue
                calculated = revision_service.calculate_windows(
                    db, company_id=company.id, metric=metric,
                    period_slot=period_slot.value, cutoff_at=context.cutoff_at,
                    mode=context.mode)
                self._add_acceleration(calculated, service=revision_service)
                revision_rows.extend(row for row in calculated
                                     if not request.from_session
                                     or row.as_of_session >= request.from_session)
        timings["revisions"] = int((perf_counter() - family_started) * 1000)
        warnings = sum(len(row.warnings_json or []) for row in revision_rows)

        derived_rows: list[CeriDerivedFeature] = []
        estimates = context.estimates_by_company.get(company.id, [])
        earnings = [row for row in context.earnings_by_company.get(company.id, [])
                    if row.report_at is None or row.report_at <= context.cutoff_at]
        family_started = perf_counter()
        earnings_updated = 0
        if capability.earnings_surprise:
            summary = self.surprise.summarize(earnings, estimates)
            earnings_updated = sum(row.consensus_snapshot_id is not None for row in earnings)
            derived_rows.append(self._derived_row(
                company_id=company.id, family="earnings_surprise", key="latest",
                as_of_session=context.cutoff,
                value={"features": [{
                    "earnings_actual_id": item.earnings_actual_id,
                    "consensus_snapshot_id": item.consensus_snapshot_id,
                    "surprise_absolute": str(item.surprise_absolute)
                    if item.surprise_absolute is not None else None,
                    "surprise_pct": str(item.surprise_pct)
                    if item.surprise_pct is not None else None,
                    "direction": item.direction, "warnings": item.warnings,
                } for item in summary.features]},
                source_ids=[row.source_record_id for row in earnings
                            if row.source_record_id is not None]))
        else:
            short_circuited.add("earnings_surprise")
        timings["earnings_surprise"] = int((perf_counter() - family_started) * 1000)

        family_started = perf_counter()
        guidance_rows = [row for row in context.guidance_by_company.get(company.id, [])
                         if row.accepted_for_scoring is True
                         and (row.effective_session is None
                              or row.effective_session <= context.cutoff)
                         and (not request.from_session
                              or row.effective_session is None
                              or row.effective_session >= request.from_session)]
        if guidance_rows:
            latest = max(guidance_rows,
                         key=lambda row: (row.effective_session or date.min, row.id or 0))
            derived_rows.append(self._derived_row(
                company_id=company.id, family="guidance", key="latest",
                as_of_session=context.cutoff,
                value={"guidance_id": latest.id, "action": latest.action,
                       "confidence": latest.confidence, "metric": latest.metric,
                       "period_type": latest.period_type,
                       "low_value": str(latest.low_value)
                       if latest.low_value is not None else None,
                       "high_value": str(latest.high_value)
                       if latest.high_value is not None else None,
                       "point_value": str(latest.point_value)
                       if latest.point_value is not None else None},
                source_ids=[row.source_record_id for row in guidance_rows]))
        else:
            short_circuited.add("guidance")
        timings["guidance"] = int((perf_counter() - family_started) * 1000)

        family_started = perf_counter()
        company_events = context.catalyst_events_by_company.get(company.id, [])
        current_catalysts = _current_catalysts(
            db, company.id, context.cutoff, events=company_events,
            revisions=[revision for event in company_events
                       for revision in context.catalyst_revisions_by_event.get(event.id, [])])
        catalyst_values: list[dict[str, Any]] = []
        catalyst_source_ids: list[int] = []
        for event, revision in current_catalysts:
            catalyst_values.append(_json_safe(asdict(self.catalysts.calculate(
                event=event, revision=revision, as_of_session=context.cutoff))))
            if revision.source_record_id is not None:
                catalyst_source_ids.append(revision.source_record_id)
        if catalyst_values:
            derived_rows.append(self._derived_row(
                company_id=company.id, family="catalysts", key="current",
                as_of_session=context.cutoff, value={"items": catalyst_values},
                source_ids=catalyst_source_ids))
        else:
            short_circuited.add("catalysts")
        timings["catalysts"] = int((perf_counter() - family_started) * 1000)

        family_started = perf_counter()
        confidence = self.confidence.calculate(
            as_of_session=context.cutoff, revision_features=revision_rows)
        derived_rows.append(self._derived_row(
            company_id=company.id, family="confidence", key="score",
            as_of_session=context.cutoff, value=_json_safe(asdict(confidence)),
            source_ids=[source_id for feature in revision_rows
                        for source_id in (feature.source_observation_ids_json or [])]))
        timings["confidence"] = int((perf_counter() - family_started) * 1000)

        family_started = perf_counter()
        price_row = self._price_response_row(company, context, current_catalysts)
        timings["price_response"] = int((perf_counter() - family_started) * 1000)
        output_rows = self._prospective_output_rows(
            company.id, revision_rows, derived_rows, price_row, context
        )
        state_row = CeriFeatureBuildState(
            company_id=company.id, as_of_session=context.cutoff,
            historical_view_mode=context.mode.value,
            config_hash=self.config.config_hash,
            calculation_version=self.config.engine.calculation_version,
            input_evidence_hash=input_hash,
            output_evidence_hash=_output_fingerprint(output_rows),
            output_feature_count=len(output_rows),
            implementation_version=FEATURE_REBUILD_IMPL_VERSION,
            completed_at=datetime.now(UTC))
        inserted, updated, deduped = self._persistence_counts(
            revision_rows, derived_rows, price_row, context)
        persistence_started = perf_counter()
        writes = self._persist_company(
            db, revision_rows=revision_rows, derived_rows=derived_rows,
            price_row=price_row, state_row=state_row, context=context)
        persistence_ms = int((perf_counter() - persistence_started) * 1000)
        context.write_count += writes
        if processing_run is not None:
            processing_run.checkpoint_json = {
                "company_id": company.id, "as_of_session": context.cutoff.isoformat(),
                "input_evidence_hash": input_hash,
                "feature_rebuild_impl_version": FEATURE_REBUILD_IMPL_VERSION}
        return CeriFeatureRebuildResult(
            features=len(revision_rows), features_inserted=inserted,
            features_updated=updated, features_deduplicated=deduped,
            earnings_updated=earnings_updated, processed_companies=1,
            companies_rebuilt=1, warnings=warnings,
            short_circuited_families=tuple(sorted(short_circuited)),
            family_runtime_ms=timings,
            family_query_counts={name: 0 for name in (
                "estimates", "earnings", "guidance", "catalyst_events",
                "catalyst_revisions", "price_bars")},
            batch_total_ms=int((perf_counter() - started) * 1000),
            persistence_ms=persistence_ms, sql_write_count=writes)

    def _derived_row(self, *, company_id: int, family: str, key: str,
                     as_of_session: date, value: dict[str, Any],
                     source_ids: list[int]) -> CeriDerivedFeature:
        source_ids = sorted(set(source_ids))
        evidence = {"company_id": company_id, "family": family, "key": key,
                    "as_of_session": as_of_session.isoformat(), "value": value,
                    "source_ids": source_ids, "config_hash": self.config.config_hash,
                    "calculation_version": self.config.engine.calculation_version}
        return CeriDerivedFeature(
            company_id=company_id, feature_family=family, feature_key=key,
            as_of_session=as_of_session, value_json=value, source_ids_json=source_ids,
            evidence_hash=_stable_hash(evidence),
            config_version=self.config.engine.config_version,
            config_hash=self.config.config_hash,
            calculation_version=self.config.engine.calculation_version)

    def _price_response_row(self, company: CeriCompany,
                            context: CeriFeatureBatchContext,
                            catalysts: list[tuple[CeriCatalystEvent,
                                                  CeriCatalystEventRevision]],
                            ) -> CeriPriceResponseFeature:
        event = _latest_price_event(
            None, company.id, context.cutoff,
            earnings=context.earnings_by_company.get(company.id, []),
            guidance=context.guidance_by_company.get(company.id, []),
            current_catalysts=catalysts)
        if event is None:
            result = self.price_response.unavailable(
                company_id=company.id, event_type="NONE", reason="NO_ACCEPTED_EVENT")
            event = ("NONE", None, None, None)
        else:
            result = self.price_response.calculate(
                None, company_id=company.id, ticker=company.ticker,
                event_type=event[0], event_id=event[1],
                event_effective_at=event[2], event_effective_session=event[3],
                stock_bars=context.bars_by_ticker.get(company.ticker.upper(), []),
                benchmark_bars=context.bars_by_ticker.get(
                    self.config.price_response.benchmark.upper(), []))
        return self.price_response.build_feature(
            result=result, company_id=company.id, ticker=company.ticker,
            event_id=event[1], event_effective_at=event[2],
            event_effective_session=event[3])

    def _persist_company(self, db: Session, *,
                         revision_rows: list[CeriRevisionFeature],
                         derived_rows: list[CeriDerivedFeature],
                         price_row: CeriPriceResponseFeature,
                         state_row: CeriFeatureBuildState,
                         context: CeriFeatureBatchContext) -> int:
        if _is_postgresql(db):
            writes = 0
            if revision_rows:
                _execute_upsert(db, CeriRevisionFeature, revision_rows,
                                "uq_ceri_revision_features_identity",
                                _REVISION_UPDATE_COLUMNS)
                writes += 1
            if derived_rows:
                _execute_upsert(db, CeriDerivedFeature, derived_rows,
                                "uq_ceri_derived_features_identity",
                                ("value_json", "source_ids_json", "evidence_hash",
                                 "config_version"))
                writes += 1
            _execute_upsert(db, CeriPriceResponseFeature, [price_row],
                            "uq_ceri_price_response_event_key", _PRICE_UPDATE_COLUMNS)
            writes += 1
            _execute_upsert(db, CeriFeatureBuildState, [state_row],
                            "uq_ceri_feature_build_states_identity",
                            ("input_evidence_hash", "output_evidence_hash",
                             "output_feature_count", "implementation_version",
                             "completed_at"))
            db.flush()
            return writes
        for row in revision_rows:
            existing = context.existing_revision_features.get(_revision_key(row))
            db.add(row) if existing is None else _copy_revision_derived(existing, row)
        for row in derived_rows:
            existing = context.existing_derived_features.get(_derived_key(row))
            db.add(row) if existing is None else _copy_derived(existing, row)
        existing_price = next((row for row in
                               context.existing_price_features_by_company.get(
                                   price_row.company_id, [])
                               if row.event_key == price_row.event_key), None)
        db.add(price_row) if existing_price is None else _copy_price(existing_price, price_row)
        existing_state = context.feature_build_state.get(_state_key(state_row))
        db.add(state_row) if existing_state is None else _copy_state(existing_state, state_row)
        db.flush()
        return 1

    def _persistence_counts(self, revision_rows: list[CeriRevisionFeature],
                            derived_rows: list[CeriDerivedFeature],
                            price_row: CeriPriceResponseFeature,
                            context: CeriFeatureBatchContext) -> tuple[int, int, int]:
        inserted = updated = deduped = 0
        pairs = [(row, context.existing_revision_features.get(_revision_key(row)))
                 for row in revision_rows]
        pairs.extend((row, context.existing_derived_features.get(_derived_key(row)))
                     for row in derived_rows)
        price_existing = next((row for row in
                               context.existing_price_features_by_company.get(
                                   price_row.company_id, [])
                               if row.event_key == price_row.event_key), None)
        pairs.append((price_row, price_existing))
        for row, existing in pairs:
            if existing is None:
                inserted += 1
            elif existing.evidence_hash == row.evidence_hash:
                deduped += 1
            else:
                updated += 1
        return inserted, updated, deduped

    def _input_fingerprint(self, company: CeriCompany,
                           context: CeriFeatureBatchContext,
                           request: CeriFeatureRebuildRequest) -> str:
        rows: list[Any] = [company]
        rows.extend(context.estimates_by_company.get(company.id, []))
        rows.extend(context.earnings_by_company.get(company.id, []))
        rows.extend(context.guidance_by_company.get(company.id, []))
        events = context.catalyst_events_by_company.get(company.id, [])
        rows.extend(events)
        rows.extend(revision for event in events
                    for revision in context.catalyst_revisions_by_event.get(event.id, []))
        source_ids = {source_id for row in rows
                      for source_id in (getattr(row, "source_record_id", None),
                                        getattr(row, "conversion_source_record_id", None))
                      if source_id is not None}
        rows.extend(context.source_records_by_id[source_id]
                    for source_id in sorted(source_ids)
                    if source_id in context.source_records_by_id)
        rows.extend(context.bars_by_ticker.get(company.ticker.upper(), []))
        rows.extend(context.bars_by_ticker.get(
            self.config.price_response.benchmark.upper(), []))
        return _stable_hash({
            "company_id": company.id, "as_of_session": context.cutoff.isoformat(),
            "from_session": request.from_session.isoformat()
            if request.from_session else None,
            "to_session": request.to_session.isoformat() if request.to_session else None,
            "historical_view_mode": context.mode.value,
            "config_hash": self.config.config_hash,
            "calculation_version": self.config.engine.calculation_version,
            "evidence": sorted((_row_fingerprint(row) for row in rows), key=str)})

    def _existing_output_fingerprint(self, company_id: int,
                                     context: CeriFeatureBatchContext) -> tuple[str, int]:
        rows: list[Any] = [row for key, row in context.existing_revision_features.items()
                           if key.company_id == company_id]
        rows.extend(row for key, row in context.existing_derived_features.items()
                    if key.company_id == company_id)
        rows.extend(context.existing_price_features_by_company.get(company_id, []))
        return _output_fingerprint(rows), len(rows)

    def _prospective_output_rows(
        self,
        company_id: int,
        revision_rows: list[CeriRevisionFeature],
        derived_rows: list[CeriDerivedFeature],
        price_row: CeriPriceResponseFeature,
        context: CeriFeatureBatchContext,
    ) -> list[Any]:
        revisions = {
            key: row for key, row in context.existing_revision_features.items()
            if key.company_id == company_id
        }
        revisions.update({_revision_key(row): row for row in revision_rows})
        derived = {
            key: row for key, row in context.existing_derived_features.items()
            if key.company_id == company_id
        }
        derived.update({_derived_key(row): row for row in derived_rows})
        prices = {
            row.event_key: row
            for row in context.existing_price_features_by_company.get(company_id, [])
        }
        prices[price_row.event_key] = price_row
        return [*revisions.values(), *derived.values(), *prices.values()]

    def _context_companies(self, context: CeriFeatureBatchContext,
                           request: CeriFeatureRebuildRequest) -> list[CeriCompany]:
        ids = set(request.company_ids or ())
        tickers = {ticker.upper() for ticker in (request.tickers or ())}
        if request.ticker:
            tickers.add(request.ticker.upper())
        return [company for company in context.companies
                if (not ids or company.id in ids)
                and (not tickers or company.ticker.upper() in tickers)]

    def _companies(self, db: Session,
                   request: CeriFeatureRebuildRequest) -> list[CeriCompany]:
        ids = set(request.company_ids or ())
        tickers = {ticker.upper() for ticker in (request.tickers or ())}
        if request.ticker:
            tickers.add(request.ticker.upper())
        if request.run_id is not None:
            run_tickers = {row.ticker.upper() for row in _scalars(
                db, select(RawCompanyRow).where(RawCompanyRow.run_id == request.run_id))}
            tickers = tickers & run_tickers if tickers else run_tickers
        statement = select(CeriCompany)
        if ids:
            statement = statement.where(CeriCompany.id.in_(ids))
        if tickers:
            statement = statement.where(func.upper(CeriCompany.ticker).in_(tickers))
        companies = _scalars(db, statement)
        companies = [company for company in companies
                     if (not ids or company.id in ids)
                     and (not tickers or company.ticker.upper() in tickers)]
        return sorted(companies, key=lambda row: (row.ticker.upper(), row.id or 0))

    def _upsert_derived(self, db: Session, *, company_id: int, family: str,
                        key: str, as_of_session: date, value: dict[str, Any],
                        source_ids: list[int]) -> CeriDerivedFeature:
        row = self._derived_row(company_id=company_id, family=family, key=key,
                                as_of_session=as_of_session, value=value,
                                source_ids=source_ids)
        existing = _maybe_scalar(db, select(CeriDerivedFeature).where(
            CeriDerivedFeature.company_id == company_id,
            CeriDerivedFeature.feature_family == family,
            CeriDerivedFeature.feature_key == key,
            CeriDerivedFeature.as_of_session == as_of_session,
            CeriDerivedFeature.config_hash == self.config.config_hash,
            CeriDerivedFeature.calculation_version == self.config.engine.calculation_version))
        if existing is None:
            db.add(row)
            existing = row
        else:
            if existing.evidence_hash == row.evidence_hash:
                return existing
            _copy_derived(existing, row)
        db.flush()
        return existing

    def _existing_feature(self, db: Session,
                          feature: CeriRevisionFeature) -> CeriRevisionFeature | None:
        return _maybe_scalar(db, select(CeriRevisionFeature).where(
            CeriRevisionFeature.company_id == feature.company_id,
            CeriRevisionFeature.metric == feature.metric,
            CeriRevisionFeature.period_key == feature.period_key,
            CeriRevisionFeature.as_of_session == feature.as_of_session,
            CeriRevisionFeature.window_days == feature.window_days,
            CeriRevisionFeature.config_hash == feature.config_hash,
            CeriRevisionFeature.calculation_version == feature.calculation_version))

    def _add_acceleration(self, calculated: list[CeriRevisionFeature], *,
                          service: Any | None = None) -> None:
        if len(calculated) < 2:
            return
        recent = min(calculated, key=lambda row: row.window_days)
        longer = max(calculated, key=lambda row: row.window_days)
        if recent is not longer:
            (service or self.revisions).with_acceleration(recent, longer)


_REVISION_UPDATE_COLUMNS = (
    "period_slot", "baseline_snapshot_id", "current_snapshot_id", "actual_elapsed_days",
    "absolute_change", "pct_change", "pct_change_unit", "upward_count", "downward_count",
    "net_breadth", "dispersion", "acceleration", "acceleration_unit", "baseline_origin",
    "comparison_mode", "current_source_record_id", "baseline_source_record_id",
    "provider_retrospective_source_record_id", "known_at", "reference_at",
    "revision_confidence_score", "revision_confidence_label", "warnings_json",
    "source_observation_ids_json", "provider_selection_reason", "unavailable_reason",
    "evidence_hash", "config_version")
_PRICE_UPDATE_COLUMNS = (
    "company_id", "ticker", "event_type", "event_id", "event_effective_at",
    "event_effective_session", "reaction_session", "benchmark", "metrics_json",
    "reasons_json", "warnings_json", "price_bar_ids_json", "evidence_hash",
    "config_version", "config_hash", "calculation_version")


def _execute_upsert(db: Session, model: Any, rows: list[Any], constraint: str,
                    update_columns: tuple[str, ...]) -> None:
    statement = pg_insert(model).values([_model_values(row) for row in rows])
    db.execute(statement.on_conflict_do_update(
        constraint=constraint,
        set_={name: getattr(statement.excluded, name) for name in update_columns}))


def _model_values(row: Any) -> dict[str, Any]:
    return {column.name: getattr(row, column.name) for column in row.__table__.columns
            if column.name not in {"id", "created_at"}}


def _is_postgresql(db: Session) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except (AttributeError, TypeError):
        return False


def _scalars(db: Session | None, statement: Any) -> list[Any]:
    scalars = getattr(db, "scalars", None)
    if not callable(scalars):
        return []
    result = scalars(statement)
    return list(result.all() if hasattr(result, "all") else result)


def _maybe_scalar(db: Session, statement: Any) -> Any | None:
    scalar = getattr(db, "scalar", None)
    if callable(scalar):
        return scalar(statement)
    rows = _scalars(db, statement)
    return rows[0] if rows else None


def _group_by(rows: Iterable[Any], attribute: str) -> dict[int, list[Any]]:
    grouped: dict[int, list[Any]] = {}
    for row in rows:
        grouped.setdefault(getattr(row, attribute), []).append(row)
    return grouped


def _revision_key(row: CeriRevisionFeature) -> RevisionFeatureKey:
    return RevisionFeatureKey(row.company_id, row.metric, row.period_key,
                              row.as_of_session, row.window_days,
                              row.config_hash, row.calculation_version)


def _derived_key(row: CeriDerivedFeature) -> DerivedFeatureKey:
    return DerivedFeatureKey(row.company_id, row.feature_family, row.feature_key,
                             row.as_of_session, row.config_hash,
                             row.calculation_version)


def _state_key(row: CeriFeatureBuildState) -> BuildStateKey:
    return BuildStateKey(row.company_id, row.as_of_session,
                         row.historical_view_mode, row.config_hash,
                         row.calculation_version)


def _row_fingerprint(row: Any) -> tuple[str, tuple[tuple[str, Any], ...]]:
    if isinstance(row, CeriSourceRecord):
        names = (
            "id", "provider", "dataset", "provider_record_id", "published_at",
            "observed_at", "source_timestamp", "retrieved_at", "content_hash",
            "normalized_hash", "idempotency_key", "supersedes_id",
        )
        return row.__tablename__, tuple(
            (name, _json_safe(getattr(row, name))) for name in names
        )
    return row.__tablename__, tuple(
        (column.name, _json_safe(getattr(row, column.name)))
        for column in row.__table__.columns
        if column.name not in {"created_at", "updated_at", "last_seen_at"})


def _output_fingerprint(rows: Iterable[Any]) -> str:
    return _stable_hash(sorted((
        row.__tablename__, getattr(row, "evidence_hash", None),
        getattr(row, "company_id", None), getattr(row, "event_key", None),
        getattr(row, "feature_family", None), getattr(row, "feature_key", None),
        getattr(row, "metric", None), getattr(row, "period_key", None),
        getattr(row, "window_days", None)) for row in rows))


def _copy_revision_derived(target: CeriRevisionFeature,
                           source: CeriRevisionFeature) -> None:
    for name in _REVISION_UPDATE_COLUMNS:
        setattr(target, name, getattr(source, name))


def _copy_derived(target: CeriDerivedFeature, source: CeriDerivedFeature) -> None:
    for name in ("value_json", "source_ids_json", "evidence_hash", "config_version"):
        setattr(target, name, getattr(source, name))


def _copy_price(target: CeriPriceResponseFeature,
                source: CeriPriceResponseFeature) -> None:
    for name in _PRICE_UPDATE_COLUMNS:
        setattr(target, name, getattr(source, name))


def _copy_state(target: CeriFeatureBuildState,
                source: CeriFeatureBuildState) -> None:
    for name in ("input_evidence_hash", "output_evidence_hash", "output_feature_count",
                 "implementation_version", "completed_at"):
        setattr(target, name, getattr(source, name))


def _current_catalysts(db: Session | None, company_id: int, cutoff: date, *,
                       events: list[CeriCatalystEvent] | None = None,
                       revisions: list[CeriCatalystEventRevision] | None = None
                       ) -> list[tuple[CeriCatalystEvent, CeriCatalystEventRevision]]:
    event_rows = events if events is not None else _scalars(db, select(CeriCatalystEvent))
    revision_rows = revisions if revisions is not None else _scalars(
        db, select(CeriCatalystEventRevision))
    event_map = {event.id: event for event in event_rows if event.company_id == company_id}
    return [(event_map[row.catalyst_event_id], row) for row in revision_rows
            if row.is_current and row.catalyst_event_id in event_map
            and (row.effective_session is None or row.effective_session <= cutoff)]


def _latest_price_event(db: Session | None, company_id: int, cutoff: date, *,
                        earnings: list[CeriEarningsActual] | None = None,
                        guidance: list[CeriGuidanceEvent] | None = None,
                        current_catalysts: list[tuple[CeriCatalystEvent,
                                                      CeriCatalystEventRevision]] | None = None
                        ) -> tuple[str, int | None, datetime | None, date | None] | None:
    candidates: list[tuple[str, int | None, datetime | None, date | None]] = []
    earnings_rows = earnings if earnings is not None else _scalars(db, select(CeriEarningsActual))
    reported = [row for row in earnings_rows if row.company_id == company_id
                and row.actual_value is not None and row.event_kind in (None, "REPORTED")
                and row.report_session is not None and row.report_session <= cutoff]
    if reported:
        row = max(reported, key=lambda item: (item.report_session, item.id or 0))
        candidates.append(("EARNINGS", row.id, row.report_at, row.report_session))
    guidance_rows = guidance if guidance is not None else _scalars(db, select(CeriGuidanceEvent))
    accepted = [row for row in guidance_rows if row.company_id == company_id
                and row.accepted_for_scoring is True
                and (row.effective_session is None or row.effective_session <= cutoff)]
    if accepted:
        row = max(accepted, key=lambda item: (item.effective_session or date.min,
                                              item.id or 0))
        candidates.append(("GUIDANCE", row.id, row.effective_at, row.effective_session))
    catalyst_rows = current_catalysts if current_catalysts is not None else _current_catalysts(
        db, company_id, cutoff)
    for _event, revision in catalyst_rows:
        if revision.issuer_relevance is not True or revision.review_state == "REJECTED":
            continue
        candidates.append(("CATALYST", revision.id, revision.announced_at,
                           revision.effective_session or revision.expected_date))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (
        item[3] or date.min, item[2] or datetime.min.replace(tzinfo=UTC), item[1] or 0))


def _merge_results(results: list[CeriFeatureRebuildResult]) -> CeriFeatureRebuildResult:
    if not results:
        return CeriFeatureRebuildResult()
    timings: dict[str, int] = {}
    queries: dict[str, int] = {}
    for result in results:
        for key, value in (result.family_runtime_ms or {}).items():
            timings[key] = timings.get(key, 0) + value
        for key, value in (result.family_query_counts or {}).items():
            queries[key] = queries.get(key, 0) + value
    return CeriFeatureRebuildResult(
        features=sum(row.features for row in results),
        features_inserted=sum(row.features_inserted for row in results),
        features_updated=sum(row.features_updated for row in results),
        features_deduplicated=sum(row.features_deduplicated for row in results),
        earnings_updated=sum(row.earnings_updated for row in results),
        processed_companies=sum(row.processed_companies for row in results),
        companies_rebuilt=sum(row.companies_rebuilt for row in results),
        companies_skipped_unchanged=sum(row.companies_skipped_unchanged for row in results),
        warnings=sum(row.warnings for row in results), failed=sum(row.failed for row in results),
        errors=tuple(error for row in results for error in row.errors),
        short_circuited_families=tuple(sorted({family for row in results
                                              for family in row.short_circuited_families})),
        family_runtime_ms=timings, family_query_counts=queries,
        batch_total_ms=sum(row.batch_total_ms for row in results),
        persistence_ms=sum(row.persistence_ms for row in results),
        sql_write_count=sum(row.sql_write_count for row in results))


def _replace_result(result: CeriFeatureRebuildResult,
                    **changes: Any) -> CeriFeatureRebuildResult:
    values = asdict(result)
    values.update(changes)
    return CeriFeatureRebuildResult(**values)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    return value


def _stable_hash(value: Any) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, default=str, separators=(",", ":")).encode()).hexdigest()


def _safe_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:500]
