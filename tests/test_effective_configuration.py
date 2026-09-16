from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal, localcontext
from enum import StrEnum
from pathlib import Path

import pytest
import yaml

from app.services.calculation_identity import (
    CalculationIdentity,
    ConfigurationCoverage,
    IdentityDimension,
    IdentityState,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.effective_configuration import (
    ConfigurationClassification as Classification,
)
from app.services.effective_configuration import (
    ConfigurationCompatibilityStatus as Status,
)
from app.services.effective_configuration import (
    ConfigurationDrift,
    ConfigurationEntry,
    ConfigurationFamily,
    ConfigurationResolution,
    ConfigurationSource,
    EffectiveConfigurationSnapshot,
    bind_configuration,
    compare_configuration,
)
from app.services.effective_configuration import (
    ConfigurationSourceKind as SourceKind,
)
from app.services.effective_configuration import (
    ConfigurationValueType as ValueType,
)
from app.services.effective_configuration_families import (
    CONFIGURATION_FAMILIES,
    resolve_ranking_profile,
    resolve_regime_configuration,
    snapshot_ranking_profile,
    snapshot_readiness_policy,
    snapshot_regime_configuration,
)
from app.services.market_regime import classify_market_regime
from app.services.market_regime_policy import MarketRegimePolicyService
from app.services.ranking_profile_config import get_ranking_profile
from app.services.ranking_profile_engine import calculate_profile_score, rank_single_row
from app.services.technical_consumer_eligibility import TECHNICAL_TO_RANKING


def snapshot(*entries, namespace="test", schema="v1", resolution=None):
    family = ConfigurationFamily(
        namespace,
        schema,
        resolution or ConfigurationResolution("test.resolver", "v1"),
        tuple((entry.key, entry.classification) for entry in entries),
    )
    return EffectiveConfigurationSnapshot(family, tuple(entries))


def behavioral(key="weight", value=0.5, **kwargs):
    return ConfigurationEntry(key, value, Classification.BEHAVIORAL, **kwargs)


def compare(left, right):
    return compare_configuration(
        IdentityDimension.known(left.identity), IdentityDimension.known(right.identity)
    ).status


def test_stable_key_mapping_and_entry_order():
    first = snapshot(behavioral("a", {"x": 1, "y": 2}), behavioral("b", True))
    second = snapshot(behavioral("b", True), behavioral("a", {"y": 2, "x": 1}))
    assert first.semantic_hash == second.semantic_hash
    assert first.resolution_hash == second.resolution_hash
    assert Canonical.dumps(first.as_dict()) == Canonical.dumps(second.as_dict())


def test_same_values_different_source_and_resolver_are_semantically_exact():
    default = snapshot(behavioral(source=ConfigurationSource(SourceKind.CODE_DEFAULT, "defaults")))
    override = snapshot(
        behavioral(source=ConfigurationSource(SourceKind.PROFILE, "profiles", "v2")),
        resolution=ConfigurationResolution("other.resolver", "v2", (SourceKind.PROFILE,)),
    )
    assert compare(default, override) is Status.EXACT
    assert default.semantic_hash == override.semantic_hash
    assert default.resolution_hash != override.resolution_hash
    # Phase-1 compares the same shared effective-value schema; provenance is separate.
    base = CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME")
    assert (
        bind_configuration(base, default).fingerprint()
        == bind_configuration(base, override).fingerprint()
    )


@pytest.mark.parametrize(
    "classification",
    [
        Classification.OPERATIONAL,
        Classification.OBSERVABILITY,
        Classification.ENVIRONMENTAL_DEPENDENCY,
    ],
)
def test_nonbehavioral_change_does_not_change_semantic_identity(classification):
    first = snapshot(behavioral(), ConfigurationEntry("runtime", 1, classification))
    second = snapshot(behavioral(), ConfigurationEntry("runtime", 2, classification))
    assert compare(first, second) is Status.EXACT
    assert first.resolution_hash != second.resolution_hash


@pytest.mark.parametrize(
    "key",
    [
        "api_key",
        "database_password",
        "broker_credentials",
        "private_key",
        "session_secret",
        "ib_flex_token",
        "database_url",
        "apiKey",
    ],
)
def test_secret_never_retained_serialized_or_hashed(key):
    # Deliberately non-serializable secret; exclusion must happen before canonicalization.
    class Secret:
        def __repr__(self):
            raise AssertionError("secret repr must never be evaluated")

    first = snapshot(
        behavioral(),
        ConfigurationEntry(
            key,
            Secret(),
            Classification.SECURITY_SECRET,
            source=ConfigurationSource(SourceKind.ENVIRONMENT, "safe-name"),
            overridden=True,
        ),
    )
    second = snapshot(
        behavioral(),
        ConfigurationEntry(
            key,
            "synthetic-sensitive-value",
            Classification.SECURITY_SECRET,
            source=ConfigurationSource(SourceKind.DOTENV, "another-safe-name"),
            defaulted=True,
        ),
    )
    for payload in (first.as_dict(), first.semantic_payload(), first.resolution_payload()):
        assert key not in Canonical.dumps(payload)
        assert "synthetic-sensitive-value" not in Canonical.dumps(payload)
        assert "safe-name" not in Canonical.dumps(payload)
    assert "synthetic-sensitive-value" not in repr(second)
    assert first.semantic_hash == second.semantic_hash
    assert first.resolution_hash == second.resolution_hash
    assert compare(first, second) is Status.EXACT


@pytest.mark.parametrize(
    "value", [{"api_key": "synthetic"}, {"nested": [{"password": "synthetic"}]}]
)
def test_nested_secret_bearing_configuration_rejected(value):
    with pytest.raises(ValueError, match="SECURITY_SECRET"):
        behavioral(value=value)


def test_secret_misclassification_and_unsafe_metadata_rejected():
    with pytest.raises(ValueError, match="SECURITY_SECRET"):
        behavioral("api_key", "synthetic")
    with pytest.raises(ValueError, match="non-secret identifier"):
        ConfigurationSource(SourceKind.DATABASE, "postgresql://user:synthetic@host/db")


@pytest.mark.parametrize("value", [0.5, Decimal("0.50"), Decimal("0.500000000")])
def test_equivalent_real_representations(value):
    assert snapshot(behavioral(value=value, value_type=ValueType.REAL)).semantic_hash == (
        snapshot(behavioral(value=0.50, value_type=ValueType.REAL)).semantic_hash
    )


def test_integer_real_string_boolean_and_null_remain_distinct():
    identities = {
        snapshot(behavioral(value=value)).semantic_hash for value in (1, 1.0, "1", True, None)
    }
    assert len(identities) == 5
    assert snapshot(behavioral(value=1, value_type=ValueType.REAL)).semantic_hash == (
        snapshot(behavioral(value=1.0, value_type=ValueType.REAL)).semantic_hash
    )
    assert snapshot(behavioral(value=0.5000000000000001)).semantic_hash != (
        snapshot(behavioral(value=0.5)).semantic_hash
    )


def test_numeric_precision_independent_of_ambient_decimal_context():
    number = Decimal("0.123456789012345678901234567890123456789")
    expected = snapshot(behavioral(value=number)).semantic_hash
    with localcontext() as context:
        context.prec = 3
        assert snapshot(behavioral(value=number)).semantic_hash == expected


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), Decimal("NaN"), Decimal("Infinity"), object(), {1: "value"}],
)
def test_invalid_values_fail_closed_without_repr(value):
    with pytest.raises((TypeError, ValueError)):
        behavioral(value=value)


