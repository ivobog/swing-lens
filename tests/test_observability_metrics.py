from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from app.observability.metrics import operational_metrics


@pytest.fixture(autouse=True)
def reset_metrics():
    operational_metrics.reset()
    yield
    operational_metrics.reset()


def test_prometheus_exposition_uses_real_counter_gauge_and_histogram() -> None:
    operational_metrics.increment("swinglens_jobs_enqueued_total", job_type="FULL_PIPELINE")
    operational_metrics.set_gauge(
        "swinglens_queue_depth", 3, queue_class="interactive", status="QUEUED"
    )
    operational_metrics.observe("swinglens_job_wait_seconds", 1.25, job_type="FULL_PIPELINE")

    body = operational_metrics.as_prometheus()

    assert "# TYPE swinglens_jobs_enqueued_total counter" in body
    assert 'swinglens_queue_depth{queue_class="interactive",status="QUEUED"} 3.0' in body
    assert 'swinglens_job_wait_seconds_count{job_type="FULL_PIPELINE"} 1.0' in body
    assert 'swinglens_job_wait_seconds_sum{job_type="FULL_PIPELINE"} 1.25' in body


@pytest.mark.parametrize(
    "label",
    ["job_id", "root_correlation_id", "workflow_key", "ticker", "query_fingerprint"],
)
def test_high_cardinality_labels_are_rejected(label: str) -> None:
    with pytest.raises(ValueError, match="high-cardinality"):
        operational_metrics.increment("swinglens_test_total", **{label: "private"})


def test_out_of_process_worker_endpoint_is_independently_scrapeable() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    code = (
        "import time; "
        "from app.observability.metrics import operational_metrics,start_metrics_http_server; "
        "operational_metrics.set_gauge('swinglens_worker_cpu_percent',12.5,"
        "worker_id='process-test'); "
        f"server=start_metrics_http_server('127.0.0.1',{port}); "
        "print('READY',flush=True); time.sleep(30)"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "READY"
        deadline = time.monotonic() + 5
        body = ""
        while time.monotonic() < deadline:
            try:
                body = (
                    urllib.request.urlopen(  # noqa: S310 - loopback test endpoint
                        f"http://127.0.0.1:{port}/metrics", timeout=1
                    )
                    .read()
                    .decode()
                )
                break
            except OSError:
                time.sleep(0.05)
        assert 'swinglens_worker_cpu_percent{worker_id="process-test"} 12.5' in body
        assert json.dumps(
            {"web_registry": operational_metrics.total("swinglens_worker_cpu_percent")}
        )
        assert operational_metrics.total("swinglens_worker_cpu_percent") == 0
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_metrics_scrape_stays_within_local_budget() -> None:
    for job_type in ("FULL_PIPELINE", "CERI_REBUILD_FEATURES", "WINNER_OUTCOME_MATURATION"):
        operational_metrics.increment("swinglens_jobs_enqueued_total", job_type=job_type)
        operational_metrics.observe(
            "swinglens_job_duration_seconds", 0.01, job_type=job_type, status="COMPLETED"
        )
    started = time.perf_counter()
    payloads = [operational_metrics.as_prometheus() for _ in range(50)]
    elapsed_per_scrape = (time.perf_counter() - started) / len(payloads)

    assert elapsed_per_scrape < 0.1
    assert all("swinglens_job_duration_seconds_bucket" in payload for payload in payloads)


def test_prometheus_config_scrapes_every_process() -> None:
    config = Path("monitoring/prometheus/prometheus.yml").read_text(encoding="utf-8")
    assert "job_name: swinglens-web" in config
    assert "job_name: swinglens-worker" in config
    assert "job_name: swinglens-supervisor" in config
    assert "host.docker.internal:9101" in config
    assert "host.docker.internal:9102" in config


def test_docker_desktop_scrape_host_is_trusted_without_public_binding() -> None:
    from app.main import create_app
    from app.settings import Settings

    app = create_app(Settings(_env_file=None, job_worker_enabled=False))
    middleware = next(
        item for item in app.user_middleware if item.cls.__name__ == "TrustedHostMiddleware"
    )
    assert "host.docker.internal" in middleware.kwargs["allowed_hosts"]
    assert app.state.settings.app_host == "127.0.0.1"
