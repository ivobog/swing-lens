from dataclasses import replace
from unittest.mock import patch

import pytest

from app.services.decision_effective_configuration import (
    configuration_from_payload,
    freeze_decision_configuration,
    resolve_alert_configuration,
    resolve_ceri_decision_configuration,
    resolve_lifecycle_configuration,
    resolve_readiness_policy_configuration,
    resolve_setup_configuration,
    resolve_winner_configuration,
)
from app.services.setup_lifecycle.config import load_setup_lifecycle_config
from app.services.winner_probability.config import load_winner_probability_config


@pytest.mark.parametrize(
    "resolver",
    [
        resolve_setup_configuration,
        resolve_lifecycle_configuration,
        resolve_alert_configuration,
        resolve_winner_configuration,
    ],
)
def test_retained_configuration_round_trip_has_no_live_reads(resolver):
    original = resolver()
    retained = original.snapshot.as_dict()
    with patch("pathlib.Path.open", side_effect=AssertionError("live config read")):
        restored = configuration_from_payload(retained)
        assert restored.snapshot.as_dict() == retained
        if resolver is resolve_winner_configuration:
            assert restored.winner_config().config_hash == original.winner_config().config_hash
        else:
            assert restored.setup_config().config_hash == original.setup_config().config_hash
    restored.values["native"]["config_hash"] = "mutated"
    assert restored.snapshot.as_dict() == retained


def test_setup_drift_preserves_original():
    config = load_setup_lifecycle_config()
    first = resolve_setup_configuration(config)
    second = resolve_setup_configuration(
        replace(
            config, confidence=replace(config.confidence, high_min=config.confidence.high_min + 1)
        )
    )
    assert first.snapshot.semantic_hash != second.snapshot.semantic_hash
    assert first.setup_config().confidence.high_min == config.confidence.high_min


def test_lifecycle_drift_preserves_prior_configuration():
    config = load_setup_lifecycle_config()
    first = resolve_lifecycle_configuration(config)
    second = resolve_lifecycle_configuration(
        replace(
            config,
            episodes=replace(
                config.episodes,
                observation_gap_sessions=config.episodes.observation_gap_sessions + 1,
            ),
        )
    )
    assert first.snapshot.semantic_hash != second.snapshot.semantic_hash
    assert first.setup_config().episodes == config.episodes


def test_cohort_only_drift_does_not_change_prediction_semantics():
    config = load_winner_probability_config()
    changed = replace(
        config, cohort=replace(config.cohort, prior_strength=config.cohort.prior_strength + 1)
    )
    assert (
        resolve_winner_configuration(config).snapshot.semantic_hash
        == resolve_winner_configuration(changed).snapshot.semantic_hash
    )
    for family in ("cohort", "generation"):
        assert (
            resolve_winner_configuration(config, family=family).snapshot.semantic_hash
            != resolve_winner_configuration(changed, family=family).snapshot.semantic_hash
        )


def test_prediction_model_drift_is_material():
    config = load_winner_probability_config()
    changed = replace(config, engine=replace(config.engine, calculation_version="next-model"))
    assert (
        resolve_winner_configuration(config).snapshot.semantic_hash
        != resolve_winner_configuration(changed).snapshot.semantic_hash
    )


def test_outcome_contract_drift_is_separate_and_retained():
    config = load_winner_probability_config()
    original = resolve_winner_configuration(config, family="outcome")
    changed = replace(config, horizon=replace(config.horizon, sessions=(1, 3, 5, 10, 30)))
    assert (
        original.snapshot.semantic_hash
        != resolve_winner_configuration(changed, family="outcome").snapshot.semantic_hash
    )
    assert original.winner_config().horizon.sessions == config.horizon.sessions


def test_snapshot_tamper_rejected():
    payload = resolve_setup_configuration().snapshot.as_dict()
    payload["entries"][0]["value"] = {"type": "string", "value": "tampered"}
    with pytest.raises(ValueError, match="INTEGRITY"):
        configuration_from_payload(payload)


def test_same_values_different_native_provenance_keep_semantics():
    loaded = load_setup_lifecycle_config()
    explicit = replace(loaded)
    first, second = resolve_setup_configuration(loaded), resolve_setup_configuration(explicit)
    assert first.snapshot.semantic_hash == second.snapshot.semantic_hash
    assert first.snapshot.resolution_hash != second.snapshot.resolution_hash
    assert (
        resolve_setup_configuration(first.setup_config()).snapshot.as_dict()
        == first.snapshot.as_dict()
    )


