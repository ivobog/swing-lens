from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, selectinload
from test_combined_ranking_identity_adoption import _cutoff, _identity_aware_sources
from test_ranking_profile_engine import _config

from alembic import command
from app.models.ceri_tables import CeriCompany
from app.models.ib_market_intelligence_tables import IBIntelligenceFeature
from app.models.tables import (
    CoreCalculationEvidence,
    CoreCalculationEvidenceSource,
    MarketCalculationContext,
    SectorRotationRow,
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvent,
    SetupSignalSnapshot,
    SignalAlertEvent,
    UploadRun,
)
from app.services.ceri import capture_service as capture
from app.services.ceri.confidence_service import CeriConfidenceService
from app.services.ceri.config import load_ceri_config
from app.services.ceri.event_risk_service import CeriEventRiskService
from app.services.ceri.opportunity_score_service import CeriOpportunityScoreService
from app.services.ceri.snapshot_service import CeriSnapshotService
from app.services.combined_ranking_identity import calculation_identity_from_debug
from app.services.contextual_calculation_identity import (
    SETUP_REGIME_COMPATIBILITY,
    build_contextual_result_identity,
    build_regime_identity,
    consumer_context_identity,
    expected_regime_identity,
    identity_metadata,
)
from app.services.contextual_consumer_eligibility import (
    CONTEXTUAL_ELIGIBILITY_KEY,
    IBMI_LIQUIDITY_TO_RANKING,
    REGIME_TO_SETUP,
    ContextualConsumerPolicy,
    contextual_decision_input,
    setup_with_contextual_permission,
)
from app.services.core_calculation_evidence import CoreEvidenceKind, persist_core_evidence
from app.services.ib_market_intelligence.calculations import options_event_premium_score
from app.services.ib_market_intelligence.config import load_ib_market_intelligence_config
from app.services.ib_market_intelligence.decision_evidence import IbmiFeatureConstituents
from app.services.ib_market_intelligence.dtos import FeatureResult
from app.services.ib_market_intelligence.repository import persist_feature
from app.services.market_clock_service import MarketClockService
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.market_regime_repository import MarketRegimeRepository, MarketRegimeSnapshotWrite
from app.services.ranking_profile_config import TradeabilityOverlayConfig, get_ranking_profile
from app.services.ranking_profile_engine import rank_single_row
from app.services.ranking_profile_service import _to_ranking_model
from app.services.sector_rotation_repository import (
    SectorRotationRepository,
    SectorRotationRowWrite,
    SectorRotationSnapshotWrite,
)
from app.services.setup_lifecycle.alert_service import (
    SetupLifecycleAlertService,
    _event_market_regime,
)
from app.services.setup_lifecycle.decision_evidence import (
    persist_lifecycle_evaluation_evidence,
    persist_setup_evidence,
)
from app.services.setup_lifecycle.dtos import ActionabilityDecision, LifecycleDecision
from app.services.setup_lifecycle.enums import (
    Actionability,
    ConfidenceLabel,
    LifecycleState,
)
from app.services.setup_lifecycle.episode_service import (
    SetupLifecycleEpisodeService,
    normalized_snapshot_from_row,
)
from app.services.setup_lifecycle.lifecycle_engine import (
    LifecycleEvaluationInput,
    SetupLifecycleEngine,
)
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.setup_lifecycle.source_loader import (
    TickerSourceContext,
    _select_compatible_context_candidate,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_native_contextual_permissions_sources_retry_history_and_lifecycle(
    disposable_postgres_database,
    monkeypatch,
):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    monkeypatch.setattr(
        capture,
        "get_settings",
        lambda: SimpleNamespace(
            ib_market_intelligence_enabled=True,
            ib_volatility_intelligence_enabled=True,
            ib_short_pressure_enabled=True,
        ),
    )
    native_cutoff = (
        MarketClockService()
        .cutoff_for(_cutoff().cutoff_at, reason="T12C_NATIVE_POSTGRES")
        .with_context_id(17)
    )
    row, fundamental, technical, cutoff = _identity_aware_sources(
        cutoff=native_cutoff, seal_fundamental=False
    )
    technical.insufficient_data, technical.data_quality_score = False, 9
    with Session(engine, expire_on_commit=False) as db:
        db.add_all(
            [
                UploadRun(id=7, filename="t12c.csv", status="COMPLETED"),
                UploadRun(id=99, filename="cross-run.csv", status="COMPLETED"),
                CeriCompany(id=1, ticker=row.ticker),
            ]
        )
        db.flush()
        db.add(
            MarketCalculationContext(
                id=cutoff.context_id,
                upload_run_id=7,
                cutoff_at=cutoff.cutoff_at,
                exchange_timezone=cutoff.exchange_timezone,
                latest_completed_session=cutoff.latest_completed_session,
                daily_bar_ready_at=cutoff.daily_bar_ready_at,
                calendar_version=cutoff.calendar_version,
                bar_readiness_version=cutoff.bar_readiness_version,
                cutoff_reason=cutoff.cutoff_reason,
            )
        )
        db.flush()
        db.add_all([row, fundamental, technical])
        db.flush()
        for kind, source in (
            (CoreEvidenceKind.FUNDAMENTAL, fundamental),
            (CoreEvidenceKind.TECHNICAL, technical),
        ):
            persist_core_evidence(db, kind=kind, current_row=source)
        features = {
            module: _feature(db, row.ticker, module, cutoff)
            for module in ("LIQUIDITY", "VOLATILITY", "SHORT_PRESSURE")
        }
        # Real cohort evidence preload is bounded, then repeated policy/profile
        # evaluation has no evidence SELECTs.
        for ticker in ("PEER1", "PEER2", "PEER3"):
            for module in features:
                _feature(db, ticker, module, cutoff)
        db.commit()
        loaded = list(
            db.scalars(
                select(IBIntelligenceFeature).options(
                    selectinload(IBIntelligenceFeature.calculation_evidence),
                )
            )
        )
        from app.models.tables import FundamentalScore, TechnicalScore

        fundamental = db.scalars(
            select(FundamentalScore).options(selectinload(FundamentalScore.calculation_evidence))
        ).one()
        technical = db.scalars(
            select(TechnicalScore).options(selectinload(TechnicalScore.calculation_evidence))
        ).one()
        selects = []

        def count(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        event.listen(engine, "before_cursor_execute", count)
        for feature in loaded:
            if feature.module == "LIQUIDITY":
                contextual_decision_input(feature, IBMI_LIQUIDITY_TO_RANKING)
        assert selects == []
        profile = replace(
            get_ranking_profile("momentum_swing"),
            tradeability_overlay=TradeabilityOverlayConfig(
                enabled=True,
                poor_penalty=0.5,
                very_poor_penalty=1,
                maximum_penalty=0.75,
            ),
        )
        for _ in range(7):
            rank_single_row(
                profile=profile,
                row=row,
                fundamental=fundamental,
                technical=technical,
                config=_config(),
                liquidity_feature=features["LIQUIDITY"],
            )
        assert selects == []
        event.remove(engine, "before_cursor_execute", count)
        decision = rank_single_row(
            profile=profile,
            row=row,
            fundamental=fundamental,
            technical=technical,
            config=_config(),
            liquidity_feature=features["LIQUIDITY"],
        )
        assert decision.penalties["ibkr_tradeability"] == 0.75
        ranking_model = _to_ranking_model(
            7,
            decision,
            calculation_identity_from_debug(technical.debug_json),
        )
        ranking = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.RANKING,
            current_row=ranking_model,
            sources={
                "technical": technical,
                "fundamental": fundamental,
                "ibmi_liquidity": features["LIQUIDITY"],
            },
        )
        ceri = _ceri(db, row.ticker, cutoff, features)
        assert all(
            permission["included"]
            for permission in ceri.evidence_lineage_json[CONTEXTUAL_ELIGIBILITY_KEY].values()
        )
        regime_config = load_market_regime_command_center_config()
        regime_identity = build_regime_identity(
            market_cutoff=cutoff,
            config=regime_config,
            run_id=99,
            pipeline_id=None,
            source_payload={"SPY": "frozen-benchmark"},
        )
        regime_write = MarketRegimeSnapshotWrite(
            as_of_date=cutoff.latest_completed_session,
            calculation_version=regime_config.calculation_version,
            config_version=regime_config.config_version,
            regime="RISK_ON",
            risk_state="Green",
            score=8,
            risk_off=False,
            gate_ok=True,
            confidence="normal",
            action_summary="Constructive",
            position_size_multiplier=1,
            debug={
                "input_symbols": {"primary_market": "SPY"},
                "market_inputs": {"SPY": {"insufficient_data": False}},
                "temporal_lineage": {
                    "calculation_cutoff_at": cutoff.cutoff_at.isoformat(),
                    "input_as_of_session": cutoff.latest_completed_session.isoformat(),
                    "calendar_version": cutoff.calendar_version,
                },
                **identity_metadata(regime_identity, policy="T12C_TEST"),
            },
        )
        object.__setattr__(
            regime_write,
            "_effective_configuration",
            regime_config._effective_configuration.snapshot,
        )
        regime = MarketRegimeRepository().upsert_snapshot(db, regime_write, run_id=99)
        selected = _select_compatible_context_candidate(
            (regime,),
            cutoff.latest_completed_session,
            7,
            expected=expected_regime_identity(market_cutoff=cutoff, config=regime_config),
            policy=SETUP_REGIME_COMPATIBILITY,
            allow_cross_run=True,
        )
        assert selected is regime and contextual_decision_input(selected, REGIME_TO_SETUP)[0]
        sector_identity = build_contextual_result_identity(
            base=consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=None),
            namespace="sector-context",
            config_hash="a" * 64,
            calculation_version="sector-rotation-1.0.0",
            engine_version="sector-rotation-1.0.0",
            source_artifacts=[],
            source_payload={"ranking": ranking.id},
        )
        sector_write = SectorRotationSnapshotWrite(
            run_id=7,
            as_of_date=cutoff.latest_completed_session,
            calculation_version="sector-rotation-1.0.0",
            mode="universe_only",
            config_hash="a" * 64,
            market_regime_snapshot_id=regime.id,
            sector_count=1,
            ticker_count=1,
            debug=identity_metadata(sector_identity, policy="T12C_TEST"),
            rows=[
                SectorRotationRowWrite(
                    sector="Technology",
                    sector_slug="technology",
                    confidence="normal",
                    rotation_state="LEADING",
                    sector_permission="ALLOW",
                    current_rank=1,
                    sector_final_score=8,
                )
            ],
        )
        sector = SectorRotationRepository().save_snapshot(
            db,
            sector_write,
            evidence_sources={
                "regime": regime,
                "ranking:ACME": SimpleNamespace(evidence_id=ranking.id),
            },
        )
        sector_row = db.scalar(
            select(SectorRotationRow).where(SectorRotationRow.snapshot_id == sector.id)
        )
        from setup_lifecycle.test_snapshot_builder import _bar

        context = TickerSourceContext(
            raw_row=row,
            fundamental_score=fundamental,
            technical_score=technical,
            market_regime_snapshot=regime,
            sector_rotation_snapshot=sector,
            sector_rotation_row=sector_row,
            price_bars=(_bar(cutoff.latest_completed_session, close=101),),
            market_cutoff=cutoff,
        )
        snapshot = _setup(db, context)
        historical = {
            evidence.id: deepcopy(evidence.payload_json)
            for evidence in (
                ranking,
                db.get(CoreCalculationEvidence, ceri.evidence_id),
                db.get(CoreCalculationEvidence, snapshot.evidence_id),
            )
        }
        earlier_family = SetupLifecycleEngine().evaluate(
            LifecycleEvaluationInput(
                snapshot=normalized_snapshot_from_row(snapshot),
            )
        )
        old_decision = LifecycleDecision(
            setup_family=earlier_family.setup_family,
            phase_code=earlier_family.phase_code,
            previous_state=None,
            proposed_state=LifecycleState.READY,
            actionability_candidate=Actionability.ACTIONABLE,
            confidence_score=90,
            confidence_label=ConfidenceLabel.HIGH,
            reason_codes=("PREVIOUS_VALID_READY",),
            evidence={},
        )
        old_evaluation = persist_lifecycle_evaluation_evidence(
            db,
            snapshot=snapshot,
            episode=None,
            decision=old_decision,
            actionability=ActionabilityDecision(Actionability.ACTIONABLE, ("GATES_PASS",)),
            evaluation_run_id=None,
            transition_eligible=True,
        )
        episode = SetupLifecycleEpisode(
            ticker=row.ticker,
            timeframe="1d",
            setup_family=earlier_family.setup_family.value,
            status="ACTIVE",
            engine_version=snapshot.engine_version,
            config_version=snapshot.config_version,
            config_hash=snapshot.config_hash,
            current_state="READY",
            current_phase=earlier_family.phase_code,
            current_actionability="ACTIONABLE",
            opened_on=snapshot.data_as_of_date,
            last_observed_on=snapshot.data_as_of_date,
            current_as_of_date=snapshot.data_as_of_date,
            state_entered_on=snapshot.data_as_of_date,
            current_snapshot_id=snapshot.id,
            confidence_score=90,
            confidence_label="HIGH",
            state_age_sessions=0,
            missing_observation_sessions=0,
            latest_evaluation_evidence_id=old_evaluation.id,
        )
        db.add(episode)
        db.flush()
        old_payload = deepcopy(old_evaluation.payload_json)
        stale_write = replace(regime_write, warnings=["severely_stale_market_data"])
        object.__setattr__(
            stale_write, "_effective_configuration", regime_config._effective_configuration.snapshot
        )
        stale = MarketRegimeRepository().upsert_snapshot(
            db,
            stale_write,
            run_id=99,
        )
        # New global READY evidence cannot replace the exact selected stale source.
        global_ready = MarketRegimeRepository().upsert_snapshot(db, regime_write, run_id=None)
        assert global_ready.evidence_id != stale.evidence_id
        new_sector = SectorRotationRepository().save_snapshot(
            db,
            replace(sector_write, rows=[replace(sector_write.rows[0], confidence="insufficient")]),
            evidence_sources={
                "regime": stale,
                "ranking:ACME": SimpleNamespace(evidence_id=ranking.id),
            },
        )
        new_row = db.scalar(
            select(SectorRotationRow).where(SectorRotationRow.snapshot_id == new_sector.id)
        )
        newer = _setup(
            db,
            replace(
                context,
                market_regime_snapshot=stale,
                sector_rotation_snapshot=new_sector,
                sector_rotation_row=new_row,
            ),
        )
        decisions = newer.source_lineage_json[CONTEXTUAL_ELIGIBILITY_KEY]
        assert not decisions["regime"]["included"] and not decisions["sector"]["included"]
        assert decisions["regime"]["decision"]["producer_evidence_id"] == stale.evidence_id
        service = SetupLifecycleEpisodeService()
        result = service.apply_snapshot(db, newer, preloaded_episodes=(episode,))
        assert db.get(SetupLifecycleEvaluationEvidence, episode.latest_evaluation_evidence_id)
        assert old_evaluation.payload_json == old_payload
        assert result.decision.proposed_state not in {
            LifecycleState.TRIGGERED,
            LifecycleState.CONFIRMED,
        }
        assert result.lifecycle_event is None
        # Optional missing context may permit independently qualified Technical
        # actionability; it cannot restore the omitted contextual signals or votes.
        normalized = setup_with_contextual_permission(normalized_snapshot_from_row(newer))
        assert normalized.signals["market_gate"].raw_value is None
        assert normalized.signals["sector_rank"].raw_value is None
        assert normalized.source_lineage["market_regime_as_of"] is None
        original_signals, original_lineage = newer.signals_json, newer.source_lineage_json
        newer.signals_json = {**original_signals, "market_regime": {"value": "RISK_ON"}}
        newer.source_lineage_json = {}
        assert _event_market_regime(SetupLifecycleEvent(evidence_json={}), newer, db=db) is None
        newer.signals_json, newer.source_lineage_json = original_signals, original_lineage
        alerts = SetupLifecycleAlertService()
        alerts.seed_builtin_rules(db)
        alerts.evaluate_episode_result(db, result)
        assert not db.scalars(
            select(SignalAlertEvent).where(
                SignalAlertEvent.evidence_json["actionability_after"].astext == "ACTIONABLE",
            )
        ).all()
        original_ibmi = features["LIQUIDITY"]
        invalid = _feature(db, row.ticker, "LIQUIDITY", cutoff, coverage="UNAVAILABLE")
        omitted = rank_single_row(
            profile=profile,
            row=row,
            fundamental=fundamental,
            technical=technical,
            config=_config(),
            liquidity_feature=invalid,
        )
        missing = rank_single_row(
            profile=profile, row=row, fundamental=fundamental, technical=technical, config=_config()
        )
        assert replace(omitted, debug=missing.debug) == missing
        blocked_vol = _feature(db, row.ticker, "VOLATILITY", cutoff, coverage="UNAVAILABLE")
        db.add(UploadRun(id=8, filename="t12c-next.csv", status="COMPLETED"))
        db.flush()
        db.add(
            MarketCalculationContext(
                id=18,
                upload_run_id=8,
                cutoff_at=cutoff.cutoff_at,
                latest_completed_session=cutoff.latest_completed_session,
                exchange_timezone=cutoff.exchange_timezone,
                daily_bar_ready_at=cutoff.daily_bar_ready_at,
                calendar_version=cutoff.calendar_version,
                bar_readiness_version=cutoff.bar_readiness_version,
                cutoff_reason=cutoff.cutoff_reason,
            )
        )
        db.flush()
        mixed = _ceri(
            db,
            row.ticker,
            cutoff.with_context_id(18),
            {**features, "VOLATILITY": blocked_vol},
            run_id=8,
        )
        permissions = mixed.evidence_lineage_json[CONTEXTUAL_ELIGIBILITY_KEY]
        assert not permissions["ibmi_volatility"]["included"]
        assert permissions["ibmi_short_pressure"]["included"]
        mixed_evidence = db.get(CoreCalculationEvidence, mixed.evidence_id)
        assert mixed_evidence.source_evidence_ids_json["ibmi_volatility"] == blocked_vol.evidence_id
        with monkeypatch.context() as changed:
            for module, name, producer, consumer, family in (
                (
                    "app.services.ranking_profile_engine",
                    "IBMI_LIQUIDITY_TO_RANKING",
                    "IBMI",
                    "RANKING",
                    "LIQUIDITY",
                ),
                (
                    "app.services.setup_lifecycle.snapshot_builder",
                    "REGIME_TO_SETUP",
                    "REGIME",
                    "SETUP",
                    None,
                ),
                (
                    "app.services.setup_lifecycle.snapshot_builder",
                    "SECTOR_TO_SETUP",
                    "SECTOR",
                    "SETUP",
                    None,
                ),
                (
                    "app.services.ceri.capture_service",
                    "IBMI_VOLATILITY_TO_CERI",
                    "IBMI",
                    "CERI",
                    "VOLATILITY",
                ),
                (
                    "app.services.ceri.capture_service",
                    "IBMI_SHORT_PRESSURE_TO_CERI",
                    "IBMI",
                    "CERI",
                    "SHORT_PRESSURE",
                ),
            ):
                changed.setattr(
                    f"{module}.{name}",
                    ContextualConsumerPolicy(
                        producer,
                        consumer,
                        "prospective-v2-test",
                        family,
                    ),
                )
            for evidence_id, payload in historical.items():
                assert db.get(CoreCalculationEvidence, evidence_id).payload_json == payload
            repeat = service.apply_snapshot(db, newer, preloaded_episodes=(episode,))
            assert repeat.decision.proposed_state == result.decision.proposed_state
        retry = persist_core_evidence(
            db,
            kind=CoreEvidenceKind.RANKING,
            current_row=ranking_model,
            sources={
                "technical": technical,
                "fundamental": fundamental,
                "ibmi_liquidity": original_ibmi,
            },
        )
        assert retry.id == ranking.id
        assert db.scalar(
            select(CoreCalculationEvidenceSource).where(
                CoreCalculationEvidenceSource.evidence_id == newer.evidence_id,
                CoreCalculationEvidenceSource.source_evidence_id == stale.evidence_id,
            )
        )
        db.commit()
    with Session(engine) as db:
        for evidence_id, payload in historical.items():
            assert db.get(CoreCalculationEvidence, evidence_id).payload_json == payload
    engine.dispose()


