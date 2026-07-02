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

    ib_host: str = "127.0.0.1"
    ib_port: int = 4002
    ib_client_id: int = 21
    ib_timeout_seconds: int = 30
    ib_use_rth: bool = True
    ib_default_duration: str = "2 Y"
    ib_default_bar_size: str = "1 day"
    ib_request_delay_seconds: float = 0.25

    def ensure_local_dirs(self) -> None:
        for directory in (self.upload_dir, self.export_dir, self.cache_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_local_dirs()
    return settings