@pytest.mark.parametrize("name", ["setup", "lifecycle", "alerts"])
def test_secret_transport_exclusion_and_behavioral_flag_identity(name):
    namespace = "test.decision." + name
    first = freeze_decision_configuration(
        namespace, {"enabled": True, "timeout": 1, "smtp_password": "first"}, ("enabled",)
    )
    operational = freeze_decision_configuration(
        namespace, {"enabled": True, "timeout": 2, "smtp_password": "second"}, ("enabled",)
    )
    behavioral = freeze_decision_configuration(
        namespace, {"enabled": False, "timeout": 1, "smtp_password": "first"}, ("enabled",)
    )
    assert first.snapshot.semantic_hash == operational.snapshot.semantic_hash
    assert first.snapshot.semantic_hash != behavioral.snapshot.semantic_hash
    assert "first" not in str(first.snapshot.as_dict())
    assert "second" not in str(operational.snapshot.as_dict())


def test_actual_setup_builder_freezes_before_math_and_binds_drift():
    from setup_lifecycle.test_snapshot_builder import _market_snapshot, _ticker_context

    from app.services.combined_ranking_identity import calculation_identity_from_debug
    from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder

    config = load_setup_lifecycle_config()
    context = _ticker_context(market_regime_snapshot=_market_snapshot())
    builder = SetupLifecycleSnapshotBuilder(config)
    first = builder.build(context)
    changed = replace(
        config, confidence=replace(config.confidence, high_min=config.confidence.high_min + 1)
    )
    second = SetupLifecycleSnapshotBuilder(changed).build(context)
    assert replace(builder.build(context).dto, calculated_at=first.dto.calculated_at) == first.dto
    i1 = calculation_identity_from_debug(first.dto.source_lineage)
    i2 = calculation_identity_from_debug(second.dto.source_lineage)
    if i1 is not None:
        assert i2 is not None and i1.fingerprint() != i2.fingerprint()
    assert (
        first.dto.effective_configuration.semantic_hash
        != second.dto.effective_configuration.semantic_hash
    )


def test_actual_lifecycle_engine_c1_survives_mutable_caller_drift():
    from setup_lifecycle.lifecycle_helpers import snapshot

    from app.services.setup_lifecycle.lifecycle_engine import (
        LifecycleEvaluationInput,
        SetupLifecycleEngine,
    )

    config = load_setup_lifecycle_config()
    engine = SetupLifecycleEngine(config=config)
    request = LifecycleEvaluationInput(
        snapshot=snapshot(
            setup_score=7.3, classification="Breakout Base", distance_to_pivot_pct=1.0
        )
    )
    expected = engine.evaluate(request)
    config.actionability["minimum_actionable_confidence"] = 100
    assert engine.evaluate(request) == expected
    assert (
        resolve_lifecycle_configuration(config).snapshot.semantic_hash
        != engine.effective_configuration.snapshot.semantic_hash
    )


def test_actual_alert_rule_matching_keeps_cached_c1_after_db_rule_drift():
    from setup_lifecycle.test_alert_service import FakeAlertRepository, _lifecycle_event

    from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService
    from app.services.setup_lifecycle.enums import LifecycleState

    repo = FakeAlertRepository.with_seeded_rules()
    service = SetupLifecycleAlertService(repository=repo)
    rules = service.rules_for_evaluation(object())
    retained = service.effective_configuration.snapshot.as_dict()
    current = next(row for row in repo.rules if row.rule_id == "NEW_READY")
    current.enabled = False
    current.cooldown_sessions = 99
    event = _lifecycle_event(
        event_id=10,
        episode_id=501,
        to_state=LifecycleState.READY,
        severity="ACTIONABLE",
        confidence_score=80,
        source_event_key="drift",
    )
    assert service.evaluate_lifecycle_event(object(), event).created == 1
    assert next(r for r in rules if r.rule_id == "NEW_READY").enabled
    assert service.effective_configuration.snapshot.as_dict() == retained


@pytest.mark.parametrize("family", ["alerts", "changes"])
def test_ceri_downstream_drift_has_separate_identity_and_retained_values(family):
    from app.services.ceri.config import load_ceri_config

    config = load_ceri_config()
    c1 = resolve_ceri_decision_configuration(config, family=family)
    if family == "changes":
        changed = replace(
            config, change_thresholds={**config.change_thresholds, "score_delta": 999.0}
        )
    else:
        changed = replace(config, alerts=replace(config.alerts, enabled=not config.alerts.enabled))
    assert (
        c1.snapshot.semantic_hash
        != resolve_ceri_decision_configuration(changed, family=family).snapshot.semantic_hash
    )
    assert (
        configuration_from_payload(c1.snapshot.as_dict()).snapshot.as_dict()
        == c1.snapshot.as_dict()
    )


