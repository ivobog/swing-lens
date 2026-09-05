from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    RawCompanyRow,
    UploadRun,
    WinnerMarketDataObligation,
)
from app.services.background_job_service import (
    ACTIVE_JOB_STATUSES,
    JobStatus,
    enqueue_job,
    is_cancel_requested,
)
from app.services.bar_cache_service import DEFAULT_WHAT_TO_SHOW
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, build_fetch_plan
from app.services.ohlcv_coverage_service import summarize_ohlcv_coverage
from app.services.operational_metrics import operational_metrics
from app.services.us_market_calendar import latest_completed_us_trading_day
from app.settings import Settings, get_settings

MARKET_DATA_PREWARM = "MARKET_DATA_PREWARM"
DEFAULT_RECENT_RUN_COUNT = 5
SUPPORTED_UNIVERSE_SOURCES = {"EXPLICIT", "TICKERS", "WATCHLIST", "RECENT_RUNS"}
PREWARM_JOB_PRIORITY = 200
PREWARM_REUSE_OBSERVATION_LIMIT = 20


class MarketDataPrewarmCancelled(RuntimeError):
    """Raised after the shared fetch executor observes a prewarm cancellation."""


@dataclass(frozen=True)
class MarketDataPrewarmRequest:
    universe_source: str = "RECENT_RUNS"
    recent_run_count: int = DEFAULT_RECENT_RUN_COUNT
    tickers: tuple[str, ...] = ()
    include_benchmarks: bool = True
    freshness_date: date | None = None
    requested_by: str = "local-user"

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> MarketDataPrewarmRequest:
        freshness_value = payload.get("freshness_date")
        freshness_date = _parse_date(freshness_value) if freshness_value else None
        return cls(
            universe_source=_normalize_source(payload.get("universe_source")),
            recent_run_count=int(payload.get("recent_run_count", DEFAULT_RECENT_RUN_COUNT)),
            tickers=tuple(_normalize_tickers(payload.get("tickers") or [])),
            include_benchmarks=bool(payload.get("include_benchmarks", True)),
            freshness_date=freshness_date,
            requested_by=str(payload.get("requested_by") or "local-user")[:200],
        )

    def to_payload(self, *, resolved_tickers: tuple[str, ...] | None = None) -> dict[str, Any]:
        return {
            "universe_source": self.universe_source,
            "recent_run_count": self.recent_run_count,
            "tickers": list(resolved_tickers if resolved_tickers is not None else self.tickers),
            "include_benchmarks": self.include_benchmarks,
            "freshness_date": (self.freshness_date or _today()).isoformat(),
            "requested_by": self.requested_by,
        }


@dataclass(frozen=True)
class MarketDataPrewarmUniverse:
    source: str
    recent_run_count: int
    tickers: tuple[str, ...]
    include_benchmarks: bool
    freshness_date: date
    requested_by: str
    bar_size: str
    data_types: tuple[str, ...]
    config_version: str
    config_fingerprint: str

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "tickers": sorted(self.tickers),
                "include_benchmarks": self.include_benchmarks,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def effective_session(self) -> date:
        return self.freshness_date

    @property
    def request_key(self) -> str:
        return (
            f"prewarm:{self.effective_session.isoformat()}:"
            f"{self.fingerprint[:20]}:{self.config_fingerprint[:20]}"
        )


@dataclass(frozen=True)
class PipelinePrewarmContext:
    job_id: int | None = None
    age_seconds: float | None = None
    effective_session: date | None = None
    fresh_for_session: bool = False
    covered_tickers: tuple[str, ...] = ()
    fetched_tickers: tuple[str, ...] = ()
    already_current_tickers: tuple[str, ...] = ()


