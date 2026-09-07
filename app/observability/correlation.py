from __future__ import annotations

import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


@dataclass(frozen=True)
class CausalityContext:
    root_correlation_id: str
    causation_id: str
    request_id: str | None = None
    background_job_id: int | None = None
    parent_job_id: int | None = None
    triggered_by_job_id: int | None = None
    trigger_kind: str = "ADMINISTRATIVE"
    trigger_name: str = "unspecified"
    fanout_group_id: str | None = None


_current: ContextVar[CausalityContext | None] = ContextVar(
    "swinglens_causality_context", default=None
)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid4().hex}"


def current_causality() -> CausalityContext | None:
    return _current.get()


def context_fields() -> dict[str, object]:
    context = current_causality()
    if context is None:
        return {}
    return {
        "root_correlation_id": context.root_correlation_id,
        "causation_id": context.causation_id,
        "request_id": context.request_id,
        "background_job_id": context.background_job_id,
        "parent_job_id": context.parent_job_id,
        "triggered_by_job_id": context.triggered_by_job_id,
        "trigger_kind": context.trigger_kind,
        "trigger_name": context.trigger_name,
        "fanout_group_id": context.fanout_group_id,
    }


def durable_causality_fields() -> dict[str, object]:
    """Return nullable fields suitable for durable downstream telemetry rows."""
    context = current_causality()
    if context is None:
        return {
            "root_correlation_id": None,
            "causation_id": None,
            "background_job_id": None,
            "triggered_by_request_id": None,
        }
    return {
        "root_correlation_id": context.root_correlation_id,
        "causation_id": context.causation_id,
        "background_job_id": context.background_job_id,
        "triggered_by_request_id": context.request_id,
    }


@contextmanager
def causality_scope(context: CausalityContext) -> Iterator[CausalityContext]:
    token = _current.set(context)
    try:
        yield context
    finally:
        _current.reset(token)


@contextmanager
def root_action_scope(
    trigger_kind: str,
    trigger_name: str,
    *,
    request_id: str | None = None,
    root_correlation_id: str | None = None,
    fanout_group_id: str | None = None,
) -> Iterator[CausalityContext]:
    context = CausalityContext(
        root_correlation_id=_safe_id(root_correlation_id) or new_id("root"),
        causation_id=new_id("cause"),
        request_id=request_id,
        trigger_kind=_bounded(trigger_kind, 64).upper(),
        trigger_name=_bounded(trigger_name, 200),
        fanout_group_id=_safe_id(fanout_group_id),
    )
    with causality_scope(context):
        yield context


@contextmanager
def worker_job_scope(job: object) -> Iterator[CausalityContext]:
    job_id = getattr(job, "id", None)
    root = getattr(job, "root_correlation_id", None) or new_id("root-recovery")
    context = CausalityContext(
        root_correlation_id=str(root),
        causation_id=str(getattr(job, "causation_id", None) or new_id("cause")),
        request_id=getattr(job, "triggered_by_request_id", None),
        background_job_id=int(job_id) if job_id is not None else None,
        parent_job_id=getattr(job, "parent_job_id", None),
        triggered_by_job_id=getattr(job, "triggered_by_job_id", None),
        trigger_kind="JOB",
        trigger_name=str(getattr(job, "job_type", "unknown")),
        fanout_group_id=getattr(job, "fanout_group_id", None),
    )
    with causality_scope(context):
        yield context


def enqueue_causality(
    *,
    job_type: str,
    explicit: CausalityContext | None = None,
    parent_job_id: int | None = None,
    trigger_kind: str | None = None,
    trigger_name: str | None = None,
    request_id: str | None = None,
    fanout_group_id: str | None = None,
) -> CausalityContext:
    inherited = explicit or current_causality()
    if inherited is None:
        return CausalityContext(
            root_correlation_id=new_id("root"),
            causation_id=new_id("cause"),
            request_id=request_id,
            parent_job_id=parent_job_id,
            trigger_kind=_bounded(trigger_kind or "ADMINISTRATIVE", 64).upper(),
            trigger_name=_bounded(trigger_name or job_type, 200),
            fanout_group_id=_safe_id(fanout_group_id),
        )
    inferred_parent = parent_job_id if parent_job_id is not None else inherited.background_job_id
    inferred_kind = trigger_kind or (
        "JOB" if inherited.background_job_id is not None else inherited.trigger_kind
    )
    inferred_name = trigger_name or (
        inherited.trigger_name if inherited.background_job_id is None else inherited.trigger_name
    )
    return replace(
        inherited,
        causation_id=new_id("cause"),
        parent_job_id=inferred_parent,
        triggered_by_job_id=inferred_parent,
        background_job_id=None,
        request_id=request_id or inherited.request_id,
        trigger_kind=_bounded(inferred_kind, 64).upper(),
        trigger_name=_bounded(inferred_name or job_type, 200),
        fanout_group_id=_safe_id(fanout_group_id) or inherited.fanout_group_id,
    )


class CorrelationMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.lower(): value for key, value in scope.get("headers", [])}
        request_id = _safe_id(_decode(headers.get(b"x-request-id"))) or new_id("request")
        root_id = _safe_id(_decode(headers.get(b"x-root-correlation-id")))
        method = str(scope.get("method") or "HTTP")
        path = str(scope.get("path") or "/")
        with root_action_scope(
            "HTTP", f"{method} {path}", request_id=request_id, root_correlation_id=root_id
        ) as context:

            async def correlated_send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    response_headers = [
                        (key, value)
                        for key, value in message.get("headers", [])
                        if key.lower() not in {b"x-request-id", b"x-root-correlation-id"}
                    ]
                    response_headers.extend(
                        [
                            (b"x-request-id", request_id.encode("ascii")),
                            (b"x-root-correlation-id", context.root_correlation_id.encode("ascii")),
                        ]
                    )
                    message = {**message, "headers": response_headers}
                await send(message)

            await self.app(scope, receive, correlated_send)


def root_action_label(context: CausalityContext) -> str:
    # Prometheus gets a bounded class; detailed trigger_name remains in PostgreSQL/logs.
    return context.trigger_kind.lower()


def workflow_family(job_type: str, context: CausalityContext | None = None) -> str:
    """Return a bounded family suitable for metrics and family-specific alerts."""
    normalized = str(job_type or "").upper()
    trigger = str(context.trigger_name if context is not None else "").lower()
    if normalized == "WINNER_OUTCOME_MATURATION":
        return "WINNER_MATURATION"
    if normalized == "WINNER_COHORT_REFRESH":
        return "WINNER_COHORT_REFRESH"
    if normalized in {"WINNER_LATEST_RESCORE", "WINNER_PREDICTION_CAPTURE"}:
        return "WINNER_RESCORE"
    if normalized.startswith("CERI_"):
        return "CERI_PIPELINE"
    if normalized == "FULL_PIPELINE":
        return "FULL_PIPELINE"
    if (context is not None and context.trigger_kind == "SCHEDULER") or "scheduler" in trigger:
        return "SCHEDULER_MAINTENANCE"
    return "OTHER"


def _safe_id(value: str | None) -> str | None:
    return value if value and _SAFE_ID.fullmatch(value) else None


def _decode(value: bytes | None) -> str | None:
    return value.decode("latin-1") if value else None


def _bounded(value: str, limit: int) -> str:
    return str(value or "unspecified")[:limit]