def test_named_readiness_policy_version_is_configuration_authority():
    from app.services.technical_consumer_eligibility import TECHNICAL_TO_SETUP

    first = resolve_readiness_policy_configuration(TECHNICAL_TO_SETUP)
    second = resolve_readiness_policy_configuration(
        replace(TECHNICAL_TO_SETUP, policy_version="next")
    )
    assert first.snapshot.semantic_hash != second.snapshot.semantic_hash


def test_winner_frozen_maturation_uses_c1_proxy_and_missing_fails():
    from types import SimpleNamespace

    from app.services.winner_probability.outcome_service import _sector_proxy_for_prediction

    c1 = resolve_winner_configuration(family="outcome")
    prediction = SimpleNamespace(
        lineage_json={
            "outcome_effective_configuration": c1.snapshot.as_dict(),
            "outcome_reference_policy": {
                "version": "winner-outcome-reference-v1",
                "sector_proxy": "XLK",
                "benchmark_ticker": "SPY",
            },
        }
    )
    with patch("pathlib.Path.open", side_effect=AssertionError("C2 proxy")):
        assert _sector_proxy_for_prediction(prediction) == "XLK"
        del prediction.lineage_json["outcome_reference_policy"]
        with pytest.raises(ValueError, match="MISSING_WINNER_OUTCOME_REFERENCE"):
            _sector_proxy_for_prediction(prediction)


def test_winner_prediction_semantic_vector_excludes_whole_file_diagnostic_hash():
    from app.services.winner_probability.capture_service import _prediction_semantic_hash

    assert _prediction_semantic_hash(
        {"score": 7, "config_hash": "cohort-c1"}
    ) == _prediction_semantic_hash({"score": 7, "config_hash": "cohort-c2"})
    assert _prediction_semantic_hash({"score": 7}) != _prediction_semantic_hash({"score": 8})


def test_native_profile_aliases_and_parser_defaults_have_actual_sources():
    from app.services.effective_configuration import ConfigurationSourceKind as Kind

    cfg = resolve_setup_configuration()
    sources = {entry.key: entry.source.kind for entry in cfg.snapshot.entries}
    assert (
        sources["native.families.policies.BREAKOUT.parameters.contraction_sessions_min"]
        is Kind.PROFILE
    )
    assert sources["native.phases.BREAKOUT"] is Kind.PROFILE
    assert sources["native.signal_registry"] is Kind.PROFILE
    assert sources["native.data_quality_labels.HIGH.allows_missing_context"] is Kind.CODE_DEFAULT
    restored = configuration_from_payload(cfg.snapshot.as_dict())
    assert (
        resolve_setup_configuration(restored.setup_config()).snapshot.resolution_hash
        == cfg.snapshot.resolution_hash
    )


def test_winner_omitted_optional_value_is_a_parser_default(tmp_path):
    from pathlib import Path

    import yaml

    from app.services.effective_configuration import ConfigurationSourceKind as Kind

    raw = yaml.safe_load(Path("config/winner_probability.yaml").read_text(encoding="utf-8"))
    raw["entry_models"].pop("diagnostics", None)
    path = tmp_path / "winner.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = resolve_winner_configuration(load_winner_probability_config(path))
    entry = next(e for e in cfg.snapshot.entries if e.key == "native.entry_models.diagnostics")
    assert entry.source.kind is Kind.CODE_DEFAULT
    assert entry.defaulted


def test_native_loaders_bootstrap_in_a_fresh_process_without_import_cycle():
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.services.setup_lifecycle.config import load_setup_lifecycle_config; "
            "from app.services.winner_probability.config import load_winner_probability_config; "
            "load_setup_lifecycle_config(); load_winner_probability_config()",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_repair_preserves_unknown_legacy_input_dimensions_and_rejects_corruption():
    from types import SimpleNamespace

    from app.services.calculation_identity import CALCULATION_IDENTITY_SCHEMA_VERSION
    from app.services.setup_lifecycle.decision_evidence import _setup_base_identity

    legacy = SimpleNamespace(calculation_identity_json={"legacy_suffix": "opaque"}, run_id=7)
    identity = _setup_base_identity(legacy, "MSFT")
    assert (
        identity.calculation_context.market_calculation_context_id.state.value == "LEGACY_UNKNOWN"
    )
    legacy.calculation_identity_json = {"schema_version": CALCULATION_IDENTITY_SCHEMA_VERSION}
    with pytest.raises(ValueError):
        _setup_base_identity(legacy, "MSFT")


