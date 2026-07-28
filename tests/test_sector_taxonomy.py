from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_taxonomy import (
    normalize_sector,
    normalize_sector_result,
    sector_from_slug,
    sector_slug,
)


def test_normalize_sector_maps_alias_case_insensitively() -> None:
    config = load_sector_rotation_config()

    assert normalize_sector(" health care ", config) == "Healthcare"
    assert normalize_sector("INFORMATION TECHNOLOGY", config) == "Technology"


def test_normalize_sector_uses_unknown_for_blank_or_unmapped_sector() -> None:
    config = load_sector_rotation_config()

    assert normalize_sector(None, config) == "Unknown"
    assert normalize_sector("   ", config) == "Unknown"
    assert normalize_sector("Made Up Sector", config) == "Unknown"

    missing = normalize_sector_result("   ", config)
    unmapped = normalize_sector_result("Made Up Sector", config)

    assert missing.status == "missing"
    assert missing.canonical_sector == "Unknown"
    assert unmapped.status == "unmapped"
    assert unmapped.raw_sector == "Made Up Sector"
    assert unmapped.canonical_sector == "Unknown"


def test_normalize_sector_preserves_canonical_label() -> None:
    config = load_sector_rotation_config()
    result = normalize_sector_result("financial services", config)

    assert normalize_sector("consumer cyclical", config) == "Consumer Cyclical"
    assert result.canonical_sector == "Financial Services"
    assert result.status == "canonical"


def test_normalize_sector_maps_tradingview_taxonomy() -> None:
    config = load_sector_rotation_config()

    expected = {
        "Commercial services": "Industrials",
        "Communications": "Communication Services",
        "Consumer durables": "Consumer Cyclical",
        "Consumer non-durables": "Consumer Defensive",
        "Consumer services": "Consumer Cyclical",
        "Distribution services": "Industrials",
        "Electronic technology": "Technology",
        "Energy minerals": "Energy",
        "Finance": "Financial Services",
        "Government": "Government / Other",
        "Health services": "Healthcare",
        "Health technology": "Healthcare",
        "Industrial services": "Industrials",
        "Miscellaneous": "Miscellaneous / Other",
        "Non-energy minerals": "Basic Materials",
        "Process industries": "Basic Materials",
        "Producer manufacturing": "Industrials",
        "Retail trade": "Consumer Cyclical",
        "Technology services": "Technology",
        "Transportation": "Industrials",
        "Utilities": "Utilities",
    }

    for raw_sector, canonical in expected.items():
        result = normalize_sector_result(raw_sector.lower(), config)
        assert result.raw_sector == raw_sector.lower()
        assert result.canonical_sector == canonical
        assert result.taxonomy == "tradingview"
        assert result.status in {"mapped", "canonical"}


def test_sector_slug_handles_spaces_and_punctuation() -> None:
    assert sector_slug("Communication Services") == "communication-services"
    assert sector_slug(" Real Estate / REITs ") == "real-estate-reits"


def test_sector_from_slug_returns_matching_sector_or_none() -> None:
    sectors = ["Communication Services", "Real Estate", "Unknown"]

    assert sector_from_slug("communication-services", sectors) == "Communication Services"
    assert sector_from_slug("real-estate", sectors) == "Real Estate"
    assert sector_from_slug("not-a-sector", sectors) is None
