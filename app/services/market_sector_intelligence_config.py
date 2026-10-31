"""Independent read-only configuration, outside durable calculation delivery.

The legacy loader remains authoritative for its source mapping (including an
existing delivery scope). No calculation configuration is persisted or registered
for cache inspection, and legacy configuration identities are never modified.
"""

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.services.market_sector_intelligence_rotation_math import ALGORITHM_VERSION, SESSION_OFFSETS
from app.services.market_sector_intelligence_universe import (
    MarketSectorDefinition,
    SectorCrosswalkRow,
    build_market_sector_universe,
    build_sector_crosswalk,
)
from app.services.sector_rotation_config import load_sector_rotation_config

CONFIG_PATH = Path("config/market_sector_intelligence.yaml")
EXPECTED_ETFS = frozenset(
    {"XLK", "XLC", "XLY", "XLP", "XLF", "XLV", "XLI", "XLE", "XLB", "XLRE", "XLU"}
)
EXPECTED_MARKET_SECTORS = frozenset(
    {
        "Technology",
        "Communication Services",
        "Consumer Cyclical",
        "Consumer Defensive",
        "Financial Services",
        "Healthcare",
        "Industrials",
        "Energy",
        "Basic Materials",
        "Real Estate",
        "Utilities",
    }
)
STANDARD_MARKET_LABELS = {
    "Consumer Cyclical": "Consumer Discretionary",
    "Consumer Defensive": "Consumer Staples",
    "Financial Services": "Financials",
    "Healthcare": "Health Care",
    "Basic Materials": "Materials",
}


class MarketSectorIntelligenceConfigError(ValueError):
    pass


@dataclass(frozen=True)
class RotationEngineConfig:
    algorithm_version: str = ALGORITHM_VERSION
    session_offsets: tuple[int, ...] = SESSION_OFFSETS
    primary_rank_metric: str = "rs_63_pct"
    max_history_limit: int = 100


@dataclass(frozen=True)
class MarketSectorIntelligenceConfig:
    version: str
    enabled: bool
    benchmark_symbol: str
    timeframe: str
    configuration_digest: str
    sectors: tuple[MarketSectorDefinition, ...]
    crosswalk: tuple[SectorCrosswalkRow, ...]
    legacy_etf_blending_enabled: bool
    rotation_engine: RotationEngineConfig = RotationEngineConfig()


def load_market_sector_intelligence_config(
    path: Path = CONFIG_PATH,
) -> MarketSectorIntelligenceConfig:
    try:
        feature = yaml.safe_load(path.read_text(encoding="utf-8"))
        legacy = load_sector_rotation_config()
        return validate_market_sector_intelligence_config(feature, legacy)
    except MarketSectorIntelligenceConfigError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError, ValueError) as exc:
        raise MarketSectorIntelligenceConfigError(
            "Check config/market_sector_intelligence.yaml and the legacy sector mapping: "
            "configuration could not be loaded."
        ) from exc


def validate_market_sector_intelligence_config(
    feature: dict[str, Any], legacy: dict[str, Any]
) -> MarketSectorIntelligenceConfig:
    if not isinstance(feature, dict) or set(feature) - {"rotation_engine"} != {
        "version",
        "enabled",
        "benchmark_symbol",
        "timeframe",
        "standard_market_labels",
    }:
        raise MarketSectorIntelligenceConfigError(
            "market_sector_intelligence.yaml requires version, enabled, benchmark_symbol, "
            "timeframe and standard_market_labels only."
        )
    if not isinstance(feature["version"], str) or not feature["version"].strip():
        raise MarketSectorIntelligenceConfigError("Independent configuration version must be text.")
    if not isinstance(feature["enabled"], bool):
        raise MarketSectorIntelligenceConfigError("Independent enabled must be a Boolean.")
    benchmark = feature["benchmark_symbol"]
    if not isinstance(benchmark, str) or benchmark.strip().upper() != "SPY":
        raise MarketSectorIntelligenceConfigError(
            "D1 benchmark_symbol must be SPY, separate from ETFs."
        )
    if feature["timeframe"] != "1 day":
        raise MarketSectorIntelligenceConfigError("D1 timeframe must be 1 day.")
    labels = feature["standard_market_labels"]
    if labels != STANDARD_MARKET_LABELS:
        raise MarketSectorIntelligenceConfigError(
            "standard_market_labels must contain the five documented display-only name differences."
        )

    rotation = feature.get("rotation_engine")
    if "rotation_engine" in feature and (
        rotation
        != {
            "algorithm_version": ALGORITHM_VERSION,
            "benchmark_symbol": "SPY",
            "timeframe": "1 day",
            "session_offsets": list(SESSION_OFFSETS),
            "primary_rank_metric": "rs_63_pct",
            "max_history_limit": 100,
        }
        or type(rotation.get("max_history_limit")) is not int
        or any(type(offset) is not int for offset in rotation.get("session_offsets", []))
    ):
        raise MarketSectorIntelligenceConfigError(
            "rotation_engine must use the exact market-rotation-v1 rules; "
            "rule changes require a new version."
        )

    taxonomy = legacy.get("sector_taxonomy")
    proxies = legacy.get("sector_etf_proxies")
    if not isinstance(taxonomy, dict) or not isinstance(proxies, dict):
        raise MarketSectorIntelligenceConfigError(
            "Legacy sector taxonomy and ETF proxies are required."
        )
    canonical = taxonomy.get("canonical")
    if (
        not isinstance(canonical, list)
        or any(not isinstance(item, str) for item in canonical)
        or len(set(canonical)) != len(canonical)
        or not (EXPECTED_MARKET_SECTORS | {"Unknown"}).issubset(canonical)
    ):
        raise MarketSectorIntelligenceConfigError("Legacy canonical sector labels are invalid.")
    for kind in ("aliases", "tradingview_map"):
        mapping = taxonomy.get(kind, {})
        if not isinstance(mapping, dict) or any(
            not isinstance(label, str) or not label.strip() or target not in canonical
            for label, target in mapping.items()
        ):
            raise MarketSectorIntelligenceConfigError(
                "Legacy aliases/mappings must target canonical labels."
            )
    if set(proxies) != EXPECTED_MARKET_SECTORS or any(
        not isinstance(ticker, str) or not ticker.strip() for ticker in proxies.values()
    ):
        raise MarketSectorIntelligenceConfigError(
            "Exactly eleven market-sector ETF proxies are required."
        )
    symbols = [ticker.strip().upper() for ticker in proxies.values()]
    if (
        len(set(symbols)) != 11
        or set(symbols) != EXPECTED_ETFS
        or benchmark.strip().upper() in symbols
    ):
        raise MarketSectorIntelligenceConfigError(
            "ETF proxies must be the eleven unique standard SPDR sector symbols, excluding SPY."
        )
    normalized = {
        **feature,
        "version": feature["version"].strip(),
        "benchmark_symbol": benchmark.strip().upper(),
    }
    evidence = {
        "market_sector_intelligence": normalized,
        "sector_taxonomy": taxonomy,
        "sector_etf_proxies": {key: value.strip().upper() for key, value in proxies.items()},
    }
    digest = hashlib.sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    sectors = build_market_sector_universe(legacy, labels)
    return MarketSectorIntelligenceConfig(
        version=normalized["version"],
        enabled=feature["enabled"],
        benchmark_symbol=normalized["benchmark_symbol"],
        timeframe=feature["timeframe"],
        configuration_digest=digest,
        sectors=sectors,
        crosswalk=build_sector_crosswalk(legacy, sectors),
        legacy_etf_blending_enabled=legacy["etf_score"]["enabled"],
    )
