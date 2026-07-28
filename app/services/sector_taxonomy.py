from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SectorNormalizationResult:
    raw_sector: str | None
    canonical_sector: str
    taxonomy: str
    status: str
    reason: str | None = None


def normalize_sector(raw_sector: str | None, config: dict[str, Any]) -> str:
    return normalize_sector_result(raw_sector, config).canonical_sector


def normalize_sector_result(
    raw_sector: str | None,
    config: dict[str, Any],
) -> SectorNormalizationResult:
    taxonomy = config.get("sector_taxonomy", {})
    unknown = _unknown_sector(config)
    raw_text = str(raw_sector or "").strip()
    if not raw_text:
        return SectorNormalizationResult(
            raw_sector=None,
            canonical_sector=unknown,
            taxonomy=_taxonomy_source(config),
            status="missing",
            reason="sector_missing",
        )

    canonical_by_key = {
        _norm_key(sector): str(sector).strip()
        for sector in taxonomy["canonical"]
    }
    tradingview_by_key = {
        _norm_key(raw): str(target).strip()
        for raw, target in taxonomy.get("tradingview_map", {}).items()
    }
    aliases_by_key = {
        _norm_key(alias): str(target).strip()
        for alias, target in taxonomy.get("aliases", {}).items()
    }

    key = _norm_key(raw_text)
    if key in canonical_by_key:
        return SectorNormalizationResult(
            raw_sector=raw_text,
            canonical_sector=canonical_by_key[key],
            taxonomy=_taxonomy_source(config),
            status="canonical",
        )
    if key in tradingview_by_key:
        return SectorNormalizationResult(
            raw_sector=raw_text,
            canonical_sector=tradingview_by_key[key],
            taxonomy=_taxonomy_source(config),
            status="mapped",
        )
    if key in aliases_by_key:
        return SectorNormalizationResult(
            raw_sector=raw_text,
            canonical_sector=aliases_by_key[key],
            taxonomy=_taxonomy_source(config),
            status="mapped",
        )
    return SectorNormalizationResult(
        raw_sector=raw_text,
        canonical_sector=unknown,
        taxonomy=_taxonomy_source(config),
        status="unmapped",
        reason="sector_unmapped",
    )


def normalize_sector_value(value: str) -> str:
    return " ".join(str(value).strip().split())


def sector_slug(sector: str) -> str:
    text = str(sector or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return slug or "unknown"


def sector_from_slug(slug: str, known_sectors: Iterable[str]) -> str | None:
    normalized_slug = sector_slug(slug)
    for sector in known_sectors:
        if sector_slug(str(sector)) == normalized_slug:
            return str(sector)
    return None


def _unknown_sector(config: dict[str, Any]) -> str:
    taxonomy_unknown = config.get("sector_taxonomy", {}).get("unknown_sector_label")
    return str(
        taxonomy_unknown or config.get("defaults", {}).get("unknown_sector_label") or "Unknown"
    ).strip()


def _taxonomy_source(config: dict[str, Any]) -> str:
    return str(config.get("sector_taxonomy", {}).get("source") or "unknown").strip()


def _norm_key(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())