def resolve_prewarm_universe(
    db: Session,
    request: MarketDataPrewarmRequest,
    settings: Settings | None = None,
) -> MarketDataPrewarmUniverse:
    settings = settings or get_settings()
    source = _normalize_source(request.universe_source)
    if source not in SUPPORTED_UNIVERSE_SOURCES:
        raise ValueError(
            f"Unsupported prewarm universe source {source!r}; "
            f"choose one of {sorted(SUPPORTED_UNIVERSE_SOURCES)}."
        )
    if request.recent_run_count < 1:
        raise ValueError("recent_run_count must be positive.")

    if source in {"EXPLICIT", "TICKERS"}:
        tickers = list(request.tickers)
    elif source == "WATCHLIST":
        tickers = _normalize_tickers(settings.market_data_prewarm_watchlist.split(","))
    else:
        tickers = _tickers_from_recent_completed_runs(db, request.recent_run_count)

    if source == "RECENT_RUNS" and isinstance(db, Session):
        # Outcome obligations outlive upload universes. Prioritize them within
        # each bounded prewarm batch so disappearing tickers remain maintained.
        obligated = list(
            db.scalars(
                select(WinnerMarketDataObligation.ticker_snapshot)
                .where(WinnerMarketDataObligation.status == "FETCH_REQUIRED")
                .distinct()
                .order_by(WinnerMarketDataObligation.ticker_snapshot)
            )
        )
        tickers = [*obligated, *tickers]

    tickers = _cap_tickers(tickers, settings.market_data_prewarm_max_tickers)
    if not tickers:
        raise ValueError(f"No tickers were resolved from prewarm universe source {source}.")

    effective_session = request.freshness_date or latest_completed_us_trading_day()
    latest_session = latest_completed_us_trading_day()
    if effective_session > latest_session:
        raise ValueError(
            f"Prewarm session {effective_session.isoformat()} is not complete; "
            f"latest completed session is {latest_session.isoformat()}."
        )
    config_fingerprint = _prewarm_config_fingerprint(settings)

    return MarketDataPrewarmUniverse(
        source=source,
        recent_run_count=request.recent_run_count,
        tickers=tuple(tickers),
        include_benchmarks=request.include_benchmarks,
        freshness_date=effective_session,
        requested_by=request.requested_by,
        bar_size=settings.ib_default_bar_size,
        data_types=DEFAULT_WHAT_TO_SHOW,
        config_version=settings.market_data_prewarm_config_version,
        config_fingerprint=config_fingerprint,
    )


def enqueue_market_data_prewarm(
    db: Session,
    request: MarketDataPrewarmRequest,
    settings: Settings | None = None,
) -> tuple[BackgroundJob, MarketDataPrewarmUniverse]:
    settings = settings or get_settings()
    if not settings.market_data_prewarm_enabled:
        raise ValueError("Market-data prewarm is disabled by MARKET_DATA_PREWARM_ENABLED.")

    universe = resolve_prewarm_universe(db, request, settings=settings)
    payload = request.to_payload(resolved_tickers=universe.tickers)
    payload.update(
        {
            "universe_fingerprint": universe.fingerprint,
            "effective_session": universe.effective_session.isoformat(),
            "freshness_date": universe.effective_session.isoformat(),
            "bar_size": universe.bar_size,
            "data_types": list(universe.data_types),
            "config_version": universe.config_version,
            "config_fingerprint": universe.config_fingerprint,
            "cancel_bound_seconds": settings.market_data_prewarm_cancel_bound_seconds,
            "max_tickers": settings.market_data_prewarm_max_tickers,
        }
    )
    job = enqueue_job(
        db,
        MARKET_DATA_PREWARM,
        payload,
        request_key=universe.request_key,
        priority=PREWARM_JOB_PRIORITY,
    )
    return job, universe


