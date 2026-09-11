from __future__ import annotations

import json
import logging
import os
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.observability.correlation import context_fields
from app.services.lifecycle_control import append_lifecycle_event
from app.services.redaction import redact_sensitive, redact_text

_LIFECYCLE_ENV_FIELDS = {
    "lifecycle_operation_id": "SWINGLENS_LIFECYCLE_OPERATION_ID",
    "runtime_instance_id": "SWINGLENS_RUNTIME_INSTANCE_ID",
    "runtime_config_fingerprint": "SWINGLENS_RUNTIME_CONFIG_FINGERPRINT",
    "git_sha": "SWINGLENS_GIT_SHA",
}

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
            **{
                field: os.environ.get(env_name)
                for field, env_name in _LIFECYCLE_ENV_FIELDS.items()
            },
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
    safe_fields = redact_sensitive(fields)
    logger.log(level, event, extra={"event": event, **safe_fields})
    if event.startswith("runtime."):
        try:
            append_lifecycle_event(
                Path.cwd(),
                event=event,
                action="runtime",
                stage=safe_fields.get("stage"),
                component=os.environ.get("PROCESS_ROLE", "UNKNOWN"),
                process_role=os.environ.get("PROCESS_ROLE", "UNKNOWN"),
                result=safe_fields.get("result"),
                reason_code=safe_fields.get("reason_code"),
                duration_ms=safe_fields.get("duration_ms"),
                message=safe_fields.get("error") or safe_fields.get("message"),
            )
        except OSError:
            pass
