import pytest
from pydantic import ValidationError

from app import worker_supervisor
from app.settings import Settings


def test_sector_launcher_sets_explicit_profile_with_custom_worker_identity(monkeypatch):
    settings = Settings(_env_file=None)
    launches = []
    monkeypatch.setattr(worker_supervisor, "get_settings", lambda: settings)
    monkeypatch.setattr(
        worker_supervisor,
        "_start_logged_child",
        lambda command, **kwargs: launches.append((command, kwargs)),
    )
    worker_supervisor._start_worker(
        "custom-owner-sector", "broker", operation_profile="sector_refresh"
    )
    worker_supervisor._start_worker("custom-owner", "interactive,broker,background")
    for index, expected in enumerate(("sector_refresh", "general")):
        command, kwargs = launches[index]
        assert kwargs["kwargs"]["env"]["JOB_WORKER_PROFILE"] == expected
        assert command[command.index("--worker-id") + 1] == (
            "custom-owner-sector" if index == 0 else "custom-owner"
        )
    assert settings.ib_client_id == 21 and settings.ib_sector_client_id == 30
    assert settings.observability_sector_worker_metrics_port == 9103


@pytest.mark.parametrize("reserved", [21, 22, 25, 26, 29])
def test_sector_client_rejects_pipeline_health_and_capability_ids(reserved):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, ib_sector_client_id=reserved)


def test_sector_metrics_port_rejects_normal_worker_collision():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, observability_sector_worker_metrics_port=9101)


def test_frozen_behavioral_configuration_preserves_worker_transport_identity():
    from types import SimpleNamespace

    from app.services.configuration_delivery import (
        EXECUTION_SETTINGS,
        configuration_delivery_scope,
        settings_for_delivery,
    )

    values = {"technical_v5_enabled": False}
    delivery = SimpleNamespace(
        configurations={"execution.settings": SimpleNamespace(values=values)}
    )
    runtime = Settings(_env_file=None, job_worker_profile="sector_refresh")
    with configuration_delivery_scope(delivery):
        effective = settings_for_delivery(runtime)
        assert effective.technical_v5_enabled is False
        assert effective.job_worker_profile == "sector_refresh"
        assert effective.ib_sector_client_id == 30 and effective.ib_client_id == 21
    assert {"job_worker_profile", "ib_sector_client_id", "ib_client_id"}.isdisjoint(
        EXECUTION_SETTINGS
    )
    assert values == {"technical_v5_enabled": False}
