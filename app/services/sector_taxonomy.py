from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any


def normalize_sector(raw_sector: str | None, config: dict[str, Any]) -> str:
    unknown = _unknown_sector(config)
    text = str(raw_sector or "").strip()
    if not text:
        return unknown

    canonical_by_key = {
        _norm_key(sector): str(sector).strip()
        for sector in config["sector_taxonomy"]["canonical"]
    }
    aliases_by_key = {
        _norm_key(alias): str(target).strip()
        for alias, target in config["sector_taxonomy"].get("aliases", {}).items()
    }

    key = _norm_key(text)
    if key in aliases_by_key:
        return aliases_by_key[key]
    if key in canonical_by_key:
        return canonical_by_key[key]
    return unknown


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
    return str(config.get("defaults", {}).get("unknown_sector_label") or "Unknown").strip()


def _norm_key(value: str) -> str:
    return " ".join(str(value).strip().casefold().split())
