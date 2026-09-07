from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from app.db import Base
from app.models import (
    ceri_tables,  # noqa: F401
    ib_market_intelligence_tables,  # noqa: F401
    tables,  # noqa: F401
)
from app.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
# Programmatic callers (especially disposable integration tests) may provide an
# explicit URL through ``Config.attributes``. Never overwrite that URL with the
# application's working-database setting.
database_url = config.attributes.get("database_url") or config.get_main_option(
    "sqlalchemy.url", None
)
database_url = str(database_url or settings.database_url)
# ConfigParser treats percent-encoded credentials as interpolation tokens.
# Escape only for Alembic's config layer; SQLAlchemy receives the original URL.
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
