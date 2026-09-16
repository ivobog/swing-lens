from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.services.calculation_identity import CalculationIdentity, IdentityDimension
from app.services.combined_decision import _load_scoring_config
from app.services.configuration_source_values import configuration_leaves
from app.services.core_effective_configuration import (
    freeze_core_configuration,
    resolve_combined_configuration,
    resolve_fundamental_configuration,
    resolve_ranking_configuration,
    resolve_technical_configuration,
)
from app.services.effective_configuration import (
    ConfigurationCompatibilityStatus,
    ConfigurationSource,
    ConfigurationSourceKind,
    compare_configuration,
)
from app.services.ranking_profile_config import get_ranking_profile, load_ranking_profiles
from app.services.technical_indicators import load_pine_defaults
from app.services.technical_scoring_config import load_technical_scoring_v4_config
from app.services.technical_scoring_v5_config import load_technical_scoring_v5_config
from app.settings import Settings


def core_configurations():
    settings = Settings(_env_file=None)
    return (
        resolve_fundamental_configuration(),
        resolve_technical_configuration(
            pine=load_pine_defaults(),
            v4=load_technical_scoring_v4_config(),
            v5=load_technical_scoring_v5_config(),
            settings=settings,
        ),
        resolve_combined_configuration(),
        resolve_ranking_configuration(
            get_ranking_profile("momentum_swing"), _load_scoring_config()
        ),
    )


def changed_configuration(configuration):
    values = configuration.values
    namespace = configuration.snapshot.family.namespace
    if namespace == "core.fundamental":
        values["missing_data"]["critical_field_penalty"] += 0.2
    elif namespace == "core.technical":
        values["pine"]["trend"]["emaFastLen"] += 2
    elif namespace == "core.combined":
        values["combined_score"].update(fundamental_score=0.6, dual_score=0.4)
    else:
        values["profile"].update(fundamental_weight=0.4, technical_weight=0.6)
    return freeze_core_configuration(namespace, values)


@pytest.mark.parametrize("index", range(4))
def test_core_same_inputs_different_behavior_cannot_reuse_calculation(index):
    first = core_configurations()[index]
    second = changed_configuration(first)
    base = CalculationIdentity.legacy_unknown(run_id=7, ticker="ACME")
    c1, c2 = first.bind(base), second.bind(base)
    assert c1.fingerprint() != c2.fingerprint()
    assert c1.temporal == c2.temporal
    assert c1.source_lineage == c2.source_lineage
    assert (
        compare_configuration(
            c1.configuration.effective_configuration,
            c2.configuration.effective_configuration,
        ).status
        is ConfigurationCompatibilityStatus.INCOMPATIBLE
    )
    first.require_retry_identity(c1)
    with pytest.raises(ValueError, match="CORE_CONFIGURATION_RETRY_MISMATCH"):
        second.require_retry_identity(c1)


@pytest.mark.parametrize("index", range(4))
def test_core_same_semantics_different_source_and_immutable_native_values(index):
    first = core_configurations()[index]
    values = first.values
    original = deepcopy(values)
    second = freeze_core_configuration(
        first.snapshot.family.namespace,
        values,
        sources={
            key: ConfigurationSource(ConfigurationSourceKind.REQUEST, "approved-request")
            for key, _ in configuration_leaves(values)
        },
    )
    values.clear()
    assert first.values == second.values == original
    assert first.snapshot.semantic_hash == second.snapshot.semantic_hash
    assert first.snapshot.resolution_hash != second.snapshot.resolution_hash
    base = CalculationIdentity.legacy_unknown(run_id=7, ticker="ACME")
    assert first.bind(base).fingerprint() == second.bind(base).fingerprint()
    assert (
        compare_configuration(
            IdentityDimension.known(first.snapshot.identity),
            IdentityDimension.known(second.snapshot.identity),
        ).status
        is ConfigurationCompatibilityStatus.EXACT
    )