def execute_market_data_prewarm(
    db: Session,
    job: BackgroundJob,
    *,
    settings: Settings | None = None,
) -> dict[str, Any]:
    settings = settings or get_settings()
    request = MarketDataPrewarmRequest.from_payload(job.payload_json or {})
    tickers = tuple(_normalize_tickers(request.tickers))
    if not tickers:
        raise ValueError("MARKET_DATA_PREWARM job payload contains no tickers.")

    lease_guard = getattr(job, "_heartbeat", None)

    def should_cancel() -> bool:
        if callable(lease_guard):
            lease_guard()
        return is_cancel_requested(db, job.id)

    if should_cancel():
        raise MarketDataPrewarmCancelled("Market-data prewarm cancellation requested.")

    plan = build_fetch_plan(
        db=db,
        tickers=list(tickers),
        include_benchmarks=request.include_benchmarks,
        settings=settings,
    )
    planned_coverage = _classify_plan_coverage(plan, tickers)
    fetch_run = execute_fetch_plan(
        db=db,
        plan=plan,
        settings=settings,
        include_benchmarks=request.include_benchmarks,
        should_cancel=should_cancel,
    )
    if fetch_run.status == "CANCELLED":
        raise MarketDataPrewarmCancelled("Market-data prewarm was cancelled.")

    benchmark_symbols = settings.ib_benchmark_symbols if request.include_benchmarks else ()
    coverage = summarize_ohlcv_coverage(
        db,
        list(tickers),
        benchmarks=benchmark_symbols,
        settings=settings,
    )
    coverage_ratio = (
        coverage.ready_count / coverage.total_tickers if coverage.total_tickers else 0.0
    )
    fetched_tickers = _fetched_tickers(fetch_run.items, tickers)
    ready_tickers = tuple(
        sorted(
            item.ticker
            for item in getattr(coverage, "items", ())
            if item.ticker in tickers and item.status == "ready"
        )
    )
    performance = getattr(fetch_run, "_performance", {}) or {}
    failures = [
        {
            "ticker": item.ticker,
            "what_to_show": item.what_to_show,
            "error_message": item.error_message,
        }
        for item in fetch_run.items
        if item.status == "FAILED"
    ]
    status = "PARTIAL" if fetch_run.status in {"PARTIAL", "FAILED"} else "COMPLETED"
    result = {
        "job_type": MARKET_DATA_PREWARM,
        "status": status,
        "request_key": job.request_key,
        "universe_source": request.universe_source,
        "universe_fingerprint": job.payload_json.get("universe_fingerprint"),
        "effective_session": job.payload_json.get(
            "effective_session",
            (request.freshness_date or latest_completed_us_trading_day()).isoformat(),
        ),
        "bar_size": job.payload_json.get("bar_size", settings.ib_default_bar_size),
        "data_types": job.payload_json.get("data_types", list(DEFAULT_WHAT_TO_SHOW)),
        "config_version": job.payload_json.get(
            "config_version",
            settings.market_data_prewarm_config_version,
        ),
        "config_fingerprint": job.payload_json.get("config_fingerprint"),
        "tickers": list(tickers),
        "ticker_count": len(tickers),
        "include_benchmarks": request.include_benchmarks,
        "freshness_date": (request.freshness_date or _today()).isoformat(),
        "requested_by": request.requested_by,
        "fetch_run_id": fetch_run.id,
        "fetch_status": fetch_run.status,
        "planned_request_count": fetch_run.planned_request_count,
        "decision_counts": getattr(fetch_run, "decision_counts_json", {}),
        "executed_request_count": fetch_run.executed_request_count,
        "requests_made": fetch_run.executed_request_count,
        "already_current_tickers": list(planned_coverage["already_current"]),
        "stale_or_missing_tickers": list(planned_coverage["stale_or_missing"]),
        "fetched_tickers": list(fetched_tickers),
        "coverage_ready_tickers": list(ready_tickers),
        "ib_pacing_wait_ms": performance.get("ib_pacing_wait_ms"),
        "ib_network_ms": performance.get("ib_network_ms"),
        "bar_cache_write_ms": performance.get("bar_cache_write_ms"),
        "prewarm_age_seconds": 0,
        "foreground_reuse_observations": list(
            job.payload_json.get("foreground_reuse_observations") or []
        ),
        "preemption_history": list(job.payload_json.get("preemption_history") or []),
        "preemption_count": int(job.payload_json.get("preemption_count") or 0),
        "success_count": fetch_run.success_count,
        "failure_count": fetch_run.failure_count,
        "skipped_count": fetch_run.skipped_count,
        "coverage_ratio": round(coverage_ratio, 6),
        "coverage_ready_count": coverage.ready_count,
        "coverage_total_tickers": coverage.total_tickers,
        "coverage_stale_count": coverage.stale_count,
        "coverage_missing_count": coverage.missing_count,
        "coverage_insufficient_count": coverage.insufficient_count,
        "coverage_missing_volume_count": coverage.missing_volume_count,
        "coverage_failed_contract_count": coverage.failed_contract_count,
        "failures": failures,
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if status == "PARTIAL":
        job.status = "PARTIAL"
    return result


def execute_market_data_prewarm_job(db: Session, job: BackgroundJob) -> dict[str, Any]:
    """Background-worker adapter using the worker's shared lease/cancel hooks."""
    from app.services.background_worker import JobDeferred

    if _foreground_pipeline_active(db):
        raise JobDeferred(
            "Foreground pipeline is active; market-data prewarm yielded the broker lane.",
            delay_seconds=get_settings().market_data_prewarm_resume_delay_seconds,
        )
    try:
        result = execute_market_data_prewarm(db, job)
    except MarketDataPrewarmCancelled as exc:
        if _foreground_preemption_requested(job):
            _prepare_preempted_job_for_resume(db, job)
            operational_metrics.increment(
                "swinglens_market_prewarm_preemptions_total",
                status="DEFERRED",
            )
            raise JobDeferred(
                "Foreground pipeline preempted market-data prewarm; completed bars were "
                "preserved and remaining coverage will be replanned.",
                delay_seconds=get_settings().market_data_prewarm_resume_delay_seconds,
            ) from exc
        operational_metrics.increment("swinglens_market_prewarm_jobs_total", status="CANCELLED")
        from app.services.background_worker import CancelRequested

        raise CancelRequested("Market-data prewarm was cancelled.") from None
    except Exception:
        operational_metrics.increment("swinglens_market_prewarm_jobs_total", status="FAILED")
        raise

    operational_metrics.increment("swinglens_market_prewarm_jobs_total", status=result["status"])
    operational_metrics.increment(
        "swinglens_market_prewarm_coverage_ratio",
        value=float(result["coverage_ratio"]),
    )
    return result


def _tickers_from_recent_completed_runs(db: Session, recent_run_count: int) -> list[str]:
    runs = db.scalars(
        select(UploadRun)
        .where(UploadRun.status == "COMPLETED")
        .order_by(
            UploadRun.processed_at.desc().nullslast(),
            UploadRun.uploaded_at.desc(),
        )
        .limit(recent_run_count)
    ).all()
    tickers: list[str] = []
    for run in runs:
        rows = db.scalars(
            select(RawCompanyRow.ticker)
            .where(RawCompanyRow.run_id == run.id)
            .order_by(RawCompanyRow.row_number)
        ).all()
        tickers.extend(rows)
    return _normalize_tickers(tickers)


def request_active_prewarm_preemption(
    db: Session,
    *,
    pipeline_run_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[int]:
    """Request cooperative cancellation of running prewarm jobs for foreground work."""
    if not isinstance(db, Session):
        return []
    settings = settings or get_settings()
    if not settings.market_data_prewarm_enabled:
        return []
    requested_at = now or datetime.now(UTC)
    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == MARKET_DATA_PREWARM,
                BackgroundJob.status == JobStatus.RUNNING,
                BackgroundJob.requested_cancel.is_(False),
            )
            .with_for_update(skip_locked=True)
        )
    )
    for job in jobs:
        payload = dict(job.payload_json or {})
        payload["foreground_preemption"] = {
            "pipeline_run_id": pipeline_run_id,
            "requested_at": requested_at.isoformat(),
            "deadline_at": (
                requested_at + timedelta(seconds=settings.market_data_prewarm_cancel_bound_seconds)
            ).isoformat(),
        }
        job.payload_json = payload
        job.requested_cancel = True
    if jobs:
        db.flush()
        operational_metrics.increment(
            "swinglens_market_prewarm_preemptions_total",
            value=len(jobs),
            status="REQUESTED",
        )
    return [job.id for job in jobs]


