from functools import lru_cache
from pathlib import Path

from pydantic import Field
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

    database_url: str = "postgresql+psycopg://postgres:postgres@127.0.0.1:5432/swinglens"

    upload_dir: Path = Field(default=Path("data/uploads"))
    export_dir: Path = Field(default=Path("data/exports"))
    cache_dir: Path = Field(default=Path("data/cache"))
    max_upload_size_mb: int = 20

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
    ib_revision_audit_enabled: bool = False

    @property
    def ib_benchmark_symbols(self) -> tuple[str, ...]:
        return tuple(
            symbol.strip().upper()
            for symbol in self.ib_benchmarks.split(",")
            if symbol.strip()
        )

    def ensure_local_dirs(self) -> None:
        for directory in (self.upload_dir, self.export_dir, self.cache_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_local_dirs()
    return settings
