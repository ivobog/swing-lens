"""The idle release window changes autonomous scheduling, not pipeline semantics."""

from app.services.process_roles import build_process_environment
from app.settings import ProcessRole, RuntimeMode, Settings


def test_process_local_controlled_window_preserves_explicit_pipeline(monkeypatch):
    stored = Settings(
        _env_file=None,
        runtime_mode=RuntimeMode.NORMAL,
        use_durable_pipeline=True,
        durable_worker_process_enabled=True,
        embedded_job_worker_enabled=False,
        winner_probability_enabled=True,
        winner_probability_capture_in_pipeline=True,
        winner_probability_auto_maturation_enabled=True,
        winner_probability_auto_cohort_refresh_enabled=True,
        winner_cohort_refresh_v2_enabled=True,
        market_data_prewarm_enabled=True,
    )
    assert stored.winner_probability_auto_maturation_enabled
    assert stored.winner_probability_auto_cohort_refresh_enabled
    assert stored.market_data_prewarm_enabled

    for key in (
        "WINNER_PROBABILITY_AUTO_MATURATION_ENABLED",
        "WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED",
        "MARKET_DATA_PREWARM_ENABLED",
    ):
        monkeypatch.setenv(key, "false")
    controlled = Settings(
        _env_file=None,
        runtime_mode=RuntimeMode.NORMAL,
        use_durable_pipeline=True,
        durable_worker_process_enabled=True,
        embedded_job_worker_enabled=False,
        winner_probability_enabled=True,
        winner_probability_capture_in_pipeline=True,
        winner_cohort_refresh_v2_enabled=True,
    )
    assert not controlled.winner_probability_auto_maturation_enabled
    assert not controlled.winner_probability_auto_cohort_refresh_enabled
    assert not controlled.market_data_prewarm_enabled
    assert controlled.winner_probability_enabled
    assert controlled.winner_probability_capture_in_pipeline
    assert controlled.use_durable_pipeline
    assert controlled.durable_worker_process_enabled
    assert controlled.runtime_mode is RuntimeMode.NORMAL
    child = build_process_environment({}, role=ProcessRole.DURABLE_WORKER, settings=controlled)
    assert child["WINNER_PROBABILITY_AUTO_MATURATION_ENABLED"] == "false"
    assert child["WINNER_PROBABILITY_AUTO_COHORT_REFRESH_ENABLED"] == "false"
    assert child["MARKET_DATA_PREWARM_ENABLED"] == "false"
    assert child["RUNTIME_MODE"] == "NORMAL"
    assert child["USE_DURABLE_PIPELINE"] == "true"
    assert child["DURABLE_WORKER_PROCESS_ENABLED"] == "true"
