"""Native equal outputs cannot authorize reuse across material policy drift."""

from dataclasses import replace
from datetime import date

import pytest

from app.services.calculation_identity import CalculationIdentity


def _reject_reuse(first, second):
    base = CalculationIdentity.legacy_unknown(run_id=7, ticker="ACME")
    c1, c2 = first.bind(base), second.bind(base)
    assert c1.fingerprint() != c2.fingerprint()
    assert c1.source_lineage == c2.source_lineage
    assert c1.temporal == c2.temporal
    with pytest.raises(ValueError, match="RETRY_MISMATCH"):
        second.require_retry_identity(c1)


def test_native_ranking_equal_complete_decisions_still_bind_different_thresholds():
    from test_ranking_profile_engine import _config, _fundamental, _profile, _row, _technical

    from app.services.core_effective_configuration import resolve_ranking_configuration
    from app.services.ranking_profile_engine import rank_single_row

    profile = _profile("momentum_swing")
    changed = replace(
        profile,
        thresholds=replace(
            profile.thresholds,
            strong_candidate_min_score=profile.thresholds.strong_candidate_min_score + 0.01,
        ),
    )
    inputs = dict(
        row=_row("ACME"),
        fundamental=_fundamental("ACME", 6.0),
        technical=_technical("ACME", trend=6, momentum=6, setup=6, risk=2, rs=6),
        config=_config(),
        today=date(2026, 7, 7),
    )
    first = rank_single_row(profile=profile, **inputs)
    second = rank_single_row(profile=changed, **inputs)
    # Debug retains policy diagnostics; every financial DTO field is compared.
    assert replace(second, debug=first.debug) == first
    _reject_reuse(
        resolve_ranking_configuration(profile, inputs["config"]),
        resolve_ranking_configuration(changed, inputs["config"]),
    )


def test_native_ceri_equal_entire_event_risk_dto_still_binds_different_caps():
    from app.services.ceri.config import ceri_config_hash
    from app.services.ceri.event_risk_service import CeriEventRiskService
    from app.services.contextual_effective_configuration import resolve_ceri_configuration

    first = resolve_ceri_configuration()
    native = first.ceri_config()
    changed = replace(native, event_risk={**native.event_risk, "secondary_penalty_cap": 2.25})
    changed = replace(changed, config_hash=ceri_config_hash(changed))
    second = resolve_ceri_configuration(changed)
    as_of = date(2026, 9, 14)
    assert CeriEventRiskService(native).calculate(as_of_session=as_of) == (
        CeriEventRiskService(changed).calculate(as_of_session=as_of)
    )
    _reject_reuse(first, second)


def test_native_setup_equal_all_signals_and_quality_still_binds_different_confidence():
    from setup_lifecycle.test_snapshot_builder import _market_snapshot, _ticker_context

    from app.services.setup_lifecycle.config import load_setup_lifecycle_config
    from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder

    native = load_setup_lifecycle_config()
    changed = replace(
        native, confidence=replace(native.confidence, high_min=native.confidence.high_min + 1)
    )
    context = _ticker_context(market_regime_snapshot=_market_snapshot())
    first_builder = SetupLifecycleSnapshotBuilder(native)
    second_builder = SetupLifecycleSnapshotBuilder(changed)
    first, second = first_builder.build(context).dto, second_builder.build(context).dto
    assert first.signals == second.signals
    assert first.data_quality_label == second.data_quality_label
    assert first.promoted_fields == second.promoted_fields
    assert first.feature_flags == second.feature_flags
    assert first.missing_data == second.missing_data
    assert first.diagnostic_high_cross == second.diagnostic_high_cross
    assert first.canonical_decision == second.canonical_decision
    assert first.warning_flags == second.warning_flags
    _reject_reuse(first_builder.effective_configuration, second_builder.effective_configuration)
