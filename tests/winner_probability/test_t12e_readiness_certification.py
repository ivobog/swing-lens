"""Whole-graph numeric consumption and native producer adversarial certification."""

import ast
import re
import subprocess
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest
from _phase3_helpers import FakeWinnerRepository
from ceri_ready_helpers import rated_ceri_snapshot
from contextual_readiness_helpers import ibmi_feature
from readiness_capture_helpers import ready_identity_context, reseal_context
from setup_lifecycle.test_snapshot_builder import _bar, _ticker_context
from sqlalchemy import inspect
from test_combined_decision import _config
from test_consumer_readiness import _acquire
from test_contextual_calculation_identity_adoption import _RowsDb
from test_phase3_consumer_readiness_matrix import POLICIES
from test_ranking_profile_engine import _profile
from test_sector_rotation_service import FakeMarketRepository, FakeSectorRepository, _metrics
from test_t12c_ready_scenarios import ceri_snapshot, ready_ceri_sources
from test_winner_calculation_identity_adoption import _service, _session

from app.models.tables import SetupSignalSnapshot
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri import capture_service as ceri_capture
from app.services.combined_decision import combine_row_decision
from app.services.contextual_consumer_eligibility import CONTEXTUAL_ELIGIBILITY_KEY
from app.services.market_regime import classify_market_regime
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.producer_readiness import (
    ReadinessReason,
    ReadinessStatus,
    normalize_producer_readiness,
    readiness_from_evidence,
)
from app.services.ranking_profile_engine import rank_profile, rank_single_row
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_universe_service import SectorUniverseService
from app.services.setup_lifecycle.actionability_policy import SetupLifecycleActionabilityPolicy
from app.services.setup_lifecycle.episode_service import normalized_snapshot_from_row
from app.services.setup_lifecycle.lifecycle_engine import (
    LifecycleEvaluationInput,
    SetupLifecycleEngine,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.technical_indicators import calculate_technical_features
from app.services.winner_probability.consumer_eligibility import (
    WinnerSourceEligibilityError,
)


def stored_state(row, status):
    """Store an explicit contract state while retaining every native numeric value."""
    evidence = row.calculation_evidence
    payload = deepcopy(evidence.payload_json)
    old = readiness_from_evidence(evidence)
    if status is ReadinessStatus.LEGACY_UNKNOWN:
        payload.pop("producer_readiness")
    else:
        payload["producer_readiness"] = replace(
            old,
            status=ReadinessStatus.READY if status == "V1_READY" else status,
            readiness_policy_version=(
                "producer-readiness-v1" if status == "V1_READY" else old.readiness_policy_version
            ),
            blocking_reasons=(),
            warning_reasons=(),
        ).canonical_payload()
    evidence.payload_json = payload


@pytest.mark.parametrize(
    "policy",
    [policy for policy in POLICIES if getattr(policy, "required_readiness_version", None)],
    ids=lambda item: item.policy_version,
)
def test_old_v1_ready_cannot_authorize_new_material_consumption(monkeypatch, policy):
    # Exercise every affected numeric consumer, without changing frozen v1 evidence.
    test_every_policy_controls_actual_numeric_consumption(monkeypatch, policy, "V1_READY")


def test_regime_missing_primary_proof_is_unknown_and_active_sparse_proxy_is_blocking():
    _features, _result, payload = native_primary(400)
    payload["debug"]["market_inputs"]["SPY"].pop("insufficient_data")
    assert (
        normalize_producer_readiness("REGIME", payload, identity_fingerprint="native").status
        is ReadinessStatus.UNKNOWN
    )
    payload["debug"]["market_inputs"]["SPY"]["insufficient_data"] = False
    payload["input_symbols"].update(risk_proxy="QQQ", use_risk_proxy=True)
    payload["debug"]["market_inputs"]["QQQ"] = {"insufficient_data": True}
    envelope = normalize_producer_readiness("REGIME", payload, identity_fingerprint="native")
    assert envelope.status is ReadinessStatus.INSUFFICIENT_EVIDENCE
    assert ReadinessReason.REGIME_INSUFFICIENT_RISK_PROXY in envelope.blocking_reasons
    payload["debug"]["market_inputs"]["QQQ"] = {}
    assert (
        normalize_producer_readiness("REGIME", payload, identity_fingerprint="native").status
        is ReadinessStatus.UNKNOWN
    )
    payload["input_symbols"]["use_risk_proxy"] = False
    assert (
        normalize_producer_readiness("REGIME", payload, identity_fingerprint="native").status
        is ReadinessStatus.READY
    )


def native_primary(count):
    dates = pd.bdate_range(end="2026-07-31", periods=count)
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": range(100, 100 + count),
            "high": range(102, 102 + count),
            "low": range(99, 99 + count),
            "close": range(101, 101 + count),
            "volume": [1_000_000] * count,
        }
    )
    features = calculate_technical_features(frame, ticker="SPY")
    result = classify_market_regime(
        features.latest,
        features.latest,
        load_market_regime_command_center_config().market_regime_params,
    )
    return (
        features,
        result,
        {
            "confidence": result.confidence,
            "score": result.score,
            "regime": result.regime,
            "gate_ok": result.gate_ok,
            "input_symbols": {"primary_market": "SPY"},
            "debug": {
                "market_inputs": {
                    "SPY": {
                        "insufficient_data": features.insufficient_data,
                        "feature_debug": features.debug,
                    }
                }
            },
        },
    )


