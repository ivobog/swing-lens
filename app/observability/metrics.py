from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock, Thread
from typing import Any

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)

OTHER_LABEL_VALUE = "OTHER"
MAX_SERIES_PER_METRIC = 256

# Labels in the first group are closed enums.  The second group represents
# configured/runtime registries (job types, stages, providers, and similar
# vocabularies); those values are admitted only up to a hard ceiling and then
# normalized to OTHER.  This makes the bound executable rather than advisory.
CLOSED_LABEL_VALUES: dict[str, frozenset[str]] = {
    "priority": frozenset({"P0", "P1", "P2"}),
    "process_role": frozenset({"web", "worker", "supervisor"}),
    "queue_class": frozenset({"interactive", "broker", "background"}),
    "severity": frozenset({"warning", "critical"}),
    "status": frozenset(
        {
            "QUEUED",
            "RUNNABLE",
            "SCHEDULED",
            "RUNNING",
            "COMPLETED",
            "PARTIAL",
            "FAILED",
            "BLOCKED",
            "RECOVERING",
            "STALLED",
            "CANCELLED",
            "STALE",
            "DEFERRED",
            "REQUESTED",
            "SUCCESS",
            "WARNING",
            "CRITICAL",
            "NORMAL",
            "ok",
            "failed",
            "degraded",
            "optional_unavailable",
        }
    ),
    "result": frozenset(
        {
            "CREATED",
            "COALESCED",
            "REJECTED",
            "success",
            "failed",
            "error",
            "invalid",
            "hit",
            "miss",
            "match",
            "mismatch",
            "empty",
            "malformed",
            "inserted",
            "corrected",
            "deduplicated",
            "quarantined",
            "completed",
            "partial",
            "cancelled",
            "shadow_miss",
            "shadow_candidate",
            "shifted_to_next_session",
        }
    ),
}

BOUNDED_LABEL_LIMITS: dict[str, int] = {
    "worker_id": 32,
    "stage": 64,
    "reason": 64,
    "reason_code": 64,
    "provider": 32,
    "dataset": 32,
    "job_type": 96,
    "workflow_family": 32,
    "category": 32,
    "component": 32,
    "decision": 32,
    "license_scope": 16,
    "log_class": 16,
    "mode": 32,
    "module": 32,
    "outcome": 32,
    "path_class": 16,
    "query_type": 32,
    "request_family": 32,
    "request_type": 64,
    "scanner": 32,
    "schema_id": 32,
    "scope": 32,
    "source": 16,
    "action": 16,
    "role": 8,
    "topology": 8,
    "trigger_source": 16,
    "weight": 16,
}

FORBIDDEN_LABELS = frozenset(
    {
        "job_id",
        "run_id",
        "pipeline_run_id",
        "request_id",
        "correlation_id",
        "root_correlation_id",
        "causation_id",
        "workflow_key",
        "request_key",
        "ticker",
        "company",
        "company_id",
        "execution_token",
        "sql_fingerprint",
        "query_fingerprint",
        "error_message",
        "path",
        "filesystem_path",
    }
)

JOB_WAIT_BUCKETS = (0.1, 0.5, 1, 2, 5, 10, 30, 60, 120, 300, 900, 1800, 3600)
JOB_DURATION_BUCKETS = (0.1, 0.5, 1, 5, 10, 30, 60, 120, 300, 600, 1800, 3600, 7200)
PROVIDER_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60)
POOL_WAIT_BUCKETS = (0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5)
PIPELINE_BUCKETS = (0.1, 1, 5, 10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200)
FANOUT_BUCKETS = (1, 2, 3, 5, 10, 20, 50, 100, 250, 500)


@dataclass(frozen=True)
class MetricDefinition:
    kind: str
    description: str
    unit: str
    labels: tuple[str, ...] = ()
    allowed_values_policy: str = "bounded-runtime-values"
    buckets: tuple[float, ...] | None = None

    @property
    def help(self) -> str:
        return self.description


def _counter(
    description: str, labels: tuple[str, ...] = (), unit: str = "events"
) -> MetricDefinition:
    return MetricDefinition("counter", description, unit, labels)


def _gauge(description: str, labels: tuple[str, ...] = (), unit: str = "items") -> MetricDefinition:
    return MetricDefinition("gauge", description, unit, labels)