def test_profile_name_collision_description_and_unrelated_profile_granularity():
    profiles = load_ranking_profiles()
    config = _load_scoring_config()
    first = {p.name: resolve_ranking_configuration(p, config) for p in profiles}
    changed = [
        replace(p, technical_weight=0.6, fundamental_weight=0.4)
        if p.name == "defensive_quality"
        else p
        for p in profiles
    ]
    second = {p.name: resolve_ranking_configuration(p, config) for p in changed}
    assert (
        first["momentum_swing"].snapshot.semantic_hash
        == second["momentum_swing"].snapshot.semantic_hash
    )
    assert (
        first["defensive_quality"].snapshot.semantic_hash
        != second["defensive_quality"].snapshot.semantic_hash
    )
    momentum = next(p for p in profiles if p.name == "momentum_swing")
    collision = resolve_ranking_configuration(
        replace(momentum, technical_weight=0.6, fundamental_weight=0.4), config
    )
    assert collision.ranking_profile().name == momentum.name
    assert collision.snapshot.semantic_hash != first[momentum.name].snapshot.semantic_hash
    description = resolve_ranking_configuration(
        replace(momentum, description="new display text"), config
    )
    assert description.snapshot.semantic_hash == first[momentum.name].snapshot.semantic_hash
    assert description.snapshot.resolution_hash != first[momentum.name].snapshot.resolution_hash
    base = CalculationIdentity.legacy_unknown(run_id=7, ticker="ACME")
    assert (
        first["momentum_swing"].bind(base).fingerprint()
        == second["momentum_swing"].bind(base).fingerprint()
    )


def test_operational_and_secret_settings_do_not_enter_technical_semantics():
    first = Settings(_env_file=None, technical_worker_processes=1)
    second = Settings(
        _env_file=None,
        technical_worker_processes=2,
        database_url="postgresql://user:synthetic-sensitive-value@localhost/db",
        eodhd_api_key="synthetic-sensitive-value",
    )
    kwargs = dict(
        pine=load_pine_defaults(),
        v4=load_technical_scoring_v4_config(),
        v5=load_technical_scoring_v5_config(),
    )
    a, b = (
        resolve_technical_configuration(**kwargs, settings=settings) for settings in (first, second)
    )
    assert a.snapshot.semantic_hash == b.snapshot.semantic_hash
    assert a.snapshot.resolution_hash == b.snapshot.resolution_hash
    assert "synthetic-sensitive-value" not in repr(a.snapshot.as_dict()) + repr(
        b.snapshot.as_dict()
    )
    assert "technical_worker_processes" not in repr(a.snapshot.as_dict())


def test_inactive_v5_changes_do_not_invalidate_technical():
    settings = Settings(
        _env_file=None, technical_v5_enabled=False, technical_v5_shadow_compare_enabled=False
    )
    v5 = load_technical_scoring_v5_config()
    kwargs = dict(
        pine=load_pine_defaults(), v4=load_technical_scoring_v4_config(), settings=settings
    )
    first = resolve_technical_configuration(**kwargs, v5=v5)
    v5["leadership"]["weights"].clear()
    second = resolve_technical_configuration(**kwargs, v5=v5)
    assert first.snapshot.semantic_hash == second.snapshot.semantic_hash


def test_combined_and_ranking_exclude_unrelated_upstream_configuration():
    raw = _load_scoring_config()
    combined = resolve_combined_configuration(raw)
    ranking = resolve_ranking_configuration(get_ranking_profile("momentum_swing"), raw)
    raw["fundamental_components"].clear()
    assert (
        combined.snapshot.semantic_hash
        == resolve_combined_configuration(raw).snapshot.semantic_hash
    )
    assert (
        ranking.snapshot.semantic_hash
        == resolve_ranking_configuration(
            get_ranking_profile("momentum_swing"), raw
        ).snapshot.semantic_hash
    )
    assert "fundamental_components" not in combined.values
    assert "combined_score" not in ranking.values


def test_winning_profile_file_and_parser_default_provenance():
    frozen = resolve_ranking_configuration(
        get_ranking_profile("momentum_swing"), _load_scoring_config()
    )
    entries = {entry.key: entry for entry in frozen.snapshot.entries}
    assert entries["profile.technical_weight"].source.kind is ConfigurationSourceKind.PROFILE
    assert entries["profile.technical_weight"].source.identifier == "config/ranking_profiles.yaml"
    assert entries["profile.tradeability_overlay.enabled"].source.kind in {
        ConfigurationSourceKind.PROFILE,
        ConfigurationSourceKind.CODE_DEFAULT,
    }
    assert (
        entries["earnings_risk_gate.penalties.blocked"].source.identifier
        == "config/scoring_weights.yaml"
    )


