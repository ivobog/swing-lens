from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SwingLens"
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    debug: bool = True
    allow_public_bind: bool = False
    use_durable_pipeline: bool = True

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/swinglens"
    database_connect_timeout_seconds: int = Field(default=3, ge=1, le=30)

    upload_dir: Path = Field(default=Path("data/uploads"))
    export_dir: Path = Field(default=Path("data/exports"))
    cache_dir: Path = Field(default=Path("data/cache"))
    max_upload_size_mb: int = 20
    max_csv_rows: int = 5000
    max_csv_columns: int = 250
    max_export_rows: int = 10000
    max_export_size_mb: int = 100
    chart_max_bars: int = 1000
    cleanup_export_retention_days: int = 30
    cleanup_cache_retention_days: int = 30
    cleanup_orphan_upload_grace_days: int = 7
    cleanup_job_retention_days: int = 90

    ib_host: str = "127.0.0.1"
    ib_port: int = 4002
    ib_client_id: int = 21
    ib_timeout_seconds: int = 30
    ib_use_rth: bool = True
    ib_default_duration: str = "3 Y"
    ib_full_backfill_duration: str = "3 Y"
    ib_top_up_duration: str = "10 D"
    ib_refresh_duration: str = "60 D"
    ib_default_bar_size: str = "1 day"
    ib_request_delay_seconds: float = 0.25
    ib_requests_per_minute: int = 20
    ib_min_seconds_between_requests: float = 3.0
    ib_backoff_seconds: float = 90.0
    ib_max_retries: int = 3
    ib_force_conservative_mode: bool = True
    ib_fetch_benchmarks: bool = True
    ib_benchmarks: str = "SPY,QQQ"
    ib_required_daily_bars: int = 252
    ib_daily_bar_stale_after_days: int = 3
    ib_revision_audit_enabled: bool = True
    ib_market_intelligence_enabled: bool = False
    ib_liquidity_enabled: bool = False
    ib_short_pressure_enabled: bool = False
    ib_volatility_intelligence_enabled: bool = False
    ib_options_activity_enabled: bool = False
    ib_scanner_enabled: bool = False
    ib_histogram_enabled: bool = False
    ib_flex_journal_enabled: bool = False
    ib_intelligence_config_path: Path = Field(
        default=Path("config/ib_market_intelligence.yaml")
    )
    ib_intelligence_shortlist_limit: int = Field(default=25, ge=1, le=100)
    ib_intelligence_historical_requests_per_minute: int = Field(default=15, ge=1, le=60)
    ib_intelligence_historical_min_spacing_seconds: float = Field(default=3.0, ge=0.0, le=60.0)
    ib_intelligence_tws_min_spacing_seconds: float = Field(default=0.25, ge=0.0, le=60.0)
    ib_intelligence_request_max_attempts: int = Field(default=3, ge=1, le=10)
    ib_intelligence_retry_initial_seconds: float = Field(default=1.0, ge=0.0, le=300.0)
    ib_intelligence_retry_max_seconds: float = Field(default=30.0, ge=0.0, le=900.0)
    ib_intelligence_historical_chunk_days: int = Field(default=60, ge=1, le=365)
    ib_intelligence_live_concurrency: int = Field(default=10, ge=1, le=100)
    ib_intelligence_market_data_line_cap: int = Field(default=100, ge=1, le=10000)
    ib_liquidity_lookback_sessions: int = Field(default=20, ge=5, le=252)
    ib_fee_rate_lookback_sessions: int = Field(default=60, ge=20, le=504)
    ib_volatility_lookback_sessions: int = Field(default=252, ge=20, le=756)
    ib_histogram_period: str = "20 days"
    ib_flex_token: str | None = None
    ib_flex_trade_query_id: str | None = None
    ib_flex_activity_query_id: str | None = None
    ib_flex_base_url: str = "https://gdcdyn.interactivebrokers.com/Universal/servlet"
    ib_flex_http_timeout_seconds: int = Field(default=30, ge=1, le=120)
    ib_flex_poll_attempts: int = Field(default=8, ge=1, le=30)
    ib_flex_poll_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    ib_flex_report_timezone: str = "UTC"

    technical_pure_boundary_enabled: bool = False
    technical_pure_boundary_shadow_compare_enabled: bool = False
    technical_process_pool_enabled: bool = False
    technical_worker_processes: int = 4
    technical_max_in_flight: int = 8
    technical_series_version_maintenance_enabled: bool = False
    technical_artifact_cache_enabled: bool = False
    technical_artifact_cache_write_enabled: bool = False
    technical_artifact_cache_shadow_read_enabled: bool = False
    fetch_technical_overlap_enabled: bool = False
    market_data_prewarm_enabled: bool = False
    market_data_prewarm_max_tickers: int = 1000
    market_data_prewarm_watchlist: str = ""

    job_worker_enabled: bool = True
    job_poll_interval_seconds: float = 2.0
    job_stale_after_seconds: int = 900
    job_worker_id: str = "local-worker-1"
    winner_probability_enabled: bool = False
    winner_probability_capture_in_pipeline: bool = False
    winner_probability_config_path: Path = Field(default=Path("config/winner_probability.yaml"))
    winner_probability_admin_enabled: bool = False
    setup_lifecycle_enabled: bool = False
    setup_lifecycle_pipeline_step_enabled: bool = False
    setup_latest_bar_projection_enabled: bool = True
    setup_latest_bar_projection_shadow_compare_enabled: bool = False
    setup_capture_handoff_enabled: bool = False
    setup_lifecycle_alerts_enabled: bool = False
    setup_lifecycle_replay_enabled: bool = False
    setup_lifecycle_reconstruction_enabled: bool = False
    setup_lifecycle_config_path: Path = Field(default=Path("config/setup_lifecycle.yaml"))
    setup_lifecycle_capture_evaluation_target_seconds: int = 60
    setup_lifecycle_api_p95_target_ms: int = 500
    setup_lifecycle_retain_indefinitely: bool = True
    setup_lifecycle_purge_enabled: bool = False
    setup_lifecycle_purge_requires_preview: bool = True
    setup_lifecycle_replay_promotion_requires_confirmation: bool = True
    ceri_enabled: bool = False
    ceri_provider_ingest_enabled: bool = False
    ceri_legacy_pipeline_scheduling_enabled: bool = True
    ceri_batched_workflow_enabled: bool = False
    ceri_provider_batch_size: int = 25
    ceri_normalization_batch_size: int = 50
    ceri_feature_batch_size: int = 50
    ceri_batch_checkpoint_interval: int = 5
    ceri_barrier_retry_seconds: int = 5
    ceri_run_capture_enabled: bool = False
    ceri_ui_enabled: bool = False
    ceri_alerts_enabled: bool = False
    ceri_admin_enabled: bool = False
    ceri_backfill_enabled: bool = False
    ceri_config_path: Path = Field(default=Path("config/ceri.yaml"))
    ceri_taxonomy_path: Path = Field(default=Path("config/ceri_catalyst_taxonomy.yaml"))
    eodhd_api_key: str | None = None
    eodhd_base_url: str = "https://eodhd.com"
    eodhd_requests_per_minute: int = Field(default=300, ge=1)
    eodhd_daily_call_budget: int = Field(default=80000, ge=1)
    eodhd_http_timeout_seconds: int = Field(default=30, ge=1, le=120)
    eodhd_max_attempts: int = Field(default=4, ge=1, le=8)
    eodhd_terms_version: str = "2026-08-personal"
    sec_user_agent: str = "SwingLens/0.1.0 operator@example.invalid"
    sec_requests_per_second: float = Field(default=2.0, gt=0, le=10)
    sec_http_timeout_seconds: int = Field(default=30, ge=1, le=120)
    sec_form4_enabled: bool = False
    runs_default_page_size: int = 25
    history_default_page_size: int = 50
    history_max_page_size: int = 200

    @property
    def ib_benchmark_symbols(self) -> tuple[str, ...]:
        return tuple(
            symbol.strip().upper() for symbol in self.ib_benchmarks.split(",") if symbol.strip()
        )

    def ensure_local_dirs(self) -> None:
        for directory in (self.upload_dir, self.export_dir, self.cache_dir):
            directory.mkdir(parents=True, exist_ok=True)

    @model_validator(mode="after")
    def validate_local_runtime_boundary(self) -> "Settings":
        public_bind_hosts = {"0.0.0.0", "::", ""}
        if self.app_host in public_bind_hosts and self.debug:
            raise ValueError("debug mode is not allowed on a public bind host")
        if self.app_host in public_bind_hosts and not self.allow_public_bind:
            raise ValueError("public bind requires ALLOW_PUBLIC_BIND=true")
        positive_fields = {
            "max_upload_size_mb": self.max_upload_size_mb,
            "max_csv_rows": self.max_csv_rows,
            "max_csv_columns": self.max_csv_columns,
            "max_export_rows": self.max_export_rows,
            "max_export_size_mb": self.max_export_size_mb,
            "chart_max_bars": self.chart_max_bars,
            "cleanup_export_retention_days": self.cleanup_export_retention_days,
            "cleanup_cache_retention_days": self.cleanup_cache_retention_days,
            "cleanup_orphan_upload_grace_days": self.cleanup_orphan_upload_grace_days,
            "cleanup_job_retention_days": self.cleanup_job_retention_days,
            "market_data_prewarm_max_tickers": self.market_data_prewarm_max_tickers,
            "ib_intelligence_shortlist_limit": self.ib_intelligence_shortlist_limit,
            "ib_intelligence_historical_requests_per_minute": (
                self.ib_intelligence_historical_requests_per_minute
            ),
            "ib_intelligence_live_concurrency": self.ib_intelligence_live_concurrency,
            "ib_intelligence_market_data_line_cap": self.ib_intelligence_market_data_line_cap,
            "ib_intelligence_request_max_attempts": self.ib_intelligence_request_max_attempts,
            "ib_intelligence_historical_chunk_days": self.ib_intelligence_historical_chunk_days,
        }
        for field_name, value in positive_fields.items():
            if value < 1:
                raise ValueError(f"{field_name} must be positive")
        if not 1 <= self.technical_worker_processes <= 8:
            raise ValueError("technical_worker_processes must be between 1 and 8")
        if self.technical_max_in_flight < 1:
            raise ValueError("technical_max_in_flight must be positive")
        for name in (
            "ceri_provider_batch_size",
            "ceri_normalization_batch_size",
            "ceri_feature_batch_size",
            "ceri_batch_checkpoint_interval",
            "ceri_barrier_retry_seconds",
        ):
            if int(getattr(self, name)) < 1:
                raise ValueError(f"{name} must be positive")
        if (
            self.ceri_legacy_pipeline_scheduling_enabled
            and self.ceri_batched_workflow_enabled
        ):
            raise ValueError(
                "legacy and batched CERI pipeline scheduling cannot both be enabled"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_local_dirs()
    return settings
