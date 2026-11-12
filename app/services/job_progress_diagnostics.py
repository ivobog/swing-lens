"""Bounded stack evidence for live workers approaching a progress timeout."""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.services.pipeline_stage_registry import progress_timeout_seconds
from app.services.process_memory import process_memory_snapshot
from app.settings import Settings

logger = logging.getLogger(__name__)


class JobProgressDiagnostics:
    """Observe progress without renewing, committing, or changing useful progress."""

    def __init__(self, *, job_id: int, handler_thread_id: int, settings: Settings) -> None:
        self.job_id = job_id
        self.handler_thread_id = handler_thread_id
        self.settings = settings
        self._emitted_signature: tuple[Any, ...] | None = None

    def observe(self, row: Mapping[str, Any], *, now: datetime | None = None) -> bool:
        observed_at = now or datetime.now(UTC)
        progress_at = row.get("last_progress_at")
        if progress_at is None:
            attempt = (row.get("operational_metadata_json") or {}).get("current_attempt") or {}
            progress_at = attempt.get("started_at") or row.get("started_at")
        if isinstance(progress_at, str):
            progress_at = datetime.fromisoformat(progress_at.replace("Z", "+00:00"))
        if progress_at is None:
            return False
        if progress_at.tzinfo is None:
            progress_at = progress_at.replace(tzinfo=UTC)
        timeout = progress_timeout_seconds(
            row.get("progress_stage"),
            default_timeout_seconds=self.settings.job_progress_timeout_seconds,
            market_data_timeout_seconds=self.settings.job_market_data_progress_timeout_seconds,
            long_stage_timeout_seconds=self.settings.job_long_stage_progress_timeout_seconds,
        )
        age = max(0.0, (observed_at - progress_at).total_seconds())
        signature = (
            row.get("progress_stage"), row.get("progress_sequence"),
            row.get("progress_current_item"), progress_at,
        )
        if age < timeout * 0.75 or signature == self._emitted_signature:
            return False
        frame = sys._current_frames().get(self.handler_thread_id)
        stack = []
        try:
            while frame is not None and len(stack) < 32:
                # Do not retain frames, source text, arguments, or local values.
                stack.append({
                    "file": frame.f_code.co_filename,
                    "function": frame.f_code.co_name,
                    "line": frame.f_lineno,
                })
                frame = frame.f_back
        finally:
            del frame
        try:
            memory = process_memory_snapshot()
            memory_values = {"rss_bytes": memory.rss_bytes, "private_bytes": memory.private_bytes}
        except Exception as exc:
            memory_values = {"sample_error_type": type(exc).__name__}
        logger.warning(
            "job.progress_stall_diagnostic",
            extra={
                "job_id": self.job_id,
                "stage": row.get("progress_stage"),
                "progress_diagnostic": {
                    "progress_age_seconds": round(age, 3),
                    "timeout_seconds": timeout,
                    "progress_sequence": row.get("progress_sequence"),
                    "current_item": row.get("progress_current_item"),
                    "processed": row.get("progress_processed"),
                    "total": row.get("progress_total"),
                    "handler_thread_id": self.handler_thread_id,
                    "stack_leaf_first": stack,
                    **memory_values,
                },
            },
        )
        self._emitted_signature = signature
        return True
