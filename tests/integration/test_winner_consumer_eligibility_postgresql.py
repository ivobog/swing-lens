"""Native Winner acquisition, evidence preload and frozen SQL persistence."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from alembic.config import Config
from contextual_readiness_helpers import configuration_for_source
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    CombinedResult,
    CoreCalculationEvidence,
    FundamentalScore,
    MarketCalculationContext,
    MarketRegimeSnapshot,
    PipelineRun,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    TechnicalScore,
    TransitionDecisionHandoffManifest,
    TransitionPreflightPlan,
    UploadRun,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionEpisode,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.combined_ranking_identity import embed_calculation_identity
from app.services.contextual_calculation_identity import (
    build_contextual_result_identity,
    build_regime_identity,
    consumer_context_identity,
    expected_sector_identity,
)
from app.services.contextual_consumer_eligibility import ContextualConsumerPolicy
from app.services.core_calculation_evidence import (
    CoreEvidenceKind,
    calculation_evidence_payload,
    get_current_readiness,
    persist_core_evidence,
)
from app.services.market_clock_service import MarketClockService
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.sector_rotation_config import (
    load_sector_rotation_config,
    sector_rotation_config_hash,
)
from app.services.technical_consumer_eligibility import TechnicalConsumerPolicy
from app.services.winner_probability.calculation_identity import (
    acquire_winner_sources,
    semantic_artifact_identity,
)
from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.consumer_eligibility import WINNER_ELIGIBILITY_KEY
from app.services.winner_probability.probability_estimator import ProbabilityEstimator
from app.services.winner_probability.repository import WinnerProbabilityRepository

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_native_sector_dependencies_freeze_permissions_and_advance_current(
    disposable_postgres_database,
):
    from app.services.contextual_consumer_eligibility import CONTEXTUAL_ELIGIBILITY_KEY
    from app.services.sector_rotation_service import SectorRotationService

    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    cutoff = _seed(engine, "ready")
    service = SectorRotationService()
    with Session(engine, expire_on_commit=False) as db:
        first = service.build_sector_rotation_snapshot(
            db, run_id=7, market_cutoff=cutoff, persist=True
        )
        permissions = first.universe_rows[0].debug[CONTEXTUAL_ELIGIBILITY_KEY]
        assert len(permissions) == 3 and all(
            item["decision"]["status"] == "ELIGIBLE" for item in permissions.values()
        )
        assert first.debug[CONTEXTUAL_ELIGIBILITY_KEY]["regime"]["included"]
        db.commit()
        projection = db.scalar(
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.run_id == 7)
            .order_by(SectorRotationSnapshot.created_at.desc(), SectorRotationSnapshot.id.desc())
            .limit(1)
        )
        first_id = projection.evidence_id
        first_payload = deepcopy(db.get(CoreCalculationEvidence, first_id).payload_json)
        assert "debug" in first_payload, [
            (item.id, item.created_at, item.evidence_id, list(item.debug_json or {}))
            for item in db.scalars(select(SectorRotationSnapshot))
        ]
        assert first_payload["debug"][CONTEXTUAL_ELIGIBILITY_KEY]["regime"]["included"]
        for model, source_id, kind, attr, value in (
            (TechnicalScore, 31, CoreEvidenceKind.TECHNICAL, "insufficient_data", True),
            (CombinedResult, 41, CoreEvidenceKind.COMBINED, "is_complete", False),
            (RankingResult, 51, CoreEvidenceKind.RANKING, "is_complete", False),
            (
                MarketRegimeSnapshot,
                61,
                CoreEvidenceKind.REGIME,
                "warnings_json",
                ["severely_stale_market_data"],
            ),
        ):
            source = db.get(model, source_id)
            setattr(source, attr, value)
            persist_core_evidence(
                db,
                kind=kind,
                current_row=source,
                effective_configuration=configuration_for_source(source)
                if kind in {CoreEvidenceKind.REGIME, CoreEvidenceKind.SECTOR}
                else None,
            )
        db.commit()
        second = service.build_sector_rotation_snapshot(
            db, run_id=7, market_cutoff=cutoff, persist=True
        )
        permissions = second.universe_rows[0].debug[CONTEXTUAL_ELIGIBILITY_KEY]
        assert len(permissions) == 3 and all(
            item["decision"]["status"] != "ELIGIBLE" for item in permissions.values()
        )
        assert not second.debug[CONTEXTUAL_ELIGIBILITY_KEY]["regime"]["included"]
        assert second.universe_rows[0].average_technical_score is None
        assert second.universe_rows[0].average_final_score is None
        db.commit()
        projection = db.scalar(
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.run_id == 7)
            .order_by(SectorRotationSnapshot.created_at.desc(), SectorRotationSnapshot.id.desc())
            .limit(1)
        )
        assert projection.evidence_id != first_id
        assert db.get(CoreCalculationEvidence, first_id).payload_json == first_payload
        frozen_second = deepcopy(
            db.get(CoreCalculationEvidence, projection.evidence_id).payload_json
        )
        retry = service.build_sector_rotation_snapshot(
            db, run_id=7, market_cutoff=cutoff, persist=True
        )
        db.commit()
        assert retry.rows == second.rows
        assert db.get(CoreCalculationEvidence, projection.evidence_id).payload_json == frozen_second
    engine.dispose()


@pytest.mark.parametrize(
    "mode",
    [
        "ready",
        "technical_insufficient",
        "ranking_incomplete",
        "optional_blocked",
        "fundamental_degraded",
        "combined_incomplete",
        "regime_sparse",
    ],
)
def test_native_winner_permissions_atomic_retry_and_frozen_history(
    disposable_postgres_database,
    monkeypatch,
    mode,
):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    cutoff = _seed(engine, mode)
    repository = WinnerProbabilityRepository()
    service = WinnerPredictionCaptureService(repository=repository)
    winner_config = load_winner_probability_config()
    with Session(engine, expire_on_commit=False) as db:
        context = repository.load_run_context(db, 7, market_cutoff=cutoff)
        from integration.test_contextual_consumer_eligibility_postgresql import _setup

        from app.services.contextual_consumer_eligibility import CONTEXTUAL_ELIGIBILITY_KEY
        from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
        from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader

        setup_context = SetupLifecycleSourceLoader().load_run_context(db, 7, market_cutoff=cutoff)
        assert setup_context.tickers[0].combined_result is not None
        evidence_selects = []

        def count_setup_evidence(_conn, _cursor, statement, _params, _context, _many):
            if (
                statement.lstrip().upper().startswith("SELECT")
                and "core_calculation_evidence" in statement
            ):
                evidence_selects.append(statement)

        event.listen(engine, "before_cursor_execute", count_setup_evidence)
        try:
            for _ in range(7):
                SetupLifecycleSnapshotBuilder().build(setup_context.tickers[0])
        finally:
            event.remove(engine, "before_cursor_execute", count_setup_evidence)
        assert evidence_selects == []
        setup_snapshot = _setup(db, setup_context.tickers[0])
        setup_permissions = setup_snapshot.source_lineage_json[CONTEXTUAL_ELIGIBILITY_KEY]
        assert setup_permissions["fundamental"]["included"] == (mode != "fundamental_degraded")
        assert setup_permissions["combined"]["included"] == (mode != "combined_incomplete")
        setup_payload = db.get(CoreCalculationEvidence, setup_snapshot.evidence_id).payload_json
        assert setup_payload["source_lineage_json"][CONTEXTUAL_ELIGIBILITY_KEY] == setup_permissions
        selects = []

        def count(_conn, _cursor, statement, _params, _context, _many):
            if statement.lstrip().upper().startswith("SELECT"):
                selects.append(statement)

        if mode not in {"technical_insufficient", "ranking_incomplete", "combined_incomplete"}:
            event.listen(engine, "before_cursor_execute", count)
            try:
                for _ in range(7):
                    acquisition = acquire_winner_sources(
                        context,
                        context.tickers[0],
                        run_id=7,
                        market_cutoff=cutoff,
                        winner_config=winner_config,
                    )
                    assert acquisition.consumer_eligibility.canonical_payload()["technical"][
                        "included"
                    ]
            finally:
                event.remove(engine, "before_cursor_execute", count)
            assert selects == []
        result = service.capture_run(db, run_id=7, market_cutoff=cutoff)
        counts = {
            model: db.scalar(select(func.count()).select_from(model))
            for model in (
                WinnerPredictionSnapshot,
                WinnerPredictionEpisode,
                WinnerForwardOutcome,
                WinnerTargetStopOutcome,
                WinnerProbabilityEstimate,
            )
        }
        if mode in {"technical_insufficient", "ranking_incomplete", "combined_incomplete"}:
            assert result.excluded == 1 and result.failed == 0
            assert all(value == 0 for value in counts.values())
            metadata = result.readiness_rejections[0][WINNER_ELIGIBILITY_KEY]
            assert len(metadata) == 6
            assert (
                metadata[
                    {
                        "technical_insufficient": "technical",
                        "ranking_incomplete": "ranking",
                        "combined_incomplete": "combined",
                    }[mode]
                ]["decision"]["status"]
                == "INELIGIBLE"
            )
            if mode == "technical_insufficient":
                assert result.exclusion_reasons == {"insufficient_completed_bars": 1}
            rejected_retry = service.capture_run(db, run_id=7, market_cutoff=cutoff)
            assert rejected_retry.excluded == 1 and rejected_retry.failed == 0
            assert rejected_retry.readiness_rejections == result.readiness_rejections
            assert all(db.scalar(select(func.count()).select_from(model)) == 0 for model in counts)
            engine.dispose()
            return
        assert result.inserted == 1 and result.failed == 0, result.as_dict()
        prediction = db.scalar(select(WinnerPredictionSnapshot))
        assert counts[WinnerPredictionEpisode] == 1
        assert counts[WinnerForwardOutcome] == 10 and counts[WinnerTargetStopOutcome] == 2
        frozen = deepcopy(prediction.feature_json), deepcopy(prediction.lineage_json)
        decisions = frozen[1][WINNER_ELIGIBILITY_KEY]
        exact_rows = {
            "fundamental": db.get(FundamentalScore, 21),
            "combined": db.get(CombinedResult, 41),
            "technical": db.get(TechnicalScore, 31),
            "ranking": db.get(RankingResult, 51),
            "regime": db.get(MarketRegimeSnapshot, 61),
            "sector": db.get(SectorRotationSnapshot, 71),
        }
        for name, source in exact_rows.items():
            policy_revision = 2 if name in {"ranking", "combined", "regime", "sector"} else 1
            assert decisions[name]["decision"]["producer_evidence_id"] == source.evidence_id
            assert decisions[name]["producer_readiness"]["evidence_id"] == source.evidence_id
            assert decisions[name]["source_id"] == source.id
            assert (
                decisions[name]["decision"]["policy_version"]
                == f"{name}-to-winner-v{policy_revision}"
            )
        regime_included = mode not in {"optional_blocked", "regime_sparse"}
        assert decisions["regime"]["included"] == regime_included
        assert decisions["sector"]["included"] == (mode != "optional_blocked")
        assert decisions["fundamental"]["included"] == (mode != "fundamental_degraded")
        if mode == "fundamental_degraded":
            assert prediction.feature_json["fundamental_score"] is None
            assert prediction.feature_json["fundamental_coverage"] is None
            assert decisions["fundamental"]["decision"]["status"] == "POLICY_UNDECIDED"
        if mode == "regime_sparse":
            assert exact_rows["regime"].confidence == "normal" and exact_rows["regime"].score == 8
            assert decisions["regime"]["producer_readiness"]["status"] == "INSUFFICIENT_EVIDENCE"
            assert (
                "REGIME_INSUFFICIENT_PRIMARY"
                in decisions["regime"]["producer_readiness"]["blocking_reasons"]
            )
        assert prediction.feature_json["market_regime"] == (
            "Confirmed Uptrend" if regime_included else None
        )
        assert prediction.feature_json["sector_state"] == (
            "Leading" if mode != "optional_blocked" else None
        )
        assert prediction.source_ids_json.get("market_regime_snapshot_id") == (
            61 if regime_included else None
        )
        retry = service.capture_run(db, run_id=7, market_cutoff=cutoff)
        assert retry.duplicate == 1 and retry.inserted == 0
        assert {
            model: db.scalar(select(func.count()).select_from(model)) for model in counts
        } == counts
        from app.services.winner_probability import consumer_eligibility as policies

        with monkeypatch.context() as prospective:
            for name, producer in (
                ("TECHNICAL_TO_WINNER", "TECHNICAL"),
                ("RANKING_TO_WINNER", "RANKING"),
                ("FUNDAMENTAL_TO_WINNER", "FUNDAMENTAL"),
                ("COMBINED_TO_WINNER", "COMBINED"),
                ("REGIME_TO_WINNER", "REGIME"),
                ("SECTOR_TO_WINNER", "SECTOR"),
            ):
                version = f"{producer.lower()}-to-winner-v2-test"
                policy = (
                    TechnicalConsumerPolicy("WINNER", version)
                    if producer == "TECHNICAL"
                    else ContextualConsumerPolicy(producer, "WINNER", version)
                )
                prospective.setattr(policies, name, policy)
            future = acquire_winner_sources(
                context,
                context.tickers[0],
                run_id=7,
                market_cutoff=cutoff,
                winner_config=winner_config,
            )
            assert all(
                item["decision"]["policy_version"].endswith("v2-test")
                for item in future.consumer_eligibility.canonical_payload().values()
            )
            changed_policy_retry = service.capture_run(db, run_id=7, market_cutoff=cutoff)
            assert changed_policy_retry.failed == 1
            assert {
                model: db.scalar(select(func.count()).select_from(model)) for model in counts
            } == counts
            db.expire(prediction)
            assert (prediction.feature_json, prediction.lineage_json) == frozen
        # Advancing a current producer is legitimate; neither the handoff nor the
        # successful prediction may be edited to accommodate that new pointer.
        technical = db.get(TechnicalScore, 31)
        technical.insufficient_data = True
        persist_core_evidence(db, kind=CoreEvidenceKind.TECHNICAL, current_row=technical)
        ranking_current = db.get(RankingResult, 51)
        ranking_current.is_complete = False
        persist_core_evidence(db, kind=CoreEvidenceKind.RANKING, current_row=ranking_current)
        regime_current = db.get(MarketRegimeSnapshot, 61)
        regime_current.warnings_json = ["severely_stale_market_data"]
        persist_core_evidence(
            db,
            kind=CoreEvidenceKind.REGIME,
            current_row=regime_current,
            effective_configuration=configuration_for_source(regime_current),
        )
        sector_current = db.get(SectorRotationSnapshot, 71)
        sector_native_row = db.get(SectorRotationRow, 81)
        sector_native_row.confidence = "insufficient"
        persist_core_evidence(
            db,
            kind=CoreEvidenceKind.SECTOR,
            current_row=sector_current,
            effective_configuration=configuration_for_source(sector_current),
            payload={
                **calculation_evidence_payload(sector_current),
                "rows": [calculation_evidence_payload(sector_native_row)],
            },
        )
        db.commit()

        def forbidden(*_args, **_kwargs):
            raise AssertionError("Historical SQL operation reacquired readiness")

        from app.services.winner_probability import calculation_identity, capture_service

        monkeypatch.setattr(calculation_identity, "acquire_winner_sources", forbidden)
        monkeypatch.setattr(capture_service, "acquire_winner_sources", forbidden)
        monkeypatch.setattr(TechnicalConsumerPolicy, "evaluate", forbidden)
        monkeypatch.setattr(ContextualConsumerPolicy, "evaluate", forbidden)
        db.expire(prediction)
        assert (prediction.feature_json, prediction.lineage_json) == frozen
        definition = db.scalar(
            select(WinnerOutcomeDefinition).where(WinnerOutcomeDefinition.is_primary.is_(True))
        )
        estimate = ProbabilityEstimator().create_latest_rescore(
            db,
            prediction=prediction,
            outcome_definition=definition,
            as_of=cutoff.cutoff_at + timedelta(days=30),
        )
        assert estimate.status == "insufficient"
        db.commit()
        assert (prediction.feature_json, prediction.lineage_json) == frozen
        from winner_probability.test_outcome_service import _bars

        from app.services.winner_probability.outcome_service import OutcomeMaturationService

        bars = _bars([100, 101, 102, 102, 103], highs=[101, 102, 103, 103, 104])
        bars += _bars([200, 200, 201, 201, 202], ticker="SPY")
        bars += _bars([50, 50, 50.5, 51, 51], ticker="XLK")
        for bar in bars:
            bar.id = None  # Let the native SQL primary key allocator assign each bar.
        db.add_all(bars)
        db.commit()
        from app.models.tables import IBContract
        from app.services.winner_probability.market_data_obligation_service import (
            MarketDataObligationService,
        )

        db.add(
            IBContract(
                ticker="MSFT",
                ib_conid=81234,
                symbol="MSFT",
                local_symbol="MSFT",
                exchange="SMART",
                primary_exchange="NASDAQ",
                currency="USD",
                sec_type="STK",
                trading_class="NMS",
                resolution_status="RESOLVED",
            )
        )
        db.flush()
        due_forward = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.entry_model == "NEXT_OPEN",
                WinnerForwardOutcome.horizon_sessions == 5,
                WinnerForwardOutcome.is_current_revision.is_(True),
            )
        )
        obligations = MarketDataObligationService().ensure_for_outcomes(
            db, [due_forward], now=datetime(2026, 8, 10, 21, tzinfo=UTC)
        )
        assert obligations.satisfied == 1 and obligations.identity_blocked == 0
        db.commit()
        matured = OutcomeMaturationService().process_due_outcomes(
            db,
            now=datetime(2026, 8, 10, 21, tzinfo=UTC),
            entry_model="NEXT_OPEN",
            horizon_sessions=5,
        )
        assert matured.matured == 1 and matured.target_stop_matured == 1, matured.as_dict()
        db.commit()
        native_forward = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.entry_model == "NEXT_OPEN",
                WinnerForwardOutcome.horizon_sessions == 5,
                WinnerForwardOutcome.is_current_revision.is_(True),
            )
        )
        assert native_forward.close_return_pct == 3
        assert prediction.feature_json == frozen[0]
        assert prediction.lineage_json[WINNER_ELIGIBILITY_KEY] == frozen[1][WINNER_ELIGIBILITY_KEY]
        altered = deepcopy(prediction.lineage_json)
        altered[WINNER_ELIGIBILITY_KEY]["technical"]["included"] = False
        prediction.lineage_json = altered
        with pytest.raises(ValueError, match="IMMUTABLE_EVIDENCE_MUTATION_REJECTED"):
            db.flush()
        db.rollback()
    engine.dispose()


def _seed(engine, mode):
    from datetime import UTC, datetime

    cutoff = (
        MarketClockService()
        .cutoff_for(
            datetime(2026, 7, 31, 21, 30, tzinfo=UTC),
            reason="T12D_NATIVE_POSTGRES",
        )
        .with_context_id(71)
    )
    base = consumer_context_identity(market_cutoff=cutoff, run_id=7, pipeline_id=61, ticker="MSFT")
    created = cutoff.cutoff_at - timedelta(hours=6)
    raw = RawCompanyRow(
        id=11,
        run_id=7,
        row_number=1,
        ticker="MSFT",
        company_name="MSFT Corp",
        sector="Technology",
        sector_canonical="Technology",
        raw_json={"Symbol": "MSFT"},
        created_at=created,
    )
    fundamental = FundamentalScore(
        id=21,
        run_id=7,
        ticker="MSFT",
        fundamental_score=8.2,
        data_coverage_score=0.92,
        v2_warning_flags_json={
            "flags": ["sparse_fundamental_data"] if mode == "fundamental_degraded" else []
        },
        created_at=created,
    )
    technical = TechnicalScore(
        id=31,
        run_id=7,
        ticker="MSFT",
        dual_score=8.4,
        classification="Clean bull pullback",
        action_bias="pullback",
        reward_risk=2.1,
        technical_confidence="normal",
        insufficient_data=mode == "technical_insufficient",
        created_at=created,
    )
    combined = CombinedResult(
        id=41,
        run_id=7,
        ticker="MSFT",
        company_name="MSFT Corp",
        sector="Technology",
        final_rank=1,
        final_score=8.5,
        fundamental_score=8.2,
        technical_classification="Clean bull pullback",
        dual_score=8.4,
        combined_decision="Strong candidate",
        earnings_risk_level="low",
        is_complete=mode != "combined_incomplete",
        has_warning=False,
        created_at=created,
    )
    ranking = RankingResult(
        id=51,
        run_id=7,
        ticker="MSFT",
        ranking_profile="momentum_swing",
        ranking_label="Momentum Swing",
        profile_rank=1,
        profile_score=8.6,
        decision_label="Strong candidate",
        is_complete=mode != "ranking_incomplete",
        created_at=created,
    )
    for name, row in (
        ("fundamental", fundamental),
        ("technical", technical),
        ("combined", combined),
        ("ranking", ranking),
    ):
        identity = build_contextual_result_identity(
            base=base,
            namespace=f"winner-test-{name}",
            config_hash="a" * 64,
            calculation_version=f"{name}-1",
            engine_version=f"{name}-1",
            source_artifacts=(),
            source_payload={"source": name},
        )
        row.debug_json = embed_calculation_identity({}, identity, policy="T12D_NATIVE_TEST")
    market = MarketRegimeSnapshot(
        id=61,
        run_id=99,
        as_of_date=cutoff.latest_completed_session,
        calculation_version="mrcc-1.0.0",
        regime="Confirmed Uptrend",
        risk_state="Green",
        score=8,
        confidence="normal",
        action_summary="constructive",
        created_at=created,
        calculation_cutoff_at=cutoff.cutoff_at,
        input_as_of_session=cutoff.latest_completed_session,
        calendar_version=cutoff.calendar_version,
        warnings_json=["severely_stale_market_data"] if mode == "optional_blocked" else [],
    )
    market.debug_json = embed_calculation_identity(
        {
            "input_symbols": {"primary_market": "SPY"},
            "market_inputs": {"SPY": {"insufficient_data": mode == "regime_sparse"}},
        },
        build_regime_identity(
            market_cutoff=cutoff,
            config=load_market_regime_command_center_config(),
            run_id=99,
            pipeline_id=88,
            source_payload={"benchmark": "compatible"},
        ),
        policy="T12D_NATIVE_TEST",
    )
    sector_config = load_sector_rotation_config()
    sector_base = replace(base, subject=replace(base.subject, ticker=base.subject.company_id))
    expected = expected_sector_identity(
        context=sector_base,
        effective_configuration=sector_config.effective_configuration,
        config_hash=sector_rotation_config_hash(sector_config),
        calculation_version="sector-rotation-1.0.0",
        mode="combined"
        if sector_config.get("etf_score", {}).get("enabled", False)
        else "universe_only",
    )
    sector_identity = build_contextual_result_identity(
        base=sector_base,
        namespace="sector-rotation",
        config_hash=sector_rotation_config_hash(sector_config),
        calculation_version="sector-rotation-1.0.0",
        engine_version="sector-rotation-1.0.0",
        source_artifacts=(),
        source_payload={"mode": "universe_only"},
    )
    sector_identity = replace(
        sector_identity,
        configuration=expected.configuration,
        algorithm=replace(sector_identity.algorithm, components=expected.algorithm.components),
    )
    sector = SectorRotationSnapshot(
        id=71,
        run_id=7,
        as_of_date=cutoff.latest_completed_session,
        calculation_version="sector-rotation-1.0.0",
        config_hash=sector_rotation_config_hash(sector_config),
        mode="universe_only",
        created_at=created,
        calculation_cutoff_at=cutoff.cutoff_at,
        input_as_of_session=cutoff.latest_completed_session,
        calendar_version=cutoff.calendar_version,
        debug_json=embed_calculation_identity({}, sector_identity, policy="T12D_NATIVE_TEST"),
    )
    sector_row = SectorRotationRow(
        id=81,
        snapshot_id=71,
        sector="Technology",
        sector_slug="technology",
        rotation_state="Leading",
        sector_permission="full_allowed",
        current_rank=1,
        confidence="insufficient" if mode == "optional_blocked" else "high",
    )
    with Session(engine) as db:
        db.add_all(
            [
                UploadRun(
                    id=7,
                    filename="t12d.csv",
                    status="COMPLETED",
                    uploaded_at=created,
                    processed_at=created,
                ),
                UploadRun(id=99, filename="cross-run.csv", status="COMPLETED"),
            ]
        )
        db.flush()
        db.add(PipelineRun(id=61, upload_run_id=7, status="COMPLETED"))
        db.flush()
        db.add(
            MarketCalculationContext(
                id=71,
                pipeline_run_id=61,
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
        db.add_all([raw, fundamental, technical, combined, ranking, market, sector])
        db.flush()
        db.add(sector_row)
        db.flush()
        native_rows = (raw, fundamental, technical, combined, ranking, market, sector, sector_row)
        for source in native_rows:
            db.refresh(source)
        for kind, source, sources in (
            (CoreEvidenceKind.FUNDAMENTAL, fundamental, {}),
            (CoreEvidenceKind.TECHNICAL, technical, {}),
            (
                CoreEvidenceKind.COMBINED,
                combined,
                {"fundamental": fundamental, "technical": technical},
            ),
            (CoreEvidenceKind.RANKING, ranking, {"combined": combined}),
            (CoreEvidenceKind.REGIME, market, {}),
        ):
            persist_core_evidence(
                db,
                kind=kind,
                current_row=source,
                sources=sources,
                effective_configuration=configuration_for_source(source)
                if kind in {CoreEvidenceKind.REGIME, CoreEvidenceKind.SECTOR}
                else None,
            )
        payload = calculation_evidence_payload(sector)
        payload["rows"] = [
            Canonical.canonicalize({**calculation_evidence_payload(sector_row), "id": 81})
        ]
        persist_core_evidence(
            db,
            kind=CoreEvidenceKind.SECTOR,
            current_row=sector,
            effective_configuration=configuration_for_source(sector),
            payload=payload,
            sources={"regime": market, "ranking:MSFT": ranking},
        )
        db.add(
            TransitionPreflightPlan(
                id=81,
                market_calculation_context_id=71,
                upload_run_id=7,
                pipeline_run_id=61,
                status="CONSUMED",
                idempotency_key="t12d-native",
                candidate_classification="NEW",
                evidence_fingerprint="a" * 64,
                technical_reconstruction_fingerprint="b" * 64,
                run_start_anchor_fingerprint="anchor-fingerprint",
                created_at=cutoff.cutoff_at,
                expires_at=cutoff.cutoff_at + timedelta(days=1),
                consumed_at=cutoff.cutoff_at,
            )
        )
        # This disposable world represents a July decision. Source writes and
        # their pointer updates share that historical availability anchor.
        for source in native_rows:
            if hasattr(type(source), "updated_at"):
                source.updated_at = created
        db.flush()
        for source in native_rows:
            db.refresh(source)
        artifacts = {
            name: semantic_artifact_identity(source)
            for name, source in (
                ("raw_row", raw),
                ("fundamental_score", fundamental),
                ("technical_score", technical),
                ("combined_result", combined),
                ("market_regime_snapshot", market),
                ("sector_rotation_snapshot", sector),
                ("sector_rotation_row", sector_row),
            )
        }
        artifacts.update(
            ranking_results=[semantic_artifact_identity(ranking)],
            eligible_price_bars=[],
            setup_signal_input_hash="setup-input",
        )
        handoff_payload = Canonical.canonicalize(
            {
                "contract": "transition-decision-handoff-v1",
                "binding": {
                    "preflight_plan_id": 81,
                    "run_start_anchor_fingerprint": "anchor-fingerprint",
                },
                "run": {"upload_run_id": 7, "pipeline_run_id": 61},
                "market_context": {
                    "id": 71,
                    "cutoff_at": cutoff.cutoff_at,
                    "exchange_timezone": cutoff.exchange_timezone,
                    "latest_completed_session": cutoff.latest_completed_session,
                    "daily_bar_ready_at": cutoff.daily_bar_ready_at,
                    "calendar_version": cutoff.calendar_version,
                    "bar_readiness_version": cutoff.bar_readiness_version,
                    "cutoff_reason": cutoff.cutoff_reason,
                },
                "decision_manifests": {},
                "artifact_lineage": {"MSFT": artifacts},
                "ceri_score_snapshots": [],
            }
        )
        db.add(
            TransitionDecisionHandoffManifest(
                id=91,
                preflight_plan_id=81,
                market_calculation_context_id=71,
                upload_run_id=7,
                pipeline_run_id=61,
                run_start_anchor_fingerprint="anchor-fingerprint",
                manifest_json=handoff_payload,
                manifest_fingerprint=Canonical.fingerprint(handoff_payload),
                created_at=cutoff.cutoff_at,
            )
        )
        if mode in {"technical_insufficient", "ranking_incomplete"}:
            # A distinct native READY current projection must not replace the
            # blocked evidence referenced by this immutable decision handoff.
            source = technical if mode == "technical_insufficient" else ranking
            kind = (
                CoreEvidenceKind.TECHNICAL
                if mode == "technical_insufficient"
                else CoreEvidenceKind.RANKING
            )
            ready_payload = calculation_evidence_payload(source)
            ready_payload[
                "insufficient_data" if mode == "technical_insufficient" else "is_complete"
            ] = False if mode == "technical_insufficient" else True
            newer = SimpleNamespace(
                run_id=7,
                ticker="MSFT",
                evidence_id=None,
                debug_json=source.debug_json,
                ranking_profile=getattr(source, "ranking_profile", None),
            )
            persist_core_evidence(
                db,
                kind=kind,
                current_row=newer,
                payload=ready_payload,
                sources={} if mode == "technical_insufficient" else {"combined": combined},
            )
            current = get_current_readiness(
                db,
                kind=kind,
                run_id=7,
                ticker="MSFT",
                ranking_profile=getattr(source, "ranking_profile", None),
            )
            assert current.status.value == "READY" and current.evidence_id != source.evidence_id
        db.commit()
    return cutoff