def resolve_pipeline_prewarm_context(
    db: Session,
    tickers: list[str] | tuple[str, ...],
    *,
    now: datetime | None = None,
) -> PipelinePrewarmContext:
    if not isinstance(db, Session):
        return PipelinePrewarmContext()
    requested = set(_normalize_tickers(tickers))
    if not requested:
        return PipelinePrewarmContext()
    observed_at = now or datetime.now(UTC)
    jobs = list(
        db.scalars(
            select(BackgroundJob)
            .where(
                BackgroundJob.job_type == MARKET_DATA_PREWARM,
                BackgroundJob.status.in_((JobStatus.COMPLETED, JobStatus.PARTIAL)),
                BackgroundJob.completed_at.is_not(None),
            )
            .order_by(BackgroundJob.completed_at.desc(), BackgroundJob.id.desc())
            .limit(20)
        )
    )
    current_session = latest_completed_us_trading_day(observed_at)
    selected_job: BackgroundJob | None = None
    selected_ready: set[str] = set()
    selected_already_current: set[str] = set()
    fetched_for_session: set[str] = set()
    for job in jobs:
        result = job.result_json or {}
        universe = set(_normalize_tickers(result.get("tickers") or []))
        if not requested.intersection(universe):
            continue
        effective_session = _optional_date(result.get("effective_session"))
        if selected_job is None:
            selected_job = job
            selected_ready = requested.intersection(
                _normalize_tickers(result.get("coverage_ready_tickers") or [])
            )
            selected_already_current = requested.intersection(
                _normalize_tickers(result.get("already_current_tickers") or [])
            )
        if effective_session == current_session:
            fetched_for_session.update(
                requested.intersection(_normalize_tickers(result.get("fetched_tickers") or []))
            )
    if selected_job is not None:
        selected_result = selected_job.result_json or {}
        selected_session = _optional_date(selected_result.get("effective_session"))
        fresh = selected_session == current_session
        return PipelinePrewarmContext(
            job_id=selected_job.id,
            age_seconds=max(
                0.0,
                (observed_at - selected_job.completed_at.astimezone(UTC)).total_seconds(),
            ),
            effective_session=selected_session,
            fresh_for_session=fresh,
            covered_tickers=tuple(sorted(selected_ready if fresh else ())),
            fetched_tickers=tuple(sorted(fetched_for_session)),
            already_current_tickers=tuple(sorted(selected_already_current)),
        )
    return PipelinePrewarmContext()