def test_real_setup_operational_change_excluded_behavioral_enable_included():
    cfg = load_setup_lifecycle_config()
    first = resolve_setup_configuration(cfg)
    operational = resolve_setup_configuration(
        replace(cfg, api=replace(cfg.api, max_page_size=cfg.api.max_page_size + 1))
    )
    behavioral = resolve_setup_configuration(
        replace(cfg, engine=replace(cfg.engine, enabled=not cfg.engine.enabled))
    )
    assert operational.snapshot.semantic_hash == first.snapshot.semantic_hash
    assert operational.snapshot.resolution_hash != first.snapshot.resolution_hash
    assert behavioral.snapshot.semantic_hash != first.snapshot.semantic_hash


@pytest.mark.parametrize("kind", ["setup", "lifecycle"])
def test_preconstructed_c2_service_cannot_run_under_c1_delivery(kind):
    from app.services.configuration_delivery import (
        ConfigurationDelivery,
        configuration_delivery_scope,
    )
    from app.services.setup_lifecycle.lifecycle_engine import SetupLifecycleEngine
    from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder

    native = load_setup_lifecycle_config()
    changed = replace(
        native, confidence=replace(native.confidence, high_min=native.confidence.high_min + 1)
    )
    if kind == "setup":
        c1 = resolve_setup_configuration(native)
        service = SetupLifecycleSnapshotBuilder(changed)
        method = service.build
    else:
        c1 = resolve_lifecycle_configuration(native)
        service = SetupLifecycleEngine(config=changed)
        method = service.evaluate
    delivery = ConfigurationDelivery({"test": "C1"}, {c1.snapshot.family.namespace: c1})
    with (
        configuration_delivery_scope(delivery),
        pytest.raises(ValueError, match="DECISION_SERVICE_CONFIGURATION_ANCHOR_MISMATCH"),
    ):
        method(None)  # Rejection precedes any source selection or math.


def test_native_maturation_uses_frozen_same_bar_policy_after_definition_drift():
    from datetime import UTC, datetime

    from winner_probability.test_outcome_service import (
        FakeOutcomeDb,
        FakeOutcomeRepository,
        _bars,
        _forward,
        _prediction,
        _target_stop,
    )

    from app.services.decision_effective_configuration import winner_outcome_reference_configuration
    from app.services.winner_probability.outcome_service import OutcomeMaturationService

    native = load_winner_probability_config()
    definition = replace(
        native.primary_outcome_definition,
        id="definition-2",
        entry_model="NEXT_OPEN",
        horizon_sessions=5,
        target_pct=2.5,
        stop_pct=2.0,
        same_bar_conflict_policy="CONSERVATIVE_STOP_FIRST",
    )
    cfg = replace(native, outcome_definitions=(definition,))
    reference = {
        "version": "winner-outcome-reference-v1",
        "benchmark_ticker": "SPY",
        "sector_proxy": "XLK",
    }
    frozen = winner_outcome_reference_configuration(
        resolve_winner_configuration(cfg, family="outcome"), reference
    )
    prediction = _prediction()
    prediction.lineage_json = {
        "outcome_effective_configuration": frozen.snapshot.as_dict(),
        "outcome_reference_policy": reference,
    }
    forward, target = _forward(), _target_stop()
    target.outcome_definition.same_bar_conflict_policy = "OPTIMISTIC_TARGET_FIRST"
    repository = FakeOutcomeRepository(
        predictions=[prediction],
        forward_outcomes=[forward],
        target_stop_outcomes=[target],
        bars={"MSFT": _bars([100] * 5, highs=[104] * 5, lows=[97] * 5), "SPY": [], "XLK": []},
    )
    with patch("pathlib.Path.open", side_effect=AssertionError("current C2 configuration read")):
        result = OutcomeMaturationService(repository=repository).process_due_outcomes(
            FakeOutcomeDb(repository), now=datetime(2026, 8, 10, 21, tzinfo=UTC)
        )
    assert result.target_stop_matured == 1
    assert target.same_bar_conflict is True
    assert target.primary_winner is False
    assert target.outcome_definition.same_bar_conflict_policy == "OPTIMISTIC_TARGET_FIRST"
