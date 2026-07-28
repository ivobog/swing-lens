from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_taxonomy import normalize_sector, sector_from_slug, sector_slug


def test_normalize_sector_maps_alias_case_insensitively() -> None:
    config = load_sector_rotation_config()

    assert normalize_sector(" health care ", config) == "Healthcare"
    assert normalize_sector("INFORMATION TECHNOLOGY", config) == "Technology"


def test_normalize_sector_uses_unknown_for_blank_or_unmapped_sector() -> None:
    config = load_sector_rotation_config()

    assert normalize_sector(None, config) == "Unknown"
    assert normalize_sector("   ", config) == "Unknown"
    assert normalize_sector("Made Up Sector", config) == "Unknown"


def test_normalize_sector_preserves_canonical_label() -> None:
    config = load_sector_rotation_config()

    assert normalize_sector("consumer discretionary", config) == "Consumer Discretionary"


def test_sector_slug_handles_spaces_and_punctuation() -> None:
    assert sector_slug("Communication Services") == "communication-services"
    assert sector_slug(" Real Estate / REITs ") == "real-estate-reits"


def test_sector_from_slug_returns_matching_sector_or_none() -> None:
    sectors = ["Communication Services", "Real Estate", "Unknown"]

    assert sector_from_slug("communication-services", sectors) == "Communication Services"
    assert sector_from_slug("real-estate", sectors) == "Real Estate"
    assert sector_from_slug("not-a-sector", sectors) is None
