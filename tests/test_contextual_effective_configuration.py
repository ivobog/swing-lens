from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from app.services.calculation_identity import CalculationIdentity
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.configuration_source_values import SourcedConfigurationValues
from app.services.contextual_effective_configuration import (
    IBMI_DEFAULTS,
    _freeze,
    contextual_configuration_from_evidence,
    resolve_ceri_configuration,
    resolve_ibmi_configuration,
    resolve_regime_configuration,
    resolve_sector_configuration,
)
from app.services.effective_configuration import (
    CONFIGURATION_PAYLOAD_KEY,
    ConfigurationClassification,
    ConfigurationSource,
    ConfigurationSourceKind,
)
from app.services.ib_market_intelligence.config import (
    load_ib_market_intelligence_config,
    secret_safe_ibmi_config_hash,
)


def configurations():
    ibmi = load_ib_market_intelligence_config()
    return [
        resolve_regime_configuration(),
        resolve_sector_configuration(),
        resolve_ceri_configuration(),
        *[resolve_ibmi_configuration(ibmi, key) for key in IBMI_DEFAULTS],
    ]


def frozen_evidence(configuration, identity=None):
    identity = identity or configuration.bind(CalculationIdentity.legacy_unknown())
    payload = {CONFIGURATION_PAYLOAD_KEY: configuration.snapshot.as_dict()}
    return SimpleNamespace(
        payload_json=payload,
        payload_fingerprint=Canonical.fingerprint(payload),
        calculation_identity_json=identity.canonical_payload(),
        calculation_identity_fingerprint=str(identity.fingerprint()),
    )


@pytest.mark.parametrize("index", range(8))
def test_contextual_drift_retry_and_integrity(index):
    first = configurations()[index]
    values = first.values
    namespace = first.snapshot.family.namespace
    if index == 0:
        values["policy"]["freshness"]["max_stale_trading_days"] += 1
    elif index == 1:
        values["config"]["defaults"]["min_tickers_for_normal_confidence"] += 1
        from app.services.sector_rotation_config import sector_rotation_config_hash

        values["selection"]["prior_config_hash"] = sector_rotation_config_hash(values["config"])
    elif index == 2:
        values["native_policy"]["posture"]["positive_min"] += 1
    else:
        module = namespace.rsplit(".", 1)[1]
        key = next(
            key for key, value in IBMI_DEFAULTS[module].items() if isinstance(value, (int, float))
        )
        values["config"][module][key] += 1
    sources = {entry.key: entry.source for entry in first.snapshot.entries}
    excluded = tuple(
        entry.key
        for entry in first.snapshot.entries
        if entry.classification is ConfigurationClassification.OPERATIONAL
    )
    display = tuple(
        entry.key
        for entry in first.snapshot.entries
        if entry.classification is ConfigurationClassification.OBSERVABILITY
    )
    second = _freeze(namespace, values, sources, excluded, display)
    base = CalculationIdentity.legacy_unknown(run_id=7, ticker="ACME")
    c1, c2 = first.bind(base), second.bind(base)
    assert c1.configuration != c2.configuration
    assert c1.source_lineage == c2.source_lineage
    assert c1.temporal == c2.temporal
    first.require_retry_identity(c1)
    with pytest.raises(ValueError, match="RETRY_MISMATCH"):
        second.require_retry_identity(c1)
    evidence = frozen_evidence(first, c1)
    historical = contextual_configuration_from_evidence(evidence).snapshot
    assert historical.semantic_hash == first.snapshot.semantic_hash
    assert historical.resolution_hash == first.snapshot.resolution_hash
    evidence.payload_json[CONFIGURATION_PAYLOAD_KEY]["semantic_hash"] = "f" * 64
    with pytest.raises(ValueError, match="integrity|hash"):
        contextual_configuration_from_evidence(evidence)


