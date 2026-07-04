from pathlib import Path

import yaml


def test_column_aliases_cover_core_sample_fields() -> None:
    aliases = yaml.safe_load(Path("config/column_aliases.yaml").read_text(encoding="utf-8"))

    expected_fields = {
        "ticker",
        "company_name",
        "sector",
        "market_cap",
        "revenue_growth_ttm_yoy",
        "fcf_ttm",
        "net_debt_to_ebitda",
        "forward_pe",
        "dollar_volume_30d",
        "tradingview_atr_pct_14d",
    }

    assert expected_fields.issubset(aliases)


def test_pine_defaults_include_required_sections() -> None:
    defaults = yaml.safe_load(Path("config/pine_defaults.yaml").read_text(encoding="utf-8"))

    assert defaults["engine"]["pine_version"] == "3.2.0"
    assert defaults["trend"]["emaFastLen"] == 10
    assert defaults["risk"]["minNotionalVolume"] == 10000000
    assert defaults["market_rs"]["marketSymbol"] == "SPY"
    assert defaults["htf"]["htfTimeframe"] == "W"


def test_scoring_weights_are_normalized() -> None:
    weights = yaml.safe_load(Path("config/scoring_weights.yaml").read_text(encoding="utf-8"))

    fundamental_total = sum(weights["fundamental_components"].values())
    combined_total = sum(weights["combined_score"].values())

    assert fundamental_total == 1.0
    assert combined_total == 1.0


def test_fundamentals_v2_config_is_normalized_and_mapped() -> None:
    aliases = yaml.safe_load(Path("config/column_aliases.yaml").read_text(encoding="utf-8"))
    config = yaml.safe_load(Path("config/fundamentals_v2.yaml").read_text(encoding="utf-8"))

    assert config["model_version"] == "fundamentals_v2.0"
    assert sum(config["weights"].values()) == 1.0

    configured_fields = {
        field
        for component in config["components"].values()
        for field in component["fields"]
    }
    priority_fields = {
        field
        for fields in config["field_priorities"].values()
        for field in fields
    }

    assert configured_fields.issubset(aliases)
    assert priority_fields.issubset(aliases)
