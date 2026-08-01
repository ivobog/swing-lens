from app.services.setup_lifecycle.constants import (
    SLSE_ADMIN_EVALUATION_MODES,
    SLSE_ADMIN_EVALUATION_SCOPES,
    SLSE_API_ERROR_CODES,
    SLSE_API_P95_TARGET_MS,
    SLSE_CAPTURE_EVALUATION_TARGET_SECONDS,
    SLSE_ORIGIN_MODE,
    SLSE_REPLAY_OUTPUT_AUTHORITATIVE_BY_DEFAULT,
    SLSE_REPLAY_PROMOTION_REQUIRES_EXPLICIT_ADMIN_ACTION,
    SLSE_RETAIN_IMMUTABLE_EVIDENCE_INDEFINITELY,
    SLSE_RUN_DELETION_POLICY,
    SLSE_TRIGGER_AUTHORITY,
)
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
    assert settings.setup_lifecycle_enabled is False
    assert settings.setup_lifecycle_pipeline_step_enabled is False
    assert settings.setup_lifecycle_alerts_enabled is False
    assert settings.setup_lifecycle_replay_enabled is False
    assert settings.setup_lifecycle_reconstruction_enabled is False
    assert str(settings.setup_lifecycle_config_path) == "config\\setup_lifecycle.yaml"
    assert settings.setup_lifecycle_capture_evaluation_target_seconds == 60
    assert settings.setup_lifecycle_api_p95_target_ms == 500
    assert settings.setup_lifecycle_retain_indefinitely is True
    assert settings.setup_lifecycle_purge_enabled is False
    assert settings.setup_lifecycle_purge_requires_preview is True
    assert settings.setup_lifecycle_replay_promotion_requires_confirmation is True
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
    monkeypatch.setenv("SETUP_LIFECYCLE_ENABLED", "true")
    monkeypatch.setenv("SETUP_LIFECYCLE_PIPELINE_STEP_ENABLED", "true")
    monkeypatch.setenv("SETUP_LIFECYCLE_ALERTS_ENABLED", "true")
    monkeypatch.setenv("SETUP_LIFECYCLE_REPLAY_ENABLED", "true")
    monkeypatch.setenv("SETUP_LIFECYCLE_RECONSTRUCTION_ENABLED", "true")
    monkeypatch.setenv("SETUP_LIFECYCLE_CONFIG_PATH", "config/test_setup_lifecycle.yaml")
    monkeypatch.setenv("SETUP_LIFECYCLE_CAPTURE_EVALUATION_TARGET_SECONDS", "45")
    monkeypatch.setenv("SETUP_LIFECYCLE_API_P95_TARGET_MS", "350")
    monkeypatch.setenv("SETUP_LIFECYCLE_RETAIN_INDEFINITELY", "false")
    monkeypatch.setenv("SETUP_LIFECYCLE_PURGE_ENABLED", "true")
    monkeypatch.setenv("SETUP_LIFECYCLE_PURGE_REQUIRES_PREVIEW", "false")
    monkeypatch.setenv("SETUP_LIFECYCLE_REPLAY_PROMOTION_REQUIRES_CONFIRMATION", "false")
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
    assert settings.setup_lifecycle_enabled is True
    assert settings.setup_lifecycle_pipeline_step_enabled is True
    assert settings.setup_lifecycle_alerts_enabled is True
    assert settings.setup_lifecycle_replay_enabled is True
    assert settings.setup_lifecycle_reconstruction_enabled is True
    assert str(settings.setup_lifecycle_config_path) == "config\\test_setup_lifecycle.yaml"
    assert settings.setup_lifecycle_capture_evaluation_target_seconds == 45
    assert settings.setup_lifecycle_api_p95_target_ms == 350
    assert settings.setup_lifecycle_retain_indefinitely is False
    assert settings.setup_lifecycle_purge_enabled is True
    assert settings.setup_lifecycle_purge_requires_preview is False
    assert settings.setup_lifecycle_replay_promotion_requires_confirmation is False
    assert settings.runs_default_page_size == 10
    assert settings.history_default_page_size == 20
    assert settings.history_max_page_size == 75


def test_setup_lifecycle_phase_0_guard_rails_are_stable_constants() -> None:
    assert SLSE_TRIGGER_AUTHORITY == "COMPLETED_DAILY_CLOSE"
    assert SLSE_ORIGIN_MODE == "LIVE_FORWARD_CAPTURE"
    assert SLSE_RUN_DELETION_POLICY == "SET_NULL_WITH_IMMUTABLE_SOURCE_IDENTITY"
    assert SLSE_REPLAY_OUTPUT_AUTHORITATIVE_BY_DEFAULT is False
    assert SLSE_REPLAY_PROMOTION_REQUIRES_EXPLICIT_ADMIN_ACTION is True
    assert SLSE_RETAIN_IMMUTABLE_EVIDENCE_INDEFINITELY is True
    assert SLSE_CAPTURE_EVALUATION_TARGET_SECONDS == 60
    assert SLSE_API_P95_TARGET_MS == 500
    assert {
        "INVALID_STATE",
        "INVALID_DATE",
        "INVALID_THRESHOLD",
        "INVALID_SORT",
        "INVALID_CURSOR",
        "INVALID_CONFIGURATION",
        "TICKER_NOT_FOUND",
        "EPISODE_NOT_FOUND",
        "EVALUATION_NOT_FOUND",
        "ALERT_NOT_FOUND",
        "RUN_LIFECYCLE_NOT_FOUND",
    } <= SLSE_API_ERROR_CODES
    assert SLSE_ADMIN_EVALUATION_SCOPES == (
        "source_run",
        "ticker",
        "date_range",
        "all_eligible",
    )
    assert SLSE_ADMIN_EVALUATION_MODES == (
        "capture_only",
        "evaluate",
        "dry_run",
        "replay",
        "repair",
    )
