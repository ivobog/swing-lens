from __future__ import annotations

import pytest

from app.settings import Settings
from scripts.ops import lifecycle_probe


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('APP_NAME="quoted value"', "quoted value"),
        ("APP_NAME=value with spaces", "value with spaces"),
        ('APP_NAME="value#hash"', "value#hash"),
        ('APP_NAME="value=equals"', "value=equals"),
        ('APP_NAME="value$money"', "value$money"),
        ('APP_NAME="a@b:c"', "a@b:c"),
        (r'APP_NAME="C:\\Program Files\\SwingLens"', r"C:\Program Files\SwingLens"),
        ("APP_NAME=", ""),
        ("APP_NAME=first\nAPP_NAME=second", "second"),
        ("APP_NAME=value # comment", "value"),
    ],
)
def test_lifecycle_config_uses_exact_pydantic_dotenv_semantics(
    tmp_path, monkeypatch, line, expected
):
    env_file = tmp_path / ".env"
    env_file.write_text(line + "\n", encoding="utf-8")
    truth = Settings(_env_file=env_file)
    monkeypatch.setattr(lifecycle_probe, "_settings", lambda: truth)
    assert lifecycle_probe._settings().app_name == expected
    report = lifecycle_probe._config_report()
    assert "postgres:postgres" not in str(report).lower()
    assert "database_url" not in str(report).lower()


def test_metrics_ports_and_disabled_state_come_from_canonical_settings(monkeypatch) -> None:
    settings = Settings(
        _env_file=None,
        observability_metrics_enabled=False,
        observability_worker_metrics_port=19101,
        observability_supervisor_metrics_port=19102,
    )
    monkeypatch.setattr(lifecycle_probe, "_settings", lambda: settings)
    assert lifecycle_probe._config_report()["metrics"] == {
        "enabled": False,
        "workerPort": 19101,
        "supervisorPort": 19102,
    }


def test_grafana_secret_is_parsed_by_pydantic_and_only_reported_as_boolean(
    tmp_path, monkeypatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('GRAFANA_ADMIN_PASSWORD="value # with $pecials"\n', encoding="utf-8")
    settings = Settings(_env_file=env_file)
    monkeypatch.setattr(lifecycle_probe, "_settings", lambda: settings)

    report = lifecycle_probe._config_report()

    assert report["grafanaPasswordConfigured"] is True
    assert "value # with $pecials" not in str(report)