def record_pipeline_prewarm_reuse(
    db: Session,
    context: PipelinePrewarmContext,
    *,
    pipeline_run_id: int,
    reused_tickers: list[str] | tuple[str, ...],
    now: datetime | None = None,
) -> None:
    if context.job_id is None or not isinstance(db, Session):
        return
    job = db.get(BackgroundJob, context.job_id)
    if job is None or not job.result_json:
        return
    observed_at = now or datetime.now(UTC)
    observations = [
        item
        for item in (job.result_json.get("foreground_reuse_observations") or [])
        if int(item.get("pipeline_run_id", -1)) != pipeline_run_id
    ]
    observations.append(
        {
            "pipeline_run_id": pipeline_run_id,
            "observed_at": observed_at.isoformat(),
            "covered_tickers": list(context.covered_tickers),
            "reused_tickers": _normalize_tickers(reused_tickers),
            "reused_ticker_count": len(set(_normalize_tickers(reused_tickers))),
        }
    )
    result = dict(job.result_json)
    result["foreground_reuse_observations"] = observations[-PREWARM_REUSE_OBSERVATION_LIMIT:]
    result["foreground_reuse_pipeline_count"] = len(result["foreground_reuse_observations"])
    result["foreground_reuse_ticker_count"] = sum(
        int(item.get("reused_ticker_count") or 0)
        for item in result["foreground_reuse_observations"]
    )
    job.result_json = result
    db.flush()


