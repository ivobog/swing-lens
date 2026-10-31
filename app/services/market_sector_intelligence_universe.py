"""Display-only views of the existing run taxonomy; no source mutation."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MarketSectorDefinition:
    run_canonical_sector: str
    standard_market_sector: str
    etf_symbol: str


@dataclass(frozen=True)
class SectorCrosswalkRow:
    source_label: str
    source_kind: str
    run_canonical_sector: str
    standard_market_sector: str | None
    etf_symbol: str | None


def build_market_sector_universe(
    legacy: dict[str, Any], labels: dict[str, str]
) -> tuple[MarketSectorDefinition, ...]:
    """Build from validated source proxies, never from an upload's constituents."""
    return tuple(
        MarketSectorDefinition(canonical, labels.get(canonical, canonical), ticker.strip().upper())
        for canonical, ticker in sorted(legacy["sector_etf_proxies"].items())
    )


def build_sector_crosswalk(
    legacy: dict[str, Any], sectors: tuple[MarketSectorDefinition, ...]
) -> tuple[SectorCrosswalkRow, ...]:
    by_canonical = {sector.run_canonical_sector: sector for sector in sectors}
    taxonomy = legacy["sector_taxonomy"]
    sources = [(label, "canonical", label) for label in taxonomy["canonical"]]
    sources.extend(
        (label, "tradingview_map", target)
        for label, target in taxonomy.get("tradingview_map", {}).items()
    )
    sources.extend(
        (label, "alias", target) for label, target in taxonomy.get("aliases", {}).items()
    )
    rows = []
    for label, kind, canonical in sources:
        sector = by_canonical.get(canonical)
        rows.append(
            SectorCrosswalkRow(
                source_label=label,
                source_kind=kind,
                run_canonical_sector=canonical,
                standard_market_sector=sector.standard_market_sector if sector else None,
                etf_symbol=sector.etf_symbol if sector else None,
            )
        )
    return tuple(
        sorted(
            rows,
            key=lambda row: (
                row.source_kind,
                " ".join(row.source_label.casefold().split()),
                row.source_label,
            ),
        )
    )
