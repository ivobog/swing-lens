from dataclasses import dataclass
from typing import Any

RESOLVED = "RESOLVED"
MISSING_SECTOR = "MISSING_SECTOR"
UNSUPPORTED_SECTOR = "UNSUPPORTED_SECTOR"
BENCHMARK_DATA_MISSING = "BENCHMARK_DATA_MISSING"


@dataclass(frozen=True)
class SectorBenchmarkResolution:
    sector: str | None
    benchmark_symbol: str | None
    status: str
    reason: str | None = None


def resolve_sector_benchmark(
    sector_canonical: str | None,
    mapping: dict[str, str],
) -> SectorBenchmarkResolution:
    sector = _canonical_sector(sector_canonical, mapping)
    if sector is None:
        return SectorBenchmarkResolution(
            sector=None,
            benchmark_symbol=None,
            status=MISSING_SECTOR,
            reason="sector_not_available; using broad-market RS only",
        )
    symbol = str(mapping.get(sector) or "").strip().upper()
    if not symbol:
        return SectorBenchmarkResolution(
            sector=sector,
            benchmark_symbol=None,
            status=UNSUPPORTED_SECTOR,
            reason=f"unsupported_sector:{sector}; using broad-market RS only",
        )
    return SectorBenchmarkResolution(sector, symbol, RESOLVED)


def mark_benchmark_data_missing(
    resolution: SectorBenchmarkResolution,
) -> SectorBenchmarkResolution:
    return SectorBenchmarkResolution(
        sector=resolution.sector,
        benchmark_symbol=resolution.benchmark_symbol,
        status=BENCHMARK_DATA_MISSING,
        reason=(
            f"benchmark_data_missing:{resolution.benchmark_symbol}; using broad-market RS only"
        ),
    )


def resolutions_for_tickers(
    sectors_by_ticker: dict[str, str | None],
    config: dict[str, Any],
) -> dict[str, SectorBenchmarkResolution]:
    mapping = config.get("mapping", {}) if isinstance(config, dict) else {}
    if not isinstance(mapping, dict):
        mapping = {}
    return {
        ticker.upper(): resolve_sector_benchmark(sector, mapping)
        for ticker, sector in sectors_by_ticker.items()
    }


def _canonical_sector(value: str | None, mapping: dict[str, str]) -> str | None:
    if value is None or not str(value).strip():
        return None
    normalized = " ".join(str(value).replace("&", "and").split()).casefold()
    aliases = {
        "information technology": "technology",
        "financial": "financials",
        "healthcare": "health care",
        "consumer cyclicals": "consumer discretionary",
        "consumer defensive": "consumer staples",
        "telecommunication services": "communication services",
        "communications": "communication services",
    }
    normalized = aliases.get(normalized, normalized)
    for candidate in mapping:
        if candidate.casefold() == normalized:
            return candidate
    return " ".join(str(value).split())