@pytest.mark.parametrize("index", range(8))
def test_contextual_provenance_and_immutable_values(index, monkeypatch):
    first = configurations()[index]
    sources = {
        entry.key: ConfigurationSource(ConfigurationSourceKind.REQUEST, "explicit-C1")
        for entry in first.snapshot.entries
    }
    excluded = tuple(
        entry.key
        for entry in first.snapshot.entries
        if entry.classification is ConfigurationClassification.OPERATIONAL
    )
    display = tuple(
        entry.key
        for entry in first.snapshot.entries
        if entry.classification is ConfigurationClassification.OBSERVABILITY
    )
    same = _freeze(first.snapshot.family.namespace, first.values, sources, excluded, display)
    assert first.snapshot.semantic_hash == same.snapshot.semantic_hash
    assert first.snapshot.resolution_hash != same.snapshot.resolution_hash
    assert all(
        entry.source.kind is not ConfigurationSourceKind.UNKNOWN for entry in first.snapshot.entries
    )
    mutable = first.values
    mutable.clear()
    assert first.values
    evidence = frozen_evidence(first)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("historical resolution touched a live source")

    monkeypatch.setattr("pathlib.Path.open", forbidden)
    monkeypatch.setattr("app.settings.get_settings", forbidden)
    historical = contextual_configuration_from_evidence(evidence)
    assert historical.values == first.values
    if index == 0:
        assert historical.regime_config().calculation_version
    elif index == 2:
        assert (
            historical.ceri_config().engine.daily_cutoff_time.isoformat()
            == first.values["config"]["engine"]["daily_cutoff_time"]
        )
    elif index > 2:
        assert historical.ibmi_config().calculation_version


def test_ibmi_module_isolation_operational_and_secret_exclusion():
    original = load_ib_market_intelligence_config()
    changed_raw = deepcopy(original.raw)
    changed_raw["liquidity"]["minimum_dollar_volume"] += 100
    changed_raw["request_budget"]["request_max_attempts"] += 1
    changed_raw["flex"]["token"] = "synthetic-secret-never-in-evidence"
    changed = replace(original, raw=changed_raw)
    for module in IBMI_DEFAULTS:
        before = resolve_ibmi_configuration(original, module)
        after = resolve_ibmi_configuration(changed, module)
        assert (before.snapshot.semantic_hash != after.snapshot.semantic_hash) == (
            module == "liquidity"
        )
        assert "synthetic-secret" not in str(after.snapshot.as_dict())
    operational = deepcopy(original.raw)
    operational["request_budget"]["request_max_attempts"] += 1
    op = replace(original, raw=operational)
    assert all(
        resolve_ibmi_configuration(original, module).snapshot.semantic_hash
        == resolve_ibmi_configuration(op, module).snapshot.semantic_hash
        for module in IBMI_DEFAULTS
    )

    secret_only = deepcopy(original.raw)
    secret_only["flex"]["token"] = "synthetic-secret-token-A"
    secret_only["engine"]["api_key"] = "synthetic-secret-key-B"
    secret_only["flex"]["cookie"] = "synthetic-secret-cookie-C"
    secret_only["flex"]["username"] = "synthetic-secret-login-D"
    assert secret_safe_ibmi_config_hash(secret_only) == original.config_hash
    changed = replace(
        original, raw=secret_only, config_hash=secret_safe_ibmi_config_hash(secret_only)
    )
    # Keep the same native file winners; dataclass replacement drops private provenance.
    object.__setattr__(changed, "_configuration_sources", original._configuration_sources)
    for module in IBMI_DEFAULTS:
        before = resolve_ibmi_configuration(original, module).snapshot
        after = resolve_ibmi_configuration(changed, module).snapshot
        assert before.semantic_hash == after.semantic_hash
        assert before.resolution_hash == after.resolution_hash
        assert "synthetic-secret" not in str(after.as_dict())


def test_ceri_active_provider_priority_order_is_frozen():
    first = resolve_ceri_configuration()
    native = first.ceri_config()
    priority = native.providers.priority
    changed = replace(
        native, providers=replace(native.providers, priority=tuple(reversed(priority)))
    )
    # Dataclass replacement does not copy the hidden resolved-snapshot cache.
    second = resolve_ceri_configuration(changed)
    assert first.snapshot.semantic_hash != second.snapshot.semantic_hash
    assert second.ceri_config().providers.priority == tuple(reversed(priority))


