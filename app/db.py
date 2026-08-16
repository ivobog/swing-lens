from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.observability.db_monitor import (
    configure_database_monitor,
    install_session_flush_monitor,
)
from app.settings import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
engine_options: dict[str, object] = {"pool_pre_ping": True}
if make_url(settings.database_url).drivername.startswith("postgresql"):
    engine_options["connect_args"] = {
        "connect_timeout": settings.database_connect_timeout_seconds
    }
engine = create_engine(settings.database_url, **engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
db_monitor = configure_database_monitor(engine, settings)
install_session_flush_monitor()


def get_db() -> Generator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
