from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import (
    IBHistogramBin,
    IBHistogramSnapshot,
    IBHistoricalMetricBar,
    IBIntelligenceRequestItem,
    IBIntelligenceRun,
    IBMarketIntelligenceSnapshot,
    IBScannerCandidate,
    IBScannerParameterCache,
    IBScannerRun,
)
from app.models.tables import BackgroundJob, PriceBar
from app.services.background_job_service import is_cancel_requested
from app.services.background_worker import CancelRequested
from app.services.ib_connection import create_ib_client
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_market_intelligence.adapters import (
    IBHistogramClient,
    IBHistoricalMetricClient,
    IBLiveSnapshotManager,
    IBScannerClient,
    capability_status_from_error,
)
from app.services.ib_market_intelligence.calculations import (
    calculate_histogram,
    calculate_liquidity,
    calculate_options_activity,
    calculate_short_pressure,
    calculate_volatility,
)
from app.services.ib_market_intelligence.config import (
    IBMarketIntelligenceConfig,
    ScannerPreset,
    load_ib_market_intelligence_config,
)
from app.services.ib_market_intelligence.enums import (
    AvailabilityStatus,
    HistoricalMetricType,
    IntelligenceModule,
    RunStatus,
)
from app.services.ib_market_intelligence.evidence_hash import evidence_hash
from app.services.ib_market_intelligence.flex import (
    flex_client_from_settings,
    import_flex_report,
)
from app.services.ib_market_intelligence.journal import (
    match_episode_to_research,
    rebuild_trade_episodes,
)
from app.services.ib_market_intelligence.repository import (
    persist_feature,
    persist_historical_metric_bar,
    persist_live_snapshot,
)
from app.services.ib_market_intelligence.request_budget import (
    IBRequestBudget,
    RequestBudgetConfig,
)
from app.services.ib_market_intelligence.resilience import (
    RetryEvent,
    RetryExhausted,
    RetryPolicy,
    retry_call,
)
from app.services.ib_market_intelligence.scanner_identity import (
    canonical_scanner_identity,
    scanner_conids_by_ticker,
)
from app.services.operational_metrics import operational_metrics
from app.settings import Settings, get_settings

HISTORICAL_MODULE_METRICS = {
    IntelligenceModule.LIQUIDITY: (HistoricalMetricType.BID_ASK,),
    IntelligenceModule.SHORT_PRESSURE: (HistoricalMetricType.FEE_RATE,),
    IntelligenceModule.VOLATILITY: (
        HistoricalMetricType.HISTORICAL_VOLATILITY,
        HistoricalMetricType.OPTION_IMPLIED_VOLATILITY,
    ),
}


@lru_cache(maxsize=1)
def shared_request_budget() -> IBRequestBudget:
    settings = get_settings()
    return IBRequestBudget(
        RequestBudgetConfig(
            historical_weighted_tokens_per_minute=settings.ib_intelligence_historical_requests_per_minute,
            historical_min_spacing_seconds=(
                settings.ib_intelligence_historical_min_spacing_seconds
            ),
            tws_min_spacing_seconds=settings.ib_intelligence_tws_min_spacing_seconds,
            live_snapshot_concurrency=settings.ib_intelligence_live_concurrency,
            market_data_line_cap=settings.ib_intelligence_market_data_line_cap,
            scanner_concurrency=10,
            flex_send_per_minute=10,
        )
    )


