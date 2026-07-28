from pathlib import Path

import pytest

from app.services.ranking_profile_config import (
    RankingProfileConfigError,
    get_ranking_profile,
    load_ranking_profiles,
)


def test_load_ranking_profiles_loads_five_enabled_profiles() -> None:
    profiles = load_ranking_profiles()

    assert [profile.name for profile in profiles] == [
        "momentum_swing",
        "quality_momentum",
        "early_rocket",
        "clean_compounder_pullback",
        "defensive_quality",
    ]
    assert profiles[0].label == "Momentum Swing"
    assert profiles[0].technical_weight == 0.55
    assert profiles[0].fundamental_weight == 0.45
    assert profiles[0].missing_data_policy.rescale_available is True
    assert profiles[0].thresholds.candidate_min_score == 6.8


def test_get_ranking_profile_returns_named_profile() -> None:
    profile = get_ranking_profile("early_rocket")

    assert profile.label == "Early Rocket"
    assert profile.technical_components["trend_repair"] == 0.20


def test_load_ranking_profiles_ignores_disabled_profiles(tmp_path: Path) -> None:
    config = tmp_path / "ranking_profiles.yaml"
    config.write_text(
        """
profiles:
  enabled_profile:
    enabled: true
    label: "Enabled"
    description: "Enabled profile."
    weights:
      technical: 0.5
      fundamental: 0.5
    technical_components:
      trend_quality: 1.0
  disabled_profile:
    enabled: false
    label: "Disabled"
    description: "Disabled profile."
    weights:
      technical: 0.5
      fundamental: 0.5
    technical_components:
      trend_quality: 1.0
""",
        encoding="utf-8",
    )

    profiles = load_ranking_profiles(config)

    assert [profile.name for profile in profiles] == ["enabled_profile"]


def test_load_ranking_profiles_rejects_empty_enabled_set(tmp_path: Path) -> None:
    config = tmp_path / "ranking_profiles.yaml"
    config.write_text(
        """
profiles:
  disabled_profile:
    enabled: false
    label: "Disabled"
    description: "Disabled profile."
    weights:
      technical: 0.5
      fundamental: 0.5
    technical_components:
      trend_quality: 1.0
""",
        encoding="utf-8",
    )

    with pytest.raises(RankingProfileConfigError, match="No enabled ranking profiles"):
        load_ranking_profiles(config)


def test_load_ranking_profiles_rejects_bad_profile_weights(tmp_path: Path) -> None:
    config = tmp_path / "ranking_profiles.yaml"
    config.write_text(
        _profile_yaml(
            """
    weights:
      technical: 0.9
      fundamental: 0.9
"""
        ),
        encoding="utf-8",
    )

    with pytest.raises(RankingProfileConfigError, match="test_profile: weights"):
        load_ranking_profiles(config)


def test_load_ranking_profiles_rejects_bad_component_weights(tmp_path: Path) -> None:
    config = tmp_path / "ranking_profiles.yaml"
    config.write_text(
        _profile_yaml(
            """
    technical_components:
      trend_quality: 0.4
      setup_quality: 0.4
"""
        ),
        encoding="utf-8",
    )

    with pytest.raises(RankingProfileConfigError, match="test_profile: technical_components"):
        load_ranking_profiles(config)


def test_load_ranking_profiles_rejects_unknown_component(tmp_path: Path) -> None:
    config = tmp_path / "ranking_profiles.yaml"
    config.write_text(
        _profile_yaml(
            """
    technical_components:
      made_up_component: 1.0
"""
        ),
        encoding="utf-8",
    )

    with pytest.raises(RankingProfileConfigError, match="test_profile: unknown technical"):
        load_ranking_profiles(config)


def test_load_ranking_profiles_rejects_unordered_thresholds(tmp_path: Path) -> None:
    config = tmp_path / "ranking_profiles.yaml"
    config.write_text(
        _profile_yaml(
            """
    thresholds:
      strong_candidate_min_score: 6.0
      candidate_min_score: 7.0
      watch_min_score: 5.5
"""
        ),
        encoding="utf-8",
    )

    with pytest.raises(RankingProfileConfigError, match="test_profile: thresholds"):
        load_ranking_profiles(config)


def test_load_ranking_profiles_rejects_negative_penalty(tmp_path: Path) -> None:
    config = tmp_path / "ranking_profiles.yaml"
    config.write_text(
        _profile_yaml(
            """
    penalties:
      missing_data: -1.0
"""
        ),
        encoding="utf-8",
    )

    with pytest.raises(RankingProfileConfigError, match="test_profile: penalties.missing_data"):
        load_ranking_profiles(config)


def _profile_yaml(override: str) -> str:
    base = """
profiles:
  test_profile:
    enabled: true
    label: "Test Profile"
    description: "A test profile."
    weights:
      technical: 0.5
      fundamental: 0.5
    technical_components:
      trend_quality: 1.0
"""
    return f"{base}{override}"
