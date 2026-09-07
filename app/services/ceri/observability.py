from __future__ import annotations

import logging
from typing import Any

from app.observability.logging import log_event
from app.observability.transaction_metrics import publish_after_commit
from app.services.ceri.export_policy import redact_sensitive
from app.services.operational_metrics import operational_metrics

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
        "source_record_corrected",
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


CERI_METRIC_CATALOG = frozenset(
    {
        "ceri_ingestion_started_total",
        "ceri_ingestion_completed_total",
        "ceri_ingestion_inserted_total",
        "ceri_ingestion_corrected_total",
        "ceri_ingestion_deduplicated_total",
        "ceri_ingestion_quarantined_total",
        "ceri_ingestion_sec_documents_skipped_total",
        "ceri_ingestion_sec_documents_would_skip_total",
        "ceri_ingestion_duration_ms",
        "ceri_processing_retries_total",
        "ceri_scores_capture_duration_ms",
        "ceri_purge_previews_total",
        "ceri_purge_affected_records_total",
        "ceri_purge_executions_total",
        "ceri_purge_blocked_total",
    }
)


class CeriMetricRegistry:
    """Stateless adapter over the bounded process-local Prometheus registry."""

    def increment(self, name: str, value: float = 1.0, *, session: Any = None, **tags: str) -> None:
        _validate_metric_name(name)
        safe_tags = _bounded_tags(tags)
        canonical = f"swinglens_{name}"
        if session is None:
            operational_metrics.increment(canonical, value=value, **safe_tags)
        else:
            publish_after_commit(session, "increment", canonical, value=value, **safe_tags)
        if name.startswith("ceri_ingestion_"):
            aggregate_tags = {
                "provider": str(tags.get("provider") or "unknown"),
                "dataset": str(tags.get("dataset") or "unknown"),
                "result": name.removeprefix("ceri_ingestion_").removesuffix("_total"),
            }
            if session is None:
                operational_metrics.increment(
                    "swinglens_ceri_ingestion_total", value=value, **aggregate_tags
                )
            else:
                publish_after_commit(
                    session,
                    "increment",
                    "swinglens_ceri_ingestion_total",
                    value=value,
                    **aggregate_tags,
                )

    def observe(self, name: str, value: float, *, session: Any = None, **tags: str) -> None:
        _validate_metric_name(name)
        metric_name = f"swinglens_{name}"
        metric_value = value
        if metric_name.endswith("_duration_ms"):
            metric_name = metric_name.removesuffix("_ms") + "_seconds"
            metric_value = value / 1000.0
        safe_tags = _bounded_tags(tags)
        if session is None:
            operational_metrics.observe(metric_name, metric_value, **safe_tags)
        else:
            publish_after_commit(session, "observe", metric_name, metric_value, **safe_tags)

    def snapshot(self) -> dict[str, Any]:
        return {
            "families": list(METRIC_FAMILIES),
            "counters": {},
            "samples": [],
            "sample_count": 0,
        }


class CeriStructuredLogger:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger(LOGGER_NAME)

    def event(self, event_name: str, **fields: Any) -> dict[str, Any]:
        payload = ceri_log_payload(event_name, **fields)
        log_event(self.logger, f"ceri.{event_name}", ceri=payload)
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
    if name not in CERI_METRIC_CATALOG:
        raise ValueError(f"Undeclared CERI metric: {name}")


def _bounded_tags(tags: dict[str, str]) -> dict[str, str]:
    forbidden = {
        "job_id",
        "run_id",
        "request_id",
        "root_correlation_id",
        "causation_id",
        "workflow_key",
        "request_key",
        "ticker",
        "company",
        "company_id",
        "execution_token",
        "query_fingerprint",
        "error_message",
        "path",
    }
    return {key: str(value) for key, value in tags.items() if key not in forbidden}
