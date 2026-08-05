from pathlib import Path

from app.services.ceri.constants import (
    CERI_ADMIN_CSRF_REQUIRED,
    CERI_API_ERROR_CODES,
    CERI_DAILY_CUTOFF_TIMEZONE,
    CERI_EFFECTIVE_SESSION_POLICY,
    CERI_JOB_CHECKPOINTS_REQUIRED,
    CERI_JOB_EXECUTION_FENCING_REQUIRED,
    CERI_JOB_HEARTBEATS_REQUIRED,
    CERI_JOB_REQUEST_KEYS_REQUIRED,
    CERI_LICENSED_PURGE_REQUIRES_AUDIT,
    CERI_LICENSED_PURGE_REQUIRES_CONFIRMATION,
    CERI_LICENSED_PURGE_REQUIRES_PREVIEW,
    CERI_LOCAL_ADMIN_REQUIRED,
    CERI_ORDER_PLACEMENT_ALLOWED,
    CERI_RUN_DELETION_POLICY,
    CERI_SOURCE_CONFLICT_POLICY,
)
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

    assert settings.app_host == "127.0.0.1"
    assert settings.use_durable_pipeline is True
    assert settings.job_worker_enabled is True
    assert settings.job_poll_interval_seconds == 2.0
    assert settings.job_stale_after_seconds == 900
    assert settings.job_worker_id == "local-worker-1"
    assert settings.winner_probability_enabled is False
    assert settings.winner_probability_capture_in_pipeline is False
    assert settings.winner_probability_config_path == Path("config/winner_probability.yaml")
    assert settings.winner_probability_admin_enabled is False
    assert settings.setup_lifecycle_enabled is False
    assert settings.setup_lifecycle_pipeline_step_enabled is False
    assert settings.setup_latest_bar_projection_enabled is False
    assert settings.setup_latest_bar_projection_shadow_compare_enabled is False
    assert settings.setup_capture_handoff_enabled is False
    assert settings.technical_pure_boundary_enabled is False
    assert settings.technical_pure_boundary_shadow_compare_enabled is False
    assert settings.technical_process_pool_enabled is False
    assert settings.technical_worker_processes == 4
    assert settings.technical_max_in_flight == 8
    assert settings.technical_series_version_maintenance_enabled is False
    assert settings.technical_artifact_cache_enabled is False
    assert settings.technical_artifact_cache_write_enabled is False
    assert settings.technical_artifact_cache_shadow_read_enabled is False
    assert settings.fetch_technical_overlap_enabled is False
    assert settings.setup_lifecycle_alerts_enabled is False
    assert settings.setup_lifecycle_replay_enabled is False
    assert settings.setup_lifecycle_reconstruction_enabled is False
    assert settings.setup_lifecycle_config_path == Path("config/setup_lifecycle.yaml")
    assert settings.setup_lifecycle_capture_evaluation_target_seconds == 60
    assert settings.setup_lifecycle_api_p95_target_ms == 500
    assert settings.setup_lifecycle_retain_indefinitely is True
    assert settings.setup_lifecycle_purge_enabled is False
    assert settings.setup_lifecycle_purge_requires_preview is True
    assert settings.setup_lifecycle_replay_promotion_requires_confirmation is True
    assert settings.ceri_enabled is False
    assert settings.ceri_provider_ingest_enabled is False
    assert settings.ceri_run_capture_enabled is False
    assert settings.ceri_ui_enabled is False
    assert settings.ceri_alerts_enabled is False
    assert settings.ceri_admin_enabled is False
    assert settings.ceri_backfill_enabled is False
    assert settings.ceri_config_path == Path("config/ceri.yaml")
    assert settings.ceri_taxonomy_path == Path("config/ceri_catalyst_taxonomy.yaml")
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
    monkeypatch.setenv("CERI_ENABLED", "true")
    monkeypatch.setenv("CERI_PROVIDER_INGEST_ENABLED", "true")
    monkeypatch.setenv("CERI_RUN_CAPTURE_ENABLED", "true")
    monkeypatch.setenv("CERI_UI_ENABLED", "true")
    monkeypatch.setenv("CERI_ALERTS_ENABLED", "true")
    monkeypatch.setenv("CERI_ADMIN_ENABLED", "true")
    monkeypatch.setenv("CERI_BACKFILL_ENABLED", "true")
    monkeypatch.setenv("CERI_CONFIG_PATH", "config/test_ceri.yaml")
    monkeypatch.setenv("CERI_TAXONOMY_PATH", "config/test_ceri_taxonomy.yaml")
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
    assert settings.winner_probability_config_path == Path("config/test_winner.yaml")
    assert settings.winner_probability_admin_enabled is True
    assert settings.setup_lifecycle_enabled is True
    assert settings.setup_lifecycle_pipeline_step_enabled is True
    assert settings.setup_lifecycle_alerts_enabled is True
    assert settings.setup_lifecycle_replay_enabled is True
    assert settings.setup_lifecycle_reconstruction_enabled is True
    assert settings.setup_lifecycle_config_path == Path("config/test_setup_lifecycle.yaml")
    assert settings.setup_lifecycle_capture_evaluation_target_seconds == 45
    assert settings.setup_lifecycle_api_p95_target_ms == 350
    assert settings.setup_lifecycle_retain_indefinitely is False
    assert settings.setup_lifecycle_purge_enabled is True
    assert settings.setup_lifecycle_purge_requires_preview is False
    assert settings.setup_lifecycle_replay_promotion_requires_confirmation is False
    assert settings.ceri_enabled is True
    assert settings.ceri_provider_ingest_enabled is True
    assert settings.ceri_run_capture_enabled is True
    assert settings.ceri_ui_enabled is True
    assert settings.ceri_alerts_enabled is True
    assert settings.ceri_admin_enabled is True
    assert settings.ceri_backfill_enabled is True
    assert settings.ceri_config_path == Path("config/test_ceri.yaml")
    assert settings.ceri_taxonomy_path == Path("config/test_ceri_taxonomy.yaml")
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


