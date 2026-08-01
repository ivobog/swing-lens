from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from app.services.ceri.export_policy import redact_sensitive

LOGGER_NAME = "swinglens.ceri"

METRIC_FAMILIES = (
    "ceri_ingestion",
    "ceri_freshness",
    "ceri_coverage",
    "ceri_scores",
    "ceri_conflicts",
    "ceri_jobs",
    "ceri_processing",
    "ceri_alerts",
    "ceri_purge",
)

STRUCTURED_EVENT_NAMES = frozenset(
    {
        "ingestion_started",
        "ingestion_completed",
        "source_record_inserted",
        "source_record_deduplicated",
        "source_record_quarantined",
        "normalization_failed",
        "revision_rebuilt",
        "score_snapshot_captured",
        "change_event_emitted",
        "alert_emitted",
        "alert_suppressed",
        "provider_quota_degraded",
        "ticker_scoring_failed",
        "purge_preview",
        "purge_executed",
        "purge_blocked",
    }
)


@dataclass(frozen=True)
class CeriMetricSample:
    name: str
    value: float
    tags: dict[str, str]
    observed_at_monotonic: float


class CeriMetricRegistry:
    def __init__(self) -> None:
        self._counters: dict[str, float] = defaultdict(float)
        self._samples: list[CeriMetricSample] = []

    def increment(self, name: str, value: float = 1.0, **tags: str) -> None:
        _validate_metric_name(name)
        key = _metric_key(name, tags)
        self._counters[key] += value
        self._samples.append(
            CeriMetricSample(
                name=name,
                value=value,
                tags={key: str(value) for key, value in tags.items()},
                observed_at_monotonic=time.monotonic(),
            )
        )

    def observe(self, name: str, value: float, **tags: str) -> None:
        _validate_metric_name(name)
        self._samples.append(
            CeriMetricSample(
                name=name,
                value=value,
                tags={key: str(value) for key, value in tags.items()},
                observed_at_monotonic=time.monotonic(),
            )
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "families": list(METRIC_FAMILIES),
            "counters": dict(self._counters),
            "samples": [sample.__dict__ for sample in self._samples],
        }


class CeriStructuredLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(LOGGER_NAME)

    def event(self, event_name: str, **fields: Any) -> dict[str, Any]:
        payload = ceri_log_payload(event_name, **fields)
        self.logger.info("ceri.%s", event_name, extra={"ceri": payload})
        return payload


def ceri_log_payload(event_name: str, **fields: Any) -> dict[str, Any]:
    if event_name not in STRUCTURED_EVENT_NAMES:
        raise ValueError(f"Unsupported CERI structured event: {event_name}")
    payload = {
        "event": event_name,
        "job_id": fields.get("job_id"),
        "processing_run_id": fields.get("processing_run_id"),
        "ingestion_run_id": fields.get("ingestion_run_id"),
        "provider": fields.get("provider"),
        "dataset": fields.get("dataset"),
        "company_id": fields.get("company_id"),
        "ticker": fields.get("ticker"),
        "calculation_version": fields.get("calculation_version"),
        "config_hash": fields.get("config_hash"),
        "request_key": fields.get("request_key"),
        "execution_token": fields.get("execution_token"),
    }
    for key, value in fields.items():
        if key not in payload:
            payload[key] = value
    return redact_sensitive(payload)


def ceri_log_event(event_name: str, **fields: Any) -> dict[str, Any]:
    return CeriStructuredLogger().event(event_name, **fields)


ceri_metrics = CeriMetricRegistry()


def _validate_metric_name(name: str) -> None:
    if not any(name == family or name.startswith(f"{family}_") for family in METRIC_FAMILIES):
        raise ValueError(f"Unsupported CERI metric family: {name}")


def _metric_key(name: str, tags: dict[str, str]) -> str:
    if not tags:
        return name
    encoded_tags = ",".join(f"{key}={tags[key]}" for key in sorted(tags))
    return f"{name}|{encoded_tags}"
