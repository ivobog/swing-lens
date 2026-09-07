"""Compatibility import for the shared Prometheus observability facade."""

from app.observability.metrics import MetricSample, PrometheusMetrics, operational_metrics

OperationalMetricRegistry = PrometheusMetrics

__all__ = ["MetricSample", "OperationalMetricRegistry", "PrometheusMetrics", "operational_metrics"]
