"""Certification-only business input clocks; worker liveness stays native."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import urlparse

from single_run_certification.candidate_validation import artifact, load_golden

_active = ContextVar("certification_golden_inputs", default=None)


def current_manifest():
    return _active.get()


def business_now():
    manifest = current_manifest()
    return (datetime.fromisoformat(manifest["business_time"]["decision_at"])
            if manifest else datetime.now(UTC))


def supply_availability_defaults(row, availability, decision_at, *, updating=False):
    """Clock defaults for the explicitly declared replay source families only."""
    table = getattr(row, "__tablename__", None)
    if table == "upload_runs":
        clocks = {"uploaded_at": "upload_at", "processed_at": "processed_at"}
    elif table == "raw_company_rows":
        clocks = {"created_at": "source_received_at"}
    elif table in {"fundamental_scores", "technical_scores", "combined_results",
                   "ranking_results", "market_regime_snapshots", "sector_rotation_snapshots"}:
        clocks = {name: "projection_at" for name in ("created_at", "updated_at")
                  if hasattr(row, name)}
    else:
        return
    for field, clock in clocks.items():
        value = getattr(row, field)
        if field == "processed_at" and value is None:
            continue  # Do not declare an unprocessed upload already processed.
        if (value is None or not isinstance(value, datetime)
                or (value.replace(tzinfo=UTC) if value.tzinfo is None else value) > decision_at
                or (field == "updated_at" and updating)):
            setattr(row, field, datetime.fromisoformat(availability[clock]))
            if updating and field == "updated_at":
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(row, field)


def frozen_cache_bars(ticker: str, count: int) -> list[dict]:
    prices = artifact("market-data.json")["cache"][ticker]
    sessions = artifact("calendar.json")["bar_sessions"][-count:]
    # The original short-history fixtures start their arithmetic progression at
    # index zero; they are not a suffix of the full 320-price progression.
    return [{"date": datetime.fromisoformat(session).date(), **{
        field: Decimal(prices[index][field])
        for field in ("open", "high", "low", "close", "volume")
    }} for index, session in enumerate(sessions)]


def fixed_datetime(at):
    class BusinessDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)

    return BusinessDateTime


@contextmanager
def business_inputs(database_url: str):
    """Scoped fixture seam, also held open by the allowlisted child adapter.

    No handler, executor, producer, selector, scoring function, heartbeat,
    execution fence, monotonic timer or watchdog implementation is replaced.
    """
    parsed = urlparse(database_url)
    if (parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not parsed.path.lstrip("/").startswith("swinglens_pytest_cert_")):
        raise RuntimeError("GOLDEN_BUSINESS_INPUTS_REQUIRE_DISPOSABLE_DATABASE")
    manifest = load_golden()
    times = {key: datetime.fromisoformat(value)
             for key, value in manifest["business_time"].items()}
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    from app.models.tables import TransitionDecisionHandoffManifest
    from app.services import (
        bar_cache_service,
        market_calculation_context_service,
        us_market_calendar,
    )
    from app.services.ceri import capture_service
    from app.services.ceri.providers import manual_provider
    from app.services.market_clock_service import MarketClockService
    from app.services.setup_lifecycle import snapshot_builder
    from app.services.winner_probability import capture_service as winner_capture

    class GoldenContextClock(MarketClockService):
        def cutoff_for(self, cutoff_at, *, reason):
            if reason in {"FULL_PIPELINE_FROZEN_AT_ENQUEUE", "TRANSITION_PREFLIGHT_RESERVED"}:
                cutoff_at = times["market_cutoff"]
            elif reason == "CERI_POST_ACQUISITION_FROZEN":
                cutoff_at = times["ceri_cutoff"]
            return super().cutoff_for(cutoff_at, reason=reason)

    def supply_decision_time(db, _context, _instances):
        # This native row's server-default timestamp is a Winner business-time
        # input. Supply only that clock default, before the real ORM insert;
        # never mutate selected identities, scores, states or sealed payloads.
        for row in db.new:
            if isinstance(row, TransitionDecisionHandoffManifest) and row.created_at is None:
                row.created_at = times["decision_at"]
        availability = manifest.get("source_availability")
        if availability:
            for row in db.new:
                supply_availability_defaults(row, availability, times["decision_at"])
            for row in db.dirty:
                if db.is_modified(row, include_collections=False):
                    supply_availability_defaults(row, availability, times["decision_at"],
                                                 updating=True)

    with ExitStack() as stack:
        token = _active.set(manifest)
        stack.callback(_active.reset, token)
        stack.enter_context(patch.object(market_calculation_context_service,
                                        "MarketClockService", GoldenContextClock))
        receipt = times["market_cutoff"]-timedelta(minutes=1)
        native_record = manual_provider._raw_provider_record

        def record_with_receipt(**kwargs):
            record = native_record(**kwargs)
            return replace(record, retrieved_at=record.retrieved_at or receipt)

        stack.enter_context(patch.object(manual_provider, "_raw_provider_record",
                                        record_with_receipt))
        # Implicit latest-session queries are business inputs too. Keep the
        # planner, receipt scope and coverage checks on the replay's session;
        # explicit timestamps and native operational/liveness clocks stay real.
        for module, at in ((us_market_calendar, times["market_cutoff"]),
                           (bar_cache_service, receipt),
                           (capture_service, times["decision_at"]),
                           (winner_capture, times["decision_at"])):
            stack.enter_context(patch.object(module, "datetime", fixed_datetime(at)))
        stack.enter_context(patch.object(snapshot_builder, "_utcnow",
                                        lambda: times["decision_at"].astimezone(UTC)))
        event.listen(Session, "before_flush", supply_decision_time)
        try:
            yield manifest
        finally:
            event.remove(Session, "before_flush", supply_decision_time)
