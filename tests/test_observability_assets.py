from __future__ import annotations

import json
import subprocess
from pathlib import Path


def test_all_required_alerts_are_provisioned_without_forbidden_dimensions() -> None:
    rules = Path("monitoring/prometheus/alerts.yml").read_text(encoding="utf-8")
    required = {
        "SwingLensWorkerMissing",
        "SwingLensSupervisorMissing",
        "SwingLensJobStalled",
        "SwingLensQueueBacklog",
        "SwingLensJobFanoutAnomaly",
        "SwingLensRepeatedJobFailures",
        "SwingLensWorkerMemoryCritical",
        "SwingLensDatabasePressure",
        "SwingLensCriticalTelemetryLoss",
        "SwingLensDiskPressure",
    }
    assert {name for name in required if f"alert: {name}" in rules} == required
    for forbidden in (
        "job_id=",
        "root_correlation_id=",
        "workflow_key=",
        "request_key=",
        "ticker=",
        "query_fingerprint=",
    ):
        assert forbidden not in rules


def test_seven_provisioned_dashboards_are_valid_json() -> None:
    paths = sorted(Path("monitoring/grafana/dashboards").glob("*.json"))
    dashboards = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    titles = {dashboard["title"] for dashboard in dashboards}
    assert titles == {
        "SwingLens Overview",
        "Jobs & Queues",
        "Pipeline Performance",
        "CERI & Providers",
        "Database / SQL Flight Recorder",
        "Worker Resources",
        "SwingLens Lifecycle Control Plane",
    }

    for dashboard in dashboards:
        assert dashboard["uid"]
        assert dashboard["templating"]["list"] == []
        for panel in dashboard["panels"]:
            assert panel["datasource"] == {
                "type": "prometheus",
                "uid": "swinglens-prometheus",
            }
            for target in panel["targets"]:
                assert target["datasource"] == {
                    "type": "prometheus",
                    "uid": "swinglens-prometheus",
                }
                assert target["refId"]
                assert target["expr"]
                assert "swinglens_pipeline_active_total" not in target["expr"]


def test_grafana_provisions_stable_prometheus_datasource_and_dashboard_folder() -> None:
    datasource = Path(
        "monitoring/grafana/provisioning/datasources/prometheus.yml"
    ).read_text(encoding="utf-8")
    provider = Path(
        "monitoring/grafana/provisioning/dashboards/dashboards.yml"
    ).read_text(encoding="utf-8")

    assert "uid: swinglens-prometheus" in datasource
    assert "url: http://prometheus:9090" in datasource
    assert "isDefault: true" in datasource
    assert "name: SwingLens" in provider
    assert "folder: SwingLens" in provider
    assert "path: /var/lib/grafana/dashboards" in provider


def test_monitoring_is_loopback_only_and_requires_an_external_grafana_secret() -> None:
    compose = Path("docker-compose.observability.yml").read_text(encoding="utf-8")
    assert 'ports: ["127.0.0.1:9090:9090"]' in compose
    assert 'ports: ["127.0.0.1:3000:3000"]' in compose
    assert "0.0.0.0:9090" not in compose
    assert "0.0.0.0:3000" not in compose
    assert "${GRAFANA_ADMIN_PASSWORD:?" in compose


def test_repository_contains_no_legacy_or_literal_grafana_admin_password() -> None:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    legacy = "swinglens" + "-local"
    for name in listed:
        path = Path(name)
        if not path.is_file():
            continue
        if path.suffix.lower() not in {
            ".env",
            ".example",
            ".md",
            ".py",
            ".toml",
            ".yml",
            ".yaml",
        }:
            continue
        content = path.read_text(encoding="utf-8", errors="ignore")
        assert legacy not in content, name
        if "GF_SECURITY_ADMIN_PASSWORD:" in content:
            assert "${GRAFANA_ADMIN_PASSWORD:?" in content, name
