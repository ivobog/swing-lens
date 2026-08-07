from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.settings import Settings, get_settings

TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def parse_explicit_bool(value: Any, *, default: bool = False) -> bool:
    """Parse external boolean values without treating non-empty strings as true."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in TRUE_VALUES:
            return True
        if normalized in FALSE_VALUES:
            return False
    raise ValueError(f"Invalid boolean value: {value!r}")


@dataclass(frozen=True)
class CeriFeatureFlags:
    """Effective CERI flags. The master flag always gates every child."""

    enabled: bool
    provider_ingest: bool
    run_capture: bool
    ui: bool
    alerts: bool
    admin: bool
    backfill: bool

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> CeriFeatureFlags:
        settings = settings or get_settings()
        enabled = parse_explicit_bool(settings.ceri_enabled, default=False)
        return cls(
            enabled=enabled,
            provider_ingest=enabled
            and parse_explicit_bool(settings.ceri_provider_ingest_enabled, default=False),
            run_capture=enabled
            and parse_explicit_bool(settings.ceri_run_capture_enabled, default=False),
            ui=enabled and parse_explicit_bool(settings.ceri_ui_enabled, default=False),
            alerts=enabled
            and parse_explicit_bool(settings.ceri_alerts_enabled, default=False),
            admin=enabled and parse_explicit_bool(settings.ceri_admin_enabled, default=False),
            backfill=enabled
            and parse_explicit_bool(settings.ceri_backfill_enabled, default=False),
        )


def ceri_flags(settings: Settings | None = None) -> CeriFeatureFlags:
    return CeriFeatureFlags.from_settings(settings)


def require_flag(flags: CeriFeatureFlags, name: str) -> None:
    if not getattr(flags, name):
        raise CeriFeatureDisabled(f"CERI {name.replace('_', ' ')} is disabled.")


class CeriFeatureDisabled(RuntimeError):
    pass
