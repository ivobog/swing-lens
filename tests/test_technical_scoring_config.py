from pathlib import Path

import pytest
import yaml

from app.services.technical_scoring_config import load_technical_scoring_v4_config


def test_load_technical_scoring_v4_config_includes_required_sections() -> None:
    config = load_technical_scoring_v4_config()

    assert config["engine"]["version"] == "4.0.0"
    assert config["engine"]["base_engine_version"] == "3.2.0"
    assert config["data_confidence"]["require_benchmark_data"] is True
    assert config["adaptive_percentiles"]["long_lookback"] == 252
    assert config["volatility_contraction"]["vcp_min_score"] == 7.0
    assert config["donchian_darvas"]["donchian_short_len"] == 20
    assert config["stage_analysis"]["allow_prime_only_in_stage_2"] is True
    assert config["relative_leadership"]["benchmark_symbols"] == ["SPY", "QQQ"]
    assert config["market_regime_v4"]["use_spy"] is True
    assert config["climax_risk"]["climax_risk_threshold"] == 7.0
    assert "Climax reversal risk" in config["classification_v4"]["danger_priority"]


def test_technical_scoring_v4_file_regime_weights_are_normalized() -> None:
    config = yaml.safe_load(
        Path("config/technical_scoring_v4.yaml").read_text(encoding="utf-8")
    )

    for weights in config["regime_weights"].values():
        assert round(sum(weights.values()), 6) == 1.0


def test_load_technical_scoring_v4_config_merges_partial_override(tmp_path: Path) -> None:
    custom_config = tmp_path / "technical_scoring_v4.yaml"
    custom_config.write_text(
        """
engine:
  version: "4.1.0"
adaptive_percentiles:
  long_lookback: 300
""",
        encoding="utf-8",
    )

    config = load_technical_scoring_v4_config(custom_config)

    assert config["engine"]["version"] == "4.1.0"
    assert config["engine"]["base_engine_version"] == "3.2.0"
    assert config["adaptive_percentiles"]["long_lookback"] == 300
    assert config["adaptive_percentiles"]["medium_lookback"] == 126
    assert config["market_regime_v4"]["allow_unknown_market_low_confidence"] is True


def test_load_technical_scoring_v4_config_rejects_bad_regime_weights(
    tmp_path: Path,
) -> None:
    custom_config = tmp_path / "technical_scoring_v4.yaml"
    custom_config.write_text(
        """
regime_weights:
  bull_trend:
    trend: 0.99
    momentum: 0.99
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="regime_weights.bull_trend"):
        load_technical_scoring_v4_config(custom_config)