def _histogram(
    description: str,
    labels: tuple[str, ...] = (),
    buckets: tuple[float, ...] = PROVIDER_BUCKETS,
    unit: str = "seconds",
) -> MetricDefinition:
    return MetricDefinition("histogram", description, unit, labels, buckets=buckets)


DEFINITIONS: dict[str, MetricDefinition] = {
    "swinglens_jobs_enqueued_total": _counter("Committed jobs created.", ("job_type",)),
    "swinglens_jobs_coalesced_total": _counter(
        "Committed enqueue attempts coalesced.", ("job_type",)
    ),
    "swinglens_jobs_finished_total": _counter(
        "Committed terminal job transitions.", ("job_type", "status")
    ),
    "swinglens_jobs_retry_total": _counter(
        "Committed job retry transitions.", ("job_type", "status")
    ),
    "swinglens_jobs_failed_total": _counter("Committed failed jobs.", ("job_type", "status")),
    "swinglens_jobs_deferred_total": _counter("Committed job deferrals.", ("job_type",)),
    "swinglens_jobs_stale_recovered_total": _counter("Committed stale-job recoveries."),
    "swinglens_job_stalls_total": _counter("Committed job stall detections.", ("job_type",)),
    "swinglens_job_progress_total": _counter("Committed job progress updates.", ("stage",)),
    "swinglens_queue_depth": _gauge(
        "Jobs by queue and readiness state.", ("queue_class", "status")
    ),
    "swinglens_queue_oldest_age_seconds": _gauge(
        "Age of oldest runnable queued job.", ("queue_class",), "seconds"
    ),
    "swinglens_jobs_running": _gauge("Running jobs.", ("job_type",)),
    "swinglens_jobs_stalled": _gauge("Stalled jobs.", ("job_type",)),
    "swinglens_jobs_recovering": _gauge("Recovering jobs.", ("job_type",)),
    "swinglens_job_wait_seconds": _histogram(
        "Runnable job wait duration.", ("job_type",), JOB_WAIT_BUCKETS
    ),
    "swinglens_job_duration_seconds": _histogram(
        "Committed job execution duration.", ("job_type", "status"), JOB_DURATION_BUCKETS
    ),
    "swinglens_job_progress_age_seconds": _gauge(
        "Age of latest job progress.", ("job_type", "stage"), "seconds"
    ),
    "swinglens_job_fanout_total": _counter(
        "Committed enqueue attempts by workflow family.", ("workflow_family", "job_type", "result")
    ),
    "swinglens_job_fanout_size": _histogram(
        "Committed root descendant count.", ("workflow_family",), FANOUT_BUCKETS, "jobs"
    ),
    "swinglens_job_fanout_depth": _histogram(
        "Maximum committed root depth.", ("workflow_family",), FANOUT_BUCKETS, "levels"
    ),
    "swinglens_job_fanout_abnormal_roots_total": _counter(
        "Roots crossing a fanout threshold.", ("workflow_family", "severity", "reason")
    ),
    "swinglens_worker_up": _gauge("Worker process liveness.", ("worker_id",), "boolean"),
    "swinglens_worker_heartbeat_age_seconds": _gauge(
        "Worker heartbeat age.", ("worker_id",), "seconds"
    ),
    "swinglens_worker_quiesce_ack_latency_seconds": _histogram(
        "Latency from a durable quiesce request to the worker acknowledgement."
    ),
    "swinglens_worker_rss_bytes": _gauge("Worker resident memory.", ("worker_id",), "bytes"),
    "swinglens_worker_private_bytes": _gauge("Worker private memory.", ("worker_id",), "bytes"),
    "swinglens_worker_memory_bytes": _gauge("Worker memory measurement.", ("worker_id",), "bytes"),
    "swinglens_worker_cpu_percent": _gauge("Worker CPU utilization.", ("worker_id",), "percent"),
    "swinglens_worker_restarts_total": _counter("Supervisor worker restarts.", ("worker_id",)),
    "swinglens_worker_memory_status": _gauge(
        "Worker memory state indicator.", ("worker_id", "status"), "boolean"
    ),
    "swinglens_supervisor_up": _gauge("Supervisor process liveness.", unit="boolean"),
    "swinglens_supervisor_heartbeat_age_seconds": _gauge(
        "Supervisor heartbeat age.", unit="seconds"
    ),
    "swinglens_lifecycle_last_operation_timestamp_seconds": _gauge(
        "Unix timestamp of the last lifecycle operation.", unit="seconds"
    ),
    "swinglens_lifecycle_last_operation_success": _gauge(
        "Whether the last lifecycle operation succeeded.", unit="boolean"
    ),
    "swinglens_lifecycle_operation_duration_seconds": _gauge(
        "Duration of the last lifecycle operation.", ("action", "stage"), "seconds"
    ),
    "swinglens_lifecycle_failures_total": _counter(
        "Lifecycle operation failures.", ("action", "stage", "reason")
    ),
    "swinglens_runtime_generation_info": _gauge(
        "Active runtime generation identity.", ("mode", "topology"), "boolean"
    ),
    "swinglens_supervisor_child_restarts_total": _counter(
        "Supervisor child restarts.", ("role", "reason")
    ),
    "swinglens_supervisor_child_crash_loop": _gauge(
        "Whether a supervised child exhausted its restart budget.", ("role",), "boolean"
    ),
    "swinglens_supervisor_child_start_failures_total": _counter(
        "Supervised child startup failures.", ("role", "stage")
    ),
    "swinglens_database_provenance_ok": _gauge(
        "Whether database provenance matches the selected safety context.", unit="boolean"
    ),
    "swinglens_alembic_head_match": _gauge(
        "Whether the database schema matches repository heads.", unit="boolean"
    ),
    "swinglens_readiness_state": _gauge(
        "Last evaluated readiness state by contract scope.", ("scope", "status"), "boolean"
    ),
    "swinglens_control_loop_up": _gauge(
        "Functional control-loop liveness.", ("process_role",), "boolean"
    ),
    "swinglens_control_loop_heartbeat_age_seconds": _gauge(
        "Age of functional control-loop progress.", ("process_role",), "seconds"
    ),
    "swinglens_system_collector_up": _gauge(
        "Web-owned authoritative system collector liveness.", unit="boolean"
    ),
    "swinglens_pipelines_started_total": _counter("Committed pipeline starts."),
    "swinglens_pipelines_coalesced_total": _counter(
        "Committed coalesced pipeline requests.", ("status",)
    ),
    "swinglens_pipelines_cancel_requested_total": _counter(
        "Committed pipeline cancellation requests.", ("status",)
    ),
    "swinglens_pipelines_finished_total": _counter("Committed pipeline finishes.", ("status",)),
    "swinglens_pipeline_runs_total": _counter("Committed pipeline results.", ("status",)),
    "swinglens_pipeline_duration_seconds": _histogram(
        "Pipeline wall duration.", ("status",), PIPELINE_BUCKETS
    ),
    "swinglens_pipeline_stage_duration_seconds": _histogram(
        "Pipeline stage duration.", ("stage", "status"), PIPELINE_BUCKETS
    ),
    "swinglens_pipeline_failures_total": _counter(
        "Pipeline stage failures.", ("stage", "reason_code")
    ),
    "swinglens_pipeline_current_stage": _gauge(
        "Current pipeline stage indicator.", ("stage",), "boolean"
    ),
    "swinglens_pipeline_active": _gauge("Active pipeline count."),
    "swinglens_pipeline_optimized_fallback_total": _counter(
        "Optimized-path fallbacks.", ("component", "reason")
    ),
    "swinglens_ranking_pipeline_runs_total": _counter("Ranking pipeline runs.", ("status",)),
    "swinglens_ranking_results_total": _counter("Ranking results.", ("status",)),
    "swinglens_technical_scoring_runs_total": _counter("Technical scoring runs.", ("mode",)),
    "swinglens_technical_worker_processes": _gauge("Configured technical worker processes."),
    "swinglens_technical_input_load_seconds": _histogram("Technical input-load duration."),
    "swinglens_technical_worker_span_seconds": _histogram("Technical worker-span duration."),
    "swinglens_technical_finalize_seconds": _histogram("Technical finalize duration."),
    "swinglens_technical_artifact_cache_total": _counter(
        "Technical artifact cache outcomes.", ("result", "reason")
    ),
    "swinglens_technical_artifact_cache_shadow_validations_total": _counter(
        "Artifact shadow validations.", ("result",)
    ),
    "swinglens_technical_artifact_cache_shadow_mismatches_total": _counter(
        "Artifact shadow mismatches."
    ),
    "swinglens_technical_process_pool_fallback_total": _counter(
        "Technical process-pool fallbacks.", ("reason",)
    ),
    "swinglens_technical_pure_boundary_shadow_mismatches_total": _counter(
        "Technical boundary shadow mismatches."
    ),
    "swinglens_technical_overlap_market_rescore_total": _counter(
        "Technical overlap market rescores."
    ),
    "swinglens_technical_overlap_tickers_total": _counter("Technical overlap tickers."),
    "swinglens_technical_source_session_lag": _histogram(
        "Technical source lag measured in exchange sessions.",
        ("source",),
        buckets=(0, 1, 2, 3, 5, 10, 20, 50, 100, 250, 500, 1000, 2500),
        unit="sessions",
    ),
    "swinglens_technical_scores_temporally_degraded_total": _counter(
        "Technical scores calculated with at least one explicitly lagged source."
    ),
    "swinglens_market_calculation_cutoffs_total": _counter(
        "Frozen market calculation cutoffs created.", ("scope",)
    ),
    "swinglens_provider_requests_total": _counter(
        "Provider request calls.", ("provider", "result")
    ),
    "swinglens_provider_request_duration_seconds": _histogram(
        "One provider operation latency.", ("provider",), PROVIDER_BUCKETS
    ),
    "swinglens_provider_response_bytes_total": _counter(
        "Provider response bytes.", ("provider",), "bytes"
    ),
    "swinglens_provider_stored_bytes_total": _counter(
        "Provider stored bytes.", ("provider",), "bytes"
    ),
    "swinglens_provider_retries_total": _counter("Provider retries.", ("provider",)),
    "swinglens_ceri_ingestion_total": _counter(
        "CERI ingestion events.", ("provider", "dataset", "result")
    ),
    "swinglens_ceri_ingestion_started_total": _counter(
        "CERI ingestions started.", ("provider", "dataset")
    ),
    "swinglens_ceri_ingestion_completed_total": _counter(
        "CERI ingestions completed.", ("provider", "dataset", "status")
    ),
    "swinglens_ceri_ingestion_inserted_total": _counter(
        "CERI records inserted.", ("provider", "dataset")
    ),
    "swinglens_ceri_ingestion_corrected_total": _counter(
        "CERI records corrected.", ("provider", "dataset")
    ),
    "swinglens_ceri_ingestion_deduplicated_total": _counter(
        "CERI records deduplicated.", ("provider", "dataset")
    ),
    "swinglens_ceri_ingestion_quarantined_total": _counter(
        "CERI records quarantined.", ("provider", "dataset")
    ),
    "swinglens_ceri_ingestion_sec_documents_skipped_total": _counter(
        "SEC documents skipped.", ("dataset",)
    ),
    "swinglens_ceri_ingestion_sec_documents_would_skip_total": _counter(
        "SEC documents shadow-skipped.", ("dataset",)
    ),
    "swinglens_ceri_ingestion_duration_seconds": _histogram(
        "CERI ingestion duration.", ("provider", "dataset", "status"), PIPELINE_BUCKETS
    ),
    "swinglens_ceri_processing_retries_total": _counter("CERI processing retries.", ("job_type",)),
    "swinglens_ceri_scores_capture_duration_seconds": _histogram(
        "CERI score capture duration.", ("scope",), PIPELINE_BUCKETS
    ),
    "swinglens_ceri_normalization_total": _counter(
        "CERI normalization outcomes.", ("dataset", "result")
    ),
    "swinglens_ceri_feature_rebuild_total": _counter("CERI feature rebuild outcomes.", ("result",)),
    "swinglens_ceri_reaction_sessions_total": _counter(
        "CERI daily-bar causal reaction-session decisions.", ("result",)
    ),
    "swinglens_ceri_scoring_total": _counter("CERI scoring outcomes.", ("result",)),
    "swinglens_ceri_purge_previews_total": _counter(
        "CERI purge previews.", ("provider", "license_scope")
    ),
    "swinglens_ceri_purge_affected_records_total": _counter(
        "CERI purge affected records.", ("provider", "license_scope"), "records"
    ),
    "swinglens_ceri_purge_executions_total": _counter(
        "CERI purge executions.", ("provider", "license_scope")
    ),
    "swinglens_ceri_purge_blocked_total": _counter(
        "CERI blocked purges.", ("provider", "license_scope", "reason")
    ),
    "swinglens_ib_connected": _gauge("IB connectivity state.", unit="boolean"),
    "swinglens_ib_fetch_requests_total": _counter("IB fetch request outcomes.", ("result",)),
    "swinglens_ib_fetch_duration_seconds": _histogram(
        "IB fetch request latency.", ("result",), PROVIDER_BUCKETS
    ),
    "swinglens_ib_fetch_decisions_total": _counter("IB fetch planning decisions.", ("decision",)),
    "swinglens_ib_pacing_wait_seconds": _histogram("IB pacing wait duration."),
    "swinglens_ib_network_seconds": _histogram("IB network duration."),
    "swinglens_bar_cache_write_seconds": _histogram("Bar-cache write duration."),
    "swinglens_ibmi_requests_total": _counter(
        "IBMI requests.", ("module", "request_family", "request_type")
    ),
    "swinglens_ibmi_retries_total": _counter(
        "IBMI request retries.", ("request_family", "request_type")
    ),
    "swinglens_ibmi_subscription_required_total": _counter(
        "IBMI subscription requirements.", ("request_family", "request_type")
    ),
    "swinglens_ibmi_tws_requests_total": _counter("IBMI TWS requests.", ("request_family",)),
    "swinglens_ibmi_pacing_waits_total": _counter("IBMI pacing waits.", ("weight",)),
    "swinglens_ibmi_pacing_wait_seconds": _histogram("IBMI pacing wait duration."),
    "swinglens_ibmi_market_data_subscriptions_total": _counter("IBMI market-data subscriptions."),
    "swinglens_ibmi_market_data_line_cap_errors_total": _counter(
        "IBMI market-data line-cap errors."
    ),
    "swinglens_ibmi_connections_total": _counter("IBMI connections."),
    "swinglens_ibmi_histogram_fetch_total": _counter(
        "IBMI histogram fetch outcomes.", ("outcome",)
    ),
    "swinglens_ibmi_calculation_failures_total": _counter(
        "IBMI calculation failures.", ("module",)
    ),
    "swinglens_ibmi_calculation_unavailable_total": _counter(
        "IBMI unavailable calculations.", ("module",)
    ),
    "swinglens_ibmi_stale_features_total": _counter("IBMI stale features.", ("module",)),
    "swinglens_ibmi_scanner_runs_total": _counter("IBMI scanner runs.", ("scanner",)),
    "swinglens_ibmi_scanner_results_total": _counter(
        "IBMI scanner results.", ("scanner",), "results"
    ),
    "swinglens_ibmi_scanner_empty_total": _counter("IBMI empty scanner results.", ("scanner",)),
    "swinglens_ibmi_flex_send_duration_seconds": _histogram("IBMI Flex send duration."),
    "swinglens_ibmi_flex_get_duration_seconds": _histogram("IBMI Flex poll duration."),
    "swinglens_ibmi_flex_import_duration_seconds": _histogram(
        "IBMI Flex import duration.", ("query_type",)
    ),
    "swinglens_ibmi_flex_import_rows_total": _counter(
        "IBMI Flex imported rows.", ("query_type",), "rows"
    ),
    "swinglens_ibmi_flex_dry_run_duration_seconds": _histogram(
        "IBMI Flex validation-only duration.", ("query_type",)
    ),
    "swinglens_ibmi_flex_dry_run_rows_total": _counter(
        "IBMI Flex validation-only parsed rows.", ("query_type",), "rows"
    ),
    "swinglens_ibmi_flex_duplicate_reports_total": _counter(
        "IBMI duplicate Flex reports.", ("query_type",)
    ),
    "swinglens_ibmi_unmatched_executions_total": _counter("IBMI unmatched executions."),
    "swinglens_ibmi_ambiguous_research_links_total": _counter("IBMI ambiguous research links."),
    "swinglens_db_pool_size": _gauge("Database pool base size.", unit="connections"),
    "swinglens_db_pool_checked_out": _gauge(
        "Database checked-out connections.", unit="connections"
    ),
    "swinglens_db_pool_overflow": _gauge(
        "Database overflow connections in use.", unit="connections"
    ),
    "swinglens_db_pool_capacity": _gauge("Configured database pool capacity.", unit="connections"),
    "swinglens_db_pool_timeouts_total": _counter("Database pool checkout timeouts."),
    "swinglens_db_pool_wait_seconds": _histogram(
        "Database pool checkout wait.", buckets=POOL_WAIT_BUCKETS
    ),
    "swinglens_db_slow_queries_total": _counter("Slow SQL queries."),
    "swinglens_db_long_transactions_total": _counter("Long database transactions."),
    "swinglens_db_monitor_records_total": _counter("SQL recorder records received.", ("priority",)),
    "swinglens_db_monitor_written_total": _counter("SQL recorder records written.", ("priority",)),
    "swinglens_db_monitor_dropped_total": _counter("SQL recorder records dropped.", ("priority",)),
    "swinglens_db_monitor_queue_depth": _gauge("SQL recorder queue depth.", ("priority",)),
    "swinglens_db_monitor_writer_errors_total": _counter("SQL recorder writer errors."),
    "swinglens_db_monitor_writer_latency_seconds": _histogram("SQL recorder write latency."),
    "swinglens_process_cpu_percent": _gauge(
        "Process CPU utilization.", ("process_role",), "percent"
    ),
    "swinglens_process_rss_bytes": _gauge("Process resident memory.", ("process_role",), "bytes"),
    "swinglens_disk_free_bytes": _gauge("Free storage bytes.", ("path_class",), "bytes"),
    "swinglens_disk_total_bytes": _gauge("Total storage bytes.", ("path_class",), "bytes"),
    "swinglens_db_size_bytes": _gauge("PostgreSQL database size.", unit="bytes"),
    "swinglens_log_storage_bytes": _gauge("Telemetry log storage size.", ("log_class",), "bytes"),
    "swinglens_resource_sampler_up": _gauge(
        "Resource sampler category health.", ("process_role", "category"), "boolean"
    ),
    "swinglens_resource_sampler_errors_total": _counter(
        "Resource sampler category failures.", ("process_role", "category")
    ),
    "swinglens_exports_generated_total": _counter("Generated exports.", ("schema_id",)),
    "swinglens_export_rows_total": _counter("Exported rows.", ("schema_id",), "rows"),
    "swinglens_price_series_version_advances_total": _counter("Price series version advances."),
    "swinglens_setup_price_rows_materialized_total": _counter(
        "Setup price rows materialized.", ("mode",), "rows"
    ),
    "swinglens_setup_latest_bar_projection_shadow_comparisons_total": _counter(
        "Setup latest-bar shadow comparisons."
    ),
    "swinglens_setup_latest_bar_projection_shadow_mismatches_total": _counter(
        "Setup latest-bar shadow mismatches."
    ),
    "swinglens_setup_latest_bar_query_seconds": _histogram(
        "Setup latest-bar query duration.", ("mode",)
    ),
    "swinglens_market_prewarm_jobs_total": _counter("Market prewarm job outcomes.", ("status",)),
    "swinglens_market_prewarm_coverage_ratio": _gauge(
        "Market prewarm coverage ratio.", unit="ratio"
    ),
    "swinglens_market_prewarm_preemptions_total": _counter(
        "Market prewarm preemptions.", ("status",)
    ),
    "winner_maturation_jobs_total": _counter("Winner maturation jobs.", ("trigger_source",)),
    "winner_maturation_zero_progress_total": _counter("Winner maturation zero-progress runs."),
    "winner_maturation_continuations_total": _counter("Winner maturation continuations."),
    "winner_maturation_continuation_depth": _gauge(
        "Winner maturation continuation depth.", unit="levels"
    ),
    "winner_maturation_due": _gauge("Winner outcomes currently due."),
    "winner_maturation_retry_eligible": _gauge("Winner outcomes retry-eligible."),
    "winner_maturation_retry_deferred": _gauge("Winner outcomes with deferred retry."),
}


