"""Manual MSI admission only; acquisition stays with the existing IB_FETCH worker."""

from datetime import UTC, datetime
from threading import Lock
from time import monotonic

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob
from app.services.background_job_service import ACTIVE_JOB_STATUSES
from app.services.ib_fetch_job_service import (
    IB_FETCH_JOB_TYPE,
    FetchJobOptions,
    create_queued_fetch_run,
    get_fetch_progress,
    submit_fetch_job,
)
from app.services.ib_fetch_plan_service import build_fetch_plan
from app.services.ib_gateway_health_service import check_status
from app.services.market_sector_intelligence_config import load_market_sector_intelligence_config
from app.services.market_sector_intelligence_technical_adapter import scoped_policy

MARKER = "msi-sector-refresh-v1"
BASES = ("TRADES", "ADJUSTED_LAST")
# Feature-scoped transaction lock; unrelated stock/pipeline acquisition is unaffected.
LOCK_KEY = 0x4D534952454652
RETRY_SECONDS = 5
_probe_lock = Lock()
_last_probe = float("-inf")


class SectorRefreshRejected(ValueError):
    def __init__(self, code, message, status_code=409):
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


def _jobs(settings):
    return select(BackgroundJob).where(
        BackgroundJob.job_type == IB_FETCH_JOB_TYPE,
        BackgroundJob.id >= settings.job_activation_min_id,
        BackgroundJob.payload_json["msi_refresh"].astext == MARKER,
    )


def active_sector_refresh(db: Session, *, settings):
    with db.no_autoflush:
        return db.scalar(
            _jobs(settings)
            .where(BackgroundJob.status.in_(ACTIVE_JOB_STATUSES))
            .order_by(BackgroundJob.id.desc())
            .limit(1)
        )


def _preflight(settings):
    # Failed probes create no durable records. Bound repeated deliberate attempts
    # too, including requests from other tabs in this web process.
    global _last_probe
    with _probe_lock:
        now = monotonic()
        if now - _last_probe < RETRY_SECONDS:
            raise SectorRefreshRejected(
                "REFRESH_RATE_LIMITED", "Wait a few seconds before trying again.", 429
            )
        _last_probe = now
    status = check_status(settings=settings)
    if not (
        status.status == "IB_API_READY"
        and status.api_connected
        and status.api_ready
        and status.smoke_response_received
    ):
        raise SectorRefreshRejected(
            "IB_GATEWAY_NOT_READY",
            "IB Gateway is not connected or ready. "
            "Start and sign in to IB Gateway, then try again.",
            503,
        )


def enqueue_sector_refresh(db: Session, *, settings):
    config = load_market_sector_intelligence_config()
    if not config.enabled:
        raise SectorRefreshRejected("MSI_DISABLED", "Market Sector Intelligence is disabled.")
    # PostgreSQL serializes admission across processes. The lock is released by
    # the route's commit/rollback, covering both run and job creation atomically.
    if db.get_bind().dialect.name != "postgresql":
        raise SectorRefreshRejected(
            "REFRESH_STORAGE_UNAVAILABLE", "Refresh storage is unavailable.", 503
        )
    if not db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_KEY}):
        raise SectorRefreshRejected(
            "REFRESH_ADMISSION_BUSY", "Another refresh is being admitted. Check again shortly.", 409
        )
    active = active_sector_refresh(db, settings=settings)
    if active is not None:
        return sector_refresh_status(db, active, coalesced=True)
    previous = db.scalar(_jobs(settings).order_by(BackgroundJob.id.desc()).limit(1))
    if previous and (datetime.now(UTC) - previous.created_at).total_seconds() < RETRY_SECONDS:
        raise SectorRefreshRejected(
            "REFRESH_RATE_LIMITED", "Wait a few seconds before trying again.", 429
        )
    _preflight(settings)  # No fetch runs, authority records or jobs exist yet.
    symbols = [sector.etf_symbol for sector in config.sectors]
    required = scoped_policy(symbols).identity["history_window_sessions"]
    # Enough daily history for native consumers, scoped to this manual operation.
    # Never enable automatic prewarm or change the stock pipeline's settings.
    plan_settings = settings.model_copy(
        update={
            "ib_fetch_benchmarks": True,
            "ib_benchmarks": "SPY,QQQ",
            "ib_default_bar_size": config.timeframe,
            "ib_full_backfill_duration": "4 Y",
            "ib_required_daily_bars": max(settings.ib_required_daily_bars, required),
        }
    )
    plan = build_fetch_plan(
        db,
        symbols,
        include_benchmarks=True,
        force_refresh=True,
        what_to_show_values=BASES,
        settings=plan_settings,
    )
    options = FetchJobOptions(force_refresh=True)
    fetch_run = create_queued_fetch_run(db, plan, options)
    job = submit_fetch_job(db, fetch_run.id, plan, options)
    if job.id < settings.job_activation_min_id:
        raise SectorRefreshRejected(
            "REFRESH_ACTIVATION_FENCE", "New job is below the activation boundary."
        )
    job.payload_json = {**job.payload_json, "msi_refresh": MARKER}
    db.flush()
    return sector_refresh_status(db, job)


def find_sector_refresh(db, job_id, *, settings):
    with db.no_autoflush:
        return db.scalar(_jobs(settings).where(BackgroundJob.id == job_id))


def sector_refresh_status(db, job, *, coalesced=False):
    with db.no_autoflush:
        progress = get_fetch_progress(db, int(job.payload_json["fetch_run_id"]))
    status = str(job.status)
    if status == "COMPLETED":
        status = (
            progress["status"]
            if progress["status"] in {"COMPLETED", "PARTIAL", "FAILED", "CANCELLED"}
            else "UNKNOWN"
        )
    elif status in {"QUEUED", "RECOVERING", "RUNNING"}:
        # A worker still owns the operation even if the fetch run has just ended.
        status = "RUNNING" if status == "RUNNING" else "QUEUED"
    elif status not in {"PARTIAL", "FAILED", "CANCELLED"}:
        status = "FAILED"
    return {
        "job_id": job.id,
        "fetch_run_id": progress["fetch_run_id"],
        "status": status,
        "coalesced": coalesced,
        "status_url": f"/api/market-sector-intelligence/refresh/{job.id}",
        "completed_items": progress["completed_items"],
        "total_items": progress["total_items"],
        "success_count": progress["success_count"],
        "failure_count": progress["failure_count"],
        "failures": [
            {"ticker": item["ticker"], "basis": item["what_to_show"]}
            for item in progress["items"]
            if item["status"] == "FAILED"
        ],
    }