def test_legacy_contextual_evidence_has_no_invented_configuration():
    assert contextual_configuration_from_evidence(SimpleNamespace(payload_json={})) is None


def test_capture_consumer_extension_preserves_frozen_native_policy_and_provenance():
    first = resolve_ceri_configuration()
    values = first.values
    values["native_policy"]["posture"]["positive_min"] = 8.5
    sources = {entry.key: entry.source for entry in first.snapshot.entries}
    sources["native_policy.posture.positive_min"] = ConfigurationSource(
        ConfigurationSourceKind.REQUEST, "frozen-C1-posture"
    )
    operational = tuple(
        entry.key
        for entry in first.snapshot.entries
        if entry.classification is ConfigurationClassification.OPERATIONAL
    )
    c1 = _freeze("contextual.ceri", values, sources, operational)
    consumer = dict(
        values["consumer"], run_capture=True, revision_feature_config_hash="frozen-input-filter"
    )
    capture = resolve_ceri_configuration(c1.ceri_config(), consumer=consumer)
    assert capture.values["native_policy"]["posture"]["positive_min"] == 8.5
    entry = next(
        entry
        for entry in capture.snapshot.entries
        if entry.key == "native_policy.posture.positive_min"
    )
    assert entry.source == sources[entry.key]


def test_ibmi_frozen_adapter_retains_legacy_hash_without_live_resolution(monkeypatch):
    first = resolve_ibmi_configuration()
    original_hash = first.values["compatibility"]["legacy_config_hash"]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("frozen IBMI retry touched a live source")

    monkeypatch.setattr(
        "app.services.ib_market_intelligence.config.load_ib_market_intelligence_config", forbidden
    )
    monkeypatch.setattr("app.settings.get_settings", forbidden)
    native = first.ibmi_config()
    assert native.config_hash == original_hash
    assert resolve_ibmi_configuration(native).snapshot.semantic_hash == first.snapshot.semantic_hash


def test_incomplete_ceri_native_policy_cannot_be_historically_certified():
    first = resolve_ceri_configuration()
    values = first.values
    del values["native_policy"]["provider"]
    partial = _freeze(
        "contextual.ceri", values, {entry.key: entry.source for entry in first.snapshot.entries}
    )
    with pytest.raises(ValueError, match="incomplete CERI native policy"):
        contextual_configuration_from_evidence(frozen_evidence(partial))


def test_frozen_contextual_resolution_has_no_per_ticker_live_reads(monkeypatch):
    frozen = configurations()
    regime = frozen[0].regime_config()
    sector = SourcedConfigurationValues(frozen[1].values["config"], ())
    sector.effective_configuration = frozen[1]
    ceri = frozen[2].ceri_config()
    ibmi = [item.ibmi_config() for item in frozen[3:]]

    def forbidden(*_args, **_kwargs):
        raise AssertionError("per-ticker resolution touched a live source")

    monkeypatch.setattr("pathlib.Path.read_text", forbidden)
    monkeypatch.setattr("pathlib.Path.open", forbidden)
    monkeypatch.setattr("app.settings.get_settings", forbidden)
    for _ in range(12):
        assert resolve_regime_configuration(regime) is frozen[0]
        assert resolve_sector_configuration(sector) is frozen[1]
        assert resolve_ceri_configuration(ceri) is frozen[2]
        for native, snapshot, module in zip(ibmi, frozen[3:], IBMI_DEFAULTS, strict=True):
            assert resolve_ibmi_configuration(native, module) is snapshot


def test_native_ibmi_retry_rejects_c2_before_input_query_or_math():
    from app.services.ib_market_intelligence.enums import IntelligenceModule
    from app.services.ib_market_intelligence.orchestration import _rebuild_ticker_feature_impl

    native = load_ib_market_intelligence_config()
    c1 = resolve_ibmi_configuration(native)
    changed = deepcopy(native.raw)
    changed["liquidity"]["minimum_dollar_volume"] += 1
    c2 = replace(native, raw=changed)
    with pytest.raises(ValueError, match="RETRY_MISMATCH"):
        _rebuild_ticker_feature_impl(
            SimpleNamespace(),
            "ACME",
            IntelligenceModule.LIQUIDITY,
            date(2026, 9, 14),
            c2,
            1,
            calculation_cutoff_at=datetime(2026, 9, 14, 21, tzinfo=UTC),
            expected_calculation_identity=c1.bind(CalculationIdentity.legacy_unknown()),
        )