def test_enum_temporal_and_collection_semantics():
    class Provider(StrEnum):
        PRIMARY = "PRIMARY"

    assert snapshot(behavioral(value=Provider.PRIMARY)).semantic_hash == (
        snapshot(behavioral(value="PRIMARY")).semantic_hash
    )
    utc = datetime(2026, 9, 16, 12, tzinfo=UTC)
    other = utc.astimezone(timezone(timedelta(hours=2)))
    assert (
        snapshot(behavioral(value=utc)).semantic_hash
        == snapshot(behavioral(value=other)).semantic_hash
    )
    assert snapshot(behavioral(value=date(2026, 9, 16))).semantic_hash != (
        snapshot(behavioral(value="2026-09-16")).semantic_hash
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        behavioral(value=datetime(2026, 9, 16))
    assert snapshot(behavioral(value={"a", "b"})).semantic_hash == (
        snapshot(behavioral(value=frozenset(["b", "a"]))).semantic_hash
    )
    assert snapshot(behavioral(value=["a", "b"])).semantic_hash != (
        snapshot(behavioral(value=["b", "a"])).semantic_hash
    )
    # Phase-2's reason_codes heuristic must not reorder ordered configuration lists.
    assert snapshot(behavioral(value={"reason_codes": ["a", "b"]})).semantic_hash != (
        snapshot(behavioral(value={"reason_codes": ["b", "a"]})).semantic_hash
    )


def test_freeze_nested_values_and_returned_payloads():
    value = {"thresholds": [0.5]}
    frozen = snapshot(behavioral(value=value))
    before = frozen.as_dict()
    value["thresholds"].append(0.6)
    exported = frozen.as_dict()
    exported["semantic"]["values"].clear()
    assert frozen.as_dict() == before
    with pytest.raises(FrozenInstanceError):
        frozen.entries[0].key = "changed"


def test_evaluation_context_freezes_utc_and_fractional_standalone_timezone_fails_closed():
    frozen = replace(
        snapshot(behavioral()),
        evaluated_at=datetime(
            2026,
            9,
            16,
            14,
            tzinfo=timezone(timedelta(hours=2)),
        ),
    )
    assert frozen.evaluated_at.tzinfo is UTC
    assert frozen.as_dict()["evaluated_at"] == "2026-09-16T12:00:00.000000Z"
    with pytest.raises(ValueError, match="whole seconds"):
        behavioral(value=timezone(timedelta(microseconds=1)))


def test_unknown_legacy_partial_and_drift():
    first, second = snapshot(behavioral()), snapshot(behavioral(value=0.6))
    unknown = IdentityDimension.unknown()
    legacy = IdentityDimension.legacy_unknown()
    known = IdentityDimension.known(first.identity)
    assert compare(first, first) is Status.EXACT
    assert compare(first, second) is Status.INCOMPATIBLE
    assert compare_configuration(unknown, known).status is Status.UNKNOWN
    assert compare_configuration(unknown, unknown).status is Status.UNKNOWN
    assert compare_configuration(legacy, known).status is Status.LEGACY_UNKNOWN
    assert compare_configuration(legacy, legacy).status is Status.LEGACY_UNKNOWN
    assert ConfigurationDrift(known, IdentityDimension.known(second.identity)).detected is True
    assert ConfigurationDrift(known, known).detected is False
    assert ConfigurationDrift(legacy, known).detected is None
    partial = snapshot(ConfigurationEntry("unknown", 1, Classification.UNKNOWN_CLASSIFICATION))
    assert partial.identity.coverage is ConfigurationCoverage.PARTIAL_DEBUG
    assert compare(partial, partial) is Status.UNKNOWN
    with pytest.raises(ValueError, match="unknown classifications"):
        bind_configuration(CalculationIdentity.legacy_unknown(), partial)


def test_namespace_schema_missing_duplicate_and_unknown_source():
    first = snapshot(behavioral())
    assert compare(first, snapshot(behavioral(), namespace="other")) is Status.INCOMPATIBLE
    assert compare(first, snapshot(behavioral(), schema="v2")) is Status.INCOMPATIBLE
    with pytest.raises(ValueError, match="exactly"):
        EffectiveConfigurationSnapshot(first.family, ())
    with pytest.raises(ValueError, match="duplicate"):
        snapshot(behavioral(), behavioral())
    assert first.entries[0].source.kind is SourceKind.UNKNOWN
    with pytest.raises(ValueError, match="invent"):
        ConfigurationSource(SourceKind.UNKNOWN, "fabricated")


def test_resolved_settings_actual_environment_dotenv_init_precedence_and_drift(
    monkeypatch, tmp_path
):
    from app.settings import Settings

    monkeypatch.delenv("IB_USE_RTH", raising=False)
    path = tmp_path / "synthetic.env"
    path.write_text("IB_USE_RTH=false\n", encoding="utf-8")
    default = Settings(_env_file=None)
    dotenv = Settings(_env_file=path)
    assert default.ib_use_rth is True and dotenv.ib_use_rth is False
    c1 = snapshot(
        behavioral(
            "ib_use_rth",
            dotenv.ib_use_rth,
            source=ConfigurationSource(SourceKind.DOTENV, "synthetic.env"),
        )
    )
    monkeypatch.setenv("IB_USE_RTH", "true")
    environment = Settings(_env_file=path)
    explicit = Settings(_env_file=path, ib_use_rth=False)
    assert environment.ib_use_rth is True and explicit.ib_use_rth is False
    c2 = snapshot(
        behavioral(
            "ib_use_rth",
            environment.ib_use_rth,
            source=ConfigurationSource(SourceKind.ENVIRONMENT, "IB_USE_RTH"),
        )
    )
    assert compare(c1, c2) is Status.INCOMPATIBLE
    assert c1.entries[0].canonical_value_json == '{"type":"boolean","value":false}'
    assert snapshot(behavioral("ib_use_rth", explicit.ib_use_rth)).semantic_hash == c1.semantic_hash


def test_calculation_identity_round_trip_preserves_other_dimensions():
    base = CalculationIdentity.legacy_unknown(run_id=1, ticker="ACME")
    first = bind_configuration(base, snapshot(behavioral()))
    second = bind_configuration(base, snapshot(behavioral(value=0.6)))
    assert base.configuration.effective_configuration.state is IdentityState.LEGACY_UNKNOWN
    assert replace(first, configuration=base.configuration) == base
    assert first.fingerprint() != second.fingerprint()
    assert CalculationIdentity.from_canonical_payload(first.canonical_payload()) == first


def test_untyped_binding_and_evidence_snapshot_rejected_before_serialization():
    from types import SimpleNamespace

    from app.services.core_calculation_evidence import CoreEvidenceKind, persist_core_evidence

    base = CalculationIdentity.legacy_unknown()
    with pytest.raises(TypeError, match="typed"):
        bind_configuration(base, object())
    with pytest.raises(TypeError, match="typed snapshot"):
        persist_core_evidence(
            None,
            kind=CoreEvidenceKind.TECHNICAL,
            current_row=SimpleNamespace(),
            calculation_identity=base,
            effective_configuration=object(),
        )


def test_ranking_full_profile_same_name_different_values_and_behavior_preserved():
    profile, frozen = resolve_ranking_profile("momentum_swing")
    assert profile == get_ranking_profile("momentum_swing")
    assert {entry.key for entry in frozen.entries} == set(_profile_keys(profile))
    before = calculate_profile_score(
        technical_profile_score=8, fundamental_score=7, profile=profile
    )
    assert (
        calculate_profile_score(technical_profile_score=8, fundamental_score=7, profile=profile)
        == before
    )
    changed = replace(
        profile,
        technical_weight=profile.technical_weight + 0.05,
        fundamental_weight=profile.fundamental_weight - 0.05,
    )
    assert profile.name == changed.name
    assert compare(frozen, snapshot_ranking_profile(changed)) is Status.INCOMPATIBLE
    relabeled = replace(profile, name="another_profile", label="Another", description="Another")
    assert compare(frozen, snapshot_ranking_profile(relabeled)) is Status.EXACT
    # Full decision output, including debug, stays identical across capture.
    from test_ranking_profile_engine import TODAY, _config, _fundamental, _row, _technical

    args = dict(
        profile=profile,
        row=_row("ACME"),
        fundamental=_fundamental("ACME", 7.0),
        technical=_technical("ACME", trend=7, momentum=7, setup=7, risk=2, rs=7),
        config=_config(),
        today=TODAY,
    )
    decision = rank_single_row(**args)
    snapshot_ranking_profile(profile)
    assert rank_single_row(**args) == decision


def _profile_keys(profile):
    from app.services.effective_configuration_families import _dataclass_values

    return _dataclass_values(profile)


def test_ranking_actual_parser_default_override_precedence(tmp_path):
    raw = yaml.safe_load(Path("config/ranking_profiles.yaml").read_text(encoding="utf-8"))
    raw["profiles"]["momentum_swing"]["thresholds"] = {}
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    profile, default = resolve_ranking_profile("momentum_swing", path)
    assert profile == get_ranking_profile("momentum_swing", path)
    assert profile.thresholds.candidate_min_score == 6.8
    entry = next(e for e in default.entries if e.key == "thresholds.candidate_min_score")
    assert entry.defaulted and entry.source.kind is SourceKind.CODE_DEFAULT
    raw["profiles"]["momentum_swing"]["thresholds"]["candidate_min_score"] = 6.8
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    _, override = resolve_ranking_profile("momentum_swing", path)
    assert compare(default, override) is Status.EXACT
    assert default.resolution_hash != override.resolution_hash


def test_ranking_invalid_optional_mapping_uses_native_default_source(tmp_path):
    raw = yaml.safe_load(Path("config/ranking_profiles.yaml").read_text(encoding="utf-8"))
    raw["profiles"]["momentum_swing"]["gates"] = "invalid-optional-mapping"
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    profile, frozen = resolve_ranking_profile("momentum_swing", path)
    assert profile == get_ranking_profile("momentum_swing", path)
    entry = next(entry for entry in frozen.entries if entry.key == "gates")
    assert entry.source.kind is SourceKind.CODE_DEFAULT and entry.defaulted


def test_ranking_safe_source_identifier_affects_only_provenance():
    _, first = resolve_ranking_profile("momentum_swing", source_identifier="operator.profile-v1")
    _, second = resolve_ranking_profile("momentum_swing", source_identifier="operator.profile-copy")
    assert compare(first, second) is Status.EXACT
    assert first.resolution_hash != second.resolution_hash


def test_regime_complete_config_includes_feature_dependencies_and_native_fallbacks():
    config, frozen = resolve_regime_configuration()
    from app.services.market_regime_policy import load_market_regime_command_center_config

    assert config == load_market_regime_command_center_config()
    features = dict(
        close=120,
        sma50=110,
        sma200=100,
        roc21=4,
        roc63=10,
        distribution_count=0,
        donchian_20_breakout=True,
    )
    result = classify_market_regime(features, features, config.market_regime_params)
    policy = MarketRegimePolicyService().policy_for(result, config)
    deps = {entry.key: json.loads(entry.canonical_value_json) for entry in frozen.entries}
    assert "feature.pine" in deps and "feature.v4" in deps
    resolve_regime_configuration()
    assert classify_market_regime(features, features, config.market_regime_params) == result
    assert MarketRegimePolicyService().policy_for(result, config) == policy
    changed = replace(config, freshness={**config.freshness, "max_stale_trading_days": 4})
    # Explicit supplied dependencies also ensure capture never rereads environment/files.
    first = snapshot_regime_configuration(
        config, pine={"length": 20}, technical_v4={"enabled": True}
    )
    second = snapshot_regime_configuration(
        changed, pine={"length": 20}, technical_v4={"enabled": True}
    )
    assert compare(first, second) is Status.INCOMPATIBLE
    assert (
        compare(
            first,
            snapshot_regime_configuration(
                config, pine={"length": 21}, technical_v4={"enabled": True}
            ),
        )
        is Status.INCOMPATIBLE
    )


def test_policy_version_is_behavioral_and_all_phase3_policy_decisions_preserved():
    from app.services.contextual_consumer_eligibility import REGIME_TO_SETUP
    from app.services.producer_readiness import ReadinessStatus, normalize_producer_readiness

    for policy in (TECHNICAL_TO_RANKING, REGIME_TO_SETUP):
        frozen = snapshot_readiness_policy(policy)
        assert (
            compare(frozen, snapshot_readiness_policy(replace(policy, policy_version="other-v2")))
            is Status.INCOMPATIBLE
        )
        producer = getattr(policy, "producer", "TECHNICAL")
        ready = normalize_producer_readiness(
            producer, {}, identity_fingerprint="a" * 64, calculation_versions={}
        )
        for status in ReadinessStatus:
            envelope = replace(ready, status=status)
            before = policy.evaluate(envelope)
            snapshot_readiness_policy(policy)
            assert policy.evaluate(envelope) == before
    assert len(CONFIGURATION_FAMILIES) == 3
    with pytest.raises(TypeError, match="authority"):
        snapshot_readiness_policy(object())
