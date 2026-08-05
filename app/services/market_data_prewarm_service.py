from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, RawCompanyRow, UploadRun
from app.services.background_job_service import enqueue_job, is_cancel_requested
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import build_fetch_plan
from app.services.ohlcv_coverage_service import summarize_ohlcv_coverage
from app.services.operational_metrics import operational_metrics
from app.settings import Settings, get_settings

MARKET_DATA_PREWARM = "MARKET_DATA_PREWARM"
DEFAULT_RECENT_RUN_COUNT = 5
SUPPORTED_UNIVERSE_SOURCES = {"EXPLICIT", "TICKERS", "WATCHLIST", "RECENT_RUNS"}


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

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            {
                "tickers": self.tickers,
                "include_benchmarks": self.include_benchmarks,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()


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

    tickers = _cap_tickers(tickers, settings.market_data_prewarm_max_tickers)
    if not tickers:
        raise ValueError(f"No tickers were resolved from prewarm universe source {source}.")

    return MarketDataPrewarmUniverse(
        source=source,
        recent_run_count=request.recent_run_count,
        tickers=tuple(tickers),
        include_benchmarks=request.include_benchmarks,
        freshness_date=request.freshness_date or _today(),
        requested_by=request.requested_by,
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
            "max_tickers": settings.market_data_prewarm_max_tickers,
        }
    )
    job = enqueue_job(
        db,
        MARKET_DATA_PREWARM,
        payload,
        request_key=universe.fingerprint,
        priority=80,
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
        "tickers": list(tickers),
        "ticker_count": len(tickers),
        "include_benchmarks": request.include_benchmarks,
        "freshness_date": (request.freshness_date or _today()).isoformat(),
        "requested_by": request.requested_by,
        "fetch_run_id": fetch_run.id,
        "fetch_status": fetch_run.status,
        "planned_request_count": fetch_run.planned_request_count,
        "executed_request_count": fetch_run.executed_request_count,
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
    try:
        result = execute_market_data_prewarm(db, job)
    except MarketDataPrewarmCancelled:
        operational_metrics.increment(
            "swinglens_market_prewarm_jobs_total", status="CANCELLED"
        )
        from app.services.background_worker import CancelRequested

        raise CancelRequested("Market-data prewarm was cancelled.") from None
    except Exception:
        operational_metrics.increment("swinglens_market_prewarm_jobs_total", status="FAILED")
        raise

    operational_metrics.increment(
        "swinglens_market_prewarm_jobs_total", status=result["status"]
    )
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