def execute_historical_refresh(
    db: Session,
    job: BackgroundJob,
    *,
    settings: Settings | None = None,
    ib_factory=None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    config = load_ib_market_intelligence_config(settings=settings)
    module = IntelligenceModule(str(job.payload_json["module"]))
    if module not in HISTORICAL_MODULE_METRICS:
        raise ValueError(f"{module} does not use historical metric refresh")
    tickers = _tickers(job.payload_json)
    resume = _resume_checkpoint(job, module.value)
    completed_ranges = {
        _range_key(
            str(item["ticker"]),
            str(item["metric"]),
            date.fromisoformat(str(item["start_date"])),
            date.fromisoformat(str(item["end_date"])),
        )
        for item in resume.get("completed_ranges", [])
        if isinstance(item, dict)
        and all(key in item for key in ("ticker", "metric", "start_date", "end_date"))
    }
    completed_tickers = {str(value).upper() for value in resume.get("completed_tickers", [])}
    ranges = _historical_date_ranges(job.payload_json, module, settings)
    run = _start_run(db, job, module.value, config)
    counts = {**_counts(), **dict(resume.get("counts") or {})}
    ib = ib_factory() if ib_factory else create_ib_client()
    try:
        def guard() -> None:
            _job_guard(db, job)

        _connect_with_retry(ib, settings, guard=guard)
        for ticker_index, ticker in enumerate(tickers):
            if ticker in completed_tickers:
                continue
            metric_availability: dict[HistoricalMetricType, str] = {}
            _job_guard(db, job)
            _checkpoint(
                db,
                job,
                run,
                {
                    "module": module.value,
                    "ticker_index": ticker_index,
                    "ticker": ticker,
                    "phase": "resolve",
                    "completed_ranges": _completed_range_payload(completed_ranges),
                    "completed_tickers": sorted(completed_tickers),
                    "counts": counts,
                },
            )
            db.commit()
            resolution = resolve_us_stock_contract(db, ticker, ib)
            if not resolution.contract:
                counts["failed"] += 1
                continue
            for metric in HISTORICAL_MODULE_METRICS[module]:
                metric_had_rows = bool(_metric_bars(db, ticker, metric))
                for range_start, range_end in ranges:
                    key = _range_key(ticker, metric.value, range_start, range_end)
                    if key in completed_ranges:
                        continue
                    recovering_failed_range = (
                        resume.get("phase") == "retry_pending"
                        and resume.get("ticker") == ticker
                        and resume.get("metric") == metric.value
                        and resume.get("range_start") == range_start.isoformat()
                        and resume.get("range_end") == range_end.isoformat()
                    )
                    duration = f"{(range_end - range_start).days + 1} D"
                    checkpoint = {
                        "version": 2,
                        "module": module.value,
                        "ticker_index": ticker_index,
                        "ticker": ticker,
                        "metric": metric.value,
                        "range_start": range_start.isoformat(),
                        "range_end": range_end.isoformat(),
                        "phase": "fetch",
                        "completed_ranges": _completed_range_payload(completed_ranges),
                        "completed_tickers": sorted(completed_tickers),
                        "counts": counts,
                    }
                    _checkpoint(db, job, run, checkpoint)
                    db.commit()
                    request_item = _start_request_item(
                        db,
                        run,
                        ticker=ticker,
                        ib_conid=getattr(resolution.contract, "conId", None),
                        request_family="HISTORICAL",
                        request_type=metric.value,
                        priority=10 if module == IntelligenceModule.LIQUIDITY else 50,
                        request={
                            "duration": duration,
                            "bar_size": "1 day",
                            "start_date": range_start.isoformat(),
                            "end_date": range_end.isoformat(),
                            "weight": 2 if metric == HistoricalMetricType.BID_ASK else 1,
                        },
                    )

                    def on_retry(
                        event: RetryEvent,
                        item: IBIntelligenceRequestItem = request_item,
                        request_type: str = metric.value,
                    ) -> None:
                        item.retry_count = event.failed_attempt
                        operational_metrics.increment(
                            "swinglens_ibmi_retries_total",
                            request_family="HISTORICAL",
                            request_type=request_type,
                        )

                    client = IBHistoricalMetricClient(
                        ib,
                        settings=settings,
                        budget=shared_request_budget(),
                        retry_policy=_retry_policy(settings),
                        reconnect=lambda: _reconnect_ib(ib, settings),
                        guard=guard,
                        on_retry=on_retry,
                    )
                    try:
                        bars = client.fetch(
                            resolution.contract,
                            metric.value,
                            duration=duration,
                            start_date=range_start,
                            end_date=range_end,
                        )
                        counts["read"] += len(bars)
                        for bar in bars:
                            _, outcome = persist_historical_metric_bar(
                                db, bar, intelligence_run_id=run.id
                            )
                            counts[outcome.lower()] = counts.get(outcome.lower(), 0) + 1
                        _finish_request_item(
                            request_item,
                            status="COMPLETED" if bars else "PARTIAL",
                            availability=(
                                AvailabilityStatus.AVAILABLE
                                if bars
                                else AvailabilityStatus.UNAVAILABLE
                            ),
                            result_counts={
                                "rows": len(bars),
                                "retry_count": request_item.retry_count,
                                "weighted_cost": (
                                    2 if metric == HistoricalMetricType.BID_ASK else 1
                                ),
                            },
                        )
                        metric_had_rows = metric_had_rows or bool(bars)
                        if recovering_failed_range:
                            counts["failed"] = max(0, counts["failed"] - 1)
                        if not bars:
                            counts["skipped"] += 1
                        completed_ranges.add(key)
                        checkpoint.update(
                            {
                                "phase": "range_complete",
                                "completed_ranges": _completed_range_payload(completed_ranges),
                                "counts": counts,
                            }
                        )
                        _checkpoint(db, job, run, checkpoint)
                        db.commit()
                    except Exception as exc:
                        availability, reason = capability_status_from_error(exc)
                        metric_availability[metric] = availability
                        _finish_request_item(
                            request_item,
                            status="FAILED",
                            availability=availability,
                            error=reason,
                        )
                        counts["failed"] += 1
                        run.warning_flags_json = [
                            *run.warning_flags_json,
                            f"{ticker}:{metric.value}:{str(exc)[:160]}",
                        ]
                        checkpoint.update({"phase": "retry_pending", "counts": counts})
                        _checkpoint(db, job, run, checkpoint)
                        db.commit()
                        if isinstance(exc, RetryExhausted):
                            raise
                        break
                metric_availability.setdefault(
                    metric,
                    AvailabilityStatus.AVAILABLE
                    if metric_had_rows
                    else AvailabilityStatus.UNAVAILABLE,
                )
            _rebuild_ticker_feature(
                db,
                ticker,
                module,
                date.today(),
                config,
                run.id,
                historical_availability=metric_availability,
            )
            completed_tickers.add(ticker)
            _checkpoint(
                db,
                job,
                run,
                {
                    "version": 2,
                    "module": module.value,
                    "ticker_index": ticker_index + 1,
                    "ticker": ticker,
                    "phase": "ticker_complete",
                    "completed_ranges": _completed_range_payload(completed_ranges),
                    "completed_tickers": sorted(completed_tickers),
                    "counts": counts,
                },
            )
            db.commit()
    except CancelRequested:
        _finish_run(db, run, RunStatus.CANCELLED, counts)
        db.commit()
        raise
    except Exception as exc:
        _finish_run(db, run, RunStatus.FAILED, counts, str(exc))
        db.commit()
        raise
    finally:
        if ib.isConnected():
            ib.disconnect()
    status = RunStatus.PARTIAL if counts["failed"] or counts["skipped"] else RunStatus.COMPLETED
    _finish_run(db, run, status, counts)
    db.commit()
    return {"intelligence_run_id": run.id, "status": status.value, **counts}


def execute_live_snapshot(
    db: Session,
    job: BackgroundJob,
    *,
    settings: Settings | None = None,
    ib_factory=None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    config = load_ib_market_intelligence_config(settings=settings)
    module = IntelligenceModule(str(job.payload_json["module"]))
    snapshot_type = {
        IntelligenceModule.SHORT_PRESSURE: "SHORTABLE",
        IntelligenceModule.OPTIONS_ACTIVITY: "OPTIONS_ACTIVITY",
        IntelligenceModule.VOLATILITY: "VOL_LIVE",
    }.get(module)
    if not snapshot_type:
        raise ValueError(f"{module} does not use live snapshots")
    tickers = _tickers(job.payload_json, limit=settings.ib_intelligence_shortlist_limit)
    resume = _resume_checkpoint(job, module.value)
    completed_tickers = {str(value).upper() for value in resume.get("completed_tickers", [])}
    run = _start_run(db, job, module.value, config)
    counts = {**_counts(), **dict(resume.get("counts") or {})}
    ib = ib_factory() if ib_factory else create_ib_client()
    try:
        def guard() -> None:
            _job_guard(db, job)

        _connect_with_retry(ib, settings, guard=guard)
        for index, ticker in enumerate(tickers):
            if ticker in completed_tickers:
                continue
            _job_guard(db, job)
            _checkpoint(
                db,
                job,
                run,
                {
                    "module": module.value,
                    "ticker_index": index,
                    "ticker": ticker,
                    "phase": "snapshot",
                    "completed_tickers": sorted(completed_tickers),
                    "counts": counts,
                },
            )
            db.commit()
            resolution = resolve_us_stock_contract(db, ticker, ib)
            if not resolution.contract:
                counts["failed"] += 1
                continue
            request_item = _start_request_item(
                db,
                run,
                ticker=ticker,
                ib_conid=getattr(resolution.contract, "conId", None),
                request_family="LIVE_MARKET_DATA",
                request_type=snapshot_type,
                priority=20,
                request={"generic_ticks": IBLiveSnapshotManager.GENERIC_TICKS[snapshot_type]},
            )

            def on_retry(
                event: RetryEvent,
                item: IBIntelligenceRequestItem = request_item,
            ) -> None:
                item.retry_count = event.failed_attempt
                operational_metrics.increment(
                    "swinglens_ibmi_retries_total",
                    request_family="LIVE_MARKET_DATA",
                    request_type=snapshot_type,
                )

            manager = IBLiveSnapshotManager(
                ib,
                budget=shared_request_budget(),
                timeout_seconds=float(settings.ib_timeout_seconds),
                retry_policy=_retry_policy(settings),
                reconnect=lambda: _reconnect_ib(ib, settings),
                guard=guard,
                on_retry=on_retry,
            )
            snapshot = manager.capture(resolution.contract, snapshot_type)
            _, inserted = persist_live_snapshot(db, snapshot, intelligence_run_id=run.id)
            _finish_request_item(
                request_item,
                status=(
                    "COMPLETED"
                    if snapshot.availability_status == AvailabilityStatus.AVAILABLE
                    else "PARTIAL"
                ),
                availability=snapshot.availability_status,
                result_counts={
                    "fields": len(snapshot.values),
                    "retry_count": int(snapshot.source_request.get("retry_count", 0)),
                    "resubscriptions": int(snapshot.source_request.get("resubscriptions", 0)),
                },
                error=snapshot.capability_reason,
            )
            request_item.retry_count = int(snapshot.source_request.get("retry_count", 0))
            counts["inserted" if inserted else "unchanged"] += 1
            if snapshot.availability_status != AvailabilityStatus.AVAILABLE:
                counts["skipped"] += 1
            _rebuild_ticker_feature(db, ticker, module, snapshot.effective_session, config, run.id)
            terminal = snapshot.availability_status in {
                AvailabilityStatus.AVAILABLE,
                AvailabilityStatus.SUBSCRIPTION_REQUIRED,
                AvailabilityStatus.NOT_SUPPORTED,
            }
            if terminal:
                completed_tickers.add(ticker)
            _checkpoint(
                db,
                job,
                run,
                {
                    "version": 2,
                    "module": module.value,
                    "ticker_index": index + (1 if terminal else 0),
                    "ticker": ticker,
                    "phase": "ticker_complete" if terminal else "retry_pending",
                    "completed_tickers": sorted(completed_tickers),
                    "counts": counts,
                },
            )
            db.commit()
            if not terminal:
                raise RetryExhausted(
                    f"IBKR live {snapshot_type}",
                    int(snapshot.source_request.get("attempts", 1)),
                    TimeoutError(snapshot.capability_reason or "live snapshot unavailable"),
                )
    except CancelRequested:
        _finish_run(db, run, RunStatus.CANCELLED, counts)
        db.commit()
        raise
    except Exception as exc:
        _finish_run(db, run, RunStatus.FAILED, counts, str(exc))
        db.commit()
        raise
    finally:
        if ib.isConnected():
            ib.disconnect()
    status = RunStatus.PARTIAL if counts["failed"] or counts["skipped"] else RunStatus.COMPLETED
    _finish_run(db, run, status, counts)
    db.commit()
    return {"intelligence_run_id": run.id, "status": status.value, **counts}


def execute_scanner_run(
    db: Session,
    job: BackgroundJob,
    *,
    settings: Settings | None = None,
    ib_factory=None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    config = load_ib_market_intelligence_config(settings=settings)
    requested = job.payload_json.get("presets") or [
        preset.name for preset in config.scanner_presets
    ]
    presets = [_preset(config, name) for name in requested]
    run = _start_run(db, job, IntelligenceModule.SCANNER, config)
    counts = _counts()
    ib = ib_factory() if ib_factory else create_ib_client()
    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        client = IBScannerClient(ib, budget=shared_request_budget())
        parameter_item = _start_request_item(
            db,
            run,
            ticker=None,
            ib_conid=None,
            request_family="SCANNER",
            request_type="SCANNER_PARAMETERS",
            priority=80,
            request={},
        )
        try:
            parameters = client.parameters()
            _finish_request_item(
                parameter_item,
                status="COMPLETED",
                availability=AvailabilityStatus.AVAILABLE,
                result_counts={"bytes": len(parameters.encode("utf-8"))},
            )
        except Exception as exc:
            availability, reason = capability_status_from_error(exc)
            _finish_request_item(
                parameter_item,
                status="FAILED",
                availability=availability,
                error=reason,
            )
            raise
        parameter_hash = evidence_hash(parameters)
        if (
            db.scalar(
                select(IBScannerParameterCache).where(
                    IBScannerParameterCache.content_hash == parameter_hash
                )
            )
            is None
        ):
            db.add(
                IBScannerParameterCache(
                    xml_payload=parameters,
                    content_hash=parameter_hash,
                    fetched_at=datetime.now(UTC),
                )
            )
        for index, preset in enumerate(presets):
            _job_guard(db, job)
            scan = IBScannerRun(
                intelligence_run_id=run.id,
                scanner_name=preset.name,
                scanner_version=preset.version,
                instrument=preset.instrument,
                location=preset.location,
                scan_code=preset.scan_code,
                max_results=preset.max_results,
                filters_json=list(preset.filters),
                config_hash=config.config_hash,
                status="RUNNING",
                started_at=datetime.now(UTC),
            )
            db.add(scan)
            db.flush()
            request_item = _start_request_item(
                db,
                run,
                ticker=None,
                ib_conid=None,
                request_family="SCANNER",
                request_type=preset.scan_code,
                priority=80,
                request={"preset": preset.name, "filters": list(preset.filters)},
            )
            try:
                if preset.scan_code not in parameters:
                    raise ValueError(
                        f"Scanner code {preset.scan_code} is not present in current IBKR parameters"
                    )
                rows = client.run(preset)
                known_conids = scanner_conids_by_ticker(
                    (row["ticker"], row["ib_conid"]) for row in rows
                )
                seen_candidates: set[str] = set()
                for row in sorted(rows, key=lambda item: (item["rank"], item["ticker"])):
                    if not row["ticker"]:
                        continue
                    candidate_key = canonical_scanner_identity(
                        ticker=row["ticker"],
                        ib_conid=row["ib_conid"],
                        contract_metadata=row["contract_metadata"],
                        known_conids_by_ticker=known_conids,
                    )
                    if candidate_key in seen_candidates:
                        counts["skipped"] += 1
                        continue
                    seen_candidates.add(candidate_key)
                    db.add(
                        IBScannerCandidate(
                            scanner_run_id=scan.id,
                            rank=row["rank"],
                            ticker=row["ticker"],
                            ib_conid=row["ib_conid"],
                            contract_metadata_json=row["contract_metadata"],
                            scanner_metadata_json=row["scanner_metadata"],
                            universe_source="IBKR_SCANNER",
                            enrichment_status="PENDING",
                        )
                    )
                    counts["inserted"] += 1
                scan.status = "COMPLETED"
                scan.completed_at = datetime.now(UTC)
                _finish_request_item(
                    request_item,
                    status="COMPLETED",
                    availability=AvailabilityStatus.AVAILABLE,
                    result_counts={"rows": len(rows)},
                )
            except Exception as exc:
                scan.status = "FAILED"
                scan.error_message = str(exc)[:1000]
                scan.completed_at = datetime.now(UTC)
                counts["failed"] += 1
                availability, reason = capability_status_from_error(exc)
                _finish_request_item(
                    request_item,
                    status="FAILED",
                    availability=availability,
                    error=reason,
                )
            _checkpoint(
                db,
                job,
                run,
                {
                    "module": "SCANNER",
                    "preset_index": index,
                    "preset": preset.name,
                    "phase": "complete",
                },
            )
            db.commit()
    except Exception as exc:
        _finish_run(db, run, RunStatus.FAILED, counts, str(exc))
        raise
    finally:
        if ib.isConnected():
            ib.disconnect()
    status = RunStatus.PARTIAL if counts["failed"] else RunStatus.COMPLETED
    _finish_run(db, run, status, counts)
    db.commit()
    return {"intelligence_run_id": run.id, "status": status.value, **counts}


def execute_histogram_fetch(
    db: Session,
    job: BackgroundJob,
    *,
    settings: Settings | None = None,
    ib_factory=None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    config = load_ib_market_intelligence_config(settings=settings)
    section = config.section("histogram")
    requested_period = effective_histogram_period(job.payload_json, section, settings)
    tickers = _tickers(job.payload_json, limit=settings.ib_intelligence_shortlist_limit)
    run = _start_run(db, job, IntelligenceModule.HISTOGRAM, config)
    counts = _counts()
    ib = ib_factory() if ib_factory else create_ib_client()
    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        client = IBHistogramClient(ib, budget=shared_request_budget())
        for index, ticker in enumerate(tickers):
            _job_guard(db, job)
            resolution = resolve_us_stock_contract(db, ticker, ib)
            if not resolution.contract:
                counts["failed"] += 1
                continue
            request_item = _start_request_item(
                db,
                run,
                ticker=ticker,
                ib_conid=getattr(resolution.contract, "conId", None),
                request_family="HISTOGRAM",
                request_type="HISTOGRAM_DATA",
                priority=50,
                request={
                    "period": requested_period,
                    "use_rth": section.get("use_rth", True),
                },
            )
            levels = client.fetch(
                resolution.contract,
                use_rth=bool(section.get("use_rth", True)),
                period=requested_period,
            )
            observed = datetime.now(UTC)
            reference = _latest_close(db, ticker)
            digest = evidence_hash(
                {
                    "ticker": ticker,
                    "period": requested_period,
                    "use_rth": bool(section.get("use_rth", True)),
                    "observed_at": observed,
                    "levels": levels,
                }
            )
            snapshot = IBHistogramSnapshot(
                intelligence_run_id=run.id,
                ticker=ticker,
                ib_conid=getattr(resolution.contract, "conId", None),
                requested_period=requested_period,
                use_rth=bool(section.get("use_rth", True)),
                observed_at=observed,
                reference_price=Decimal(str(reference)) if reference is not None else None,
                availability_status=AvailabilityStatus.AVAILABLE
                if levels
                else AvailabilityStatus.UNAVAILABLE,
                evidence_hash=digest,
                source_semantics="IBKR_HISTOGRAM_PRICE_LEVEL_ACTIVITY",
                warnings_json=["NOT_EXCHANGE_VOLUME_PROFILE"],
            )
            db.add(snapshot)
            db.flush()
            ranked = sorted(levels, key=lambda level: (-level.activity_count, level.price))
            rank_by_price = {level.price: rank for rank, level in enumerate(ranked, start=1)}
            counts_values = sorted(level.activity_count for level in levels)
            for level in levels:
                percentile = (
                    100.0
                    * sum(value <= level.activity_count for value in counts_values)
                    / len(counts_values)
                )
                db.add(
                    IBHistogramBin(
                        histogram_snapshot_id=snapshot.id,
                        price=Decimal(str(level.price)),
                        activity_count=Decimal(str(level.activity_count)),
                        activity_rank=rank_by_price[level.price],
                        density_percentile=Decimal(str(percentile)),
                    )
                )
            feature = calculate_histogram(levels, reference_price=reference, config=section)
            persist_feature(
                db,
                ticker=ticker,
                ib_conid=getattr(resolution.contract, "conId", None),
                as_of_session=observed.date(),
                feature=feature,
                config=config,
                intelligence_run_id=run.id,
            )
            counts["inserted"] += 1
            _finish_request_item(
                request_item,
                status="COMPLETED",
                availability=(
                    AvailabilityStatus.AVAILABLE if levels else AvailabilityStatus.UNAVAILABLE
                ),
                result_counts={"bins": len(levels)},
            )
            _checkpoint(
                db,
                job,
                run,
                {
                    "module": "HISTOGRAM",
                    "ticker_index": index,
                    "ticker": ticker,
                    "phase": "complete",
                },
            )
            db.commit()
    except Exception as exc:
        _finish_run(db, run, RunStatus.FAILED, counts, str(exc))
        raise
    finally:
        if ib.isConnected():
            ib.disconnect()
    _finish_run(db, run, RunStatus.COMPLETED, counts)
    db.commit()
    return {"intelligence_run_id": run.id, "status": "COMPLETED", **counts}


def execute_flex_import(
    db: Session,
    job: BackgroundJob,
    *,
    settings: Settings | None = None,
    client_factory=None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    config = load_ib_market_intelligence_config(settings=settings)
    resume = _resume_checkpoint(job, IntelligenceModule.FLEX.value)
    run = _start_run(db, job, IntelligenceModule.FLEX, config)
    query_type = str(job.payload_json.get("query_type", "TRADE_CONFIRMATIONS"))
    query_id = (
        settings.ib_flex_trade_query_id
        if query_type == "TRADE_CONFIRMATIONS"
        else settings.ib_flex_activity_query_id
    )
    if not query_id:
        raise ValueError(f"Flex query ID for {query_type} is not configured")
    request_item = _start_request_item(
        db,
        run,
        ticker=None,
        ib_conid=None,
        request_family="FLEX_HTTPS",
        request_type=query_type,
        priority=70,
        request={
            "query_type": query_type,
            "dry_run": bool(job.payload_json.get("dry_run", False)),
        },
    )
    def guard() -> None:
        _job_guard(db, job)

    def on_retry(event: RetryEvent) -> None:
        request_item.retry_count = max(request_item.retry_count, event.failed_attempt)
        operational_metrics.increment(
            "swinglens_ibmi_retries_total",
            request_family="FLEX_HTTPS",
            request_type=query_type,
        )

    client = (
        client_factory()
        if client_factory
        else flex_client_from_settings(
            settings,
            budget=shared_request_budget(),
            guard=guard,
            on_retry=on_retry,
        )
    )
    resume_reference = (
        str(resume.get("_reference_code"))
        if resume.get("phase") in {"get_statement", "downloaded"}
        and resume.get("query_type") == query_type
        and resume.get("_reference_code")
        else None
    )
    start_attempt = (
        max(0, int(resume.get("next_attempt", resume.get("attempt", 1))) - 1)
        if resume_reference
        else 0
    )

    def flex_checkpoint(values: dict[str, Any]) -> None:
        checkpoint = {
            "version": 2,
            "module": IntelligenceModule.FLEX.value,
            "query_type": query_type,
            **values,
        }
        _checkpoint(db, job, run, checkpoint)
        request_item.retry_count = max(
            request_item.retry_count,
            max(0, int(values.get("attempt", 1)) - 1),
        )
        db.commit()

    try:
        reference, content = client.download(
            query_id,
            attempts=settings.ib_flex_poll_attempts,
            poll_seconds=settings.ib_flex_poll_seconds,
            reference_code=resume_reference,
            start_attempt=start_attempt,
            on_checkpoint=flex_checkpoint,
            guard=guard,
        )
        result = import_flex_report(
            db,
            content=content,
            query_type=query_type,
            query_id=query_id,
            reference_code=reference,
            dry_run=bool(job.payload_json.get("dry_run", False)),
            intelligence_run_id=run.id,
            report_timezone=settings.ib_flex_report_timezone,
        )
        if not job.payload_json.get("dry_run", False) and result["status"] != "DUPLICATE_REPORT":
            episodes = rebuild_trade_episodes(db)
            for episode in episodes:
                match_episode_to_research(
                    db,
                    episode,
                    lookback_sessions=int(
                        config.section("flex").get("research_lookback_sessions", 5)
                    ),
                    policy=str(
                        config.section("flex").get(
                            "matching_policy_version", "latest-completed-before-entry-v1"
                        )
                    ),
                )
            result["episodes"] = len(episodes)
        _finish_run(db, run, RunStatus.COMPLETED, {"inserted": int(result.get("inserted", 0))})
        _finish_request_item(
            request_item,
            status="COMPLETED",
            availability=AvailabilityStatus.AVAILABLE,
            result_counts={
                "rows": int(result.get("rows", 0)),
                "inserted": int(result.get("inserted", 0)),
                "retry_count": request_item.retry_count,
            },
        )
        _checkpoint(
            db,
            job,
            run,
            {
                "version": 2,
                "module": IntelligenceModule.FLEX.value,
                "query_type": query_type,
                "phase": "complete",
                "reference_code_hash": run.checkpoint_json.get("reference_code_hash"),
                "attempt": request_item.retry_count + 1,
                "content_hash": run.checkpoint_json.get("content_hash"),
            },
        )
        db.commit()
        return {"intelligence_run_id": run.id, **result}
    except CancelRequested:
        _finish_request_item(
            request_item,
            status="CANCELLED",
            availability=AvailabilityStatus.UNKNOWN,
            error="Cancellation requested",
        )
        _finish_run(db, run, RunStatus.CANCELLED, _counts())
        db.commit()
        raise
    except Exception as exc:
        _finish_request_item(
            request_item,
            status="FAILED",
            availability=AvailabilityStatus.FAILED,
            error=str(exc),
        )
        _finish_run(db, run, RunStatus.FAILED, _counts(), str(exc))
        db.commit()
        raise


def execute_feature_rebuild(db: Session, job: BackgroundJob) -> dict[str, Any]:
    config = load_ib_market_intelligence_config()
    module = IntelligenceModule(str(job.payload_json["module"]))
    tickers = _tickers(job.payload_json)
    run = _start_run(db, job, module, config)
    inserted = 0
    for ticker in tickers:
        _, was_inserted = _rebuild_ticker_feature(db, ticker, module, date.today(), config, run.id)
        inserted += int(was_inserted)
    _finish_run(db, run, RunStatus.COMPLETED, {"inserted": inserted})
    db.commit()
    return {"intelligence_run_id": run.id, "inserted": inserted}


def _rebuild_ticker_feature(
    db: Session,
    ticker: str,
    module: IntelligenceModule,
    as_of: date,
    config: IBMarketIntelligenceConfig,
    run_id: int,
    historical_availability: dict[HistoricalMetricType, str] | None = None,
):
    historical_availability = historical_availability or {}
    ib_conid = None
    if module == IntelligenceModule.LIQUIDITY:
        bars = _metric_bars(db, ticker, HistoricalMetricType.BID_ASK)
        feature = calculate_liquidity(
            bars,
            as_of=as_of,
            dollar_volume=_dollar_volume(db, ticker),
            config={**config.section("liquidity"), **config.section("freshness")},
        )
    elif module == IntelligenceModule.SHORT_PRESSURE:
        bars = _metric_bars(db, ticker, HistoricalMetricType.FEE_RATE)
        snapshot = _latest_snapshot(db, ticker, "SHORTABLE")
        values = snapshot.values_json if snapshot else {}
        status = _snapshot_availability(snapshot, config)
        feature = calculate_short_pressure(
            bars,
            as_of=as_of,
            shortable_shares=values.get("shortable_shares"),
            shortable_state=str(values.get("shortable_state"))
            if values.get("shortable_state") is not None
            else None,
            availability_status=status,
            config={**config.section("short_pressure"), **config.section("freshness")},
        )
    elif module == IntelligenceModule.VOLATILITY:
        hv = _metric_bars(db, ticker, HistoricalMetricType.HISTORICAL_VOLATILITY)
        iv = _metric_bars(db, ticker, HistoricalMetricType.OPTION_IMPLIED_VOLATILITY)
        iv_availability = historical_availability.get(
            HistoricalMetricType.OPTION_IMPLIED_VOLATILITY
        ) or _latest_historical_availability(
            db, ticker, HistoricalMetricType.OPTION_IMPLIED_VOLATILITY, has_rows=bool(iv)
        )
        feature = calculate_volatility(
            hv,
            iv,
            as_of=as_of,
            config={**config.section("volatility"), **config.section("freshness")},
            iv_availability=iv_availability,
        )
    elif module == IntelligenceModule.OPTIONS_ACTIVITY:
        snapshot = _latest_snapshot(db, ticker, "OPTIONS_ACTIVITY")
        feature = calculate_options_activity(
            snapshot.values_json if snapshot else {},
            availability_status=_snapshot_availability(snapshot, config),
            config=config.section("options_activity"),
            evidence_hash=snapshot.evidence_hash if snapshot else None,
        )
    else:
        raise ValueError(f"Feature rebuild is not supported for {module}")
    return persist_feature(
        db,
        ticker=ticker,
        ib_conid=ib_conid,
        as_of_session=as_of,
        feature=feature,
        config=config,
        intelligence_run_id=run_id,
    )


def _start_run(
    db: Session,
    job: BackgroundJob,
    module: str | IntelligenceModule,
    config: IBMarketIntelligenceConfig,
) -> IBIntelligenceRun:
    checkpoint = _resume_checkpoint(job, str(module))
    scope = {key: value for key, value in job.payload_json.items() if key != "checkpoint"}
    row = IBIntelligenceRun(
        background_job_id=job.id,
        job_type=job.job_type,
        module=str(module),
        status=RunStatus.RUNNING,
        deterministic_request_key=job.request_key or f"job:{job.id}",
        scope_json=scope,
        config_version=config.config_version,
        config_hash=config.config_hash,
        counts_json=_counts(),
        checkpoint_json=_public_checkpoint(checkpoint),
        warning_flags_json=[],
        started_at=datetime.now(UTC),
    )
    db.add(row)
    db.flush()
    return row


def _finish_run(
    db: Session,
    run: IBIntelligenceRun,
    status: RunStatus,
    counts: dict[str, int],
    error: str | None = None,
) -> None:
    run.status = status.value
    run.counts_json = counts
    run.error_message = error[:2000] if error else None
    run.completed_at = datetime.now(UTC)
    db.flush()


def _start_request_item(
    db: Session,
    run: IBIntelligenceRun,
    *,
    ticker: str | None,
    ib_conid: int | None,
    request_family: str,
    request_type: str,
    priority: int,
    request: dict[str, Any],
) -> IBIntelligenceRequestItem:
    key = evidence_hash(
        {
            "run": run.id,
            "ticker": ticker,
            "family": request_family,
            "type": request_type,
            "request": request,
        }
    )
    item = IBIntelligenceRequestItem(
        intelligence_run_id=run.id,
        deterministic_request_key=key,
        ticker=ticker,
        ib_conid=ib_conid,
        request_family=request_family,
        request_type=request_type,
        priority=priority,
        status="RUNNING",
        availability_status=AvailabilityStatus.UNKNOWN,
        request_json=request,
        result_counts_json={},
        retry_count=0,
        started_at=datetime.now(UTC),
    )
    db.add(item)
    db.flush()
    return item


def _finish_request_item(
    item: IBIntelligenceRequestItem,
    *,
    status: str,
    availability: str,
    result_counts: dict[str, int] | None = None,
    error: str | None = None,
) -> None:
    item.status = status
    item.availability_status = str(availability)
    item.result_counts_json = result_counts or {}
    item.error_message = error[:1000] if error else None
    item.completed_at = datetime.now(UTC)


def _checkpoint(
    db: Session, job: BackgroundJob, run: IBIntelligenceRun, checkpoint: dict[str, Any]
) -> None:
    run.checkpoint_json = _public_checkpoint(checkpoint)
    job.payload_json = {**job.payload_json, "checkpoint": checkpoint}
    heartbeat = getattr(job, "_heartbeat", None)
    if callable(heartbeat):
        heartbeat()
    db.flush()


def _public_checkpoint(checkpoint: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in checkpoint.items() if not key.startswith("_")}


def _resume_checkpoint(job: BackgroundJob, module: str) -> dict[str, Any]:
    checkpoint = job.payload_json.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return {}
    checkpoint_module = checkpoint.get("module")
    if checkpoint_module and str(checkpoint_module) != str(module):
        return {}
    return dict(checkpoint)


def _retry_policy(settings: Settings) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=settings.ib_intelligence_request_max_attempts,
        initial_backoff_seconds=settings.ib_intelligence_retry_initial_seconds,
        max_backoff_seconds=settings.ib_intelligence_retry_max_seconds,
    )


def _connect_with_retry(
    ib: Any,
    settings: Settings,
    *,
    guard: Any = None,
) -> None:
    def on_retry(_event: RetryEvent) -> None:
        operational_metrics.increment(
            "swinglens_ibmi_retries_total", request_family="CONNECTION", request_type="TWS"
        )

    retry_call(
        lambda: _reconnect_ib(ib, settings),
        operation_name="IBKR TWS connection",
        policy=_retry_policy(settings),
        guard=guard,
        on_retry=on_retry,
    )


def _reconnect_ib(ib: Any, settings: Settings) -> None:
    is_connected = getattr(ib, "isConnected", None)
    if callable(is_connected) and is_connected():
        return
    ib.connect(
        settings.ib_host,
        settings.ib_port,
        clientId=settings.ib_client_id,
        timeout=settings.ib_timeout_seconds,
        readonly=True,
    )
    operational_metrics.increment("swinglens_ibmi_connections_total")


def _historical_date_ranges(
    payload: dict[str, Any], module: IntelligenceModule, settings: Settings
) -> list[tuple[date, date]]:
    end_date = _payload_date(payload.get("end_date")) or date.today()
    start_date = _payload_date(payload.get("start_date"))
    if start_date is None:
        duration_days = int(_historical_duration(module, settings).split()[0])
        start_date = end_date - timedelta(days=max(0, duration_days - 1))
    if start_date > end_date:
        raise ValueError("historical start_date cannot be after end_date")
    ranges: list[tuple[date, date]] = []
    cursor = start_date
    chunk_days = settings.ib_intelligence_historical_chunk_days
    while cursor <= end_date:
        chunk_end = min(end_date, cursor + timedelta(days=chunk_days - 1))
        ranges.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return ranges


def _payload_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _range_key(
    ticker: str, metric: str, start_date: date, end_date: date
) -> tuple[str, str, str, str]:
    return (
        ticker.upper(),
        metric,
        start_date.isoformat(),
        end_date.isoformat(),
    )


def _completed_range_payload(
    completed_ranges: set[tuple[str, str, str, str]],
) -> list[dict[str, str]]:
    return [
        {
            "ticker": ticker,
            "metric": metric,
            "start_date": start_date,
            "end_date": end_date,
        }
        for ticker, metric, start_date, end_date in sorted(completed_ranges)
    ]


def _job_guard(db: Session, job: BackgroundJob) -> None:
    if is_cancel_requested(db, job.id):
        raise CancelRequested("IB market-intelligence job cancellation requested")


def _tickers(payload: dict[str, Any], limit: int | None = None) -> list[str]:
    raw = payload.get("tickers") or []
    if isinstance(raw, str):
        raw = raw.split(",")
    tickers = list(dict.fromkeys(str(value).strip().upper() for value in raw if str(value).strip()))
    if not tickers:
        raise ValueError("At least one ticker is required")
    return tickers[:limit] if limit else tickers


def _counts() -> dict[str, int]:
    return {"read": 0, "inserted": 0, "revised": 0, "unchanged": 0, "skipped": 0, "failed": 0}


def effective_histogram_period(
    payload: dict[str, Any], section: dict[str, Any], settings: Settings
) -> str:
    return str(payload.get("period") or section.get("period", settings.ib_histogram_period))


def _historical_duration(module: IntelligenceModule, settings: Settings) -> str:
    if module == IntelligenceModule.LIQUIDITY:
        return f"{max(settings.ib_liquidity_lookback_sessions, 60)} D"
    if module == IntelligenceModule.SHORT_PRESSURE:
        return f"{settings.ib_fee_rate_lookback_sessions} D"
    return f"{settings.ib_volatility_lookback_sessions} D"


def _metric_bars(
    db: Session, ticker: str, metric: HistoricalMetricType
) -> list[IBHistoricalMetricBar]:
    return db.scalars(
        select(IBHistoricalMetricBar)
        .where(IBHistoricalMetricBar.ticker == ticker.upper())
        .where(IBHistoricalMetricBar.metric_type == metric.value)
        .order_by(IBHistoricalMetricBar.session_date)
    ).all()


def _latest_historical_availability(
    db: Session,
    ticker: str,
    metric: HistoricalMetricType,
    *,
    has_rows: bool,
) -> str:
    status = db.scalar(
        select(IBIntelligenceRequestItem.availability_status)
        .where(IBIntelligenceRequestItem.ticker == ticker.upper())
        .where(IBIntelligenceRequestItem.request_family == "HISTORICAL")
        .where(IBIntelligenceRequestItem.request_type == metric.value)
        .order_by(IBIntelligenceRequestItem.started_at.desc())
    )
    if status:
        return str(status)
    return AvailabilityStatus.AVAILABLE if has_rows else AvailabilityStatus.UNAVAILABLE


def _latest_snapshot(
    db: Session, ticker: str, snapshot_type: str
) -> IBMarketIntelligenceSnapshot | None:
    return db.scalar(
        select(IBMarketIntelligenceSnapshot)
        .where(IBMarketIntelligenceSnapshot.ticker == ticker.upper())
        .where(IBMarketIntelligenceSnapshot.snapshot_type == snapshot_type)
        .order_by(IBMarketIntelligenceSnapshot.observed_at.desc())
    )


def _snapshot_availability(
    snapshot: IBMarketIntelligenceSnapshot | None,
    config: IBMarketIntelligenceConfig,
) -> str:
    if snapshot is None:
        return AvailabilityStatus.UNAVAILABLE
    if snapshot.availability_status != AvailabilityStatus.AVAILABLE:
        return snapshot.availability_status
    observed_at = snapshot.observed_at
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)
    age_minutes = (datetime.now(UTC) - observed_at).total_seconds() / 60
    maximum_age = int(config.section("freshness").get("live_max_age_minutes", 30))
    return AvailabilityStatus.STALE if age_minutes > maximum_age else AvailabilityStatus.AVAILABLE


def _latest_close(db: Session, ticker: str) -> float | None:
    value = db.scalar(
        select(PriceBar.close)
        .where(PriceBar.ticker == ticker.upper())
        .where(PriceBar.close.is_not(None))
        .where(PriceBar.what_to_show.in_(("ADJUSTED_LAST", "TRADES")))
        .order_by(PriceBar.bar_date.desc())
    )
    return float(value) if value is not None else None


def _dollar_volume(db: Session, ticker: str) -> float | None:
    rows = db.execute(
        select(PriceBar.close, PriceBar.volume)
        .where(PriceBar.ticker == ticker.upper())
        .where(PriceBar.close.is_not(None), PriceBar.volume.is_not(None))
        .where(PriceBar.what_to_show.in_(("ADJUSTED_LAST", "TRADES")))
        .order_by(PriceBar.bar_date.desc())
        .limit(20)
    ).all()
    values = sorted(float(close) * float(volume) for close, volume in rows)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def _preset(config: IBMarketIntelligenceConfig, name: str) -> ScannerPreset:
    for preset in config.scanner_presets:
        if preset.name == name:
            return preset
    raise ValueError(f"Unknown scanner preset: {name}")
