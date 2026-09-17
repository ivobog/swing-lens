from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.services.ib_market_intelligence.evidence_hash import evidence_hash
from app.settings import Settings, get_settings


class IBMarketIntelligenceConfigError(ValueError):
    pass


def secret_safe_ibmi_config_hash(raw: dict[str, Any]) -> str:
    """Keep legacy non-secret compatibility hashing without credential-derived digests."""
    from app.services.effective_configuration import _secret_key

    def nonsecret(value):
        if isinstance(value, dict):
            return {
                key: nonsecret(item)
                for key, item in value.items()
                if not _secret_key(key)
                and key.lower()
                not in {"username", "login", "cookie", "cookies", "authorization", "auth"}
            }
        if isinstance(value, (list, tuple)):
            return [nonsecret(item) for item in value]
        return value

    return evidence_hash(nonsecret(raw))


@dataclass(frozen=True)
class ScannerPreset:
    name: str
    version: str
    instrument: str
    location: str
    scan_code: str
    max_results: int
    filters: tuple[dict[str, str], ...]


@dataclass(frozen=True)
class IBMarketIntelligenceConfig:
    raw: dict[str, Any]
    config_hash: str
    calculation_version: str
    config_version: str
    source_version: str
    scanner_presets: tuple[ScannerPreset, ...]

    def section(self, name: str) -> dict[str, Any]:
        return dict(self.raw.get(name) or {})


def load_ib_market_intelligence_config(
    path: Path | None = None,
    settings: Settings | None = None,
) -> IBMarketIntelligenceConfig:
    from app.services.configuration_delivery import current_delivery, delivered_native

    if current_delivery() is not None:
        return delivered_native("ibmi")

    settings = settings or get_settings()
    config_path = path or settings.ib_intelligence_config_path
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise IBMarketIntelligenceConfigError(f"Unable to load {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise IBMarketIntelligenceConfigError("IB market-intelligence config must be a mapping")
    engine = _mapping(raw, "engine")
    required_engine = ("calculation_version", "config_version", "source_version")
    missing = [key for key in required_engine if not engine.get(key)]
    if missing:
        raise IBMarketIntelligenceConfigError(f"engine is missing: {', '.join(missing)}")
    scanner = _mapping(raw, "scanner")
    presets = tuple(_scanner_preset(item, scanner) for item in scanner.get("presets", []))
    names = [item.name for item in presets]
    if len(names) != len(set(names)):
        raise IBMarketIntelligenceConfigError("scanner preset names must be unique")
    result = IBMarketIntelligenceConfig(
        raw=raw,
        config_hash=secret_safe_ibmi_config_hash(raw),
        calculation_version=str(engine["calculation_version"]),
        config_version=str(engine["config_version"]),
        source_version=str(engine["source_version"]),
        scanner_presets=presets,
    )
    from app.services.configuration_source_values import winning_sources

    object.__setattr__(
        result,
        "_configuration_sources",
        winning_sources(
            raw,
            raw,
            config_path.as_posix() if not config_path.is_absolute() else None,
            "ibmi-parser-defaults",
        ),
    )
    from app.services.contextual_effective_configuration import (
        IBMI_DEFAULTS,
        resolve_ibmi_configuration,
    )

    object.__setattr__(
        result,
        "_effective_configurations",
        {module: resolve_ibmi_configuration(result, module) for module in IBMI_DEFAULTS},
    )
    return result


def effective_module_enabled(
    module_setting: bool,
    *,
    settings: Settings | None = None,
    config_section: dict[str, Any] | None = None,
) -> bool:
    settings = settings or get_settings()
    return bool(
        settings.ib_market_intelligence_enabled
        and module_setting
        and (config_section or {}).get("enabled", False)
    )


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise IBMarketIntelligenceConfigError(f"{key} must be a mapping")
    return value


def _scanner_preset(value: Any, scanner: dict[str, Any]) -> ScannerPreset:
    if not isinstance(value, dict):
        raise IBMarketIntelligenceConfigError("scanner preset must be a mapping")
    required = ("name", "instrument", "location", "scan_code")
    missing = [key for key in required if not str(value.get(key, "")).strip()]
    if missing:
        raise IBMarketIntelligenceConfigError(f"scanner preset is missing: {', '.join(missing)}")
    max_results = int(value.get("max_results", scanner.get("max_results", 50)))
    if not 1 <= max_results <= 50:
        raise IBMarketIntelligenceConfigError("scanner max_results must be between 1 and 50")
    filters: list[dict[str, str]] = []
    for raw_filter in value.get("filters", []):
        if not isinstance(raw_filter, dict) or not raw_filter.get("tag"):
            raise IBMarketIntelligenceConfigError("scanner filters require tag and value")
        filters.append({"tag": str(raw_filter["tag"]), "value": str(raw_filter.get("value", ""))})
    return ScannerPreset(
        name=str(value["name"]),
        version=str(value.get("version", "1")),
        instrument=str(value["instrument"]),
        location=str(value["location"]),
        scan_code=str(value["scan_code"]),
        max_results=max_results,
        filters=tuple(filters),
    )