@pytest.mark.parametrize("count", [2, 25, 100, 400])
def test_native_regime_history_flag_overrides_normal_without_changing_math(count):
    features, result, payload = native_primary(count)
    assert result.confidence == "normal"  # Reproduce CORE-001 in the actual classifier.
    envelope = normalize_producer_readiness("REGIME", payload, identity_fingerprint="native")
    assert envelope.readiness_policy_version == "regime-readiness-v2"
    assert envelope.status is (
        ReadinessStatus.INSUFFICIENT_EVIDENCE
        if features.insufficient_data
        else ReadinessStatus.READY
    )
    assert (
        ReadinessReason.REGIME_INSUFFICIENT_PRIMARY in envelope.blocking_reasons
    ) is features.insufficient_data
    assert payload["score"] == result.score and payload["gate_ok"] == result.gate_ok
    assert (
        envelope.native_coverage.canonical_payload()["primary_market_input"]["insufficient_data"]
        is features.insufficient_data
    )


def test_policy_inventory_covers_every_defined_edge_and_versions_are_unique():
    declared = set()
    for filename in (
        "technical_consumer_eligibility.py",
        "contextual_consumer_eligibility.py",
        "winner_probability/consumer_eligibility.py",
    ):
        for node in ast.walk(ast.parse(Path("app/services", filename).read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id
                in {
                    "TechnicalConsumerPolicy",
                    "ContextualConsumerPolicy",
                }
            ):
                declared.add(node.args[1 if node.func.id == "TechnicalConsumerPolicy" else 2].value)
    assert declared == {policy.policy_version for policy in POLICIES}
    assert len(declared) == len(POLICIES) == 23
    document = Path("docs/architecture/SWINGLENS_READINESS_PHASE3_CERTIFIED.md").read_text()
    documented = {
        cells[2].strip(): [cell.strip() for cell in cells[3:10]]
        for line in document.splitlines()
        if re.search(r"\|\s*[a-z-]+-v[12]\s*\|", line)
        for cells in [line.split("|")]
    }
    assert set(documented) == declared
    from app.services.producer_readiness import ProducerReadinessEnvelope

    short = {"ELIGIBLE": "E", "INELIGIBLE": "I", "POLICY_UNDECIDED": "U"}
    for policy in POLICIES:
        actual = [
            short[
                policy.evaluate(
                    ProducerReadinessEnvelope(
                        getattr(policy, "producer", "TECHNICAL"),
                        state,
                        "document-check",
                        readiness_policy_version=getattr(policy, "required_readiness_version", None)
                        or "producer-readiness-v1",
                    )
                ).status.value
            ]
            for state in ReadinessStatus
        ]
        assert documented[policy.policy_version] == actual


def test_certification_adds_no_columns_or_table_constraints():
    baseline = subprocess.run(
        ["git", "show", "3dfac7a62f9701875705f3483afd00cbea5ea075:app/models/tables.py"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    ).stdout

    def schema_nodes(source):
        return [
            ast.dump(node, include_attributes=False)
            for node in ast.walk(ast.parse(source))
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "mapped_column"
            )
            or (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__table_args__"
                    for target in node.targets
                )
            )
        ]

    assert schema_nodes(Path("app/models/tables.py").read_text()) == schema_nodes(baseline)


