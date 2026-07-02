import asyncio
from collections.abc import Callable
from dataclasses import dataclass

from ib_insync import IB

from app.settings import Settings, get_settings


@dataclass(frozen=True)
class IBConnectionStatus:
    connected: bool
    host: str
    port: int
    client_id: int
    message: str


IBFactory = Callable[[], IB]


def create_ib_client(ib_factory: IBFactory = IB) -> IB:
    _ensure_event_loop()
    return ib_factory()


def _ensure_event_loop() -> None:
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def check_ib_connection(
    settings: Settings | None = None,
    ib_factory: IBFactory = IB,
) -> IBConnectionStatus:
    settings = settings or get_settings()
    ib = create_ib_client(ib_factory)
    try:
        ib.connect(
            settings.ib_host,
            settings.ib_port,
            clientId=settings.ib_client_id,
            timeout=settings.ib_timeout_seconds,
            readonly=True,
        )
        return IBConnectionStatus(
            connected=ib.isConnected(),
            host=settings.ib_host,
            port=settings.ib_port,
            client_id=settings.ib_client_id,
            message="Connected to Interactive Brokers.",
        )
    except Exception as exc:
        return IBConnectionStatus(
            connected=False,
            host=settings.ib_host,
            port=settings.ib_port,
            client_id=settings.ib_client_id,
            message=str(exc),
        )
    finally:
        if ib.isConnected():
            ib.disconnect()
