from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import UTC, datetime
from typing import Any

from app.observability.correlation import context_fields
from app.services.redaction import redact_sensitive, redact_text

_STANDARD = frozenset(logging.makeLogRecord({}).__dict__)
_OPTIONAL_ENVELOPE_FIELDS = (
    "request_id",
    "root_correlation_id",
    "causation_id",
    "background_job_id",
    "parent_job_id",
    "triggered_by_job_id",
    "job_type",
    "run_id",
    "pipeline_run_id",
    "workflow_key",
    "request_key",
    "worker_id",
    "stage",
    "status",
    "reason_code",
    "provider",
    "dataset",
    "config_hash",
    "model_version",
    "duration_ms",
)


class JsonLogFormatter(logging.Formatter):
    def __init__(self, process_role: str) -> None:
        super().__init__()
        self.process_role = process_role

    def format(self, record: logging.LogRecord) -> str:
        event = str(getattr(record, "event", None) or record.getMessage().split(" ", 1)[0])
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "severity": record.levelname,
            "event": event,
            "process_role": self.process_role,
            "process_id": os.getpid(),
            **context_fields(),
        }
        for key in _OPTIONAL_ENVELOPE_FIELDS:
            payload.setdefault(key, None)
        for key, value in record.__dict__.items():
            if key not in _STANDARD and key not in {"message", "asctime"}:
                payload[key] = value
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["error_summary"] = redact_text(str(record.exc_info[1]))
            payload["stack"] = traceback.format_exception(*record.exc_info)[-1].strip()
        return json.dumps(redact_sensitive(payload), separators=(",", ":"), default=str)


def configure_json_logging(process_role: str, *, level: int = logging.INFO) -> None:
    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter(process_role))
    root.handlers[:] = [handler]
    root.setLevel(level)


def log_event(
    logger: logging.Logger, event: str, *, level: int = logging.INFO, **fields: Any
) -> None:
    logger.log(level, event, extra={"event": event, **redact_sensitive(fields)})
