from copy import deepcopy

import pytest
import yaml

from app.services.market_sector_intelligence_config import (
    CONFIG_PATH,
    MarketSectorIntelligenceConfigError,
    load_market_sector_intelligence_config,
    validate_market_sector_intelligence_config,
)
from app.services.sector_rotation_config import (
    load_sector_rotation_config,
    sector_rotation_config_hash,
)
from app.services.sector_taxonomy import normalize_sector_result


def inputs():
    return yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")), load_sector_rotation_config()


def test_default_universe_preserves_legacy_configuration():
    feature, legacy = inputs()
    original = deepcopy(dict(legacy))
    legacy_hash = sector_rotation_config_hash(legacy)
    config = validate_market_sector_intelligence_config(feature, legacy)

    assert config.enabled is True
    assert config.benchmark_symbol == "SPY"
    assert config.timeframe == "1 day"
    assert config.version == "1.0.0"
    assert len(config.sectors) == 11
    assert {row.etf_symbol for row in config.sectors} == {
        "XLK",
        "XLC",
        "XLY",
        "XLP",
        "XLF",
        "XLV",
        "XLI",
        "XLE",
        "XLB",
        "XLRE",
        "XLU",
    }
    assert (
        sum(row.run_canonical_sector != row.standard_market_sector for row in config.sectors) == 5
    )
    assert config.legacy_etf_blending_enabled is False
    assert legacy == original
    assert sector_rotation_config_hash(legacy) == legacy_hash
    assert load_market_sector_intelligence_config() == config


@pytest.mark.parametrize(
    ("label", "canonical", "market", "ticker", "kind"),
    [
        ("Finance", "Financial Services", "Financials", "XLF", "tradingview_map"),
        ("Financials", "Financial Services", "Financials", "XLF", "alias"),
        (
            "Consumer non-durables",
            "Consumer Defensive",
            "Consumer Staples",
            "XLP",
            "tradingview_map",
        ),
        ("Unknown", "Unknown", None, None, "canonical"),
        ("ETF / Fund", "ETF / Fund", None, None, "canonical"),
        ("Government", "Government / Other", None, None, "tradingview_map"),
        ("Miscellaneous / Other", "Miscellaneous / Other", None, None, "canonical"),
    ],
)
def test_crosswalk_retains_source_vocabulary(label, canonical, market, ticker, kind):
    feature, legacy = inputs()
    config = validate_market_sector_intelligence_config(feature, legacy)
    row = next(
        row for row in config.crosswalk if row.source_label == label and row.source_kind == kind
    )
    assert row.run_canonical_sector == canonical
    assert row.standard_market_sector == market
    assert row.etf_symbol == ticker
    assert normalize_sector_result(label, legacy).canonical_sector == canonical
    assert normalize_sector_result("not a known sector", legacy).status == "unmapped"


def test_digest_is_deterministic_and_only_tracks_relevant_sources():
    feature, legacy = inputs()
    original = validate_market_sector_intelligence_config(feature, legacy)
    reordered = dict(reversed(list(feature.items())))
    assert validate_market_sector_intelligence_config(reordered, legacy) == original
    legacy["universe_score"]["weights"]["risk_control"] = 0.99
    assert validate_market_sector_intelligence_config(feature, legacy) == original
    legacy["sector_taxonomy"]["aliases"]["New finance label"] = "Financial Services"
    assert validate_market_sector_intelligence_config(feature, legacy).configuration_digest != (
        original.configuration_digest
    )
    feature["enabled"] = False
    assert validate_market_sector_intelligence_config(feature, legacy).configuration_digest != (
        original.configuration_digest
    )


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("enabled", "true"),
        ("benchmark_symbol", ""),
        ("benchmark_symbol", "XLK"),
        ("benchmark_symbol", "QQQ"),
        ("timeframe", "1 week"),
        ("version", ""),
        ("standard_market_labels", {"Technology": "Other"}),
    ],
)
def test_invalid_feature_configuration_is_rejected(key, value):
    feature, legacy = inputs()
    feature[key] = value
    with pytest.raises(MarketSectorIntelligenceConfigError):
        validate_market_sector_intelligence_config(feature, legacy)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unexpected", "non_market", "bad_alias"])
def test_invalid_source_mapping_is_rejected(fault):
    feature, legacy = inputs()
    if fault == "missing":
        del legacy["sector_etf_proxies"]["Technology"]
    elif fault == "duplicate":
        legacy["sector_etf_proxies"]["Technology"] = "XLF"
    elif fault == "unexpected":
        legacy["sector_etf_proxies"]["Technology"] = "QQQ"
    elif fault == "non_market":
        legacy["sector_etf_proxies"]["Unknown"] = legacy["sector_etf_proxies"].pop("Technology")
    else:
        legacy["sector_taxonomy"]["aliases"]["Bad alias"] = "No such canonical"
    with pytest.raises(MarketSectorIntelligenceConfigError):
        validate_market_sector_intelligence_config(feature, legacy)


def test_missing_or_malformed_config_has_actionable_error(tmp_path):
    path = tmp_path / "missing.yaml"
    with pytest.raises(
        MarketSectorIntelligenceConfigError, match="market_sector_intelligence.yaml"
    ):
        load_market_sector_intelligence_config(path)
    path.write_text("enabled: [", encoding="utf-8")
    with pytest.raises(MarketSectorIntelligenceConfigError):
        load_market_sector_intelligence_config(path)