def test_ceri_phase_0_guard_rails_are_stable_constants() -> None:
    assert CERI_DAILY_CUTOFF_TIMEZONE == "America/New_York"
    assert CERI_EFFECTIVE_SESSION_POLICY == "AFTER_HOURS_NEXT_COMPLETED_US_SESSION"
    assert CERI_SOURCE_CONFLICT_POLICY == "PROVIDER_PRIORITY_PRESERVE_ALL_OBSERVATIONS"
    assert CERI_RUN_DELETION_POLICY == "SET_NULL_RETAIN_IMMUTABLE_EVIDENCE"
    assert CERI_LOCAL_ADMIN_REQUIRED is True
    assert CERI_ADMIN_CSRF_REQUIRED is True
    assert CERI_ORDER_PLACEMENT_ALLOWED is False
    assert CERI_JOB_REQUEST_KEYS_REQUIRED is True
    assert CERI_JOB_EXECUTION_FENCING_REQUIRED is True
    assert CERI_JOB_HEARTBEATS_REQUIRED is True
    assert CERI_JOB_CHECKPOINTS_REQUIRED is True
    assert CERI_LICENSED_PURGE_REQUIRES_PREVIEW is True
    assert CERI_LICENSED_PURGE_REQUIRES_CONFIRMATION is True
    assert CERI_LICENSED_PURGE_REQUIRES_AUDIT is True
    assert {
        "INVALID_FILTER",
        "INVALID_DATE_RANGE",
        "INVALID_CONFIGURATION",
        "TICKER_NOT_FOUND",
        "RUN_NOT_FOUND",
        "PROVIDER_CAPABILITY_UNAVAILABLE",
        "CONFIG_VERSION_NOT_FOUND",
        "REVIEW_CONFLICT",
        "BACKFILL_ALREADY_ACTIVE",
        "LICENSE_RESTRICTED",
        "PURGE_CONFIRMATION_REQUIRED",
        "ADMIN_FORBIDDEN",
        "DUPLICATE_ACTIVE_BACKFILL",
        "LICENSE_RESTRICTED_FIELD",
        "PURGE_CONFLICT",
        "UNAUTHORIZED_LOCAL_ADMIN",
    } <= CERI_API_ERROR_CODES