def _feature(db, ticker, module, cutoff, *, coverage="AVAILABLE"):
    return persist_feature(
        db,
        ticker=ticker,
        ib_conid=None,
        as_of_session=cutoff.latest_completed_session,
        calculated_at=cutoff.cutoff_at,
        calculation_cutoff_at=cutoff.cutoff_at,
        config=load_ib_market_intelligence_config(),
        constituents=IbmiFeatureConstituents(),
        feature=FeatureResult(
            module=module,
            classification="VERY_POOR" if module == "LIQUIDITY" else "HIGH",
            score=8,
            confidence="HIGH",
            freshness_status="AVAILABLE",
            coverage_status=coverage,
            components={"dollar_volume": 2_000_000, "iv_hv_ratio": 2.5},
            evidence_hashes=("a" * 64,),
        ),
    )[0]


def _setup(db, context):
    snapshot = SetupSignalSnapshot()
    SetupLifecycleRepository()._apply_snapshot_fields(
        snapshot,
        SetupLifecycleSnapshotBuilder().build(context).dto,
    )
    db.add(snapshot)
    db.flush()
    assert persist_setup_evidence(db, snapshot)
    return snapshot


def _ceri(db, ticker, cutoff, features, *, run_id=7):
    permissions = {}
    kwargs = dict(
        as_of_session=cutoff.latest_completed_session,
        market_cutoff=cutoff,
        ibmi_config=load_ib_market_intelligence_config(),
        decisions=permissions,
    )
    vol = capture._point_in_time_volatility_feature(
        db,
        ticker,
        cutoff.cutoff_at,
        candidates=(features["VOLATILITY"],),
        **kwargs,
    )
    short = capture._point_in_time_short_pressure_feature(
        db,
        ticker,
        cutoff.cutoff_at,
        candidates=(features["SHORT_PRESSURE"],),
        **kwargs,
    )
    config = load_ceri_config()
    base = consumer_context_identity(
        market_cutoff=cutoff, run_id=run_id, pipeline_id=None, ticker=ticker, company_id=1
    )
    identity = build_contextual_result_identity(
        base=base,
        namespace="ceri-context",
        config_hash=config.config_hash,
        calculation_version=config.engine.calculation_version,
        engine_version=config.engine.calculation_version,
        source_artifacts=[],
        source_payload=permissions,
        company_id=1,
    )
    identity = config._effective_configuration.bind(identity)
    lineage = {
        CONTEXTUAL_ELIGIBILITY_KEY: permissions,
        "historical_view_mode": "AS_KNOWN",
        "ib_context_selected_feature_ids": [
            feature.id
            for feature in (
                features["VOLATILITY"],
                features["SHORT_PRESSURE"],
            )
        ],
        **identity_metadata(identity, policy="T12C_TEST"),
    }
    service = CeriSnapshotService(config)
    snapshot = service.build_snapshot(
        run_id=run_id,
        company_id=1,
        ticker=ticker,
        as_of_session=cutoff.latest_completed_session,
        cutoff_at=cutoff.cutoff_at,
        opportunity=CeriOpportunityScoreService(config).calculate(revision_features=[]),
        confidence=CeriConfidenceService(config).calculate(
            revision_features=[],
            as_of_session=cutoff.latest_completed_session,
        ),
        event_risk=CeriEventRiskService(config).calculate(
            as_of_session=cutoff.latest_completed_session,
            options_event_premium_score=options_event_premium_score(vol) if vol else None,
            short_pressure_classification=short.classification if short else None,
        ),
        source_ids=[],
        evidence_lineage=lineage,
    )
    return service.persist_snapshot(db, snapshot)