def universe(monkeypatch, context, *, omit=None):
    ticker = context.tickers[0]
    for method, rows in (
        ("_raw_rows_for_run", [ticker.raw_row]),
        ("_fundamentals_for_run", [ticker.fundamental_score]),
        ("_technicals_for_run", [ticker.technical_score]),
        ("_combined_results_for_run", [ticker.combined_result]),
        ("_ranking_results_for_run", list(ticker.ranking_results)),
    ):
        if omit and omit in method:
            rows = []
        monkeypatch.setattr(
            f"app.services.sector_universe_service.{method}", lambda _db, _run, rows=rows: rows
        )
    return SectorUniverseService().build(object(), 7, load_sector_rotation_config())


@pytest.mark.parametrize("policy", POLICIES, ids=lambda item: item.policy_version)
@pytest.mark.parametrize(
    "status", [state for state in ReadinessStatus if state is not ReadinessStatus.READY]
)
def test_every_policy_controls_actual_numeric_consumption(monkeypatch, policy, status):
    context, cutoff = ready_identity_context()
    ticker = context.tickers[0]
    source = {
        "TECHNICAL": ticker.technical_score,
        "FUNDAMENTAL": ticker.fundamental_score,
        "COMBINED": ticker.combined_result,
        "RANKING": ticker.ranking_results[0],
        "REGIME": context.market_regime_snapshot,
        "SECTOR": context.sector_rotation_snapshot,
    }.get(getattr(policy, "producer", "TECHNICAL"))
    if getattr(policy, "producer", "TECHNICAL") == "IBMI":
        source = ibmi_feature(policy.module, ticker="MSFT")
    stored_state(source, status)
    frozen_source = source.calculation_evidence
    original_payload = deepcopy(frozen_source.payload_json)
    producer = getattr(policy, "producer", "TECHNICAL")
    assert (
        policy.evaluate(readiness_from_evidence(source.calculation_evidence)).status.value
        != "ELIGIBLE"
    )
    if policy.consumer == "WINNER":
        if producer in {"TECHNICAL", "RANKING", "COMBINED"}:
            repository = FakeWinnerRepository(context)
            result = _service(repository).capture_run(_session(), run_id=7, market_cutoff=cutoff)
            assert result.excluded == 1 and result.failed == 0
            assert not any(
                (
                    repository.predictions,
                    repository.episodes,
                    repository.estimates,
                    repository.forward_outcomes,
                    repository.target_stop_outcomes,
                )
            )
        else:
            acquired = _acquire(context, cutoff)
            assert (
                acquired.ticker_context.fundamental_score is None
                if producer == "FUNDAMENTAL"
                else (
                    acquired.run_context.market_regime_snapshot is None
                    if producer == "REGIME"
                    else acquired.ticker_context.sector_row is None
                )
            )
            repository = FakeWinnerRepository(context)
            assert (
                _service(repository)
                .capture_run(_session(), run_id=7, market_cutoff=cutoff)
                .inserted
                == 1
            )
            keys = {
                "FUNDAMENTAL": ("fundamental_score", "fundamental_coverage"),
                "REGIME": ("market_regime", "market_risk_state"),
                "SECTOR": ("sector_rank", "sector_state"),
            }[producer]
            assert all(repository.predictions[0].feature_json[key] is None for key in keys)
    elif policy.consumer == "COMBINED":
        args = (ticker.raw_row, ticker.fundamental_score, ticker.technical_score)
        result = combine_row_decision(*args, config=_config())
        missing = combine_row_decision(
            args[0],
            None if producer == "FUNDAMENTAL" else args[1],
            None if producer == "TECHNICAL" else args[2],
            config=_config(),
        )
        assert replace(result, debug_evidence={}) == replace(missing, debug_evidence={})
    elif policy.consumer == "RANKING":
        kwargs = dict(
            profile=_profile("momentum_swing"),
            row=ticker.raw_row,
            fundamental=ticker.fundamental_score,
            technical=ticker.technical_score,
            config=_config(),
        )
        if producer == "IBMI":
            from app.services.ranking_profile_config import TradeabilityOverlayConfig

            kwargs["profile"] = replace(
                kwargs["profile"],
                tradeability_overlay=TradeabilityOverlayConfig(
                    enabled=True, poor_penalty=0.5, very_poor_penalty=1, maximum_penalty=0.75
                ),
            )
            ready_source = ibmi_feature(policy.module, ticker="MSFT")
            ready_result = rank_single_row(**kwargs, liquidity_feature=ready_source)
            assert ready_result.penalties["ibkr_tradeability"] == 0.75
            kwargs["liquidity_feature"] = source
        result = rank_single_row(**kwargs)
        kwargs[
            {"TECHNICAL": "technical", "FUNDAMENTAL": "fundamental", "IBMI": "liquidity_feature"}[
                producer
            ]
        ] = None
        missing = rank_single_row(**kwargs)
        assert replace(result, debug={}) == replace(missing, debug={})
    elif policy.consumer == "SETUP":
        setup = _ticker_context(
            fundamental_score=ticker.fundamental_score,
            technical_score=ticker.technical_score,
            combined_result=ticker.combined_result,
            market_regime_snapshot=context.market_regime_snapshot,
            sector_rotation_snapshot=context.sector_rotation_snapshot,
            sector_rotation_row=ticker.sector_row,
        )
        result = SetupLifecycleSnapshotBuilder().build(setup).dto
        key = {
            "TECHNICAL": "technical_score",
            "FUNDAMENTAL": "fundamental_score",
            "COMBINED": "combined_result",
            "REGIME": "market_regime_snapshot",
            "SECTOR": "sector_rotation_snapshot",
        }[producer]
        missing = SetupLifecycleSnapshotBuilder().build(replace(setup, **{key: None})).dto
        assert result.promoted_fields == missing.promoted_fields
        assert result.signals == missing.signals
    elif policy.consumer == "CERI":
        cutoff, config, vol, short = ready_ceri_sources(monkeypatch)
        source = vol if policy.module == "VOLATILITY" else short
        stored_state(source, status)
        frozen_source = source.calculation_evidence
        original_payload = deepcopy(frozen_source.payload_json)
        selector = (
            ceri_capture._point_in_time_volatility_feature
            if policy.module == "VOLATILITY"
            else ceri_capture._point_in_time_short_pressure_feature
        )
        decisions = {}
        selected = selector(
            _RowsDb([source]),
            "MSFT",
            cutoff.cutoff_at,
            as_of_session=cutoff.latest_completed_session,
            market_cutoff=cutoff,
            ibmi_config=config,
            decisions=decisions,
        )
        assert selected is None and not next(iter(decisions.values()))["included"]
        ready_kwargs = dict(
            as_of_session=cutoff.latest_completed_session, market_cutoff=cutoff, ibmi_config=config
        )
        if policy.module != "VOLATILITY":
            vol = ceri_capture._point_in_time_volatility_feature(
                _RowsDb([vol]), "MSFT", cutoff.cutoff_at, **ready_kwargs
            )
        if policy.module != "SHORT_PRESSURE":
            short = ceri_capture._point_in_time_short_pressure_feature(
                _RowsDb([short]), "MSFT", cutoff.cutoff_at, **ready_kwargs
            )
        a = ceri_snapshot(
            cutoff,
            selected if policy.module == "VOLATILITY" else vol,
            selected if policy.module == "SHORT_PRESSURE" else short,
        )
        b = ceri_snapshot(
            cutoff,
            None if policy.module == "VOLATILITY" else vol,
            None if policy.module == "SHORT_PRESSURE" else short,
        )
        assert a[1:] == b[1:]

        def payload(row):
            return Canonical.canonicalize(
                {column.key: getattr(row, column.key) for column in inspect(type(row)).column_attrs}
            )

        assert payload(a[0]) == payload(b[0])
    elif producer in {"TECHNICAL", "FUNDAMENTAL", "COMBINED", "RANKING"}:
        a = universe(monkeypatch, context)
        b = universe(
            monkeypatch,
            context,
            omit={
                "FUNDAMENTAL": "fundamental",
                "TECHNICAL": "technical",
                "COMBINED": "combined",
                "RANKING": "ranking",
            }[producer],
        )
        assert [replace(row, debug={}) for row in a] == [replace(row, debug={}) for row in b]
    else:
        from test_sector_rotation_service import _service as sector_service

        config = load_sector_rotation_config()
        repository = FakeSectorRepository(
            previous_snapshot=source if producer == "SECTOR" else None,
            previous_rows=[ticker.sector_row],
        )
        actual = sector_service(
            universe_rows=[_metrics("Technology", score=8.4)],
            repository=repository,
            market_repository=FakeMarketRepository(
                run_snapshot=source if producer == "REGIME" else None
            ),
        )
        missing = sector_service(universe_rows=[_metrics("Technology", score=8.4)])
        a = actual.build_sector_rotation_snapshot(
            object(),
            run_id=7,
            as_of_date=cutoff.latest_completed_session,
            persist=False,
            config=config,
        )
        b = missing.build_sector_rotation_snapshot(
            object(),
            run_id=7,
            as_of_date=cutoff.latest_completed_session,
            persist=False,
            config=config,
        )
        assert a.rows == b.rows
        edge = "regime" if producer == "REGIME" else "prior_sector"
        assert not a.debug[CONTEXTUAL_ELIGIBILITY_KEY][edge]["included"]
    assert frozen_source.payload_json == original_payload


