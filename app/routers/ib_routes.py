from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.bar_cache_service import DEFAULT_WHAT_TO_SHOW
from app.services.ib_connection import check_ib_connection, create_ib_client
from app.services.ib_contract_resolver import resolve_us_stock_contract
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import build_fetch_plan
from app.settings import get_settings

router = APIRouter(prefix="/ib", tags=["interactive-brokers"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/status")
def ib_status() -> dict[str, object]:
    settings = get_settings()
    return {
        "host": settings.ib_host,
        "port": settings.ib_port,
        "client_id": settings.ib_client_id,
        "default_duration": settings.ib_default_duration,
        "full_backfill_duration": settings.ib_full_backfill_duration,
        "top_up_duration": settings.ib_top_up_duration,
        "refresh_duration": settings.ib_refresh_duration,
        "default_bar_size": settings.ib_default_bar_size,
        "requests_per_minute": settings.ib_requests_per_minute,
        "min_seconds_between_requests": settings.ib_min_seconds_between_requests,
        "backoff_seconds": settings.ib_backoff_seconds,
        "max_retries": settings.ib_max_retries,
        "conservative_mode": settings.ib_force_conservative_mode,
        "fetch_benchmarks": settings.ib_fetch_benchmarks,
        "benchmarks": list(settings.ib_benchmark_symbols),
        "required_daily_bars": settings.ib_required_daily_bars,
        "daily_bar_stale_after_days": settings.ib_daily_bar_stale_after_days,
        "revision_audit_enabled": settings.ib_revision_audit_enabled,
        "use_rth": settings.ib_use_rth,
        "order_endpoints": False,
    }


@router.post("/test")
def test_ib_connection() -> dict[str, object]:
    status = check_ib_connection()
    return {
        "connected": status.connected,
        "host": status.host,
        "port": status.port,
        "client_id": status.client_id,
        "message": status.message,
    }


@router.post("/resolve/{ticker}")
def resolve_ticker(ticker: str, db: DbSession, force_refresh: bool = False) -> dict[str, object]:
    ib = create_ib_client()
    settings = get_settings()
    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        resolution = resolve_us_stock_contract(db, ticker, ib, force_refresh=force_refresh)
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        if ib.isConnected():
            ib.disconnect()

    row = resolution.cache_row
    return {
        "ticker": row.ticker,
        "status": row.resolution_status,
        "ib_conid": row.ib_conid,
        "symbol": row.symbol,
        "exchange": row.exchange,
        "primary_exchange": row.primary_exchange,
        "currency": row.currency,
        "sec_type": row.sec_type,
        "error_message": row.error_message,
    }


@router.post("/fetch")
def fetch_bars(
    db: DbSession,
    tickers: Annotated[str, Query(description="Comma-separated ticker list")],
    include_benchmarks: bool = True,
    force_refresh: bool = False,
    force_full_backfill: bool = False,
    what_to_show: Annotated[list[str] | None, Query()] = None,
) -> dict[str, object]:
    ticker_list = [ticker.strip() for ticker in tickers.split(",") if ticker.strip()]
    if not ticker_list:
        raise HTTPException(status_code=400, detail="Provide at least one ticker.")

    try:
        plan = build_fetch_plan(
            db=db,
            tickers=ticker_list,
            include_benchmarks=include_benchmarks,
            force_refresh=force_refresh,
            force_full_backfill=force_full_backfill,
            what_to_show_values=_what_to_show_values(what_to_show),
        )
        fetch_run = execute_fetch_plan(
            db,
            plan,
            include_benchmarks=include_benchmarks,
            force_refresh=force_refresh,
            force_full_backfill=force_full_backfill,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {
        "fetch_run_id": fetch_run.id,
        "status": fetch_run.status,
        "message": fetch_run.message,
        "requested_tickers": ticker_list,
        "include_benchmarks": include_benchmarks,
        "force_refresh": force_refresh,
        "force_full_backfill": force_full_backfill,
        "symbols_including_benchmarks": fetch_run.symbols_including_benchmarks,
        "planned_request_count": fetch_run.planned_request_count,
        "executed_request_count": fetch_run.executed_request_count,
        "fetched": fetch_run.fetched_count,
        "inserted": fetch_run.inserted_count,
        "updated": fetch_run.updated_count,
        "revised": fetch_run.revised_count,
        "unchanged": fetch_run.unchanged_count,
        "failures": [
            {
                "ticker": item.ticker,
                "what_to_show": item.what_to_show,
                "error_message": item.error_message,
            }
            for item in fetch_run.items
            if item.status == "FAILED"
        ],
        "items": [
            {
                "ticker": item.ticker,
                "what_to_show": item.what_to_show,
                "fetched": item.fetched,
                "inserted": item.inserted,
                "updated": item.updated,
                "revised": item.revised,
                "unchanged": item.unchanged,
                "status": item.status,
                "action": item.action,
                "duration": item.duration,
                "reason": item.reason,
            }
            for item in fetch_run.items
        ],
    }


def _what_to_show_values(values: list[str] | None) -> tuple[str, ...]:
    allowed = set(DEFAULT_WHAT_TO_SHOW)
    normalized = tuple(value for value in values or DEFAULT_WHAT_TO_SHOW if value in allowed)
    return normalized or DEFAULT_WHAT_TO_SHOW