def _prewarm_config_fingerprint(settings: Settings) -> str:
    canonical = json.dumps(
        {
            "config_version": settings.market_data_prewarm_config_version,
            "bar_size": settings.ib_default_bar_size,
            "data_types": DEFAULT_WHAT_TO_SHOW,
            "use_rth": settings.ib_use_rth,
            "required_daily_bars": settings.ib_required_daily_bars,
            "revision_window_sessions": settings.ib_revision_window_sessions,
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _foreground_pipeline_active(db: Session) -> bool:
    if not isinstance(db, Session):
        return False
    return bool(
        db.scalar(
            select(BackgroundJob.id)
            .where(
                BackgroundJob.job_type == "FULL_PIPELINE",
                BackgroundJob.status.in_(ACTIVE_JOB_STATUSES),
            )
            .limit(1)
        )
    )


def _foreground_preemption_requested(job: BackgroundJob) -> bool:
    return bool((job.payload_json or {}).get("foreground_preemption"))


def _prepare_preempted_job_for_resume(
    db: Session,
    job: BackgroundJob,
    *,
    now: datetime | None = None,
) -> None:
    observed_at = now or datetime.now(UTC)
    payload = dict(job.payload_json or {})
    preemption = dict(payload.pop("foreground_preemption", {}) or {})
    requested_at = _optional_datetime(preemption.get("requested_at"))
    preemption["stopped_at"] = observed_at.isoformat()
    preemption["stop_latency_seconds"] = (
        round(max(0.0, (observed_at - requested_at).total_seconds()), 3)
        if requested_at is not None
        else None
    )
    history = list(payload.get("preemption_history") or [])
    history.append(preemption)
    payload["preemption_history"] = history[-10:]
    payload["preemption_count"] = int(payload.get("preemption_count") or 0) + 1
    payload["last_preempted_at"] = observed_at.isoformat()
    job.payload_json = payload
    job.requested_cancel = False
    db.flush()


def _classify_plan_coverage(
    plan: FetchPlan,
    requested_tickers: tuple[str, ...],
) -> dict[str, tuple[str, ...]]:
    requested = set(requested_tickers)
    by_ticker: dict[str, list[Any]] = {}
    for item in getattr(plan, "items", ()):
        if item.ticker in requested:
            by_ticker.setdefault(item.ticker, []).append(item)
    already_current = tuple(
        sorted(
            ticker
            for ticker, items in by_ticker.items()
            if items and all(item.action == FetchAction.SKIP for item in items)
        )
    )
    stale_or_missing = tuple(
        sorted(
            ticker
            for ticker, items in by_ticker.items()
            if any(item.action != FetchAction.SKIP for item in items)
        )
    )
    return {
        "already_current": already_current,
        "stale_or_missing": stale_or_missing,
    }


def _fetched_tickers(items: list[Any], requested_tickers: tuple[str, ...]) -> tuple[str, ...]:
    requested = set(requested_tickers)
    return tuple(
        sorted(
            {
                item.ticker
                for item in items
                if item.ticker in requested
                and item.status == "SUCCESS"
                and (getattr(item, "fetched", 0) or 0) > 0
            }
        )
    )


def _optional_date(value: Any) -> date | None:
    if not value:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def _optional_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _cap_tickers(tickers: list[str], maximum: int) -> list[str]:
    if maximum < 1:
        raise ValueError("market_data_prewarm_max_tickers must be positive.")
    return _normalize_tickers(tickers)[:maximum]


def _normalize_source(value: Any) -> str:
    return str(value or "RECENT_RUNS").strip().upper().replace("-", "_")


def _normalize_tickers(values: Any) -> list[str]:
    if isinstance(values, str):
        values = values.split(",")
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values or []:
        symbol = str(value).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            normalized.append(symbol)
    return normalized


def _parse_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _today() -> date:
    return datetime.now(UTC).date()