def test_certified_technical_finalization_cannot_guess_configuration():
    from app.services.technical_score_service import finalize_technical_scores

    with pytest.raises(ValueError, match="frozen before feature construction"):
        finalize_technical_scores(SimpleNamespace(), 7, [], pipeline_run_id=11, persist=False)


def test_technical_native_per_ticker_path_resolves_files_once(monkeypatch):
    from test_combined_ranking_identity_adoption import FakeDb
    from test_technical_work import _synthetic_frame

    from app.services import pine_replica_engine, technical_indicators, technical_score_service

    settings = Settings(
        _env_file=None, technical_v5_enabled=False, technical_v5_shadow_compare_enabled=False
    )
    monkeypatch.setattr(technical_score_service, "get_settings", lambda: settings)
    counts = {}
    for name in (
        "load_pine_defaults",
        "load_technical_scoring_v4_config",
        "load_technical_scoring_v5_config",
    ):
        original = getattr(technical_score_service, name)

        def resolve_once(*args, _name=name, _original=original, **kwargs):
            counts[_name] = counts.get(_name, 0) + 1
            return _original(*args, **kwargs)

        monkeypatch.setattr(technical_score_service, name, resolve_once)

    def reject_hidden_read(*_args, **_kwargs):
        raise AssertionError("per-ticker behavioral source reread")

    for module in (technical_indicators, pine_replica_engine):
        monkeypatch.setattr(module, "load_pine_defaults", reject_hidden_read)
        monkeypatch.setattr(module, "load_technical_scoring_v4_config", reject_hidden_read)
    frame = _synthetic_frame()
    monkeypatch.setattr(
        technical_score_service, "_load_preferred_bounded", lambda *_args: (frame, frame)
    )
    monkeypatch.setattr(technical_score_service, "_call_price_frame", lambda *_args: frame)
    scores = technical_score_service.score_run_technicals(
        FakeDb(), 7, tickers=["AAA", "BBB", "CCC"]
    )
    assert len(scores) == 3
    assert all(score.dual_score is not None for score in scores)
    assert counts == {
        "load_pine_defaults": 1,
        "load_technical_scoring_v4_config": 1,
        "load_technical_scoring_v5_config": 1,
    }


def test_profile_parser_resolved_once_per_pipeline_step(monkeypatch):
    from test_ranking_profile_service import FakeDb, _patch_run_inputs

    from app.models.tables import UploadRun
    from app.services import ranking_profile_service

    _patch_run_inputs(monkeypatch)
    original = ranking_profile_service.load_ranking_profiles
    counts = {"profiles": 0, "shared": 0}

    def profiles_once():
        counts["profiles"] += 1
        return original()

    shared = ranking_profile_service._load_scoring_config

    def shared_once():
        counts["shared"] += 1
        return shared()

    monkeypatch.setattr(ranking_profile_service, "load_ranking_profiles", profiles_once)
    monkeypatch.setattr(ranking_profile_service, "_load_scoring_config", shared_once)
    db = FakeDb(upload_runs={7: UploadRun(id=7, filename="scope.csv", status="COMPLETED")})
    result = ranking_profile_service.execute_ranking_pipeline_step(db, 7)
    assert result.profile_count == 5 and result.result_count == 10
    assert counts == {"profiles": 1, "shared": 1}


