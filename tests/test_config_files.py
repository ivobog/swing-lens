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
        "upcoming_earnings_date",
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


def test_scoring_weights_include_earnings_risk_gate_defaults() -> None:
    weights = yaml.safe_load(Path("config/scoring_weights.yaml").read_text(encoding="utf-8"))
    gate = weights["earnings_risk_gate"]

    assert gate["enabled"] is True
    assert gate["block_if_within_days"] == 2
    assert gate["high_risk_if_within_days"] == 5
    assert gate["medium_risk_if_within_days"] == 10
    assert gate["missing_date_policy"] == "warn"
    assert gate["apply_to_combined_score"] is True
    assert gate["block_new_entries"] is True
    assert gate["penalties"] == {
        "blocked": 3.0,
        "high": 2.0,
        "medium": 1.0,
        "unknown": 0.3,
        "clear": 0.0,
    }


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


def test_technical_scoring_v4_config_has_required_sections() -> None:
    config = yaml.safe_load(Path("config/technical_scoring_v4.yaml").read_text(encoding="utf-8"))

    required_sections = {
        "engine",
        "data_confidence",
        "adaptive_percentiles",
        "volatility_contraction",
        "donchian_darvas",
        "stage_analysis",
        "relative_leadership",
        "market_regime_v4",
        "climax_risk",
        "regime_weights",
        "classification_v4",
    }

    assert required_sections.issubset(config)
    assert config["engine"]["version"] == "4.0.0"
    assert config["relative_leadership"]["benchmark_symbols"] == ["SPY", "QQQ"]
    assert "Late-stage extension" in config["classification_v4"]["danger_priority"]


def test_ranking_profiles_config_defines_enabled_starter_profiles() -> None:
    config = yaml.safe_load(Path("config/ranking_profiles.yaml").read_text(encoding="utf-8"))
    profiles = config["profiles"]

    assert list(profiles) == [
        "momentum_swing",
        "quality_momentum",
        "early_rocket",
        "clean_compounder_pullback",
        "defensive_quality",
    ]
    for profile in profiles.values():
        assert profile["enabled"] is True
        assert round(sum(profile["weights"].values()), 6) == 1.0
        assert round(sum(profile["technical_components"].values()), 6) == 1.0


def test_market_regime_command_center_config_defines_policy_matrix() -> None:
    config = yaml.safe_load(
        Path("config/market_regime_command_center.yaml").read_text(encoding="utf-8")
    )
    policies = config["policies"]

    expected_regimes = {
        "Bull trend",
        "Risk-on breakout",
        "Bull pullback",
        "Choppy",
        "Bear rally",
        "Distribution",
        "Correction",
        "Crash risk",
        "Unknown",
    }

    assert config["engine"]["version"] == "mrcc-1.0.0"
    assert config["symbols"]["primary_market"] == "SPY"
    assert config["symbols"]["risk_proxy"] == "QQQ"
    assert set(config["risk_state_mapping"]) == expected_regimes
    assert set(policies) == expected_regimes
    for regime, policy in policies.items():
        assert 0 <= float(policy["position_size_multiplier"]) <= 1
        assert policy["summary"]
        assert config["risk_state_mapping"][regime] in {"Green", "Yellow", "Orange", "Red", "Gray"}


def test_sector_rotation_config_defines_v1_universe_defaults() -> None:
    config = yaml.safe_load(Path("config/sector_rotation.yaml").read_text(encoding="utf-8"))

    assert config["version"] == "1.0.0"
    assert config["defaults"]["default_ranking_profile"] == "momentum_swing"
    assert "Unknown" in config["sector_taxonomy"]["canonical"]
    assert config["sector_taxonomy"]["aliases"]["Health Care"] == "Healthcare"
    assert round(sum(config["universe_score"]["weights"].values()), 6) == 1.0
    assert config["etf_score"]["enabled"] is False
    assert round(sum(config["combined_score"]["weights"].values()), 6) == 1.0
    assert config["combined_score"]["missing_etf_policy"] == "use_universe_only"
    assert set(config["permissions"]["market_buckets"]) == {
        "supportive",
        "choppy",
        "risk_off",
        "unknown",
    }