def test_same_world_cannot_regain_actionability_after_all_sources_change(monkeypatch):
    context, cutoff = ready_identity_context()
    ticker = context.tickers[0]
    from datetime import timedelta
    from decimal import Decimal

    from contextual_readiness_helpers import seal_contextual
    from core_readiness_helpers import seal_core
    from test_winner_calculation_identity_adoption import _refresh_handoff

    from app.services.combined_decision import _to_model
    from app.services.combined_ranking_identity import embed_calculation_identity
    from app.services.contextual_calculation_identity import artifact_identity
    from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
    from app.services.ranking_profile_config import TradeabilityOverlayConfig
    from app.services.ranking_profile_service import _to_ranking_model

    # Explicit healthy native facts, before producing the first consumer world.
    ticker.raw_row.upcoming_earnings_date = cutoff.latest_completed_session + timedelta(days=30)
    for key, value in {
        "trend_score": 8,
        "momentum_score": 8,
        "setup_score": 8,
        "risk_score": 2,
        "combined_relative_strength_score": 8,
        "market_score": 8,
        "vcp_score": 8,
        "box_tightness_score": 8,
        "breakout_quality_score": 8,
        "climax_risk_score": 1,
    }.items():
        setattr(ticker.technical_score, key, Decimal(value))
    reseal_context(context, cutoff, "technical")
    profile = replace(
        _profile("momentum_swing"),
        tradeability_overlay=TradeabilityOverlayConfig(
            enabled=True, poor_penalty=0.5, very_poor_penalty=1, maximum_penalty=0.75
        ),
    )
    ibmi_config = load_ib_market_intelligence_config()
    features = {
        module: ibmi_feature(
            module,
            ticker="MSFT",
            classification="GOOD" if module == "LIQUIDITY" else "VERY_POOR",
            config_hash=ibmi_config.config_hash,
            calculation_cutoff_at=cutoff.cutoff_at,
            calculated_at=cutoff.cutoff_at,
            as_of_session=cutoff.latest_completed_session,
            calendar_version=cutoff.calendar_version,
        )
        for module in ("LIQUIDITY", "VOLATILITY", "SHORT_PRESSURE")
    }
    monkeypatch.setattr(
        ceri_capture,
        "get_settings",
        lambda: type(
            "Config",
            (),
            {
                "ib_market_intelligence_enabled": True,
                "ib_volatility_intelligence_enabled": True,
                "ib_short_pressure_enabled": True,
            },
        )(),
    )
    ceri_kwargs = dict(
        as_of_session=cutoff.latest_completed_session, market_cutoff=cutoff, ibmi_config=ibmi_config
    )

    def ceri_world():
        decisions = {}
        vol = ceri_capture._point_in_time_volatility_feature(
            _RowsDb([features["VOLATILITY"]]),
            "MSFT",
            cutoff.cutoff_at,
            decisions=decisions,
            **ceri_kwargs,
        )
        short = ceri_capture._point_in_time_short_pressure_feature(
            _RowsDb([features["SHORT_PRESSURE"]]),
            "MSFT",
            cutoff.cutoff_at,
            decisions=decisions,
            **ceri_kwargs,
        )
        from app.services.ib_market_intelligence.calculations import options_event_premium_score

        return rated_ceri_snapshot(
            cutoff,
            options_event_premium_score(vol) if vol else None,
            short.classification if short else None,
        ), decisions

    ready_ceri, ready_ibmi_decisions = ceri_world()
    assert ready_ceri[1].rated and ready_ceri[2].coverage_pct == 100
    assert all(item["included"] for item in ready_ibmi_decisions.values())
    # Actual numeric consumers use one sealed T/F/R/M/S world.
    combined = combine_row_decision(
        ticker.raw_row, ticker.fundamental_score, ticker.technical_score, config=_config()
    )
    ranking = rank_profile(
        profile=profile,
        rows=[ticker.raw_row],
        fundamentals={"MSFT": ticker.fundamental_score},
        technicals={"MSFT": ticker.technical_score},
        config=_config(),
        liquidity_features={"MSFT": features["LIQUIDITY"]},
    )
    assert combined.has_technical and ranking[0].has_technical
    assert ranking[0].debug[CONTEXTUAL_ELIGIBILITY_KEY]["ibmi_liquidity"]["included"]
    # A native liquidity penalty also warns/degrades Ranking, so W1's READY
    # prerequisite uses GOOD liquidity. The all-policy attack above separately
    # proves the active 0.75 penalty disappears for every non-ready IBMI state.
    assert "ibkr_tradeability" not in ranking[0].penalties
    combined_model = _to_model(7, 1, combined)
    combined_model.id, combined_model.created_at = (
        ticker.combined_result.id,
        ticker.combined_result.created_at,
    )
    combined_model.debug_json = embed_calculation_identity(
        combined.debug_evidence,
        artifact_identity(ticker.combined_result),
        policy="T12E_NATIVE_CONSUMER",
    )
    seal_core(combined_model)
    ranking_model = _to_ranking_model(7, ranking[0])
    ranking_model.id, ranking_model.created_at = (
        ticker.ranking_results[0].id,
        ticker.ranking_results[0].created_at,
    )
    ranking_model.debug_json = embed_calculation_identity(
        ranking[0].debug,
        artifact_identity(ticker.ranking_results[0]),
        policy="T12E_NATIVE_CONSUMER",
    )
    seal_core(ranking_model)
    context = replace(
        context,
        tickers=(
            replace(ticker, combined_result=combined_model, ranking_results=(ranking_model,)),
        ),
    )
    ticker = context.tickers[0]
    _refresh_handoff(context, cutoff)
    setup = _ticker_context(
        technical_score=ticker.technical_score,
        fundamental_score=ticker.fundamental_score,
        combined_result=ticker.combined_result,
        market_regime_snapshot=context.market_regime_snapshot,
        sector_rotation_snapshot=context.sector_rotation_snapshot,
        sector_rotation_row=ticker.sector_row,
        market_cutoff=cutoff,
        price_bars=(_bar(cutoff.latest_completed_session, close=101),),
    )
    ticker.raw_row.run = context.upload_run
    setup = replace(setup, raw_row=ticker.raw_row)
    ready_setup = SetupLifecycleSnapshotBuilder().build(setup).dto
    ready_acquisition = _acquire(context, cutoff)
    frozen = ready_acquisition.consumer_eligibility.canonical_payload()
    assert all(item["included"] for item in frozen.values())
    winner_repository = FakeWinnerRepository(context)
    winner_service = _service(winner_repository)
    assert winner_service.capture_run(_session(), run_id=7, market_cutoff=cutoff).inserted == 1
    prediction = winner_repository.predictions[0]
    frozen_prediction = deepcopy(prediction.feature_json), prediction.feature_vector_hash
    positive = (
        ticker.technical_score.dual_score,
        ticker.ranking_results[0].profile_score,
        context.market_regime_snapshot.score,
        ticker.sector_row.current_rank,
    )
    ticker.technical_score.insufficient_data = True
    reseal_context(context, cutoff, "technical")
    stored_state(ticker.ranking_results[0], ReadinessStatus.UNKNOWN)
    stored_state(context.market_regime_snapshot, ReadinessStatus.STALE)
    stored_state(context.sector_rotation_snapshot, ReadinessStatus.INSUFFICIENT_EVIDENCE)
    for feature in features.values():
        feature.coverage_status = "FAILED"
        seal_contextual(feature, evidence_id=feature.evidence_id + 10000)
    changed_ceri, changed_ibmi_decisions = ceri_world()
    assert not any(item["included"] for item in changed_ibmi_decisions.values())
    assert changed_ceri[0].event_risk_score == 0
    assert ready_ceri[0].event_risk_score > 0
    liquid = features["LIQUIDITY"]
    changed_combined = combine_row_decision(
        ticker.raw_row, ticker.fundamental_score, ticker.technical_score, config=_config()
    )
    changed_rank = rank_profile(
        profile=profile,
        rows=[ticker.raw_row],
        fundamentals={"MSFT": ticker.fundamental_score},
        technicals={"MSFT": ticker.technical_score},
        config=_config(),
        liquidity_features={"MSFT": liquid},
    )
    assert not changed_combined.has_technical and not changed_rank[0].has_technical
    changed_setup = SetupLifecycleSnapshotBuilder().build(setup).dto
    assert changed_setup.promoted_fields["dual_score"] is None
    assert changed_setup.promoted_fields["market_regime"] is None
    assert changed_setup.signals["sector_rank"]["value"] is None
    model = SetupSignalSnapshot()
    SetupLifecycleRepository()._apply_snapshot_fields(model, changed_setup)
    normalized = normalized_snapshot_from_row(model)
    lifecycle = SetupLifecycleEngine().evaluate(LifecycleEvaluationInput(snapshot=normalized))
    actionability = SetupLifecycleActionabilityPolicy().evaluate(lifecycle, normalized)
    assert actionability.actionability.value == "BLOCKED"
    from setup_lifecycle.test_alert_service import FakeAlertRepository, _lifecycle_event

    from app.services.setup_lifecycle.alert_service import SetupLifecycleAlertService

    alert_repository = FakeAlertRepository.with_seeded_rules()
    event = _lifecycle_event(
        event_id=991,
        episode_id=992,
        to_state=lifecycle.proposed_state,
        source_event_key="t12e-invalid-world",
    )
    event.actionability_after = actionability.actionability.value
    SetupLifecycleAlertService(repository=alert_repository).evaluate_lifecycle_event(
        object(), event
    )
    assert not any(alert.severity == "ACTIONABLE" for alert in alert_repository.alerts)
    with pytest.raises(WinnerSourceEligibilityError) as caught:
        _acquire(context, cutoff)
    assert {"technical", "ranking"} <= set(caught.value.sources)
    rejected = winner_service.capture_run(_session(), run_id=7, market_cutoff=cutoff)
    assert rejected.excluded == 1 and rejected.failed == 0
    assert len(winner_repository.predictions) == 1
    assert (prediction.feature_json, prediction.feature_vector_hash) == frozen_prediction
    assert ready_setup.promoted_fields["dual_score"] is not None
    assert frozen == ready_acquisition.consumer_eligibility.canonical_payload()
    assert positive == (
        ticker.technical_score.dual_score,
        ticker.ranking_results[0].profile_score,
        context.market_regime_snapshot.score,
        ticker.sector_row.current_rank,
    )