def test_native_settings_precedence_winners_and_same_semantics_different_sources(
    tmp_path, monkeypatch
):
    dotenv = tmp_path / "configuration.env"
    dotenv.write_text("TECHNICAL_V5_ENABLED=false\n", encoding="utf-8")
    monkeypatch.setenv("TECHNICAL_V5_ENABLED", "true")
    env = Settings(_env_file=dotenv)
    request = Settings(_env_file=dotenv, technical_v5_enabled=True)
    overridden = Settings(_env_file=dotenv, technical_v5_enabled=False)
    assert env.technical_v5_enabled is request.technical_v5_enabled is True
    assert overridden.technical_v5_enabled is False
    assert (
        dict((key, kind) for key, kind, _value in env._core_configuration_sources)[
            "technical_v5_enabled"
        ]
        == "ENVIRONMENT"
    )
    assert (
        dict((key, kind) for key, kind, _value in request._core_configuration_sources)[
            "technical_v5_enabled"
        ]
        == "REQUEST"
    )
    monkeypatch.delenv("TECHNICAL_V5_ENABLED")
    file = Settings(_env_file=dotenv)
    default = Settings(_env_file=None)
    assert (
        dict((key, kind) for key, kind, _value in file._core_configuration_sources)[
            "technical_v5_enabled"
        ]
        == "DOTENV"
    )
    assert (
        dict((key, kind) for key, kind, _value in default._core_configuration_sources)[
            "technical_v5_enabled"
        ]
        == "CODE_DEFAULT"
    )
    kwargs = dict(
        pine=load_pine_defaults(),
        v4=load_technical_scoring_v4_config(),
        v5=load_technical_scoring_v5_config(),
    )
    a = resolve_technical_configuration(**kwargs, settings=env)
    b = resolve_technical_configuration(**kwargs, settings=request)
    assert a.snapshot.semantic_hash == b.snapshot.semantic_hash
    assert a.snapshot.resolution_hash != b.snapshot.resolution_hash
    assert "_core_configuration_sources" not in env.model_dump()
    assert str(dotenv) not in repr(a.snapshot.as_dict())


@pytest.mark.parametrize("index", range(4))
def test_anchored_stage_rejects_changed_configuration_before_calculation(index, monkeypatch):
    from test_combined_ranking_identity_adoption import FakeDb, SourceDb, _identity_aware_sources

    from app.services import (
        combined_decision,
        fundamental_score_service,
        ranking_profile_service,
        technical_score_service,
    )

    first = core_configurations()[index]
    second = changed_configuration(first)
    anchor = first.bind(CalculationIdentity.legacy_unknown(run_id=7, ticker="ACME"))
    row, _f, _t, cutoff = _identity_aware_sources()
    kwargs = dict(effective_configuration=second, expected_calculation_identity=anchor)
    with pytest.raises(ValueError, match="CORE_CONFIGURATION_RETRY_MISMATCH"):
        if index == 0:
            fundamental_score_service.recalculate_run_fundamentals(SourceDb(row), 7, **kwargs)
        elif index == 1:
            technical_score_service.score_run_technicals(
                FakeDb(), 7, tickers=["ACME"], market_cutoff=cutoff, **kwargs
            )
        elif index == 2:
            monkeypatch.setattr(combined_decision, "_rows_for_run", lambda *_: [])
            monkeypatch.setattr(combined_decision, "_fundamentals_for_run", lambda *_: [])
            monkeypatch.setattr(combined_decision, "_technicals_for_run", lambda *_: [])
            combined_decision.refresh_combined_results(FakeDb(), 7, **kwargs)
        else:
            monkeypatch.setattr(
                ranking_profile_service, "_load_run_inputs", lambda *_: ([], {}, {})
            )
            ranking_profile_service.refresh_ranking_profile(FakeDb(), 7, "momentum_swing", **kwargs)


def test_technical_benchmark_selection_and_unused_pine_labels():
    kwargs = dict(
        pine=load_pine_defaults(),
        v4=load_technical_scoring_v4_config(),
        v5=load_technical_scoring_v5_config(),
        settings=Settings(_env_file=None),
    )
    first = resolve_technical_configuration(**kwargs, benchmark_ticker="SPY")
    second = resolve_technical_configuration(**kwargs, benchmark_ticker="QQQ")
    assert first.snapshot.semantic_hash != second.snapshot.semantic_hash
    kwargs["pine"]["market_rs"]["marketSymbol"] = "unused-label"
    assert (
        first.snapshot.semantic_hash
        == resolve_technical_configuration(**kwargs, benchmark_ticker="SPY").snapshot.semantic_hash
    )


def test_empty_pine_cannot_trigger_an_untracked_live_fallback():
    with pytest.raises(ValueError, match="resolved Pine"):
        resolve_technical_configuration(pine={}, v4={}, v5={}, settings=Settings(_env_file=None))


def test_financial_threshold_and_dsn_secret_guard():
    freeze_core_configuration(
        "core.fundamental", {"thresholds": {"net_debt_to_ebitda_warning": 4.0}}
    )
    for key in ("dsn", "dsn_url", "source_dsn", "DSNURL", "sourceDSNURL", "source.dsnUrl"):
        with pytest.raises(ValueError, match="SECURITY_SECRET"):
            freeze_core_configuration("core.fundamental", {key: "synthetic-sensitive-value"})