@dataclass(frozen=True)
class MetricSample:
    name: str
    value: float
    labels: dict[str, str]


class PrometheusMetrics:
    """Prometheus facade; the mirror is only for compatibility introspection."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._enabled = True
        self._totals: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._label_values_seen: dict[str, set[str]] = {}
        self._series_by_metric: dict[str, set[tuple[tuple[str, str], ...]]] = {}
        self._reset_registry()

    @property
    def registry(self) -> CollectorRegistry:
        return self._registry

    def increment(self, name: str, value: float = 1.0, **labels: Any) -> None:
        if not self._enabled:
            return
        definition = self._definition(name, "counter", labels)
        metric_labels = self._labels(name, definition, labels)
        metric = self._metric(name, definition)
        try:
            (metric.labels(**metric_labels) if metric_labels else metric).inc(float(value))
        except Exception as exc:
            self._report_emission_failure(name, "increment", exc)
            return
        key = (name, tuple(sorted(metric_labels.items())))
        with self._lock:
            self._totals[key] = self._totals.get(key, 0.0) + float(value)

    def set_gauge(self, name: str, value: float, **labels: Any) -> None:
        if not self._enabled:
            return
        definition = self._definition(name, "gauge", labels)
        if definition.kind != "gauge":
            raise ValueError(f"{name} is a {definition.kind}, not a gauge")
        metric_labels = self._labels(name, definition, labels)
        metric = self._metric(name, definition)
        try:
            (metric.labels(**metric_labels) if metric_labels else metric).set(float(value))
        except Exception as exc:
            self._report_emission_failure(name, "set_gauge", exc)
            return
        with self._lock:
            self._totals[(name, tuple(sorted(metric_labels.items())))] = float(value)

    def observe(self, name: str, value: float, **labels: Any) -> None:
        if not self._enabled:
            return
        definition = self._definition(name, "histogram", labels)
        if definition.kind != "histogram":
            raise ValueError(f"{name} is a {definition.kind}, not a histogram")
        metric_labels = self._labels(name, definition, labels)
        metric = self._metric(name, definition)
        try:
            (metric.labels(**metric_labels) if metric_labels else metric).observe(float(value))
        except Exception as exc:
            self._report_emission_failure(name, "observe", exc)

    @staticmethod
    def _report_emission_failure(name: str, action: str, exc: Exception) -> None:
        try:
            logger.warning(
                "observability.metric_emission_failed",
                extra={
                    "metric_name": name,
                    "metric_action": action,
                    "error_type": type(exc).__name__,
                },
            )
        except Exception:
            pass

    def samples(self) -> list[MetricSample]:
        with self._lock:
            values = list(self._totals.items())
        return sorted(
            (MetricSample(name, value, dict(labels)) for (name, labels), value in values),
            key=lambda sample: (sample.name, sorted(sample.labels.items())),
        )

    def total(self, name: str, **labels: Any) -> float:
        expected = {str(key): str(value) for key, value in labels.items()}
        with self._lock:
            return sum(
                value
                for (metric_name, metric_labels), value in self._totals.items()
                if metric_name == name
                and all(dict(metric_labels).get(key) == value for key, value in expected.items())
            )

    def as_prometheus(self) -> str:
        if not self._enabled:
            return ""
        return generate_latest(self._registry).decode("utf-8")

    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(self, *, enabled: bool) -> None:
        with self._lock:
            changed = self._enabled != bool(enabled)
            self._enabled = bool(enabled)
            if changed:
                self._totals.clear()
                self._label_values_seen.clear()
                self._series_by_metric.clear()
                self._reset_registry()

    def remove_series(self, name: str, **labels: Any) -> None:
        """Remove an obsolete labelled gauge child and its compatibility entry."""
        definition = self._definition(name, "gauge", labels)
        normalized = self._labels(name, definition, labels, reserve=False)
        metric = self._metric(name, definition)
        if normalized:
            try:
                metric.remove(*(normalized[key] for key in definition.labels))
            except KeyError:
                pass
        key = tuple(sorted(normalized.items()))
        with self._lock:
            self._totals.pop((name, key), None)
            self._series_by_metric.get(name, set()).discard(key)

    def reset(self) -> None:
        with self._lock:
            # reset() is the compatibility/test reset API. Runtime enablement is
            # controlled by configure(), which does not call reset().
            self._enabled = True
            self._totals.clear()
            self._label_values_seen.clear()
            self._series_by_metric.clear()
            self._reset_registry()

    def _reset_registry(self) -> None:
        self._registry = CollectorRegistry(auto_describe=True)
        self._metrics: dict[str, Counter | Gauge | Histogram] = {}
        self._schemas: dict[str, MetricDefinition] = {}
        for name, definition in DEFINITIONS.items():
            if definition.kind == "counter":
                metric = Counter(name, definition.help, definition.labels, registry=self._registry)
            elif definition.kind == "gauge":
                metric = Gauge(name, definition.help, definition.labels, registry=self._registry)
            else:
                metric = Histogram(
                    name,
                    definition.help,
                    definition.labels,
                    buckets=definition.buckets or Histogram.DEFAULT_BUCKETS,
                    registry=self._registry,
                )
            self._metrics[name] = metric
            self._schemas[name] = definition

    def _definition(
        self, name: str, fallback_kind: str, labels: dict[str, Any]
    ) -> MetricDefinition:
        _validate_metric_name(name)
        _validate_label_names(labels)
        explicit = DEFINITIONS.get(name)
        if explicit is None:
            raise ValueError(f"undeclared Prometheus metric: {name}")
        if explicit.kind != fallback_kind:
            raise ValueError(f"{name} is a {explicit.kind}, not a {fallback_kind}")
        return explicit

    def _metric(self, name: str, definition: MetricDefinition) -> Counter | Gauge | Histogram:
        with self._lock:
            existing = self._metrics.get(name)
            if existing is None:
                raise ValueError(f"metric catalog was not initialized for {name}")
            return existing

    def _labels(
        self,
        metric_name: str,
        definition: MetricDefinition,
        labels: dict[str, Any],
        *,
        reserve: bool = True,
    ) -> dict[str, str]:
        if set(definition.labels) != set(labels):
            raise ValueError(
                f"metric labels must be {sorted(definition.labels)}; received {sorted(labels)}"
            )
        normalized: dict[str, str] = {}
        with self._lock:
            for key in definition.labels:
                value = str(labels[key])
                closed = CLOSED_LABEL_VALUES.get(key)
                if closed is not None:
                    normalized[key] = value if value in closed else OTHER_LABEL_VALUE
                    continue
                limit = BOUNDED_LABEL_LIMITS.get(key)
                if limit is None:
                    normalized[key] = OTHER_LABEL_VALUE
                    continue
                seen = self._label_values_seen.setdefault(key, set())
                if value in seen or len(seen) < limit:
                    normalized[key] = value
                    if reserve:
                        seen.add(value)
                else:
                    normalized[key] = OTHER_LABEL_VALUE

            series = tuple(sorted(normalized.items()))
            known_series = self._series_by_metric.setdefault(metric_name, set())
            if series not in known_series and len(known_series) >= MAX_SERIES_PER_METRIC:
                normalized = {key: OTHER_LABEL_VALUE for key in definition.labels}
                series = tuple(sorted(normalized.items()))
            if reserve:
                known_series.add(series)
        return normalized


operational_metrics = PrometheusMetrics()


def start_metrics_http_server(host: str, port: int) -> ThreadingHTTPServer | None:
    """Start a process-local, read-only Prometheus endpoint."""
    if int(port) <= 0:
        return None

    class MetricsHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") != "/metrics":
                self.send_error(404)
                return
            body = generate_latest(operational_metrics.registry)
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    try:
        server = ThreadingHTTPServer((host, int(port)), MetricsHandler)
    except OSError:
        logger.exception(
            "observability.metrics_endpoint_start_failed", extra={"metrics_port": port}
        )
        return None
    Thread(target=server.serve_forever, name=f"metrics-http-{port}", daemon=True).start()
    return server


def _validate_metric_name(name: str) -> None:
    if not re.fullmatch(r"[a-zA-Z_:][a-zA-Z0-9_:]*", name):
        raise ValueError(f"invalid Prometheus metric name: {name}")


def _validate_label_names(labels: dict[str, Any]) -> None:
    forbidden = FORBIDDEN_LABELS.intersection(labels)
    if forbidden:
        raise ValueError(f"high-cardinality Prometheus labels are forbidden: {sorted(forbidden)}")
    invalid = [name for name in labels if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name)]
    if invalid:
        raise ValueError(f"invalid Prometheus labels: {sorted(invalid)}")
