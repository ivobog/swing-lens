from app.settings import Settings


def test_phase_0_durable_pipeline_settings_default_to_enabled_values() -> None:
    settings = Settings(_env_file=None)

    assert settings.use_durable_pipeline is True
    assert settings.job_worker_enabled is True
    assert settings.job_poll_interval_seconds == 2.0
    assert settings.job_stale_after_seconds == 900
    assert settings.job_worker_id == "local-worker-1"
    assert settings.winner_probability_enabled is False
    assert settings.winner_probability_capture_in_pipeline is False
    assert str(settings.winner_probability_config_path) == "config\\winner_probability.yaml"
    assert settings.winner_probability_admin_enabled is False
    assert settings.runs_default_page_size == 25
    assert settings.history_default_page_size == 50
    assert settings.history_max_page_size == 200


def test_phase_0_durable_pipeline_settings_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("USE_DURABLE_PIPELINE", "true")
    monkeypatch.setenv("JOB_WORKER_ENABLED", "true")
    monkeypatch.setenv("JOB_POLL_INTERVAL_SECONDS", "0.5")
    monkeypatch.setenv("JOB_STALE_AFTER_SECONDS", "60")
    monkeypatch.setenv("JOB_WORKER_ID", "test-worker")
    monkeypatch.setenv("WINNER_PROBABILITY_ENABLED", "true")
    monkeypatch.setenv("WINNER_PROBABILITY_CAPTURE_IN_PIPELINE", "true")
    monkeypatch.setenv("WINNER_PROBABILITY_CONFIG_PATH", "config/test_winner.yaml")
    monkeypatch.setenv("WINNER_PROBABILITY_ADMIN_ENABLED", "true")
    monkeypatch.setenv("RUNS_DEFAULT_PAGE_SIZE", "10")
    monkeypatch.setenv("HISTORY_DEFAULT_PAGE_SIZE", "20")
    monkeypatch.setenv("HISTORY_MAX_PAGE_SIZE", "75")

    settings = Settings(_env_file=None)

    assert settings.use_durable_pipeline is True
    assert settings.job_worker_enabled is True
    assert settings.job_poll_interval_seconds == 0.5
    assert settings.job_stale_after_seconds == 60
    assert settings.job_worker_id == "test-worker"
    assert settings.winner_probability_enabled is True
    assert settings.winner_probability_capture_in_pipeline is True
    assert str(settings.winner_probability_config_path) == "config\\test_winner.yaml"
    assert settings.winner_probability_admin_enabled is True
    assert settings.runs_default_page_size == 10
    assert settings.history_default_page_size == 20
    assert settings.history_max_page_size == 75
