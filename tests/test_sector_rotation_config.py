from pathlib import Path

import pytest

from app.services.sector_rotation_config import (
    SectorRotationConfigError,
    load_sector_rotation_config,
    sector_rotation_config_hash,
)


def test_load_sector_rotation_config_loads_defaults() -> None:
    config = load_sector_rotation_config()

    assert config["version"] == "1.0.0"
    assert config["defaults"]["default_ranking_profile"] == "momentum_swing"
    assert config["defaults"]["top_candidate_cutoffs"] == [10, 25, 50]
    assert config["etf_score"]["enabled"] is False
    assert config["sector_etf_proxies"]["Technology"] == "XLK"


def test_sector_rotation_config_hash_is_stable() -> None:
    config = load_sector_rotation_config()

    assert sector_rotation_config_hash(config) == sector_rotation_config_hash(dict(config))
    assert len(sector_rotation_config_hash(config)) == 64


def test_load_sector_rotation_config_rejects_bad_universe_weights(tmp_path: Path) -> None:
    config = _config_with_replacement(
        tmp_path,
        "    risk_control: 0.15",
        "    risk_control: 0.99",
    )

    with pytest.raises(SectorRotationConfigError, match="universe_score.weights"):
        load_sector_rotation_config(config)


def test_load_sector_rotation_config_rejects_alias_to_unknown_canonical(
    tmp_path: Path,
) -> None:
    config = _config_with_replacement(
        tmp_path,
        "    Basic Materials: Materials",
        "    Basic Materials: Not Canonical",
    )

    with pytest.raises(SectorRotationConfigError, match="sector_taxonomy.aliases"):
        load_sector_rotation_config(config)


def test_load_sector_rotation_config_rejects_high_threshold_below_normal(
    tmp_path: Path,
) -> None:
    config = _config_with_replacement(
        tmp_path,
        "  min_tickers_for_high_confidence: 10",
        "  min_tickers_for_high_confidence: 2",
    )

    with pytest.raises(SectorRotationConfigError, match="high_confidence"):
        load_sector_rotation_config(config)


def test_load_sector_rotation_config_rejects_unknown_permission(tmp_path: Path) -> None:
    config = _config_with_replacement(
        tmp_path,
        "      Leading: full_allowed",
        "      Leading: imaginary_permission",
        count=1,
    )

    with pytest.raises(SectorRotationConfigError, match="unknown permission"):
        load_sector_rotation_config(config)


def _config_with_replacement(
    tmp_path: Path,
    old: str,
    new: str,
    count: int = -1,
) -> Path:
    source = Path("config/sector_rotation.yaml").read_text(encoding="utf-8")
    target = tmp_path / "sector_rotation.yaml"
    target.write_text(source.replace(old, new, count), encoding="utf-8")
    return target