def test_winner_contextual_expectations_resolve_once_per_run_context(monkeypatch):
    from app.services.winner_probability import calculation_identity as winner

    regime, sector = configurations()[:2]
    counts = {"regime": 0, "sector": 0}

    def load_regime():
        counts["regime"] += 1
        return regime.regime_config()

    def load_sector():
        counts["sector"] += 1
        result = SourcedConfigurationValues(sector.values["config"], ())
        result.effective_configuration = sector
        return result

    monkeypatch.setattr(winner, "load_market_regime_command_center_config", load_regime)
    monkeypatch.setattr(winner, "load_sector_rotation_config", load_sector)
    context = SimpleNamespace()
    for _ in range(12):
        actual = winner._contextual_expectation_configurations(context)
        assert actual == (regime, sector)
    assert counts == {"regime": 1, "sector": 1}
    winner._contextual_expectation_configurations(SimpleNamespace())
    assert counts == {"regime": 2, "sector": 2}


def test_sealed_legacy_ibmi_hash_cannot_supply_effective_configuration():
    from app.services.calculation_identity import IdentityState
    from app.services.contextual_calculation_identity import build_ibmi_feature_identity

    native = load_ib_market_intelligence_config()
    row = SimpleNamespace(
        ticker="ACME",
        module="LIQUIDITY",
        config_hash=native.config_hash,
        calculation_version=native.calculation_version,
        source_version=native.source_version,
        as_of_session=date(2026, 9, 14),
        calculated_at=datetime(2026, 9, 14, 21, tzinfo=UTC),
        calculation_cutoff_at=datetime(2026, 9, 14, 21, tzinfo=UTC),
        operation_mode="HISTORICAL",
        evidence_id=44,
        calculation_evidence=SimpleNamespace(payload_json={}),
    )
    identity = build_ibmi_feature_identity(row)
    assert identity.configuration.effective_configuration.state is IdentityState.UNKNOWN
    assert identity.algorithm.calculation_version.state is IdentityState.KNOWN


@pytest.mark.parametrize("index", [2, 3])
def test_partial_frozen_rules_cannot_fall_back_to_current_defaults(index):
    first = configurations()[index]
    values = first.values
    if index == 2:
        values["native_policy"]["guidance"] = {}
    else:
        del values["config"]["liquidity"]["lookback_sessions"]
    sources = {entry.key: entry.source for entry in first.snapshot.entries}
    if index == 2:
        sources["native_policy.guidance"] = ConfigurationSource(
            ConfigurationSourceKind.REQUEST, "explicit-empty-guidance-policy"
        )
    partial = _freeze(first.snapshot.family.namespace, values, sources)
    with pytest.raises(ValueError, match="incomplete"):
        contextual_configuration_from_evidence(frozen_evidence(partial))


def test_material_unknown_provenance_is_not_certified():
    first = resolve_ceri_configuration()
    sources = {entry.key: entry.source for entry in first.snapshot.entries}
    sources["native_policy.posture.positive_min"] = ConfigurationSource()
    partial = _freeze(first.snapshot.family.namespace, first.values, sources)
    with pytest.raises(ValueError, match="UNKNOWN contextual"):
        partial.ceri_config()


def test_sector_retains_native_full_hash_prior_selection_authority():
    first = resolve_sector_configuration()
    config = first.values["config"]
    assert config["etf_score"]["enabled"] is False
    key = "benchmark_ticker"
    config["etf_score"][key] = "QQQ"
    second = resolve_sector_configuration(config)
    assert (
        first.values["selection"]["prior_config_hash"]
        != second.values["selection"]["prior_config_hash"]
    )
    assert first.snapshot.semantic_hash != second.snapshot.semantic_hash
    assert (
        next(
            entry for entry in second.snapshot.entries if entry.key == f"config.etf_score.{key}"
        ).classification
        is ConfigurationClassification.OBSERVABILITY
    )
