from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from alembic.config import Config
from historical_evidence_support import seed_pre_phase5_evidence
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.tables import (
    AcquisitionPlanRecord,
    CombinedResult,
    FundamentalScore,
    IBFetchItem,
    IBFetchRun,
    MarketCalculationContext,
    MarketRegimeSnapshot,
    PipelineRun,
    PipelineStep,
    PriceBar,
    PriceBarRevision,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    TechnicalScore,
    TransitionDecisionHandoffManifest,
    UploadRun,
)
from app.services.configuration_delivery import resolve_pipeline_configurations
from app.services.contextual_calculation_identity import (
    artifact_identity,
    build_contextual_result_identity,
    build_regime_identity,
    consumer_context_identity,
    embed_identity,
    expected_sector_identity,
)
from app.services.core_calculation_evidence import CoreEvidenceKind
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.market_regime_policy import load_market_regime_command_center_config
from app.services.pipeline_executor import _pipeline_step, _validate_resume_evidence
from app.services.pipeline_stage_registry import DECISION_HANDOFF_PIPELINE_STEP
from app.services.price_bar_repository import load_price_bar_rows_for_context
from app.services.sector_rotation_config import (
    load_sector_rotation_config,
    sector_rotation_config_hash,
)
from app.services.setup_lifecycle.source_loader import (
    SetupLifecycleSourceLoader,
    _latest_price_bar_history_statement,
)
from app.services.transition_preflight_plan_service import (
    TransitionPreflightError,
    freeze_transition_decision_handoff_manifest,
)

CUTOFF = datetime(2026, 10, 7, 0, 9, 10, tzinfo=UTC)
SESSION = date(2026, 10, 6)
OLDER_SESSION = date(2026, 10, 5)


def test_run26_boundary_uses_authorized_acquisition_and_freezes_resumable_handoff(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)

    with Session(engine, expire_on_commit=False) as db:
        run, pipeline, context, authorized_item = _seed_run_and_acquisition(db)
        authorized_raw = _raw(db, run.id, 1, "AUTH")
        no_bar_raw = _raw(db, run.id, 2, "NOBAR")
        _baseline_bar(db, "AUTH", OLDER_SESSION, CUTOFF - timedelta(hours=1), close="90")
        acquired = _acquired_bar(
            db,
            ticker="AUTH",
            session=SESSION,
            item=authorized_item,
            observed_at=CUTOFF + timedelta(minutes=5),
            close="999",
        )
        acquired.revised_at = CUTOFF + timedelta(minutes=20)
        acquired.revision_count = 1
        acquired.data_hash = "auth-current-revision"
        db.flush()
        db.add(
            PriceBarRevision(
                price_bar_id=acquired.id,
                ticker="AUTH",
                bar_date=SESSION,
                timeframe="1 day",
                what_to_show="TRADES",
                revision_number=1,
                previous_data_hash="auth-at-fetch-completion",
                new_data_hash="auth-current-revision",
                previous_values_json={
                    "open": "99",
                    "high": "101",
                    "low": "98",
                    "close": "100",
                    "volume": "1000",
                    "source": "IBKR",
                    "what_to_show": "TRADES",
                },
                new_values_json={
                    "open": "998",
                    "high": "1000",
                    "low": "997",
                    "close": "999",
                    "volume": "1000",
                },
                observed_at=CUTOFF + timedelta(minutes=20),
            )
        )
        _seed_unrelated_post_cutoff_acquisition(db, run.id)
        market, sector, regime_effective, sector_effective = _seed_required_context(
            db, run.id, pipeline.id, context
        )
        core_configurations = resolve_pipeline_configurations(db)
        for raw in (authorized_raw, no_bar_raw):
            _seed_core_ticker_artifacts(
                db,
                raw,
                pipeline.id,
                context,
                core_configurations,
            )
        _seal(
            db,
            market,
            CoreEvidenceKind.REGIME,
            effective_configuration=regime_effective,
        )
        _seal(
            db,
            sector,
            CoreEvidenceKind.SECTOR,
            sources={"market_regime": market},
            effective_configuration=sector_effective,
        )
        db.commit()
        run_id = run.id
        pipeline_id = pipeline.id
        context_id = context.id

    with Session(engine, expire_on_commit=False) as db:
        context = db.get(MarketCalculationContext, context_id)
        cutoff = cutoff_from_row(context)
        old_visibility = tuple(
            db.scalars(
                _latest_price_bar_history_statement(
                    ("AUTH",),
                    cutoff=SESSION,
                    cutoff_at=CUTOFF,
                    session_count=2,
                )
            )
        )
        assert [row.bar_date for row in old_visibility] == [OLDER_SESSION]

        loaded = SetupLifecycleSourceLoader(
            latest_bar_projection_enabled=True,
            shadow_compare_enabled=False,
        ).load_run_context(db, run_id, market_cutoff=cutoff)
        by_ticker = {row.ticker: row for row in loaded.tickers}
        assert by_ticker["AUTH"].latest_completed_bar.bar_date == SESSION
        assert by_ticker["AUTH"].latest_completed_bar.close == Decimal("100")
        assert by_ticker["AUTH"].market_regime_snapshot.id == market.id
        assert by_ticker["AUTH"].sector_rotation_snapshot.id == sector.id
        assert by_ticker["NOBAR"].latest_completed_bar is None
        assert by_ticker["NOBAR"].market_regime_snapshot.id == market.id
        assert by_ticker["NOBAR"].sector_rotation_snapshot.id == sector.id

        unrelated = load_price_bar_rows_for_context(
            db,
            ("LEAK",),
            what_to_show=("TRADES",),
            timeframes=("1 day",),
            max_session=SESSION,
            as_of=CUTOFF,
            calculation_context_id=context_id,
            session_count=2,
            one_source_per_session=True,
            source_priority=("TRADES",),
        )
        assert [(row.bar_date, row.close) for row in unrelated] == [
            (OLDER_SESSION, Decimal("80"))
        ]

        handoff = freeze_transition_decision_handoff_manifest(
            db,
            upload_run_id=run_id,
            market_cutoff=cutoff,
        )
        db.commit()
        assert handoff is not None
        assert all(
            entry["market_regime_snapshot"]["id"] == market.id
            and entry["sector_rotation_snapshot"]["id"] == sector.id
            for entry in handoff.manifest_json["artifact_lineage"].values()
        )

    with Session(engine) as db:
        pipeline = db.get(PipelineRun, pipeline_id)
        result = _validate_resume_evidence(
            db,
            pipeline=pipeline,
            upload_run_id=run_id,
        )
        assert result["historical_read_mode"] == "CERTIFIED_EVIDENCE"
        assert result["decision_handoff_manifest_id"] == db.scalar(
            select(TransitionDecisionHandoffManifest.id)
        )


def test_missing_required_context_is_rejected_before_handoff_publication(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)

    with Session(engine, expire_on_commit=False) as db:
        run, pipeline, context, _item = _seed_run_and_acquisition(db)
        step = PipelineStep(
            pipeline_run_id=pipeline.id,
            step_name=DECISION_HANDOFF_PIPELINE_STEP,
            step_order=10,
            status="PENDING",
        )
        db.add(step)
        raw_rows = (_raw(db, run.id, 1, "AUTH"), _raw(db, run.id, 2, "NOBAR"))
        market, sector, regime_effective, _sector_effective = _seed_required_context(
            db, run.id, pipeline.id, context
        )
        db.delete(sector)
        db.flush()
        configurations = resolve_pipeline_configurations(db)
        for raw in raw_rows:
            _seed_core_ticker_artifacts(
                db,
                raw,
                pipeline.id,
                context,
                configurations,
            )
        _seal(
            db,
            market,
            CoreEvidenceKind.REGIME,
            effective_configuration=regime_effective,
        )
        cutoff = cutoff_from_row(context)

        with pytest.raises(
            TransitionPreflightError,
            match=r"required_artifact_unavailable.*artifact=sector_rotation_snapshot",
        ):
            with _pipeline_step(db, pipeline, DECISION_HANDOFF_PIPELINE_STEP):
                freeze_transition_decision_handoff_manifest(
                    db,
                    upload_run_id=run.id,
                    market_cutoff=cutoff,
                )

        assert step.status == "FAILED"
        assert step.completed_at is not None
        assert (
            db.scalar(select(func.count()).select_from(TransitionDecisionHandoffManifest)) == 0
        )


def _seed_run_and_acquisition(db: Session):
    plan_id = "run26-regression".ljust(64, "0")
    db.add(
        AcquisitionPlanRecord(
            plan_id=plan_id,
            plan_kind="RUN26_REGRESSION",
            plan_version="v1",
            payload_json={"tickers": ["AUTH", "NOBAR"]},
        )
    )
    run = UploadRun(filename="run26-regression.csv", row_count=2, status="COMPLETED")
    db.add(run)
    db.flush()
    pipeline = PipelineRun(
        upload_run_id=run.id,
        acquisition_plan_id=plan_id,
        status="FREEZING_DECISION_HANDOFF_MANIFEST",
    )
    db.add(pipeline)
    db.flush()
    context = MarketCalculationContext(
        pipeline_run_id=pipeline.id,
        upload_run_id=run.id,
        cutoff_at=CUTOFF,
        exchange_timezone="America/New_York",
        latest_completed_session=SESSION,
        daily_bar_ready_at=None,
        calendar_version="test-v1",
        bar_readiness_version="test-v1",
        cutoff_reason="RUN26_REGRESSION",
    )
    fetch_run = IBFetchRun(
        acquisition_plan_id=plan_id,
        run_id=run.id,
        requested_tickers=["AUTH"],
        symbols_including_benchmarks=["AUTH"],
        status="COMPLETED",
        completed_at=CUTOFF + timedelta(minutes=10),
    )
    db.add_all([context, fetch_run])
    db.flush()
    item = IBFetchItem(
        fetch_run_id=fetch_run.id,
        ticker="AUTH",
        what_to_show="TRADES",
        bar_size="1 day",
        status="SUCCESS",
        inserted=1,
        completed_at=CUTOFF + timedelta(minutes=10),
    )
    db.add(item)
    db.flush()
    return run, pipeline, context, item


def _raw(db: Session, run_id: int, row_number: int, ticker: str) -> RawCompanyRow:
    row = RawCompanyRow(
        run_id=run_id,
        row_number=row_number,
        ticker=ticker,
        company_name=ticker,
        sector="Technology",
        sector_canonical="Technology",
        raw_json={"ticker": ticker, "pivot_price": "100", "trigger_price": "101"},
    )
    db.add(row)
    db.flush()
    return row


def _baseline_bar(
    db: Session, ticker: str, session: date, observed_at: datetime, *, close: str
) -> PriceBar:
    row = PriceBar(
        ticker=ticker,
        bar_date=session,
        timeframe="1 day",
        open=Decimal(close) - 1,
        high=Decimal(close) + 1,
        low=Decimal(close) - 2,
        close=Decimal(close),
        volume=Decimal("1000"),
        source="IBKR",
        what_to_show="TRADES",
        created_at=observed_at,
        first_seen_at=observed_at,
        last_seen_at=observed_at,
        revision_count=0,
        data_hash=f"{ticker}-{session}-baseline",
    )
    db.add(row)
    db.flush()
    return row


def _acquired_bar(
    db: Session,
    *,
    ticker: str,
    session: date,
    item: IBFetchItem,
    observed_at: datetime,
    close: str,
) -> PriceBar:
    row = _baseline_bar(db, ticker, session, observed_at, close=close)
    row.first_fetch_run_id = item.fetch_run_id
    row.first_fetch_item_id = item.id
    db.flush()
    return row


def _seed_unrelated_post_cutoff_acquisition(db: Session, run_id: int) -> None:
    _baseline_bar(db, "LEAK", OLDER_SESSION, CUTOFF - timedelta(hours=1), close="80")
    other_plan = "unrelated".ljust(64, "0")
    db.add(
        AcquisitionPlanRecord(
            plan_id=other_plan,
            plan_kind="UNRELATED",
            plan_version="v1",
            payload_json={"tickers": ["LEAK"]},
        )
    )
    fetch = IBFetchRun(
        acquisition_plan_id=other_plan,
        run_id=run_id,
        requested_tickers=["LEAK"],
        symbols_including_benchmarks=["LEAK"],
        status="COMPLETED",
        completed_at=CUTOFF + timedelta(minutes=9),
    )
    db.add(fetch)
    db.flush()
    item = IBFetchItem(
        fetch_run_id=fetch.id,
        ticker="LEAK",
        what_to_show="TRADES",
        bar_size="1 day",
        status="SUCCESS",
        inserted=1,
        completed_at=CUTOFF + timedelta(minutes=9),
    )
    db.add(item)
    db.flush()
    _acquired_bar(
        db,
        ticker="LEAK",
        session=SESSION,
        item=item,
        observed_at=CUTOFF + timedelta(minutes=4),
        close="81",
    )


def _seed_required_context(
    db: Session,
    run_id: int,
    pipeline_id: int,
    context: MarketCalculationContext,
) -> tuple[MarketRegimeSnapshot, SectorRotationSnapshot, object, object]:
    cutoff = cutoff_from_row(context)
    regime_config = load_market_regime_command_center_config()
    market_identity = build_regime_identity(
        market_cutoff=cutoff,
        config=regime_config,
        run_id=run_id,
        pipeline_id=pipeline_id,
        source_payload={"regression": "run26"},
    )
    market = MarketRegimeSnapshot(
        run_id=run_id,
        as_of_date=SESSION,
        calculation_context_id=context.id,
        calculation_cutoff_at=CUTOFF,
        input_as_of_session=SESSION,
        calendar_version=context.calendar_version,
        calculation_version=regime_config.calculation_version,
        config_version="run26-regression",
        regime="NEUTRAL",
        risk_state="NORMAL",
        score=50,
        confidence="normal",
        action_summary="regression",
        debug_json=embed_identity({}, market_identity, policy="RUN26_REGRESSION"),
        evidence_hash="market-run26-regression",
    )
    db.add(market)
    db.flush()

    sector_config = load_sector_rotation_config()
    mode = (
        "combined"
        if sector_config.get("etf_score", {}).get("enabled", False)
        else "universe_only"
    )
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=run_id,
        pipeline_id=pipeline_id,
    )
    expected = expected_sector_identity(
        context=base,
        config_hash=sector_rotation_config_hash(sector_config),
        calculation_version="sector-rotation-1.0.0",
        mode=mode,
        effective_configuration=sector_config.effective_configuration,
    )
    sector_identity = build_contextual_result_identity(
        base=base,
        namespace="sector-rotation",
        config_hash=sector_rotation_config_hash(sector_config),
        calculation_version="sector-rotation-1.0.0",
        engine_version="sector-rotation-1.0.0",
        source_artifacts=(("market-regime", market, artifact_identity(market)),),
        source_payload={"regression": "run26"},
    )
    sector_identity = replace(
        sector_identity,
        configuration=expected.configuration,
        algorithm=replace(sector_identity.algorithm, components=expected.algorithm.components),
    )
    sector = SectorRotationSnapshot(
        run_id=run_id,
        market_regime_snapshot_id=market.id,
        as_of_date=SESSION,
        calculation_context_id=context.id,
        calculation_cutoff_at=CUTOFF,
        input_as_of_session=SESSION,
        calendar_version=context.calendar_version,
        calculation_version="sector-rotation-1.0.0",
        config_version="run26-regression",
        config_hash=sector_rotation_config_hash(sector_config),
        mode=mode,
        debug_json=embed_identity({}, sector_identity, policy="RUN26_REGRESSION"),
        evidence_hash="sector-run26-regression",
    )
    db.add(sector)
    db.flush()
    db.add(
        SectorRotationRow(
            snapshot_id=sector.id,
            sector="Technology",
            sector_slug="technology",
            rotation_state="NEUTRAL",
            sector_permission="ALLOWED",
            confidence="HIGH",
            current_rank=1,
        )
    )
    db.flush()
    return (
        market,
        sector,
        regime_config._effective_configuration.snapshot,
        sector_config.effective_configuration.snapshot,
    )


def _seed_core_ticker_artifacts(
    db: Session,
    raw: RawCompanyRow,
    pipeline_id: int,
    context: MarketCalculationContext,
    configurations,
) -> None:
    ticker = raw.ticker.upper()
    cutoff = cutoff_from_row(context)
    base = consumer_context_identity(
        market_cutoff=cutoff,
        run_id=raw.run_id,
        pipeline_id=pipeline_id,
        ticker=ticker,
    )
    ranking_configuration_key = next(
        key for key in configurations if key.startswith("core.ranking:")
    )
    ranking_profile = ranking_configuration_key.split(":", 1)[1]

    def identity(namespace: str):
        configuration_key = (
            ranking_configuration_key if namespace == "ranking" else f"core.{namespace}"
        )
        configuration = configurations[configuration_key]
        built = build_contextual_result_identity(
            base=base,
            namespace=f"core.{namespace}",
            config_hash=configuration.snapshot.semantic_hash,
            calculation_version="test-v1",
            engine_version="test-v1",
            source_artifacts=(),
            source_payload={"ticker": ticker, "artifact": namespace},
        )
        return configuration.bind(built)

    fundamental = FundamentalScore(
        run_id=raw.run_id,
        ticker=ticker,
        fundamental_score=Decimal("80"),
        data_coverage_score=Decimal("10"),
        debug_json=embed_identity({}, identity("fundamental"), policy="RUN26_REGRESSION"),
    )
    technical = TechnicalScore(
        run_id=raw.run_id,
        ticker=ticker,
        calculation_context_id=context.id,
        calculation_cutoff_at=CUTOFF,
        input_as_of_session=SESSION,
        calendar_version=context.calendar_version,
        technical_engine_version="test-v1",
        classification="WATCH",
        stage="PIVOT_READY",
        dual_score=Decimal("75"),
        setup_score=Decimal("75"),
        data_quality_score=Decimal("9"),
        debug_json=embed_identity({}, identity("technical"), policy="RUN26_REGRESSION"),
    )
    db.add_all([fundamental, technical])
    db.flush()
    _seal(
        db,
        fundamental,
        CoreEvidenceKind.FUNDAMENTAL,
        effective_configuration=configurations["core.fundamental"].snapshot,
    )
    _seal(
        db,
        technical,
        CoreEvidenceKind.TECHNICAL,
        effective_configuration=configurations["core.technical"].snapshot,
    )

    source_ids = {
        "raw_row_id": raw.id,
        "fundamental_evidence_id": fundamental.evidence_id,
        "technical_evidence_id": technical.evidence_id,
    }
    combined = CombinedResult(
        run_id=raw.run_id,
        ticker=ticker,
        final_score=Decimal("80"),
        combined_decision="WATCH",
        is_complete=True,
        has_fundamental=True,
        has_technical=True,
        debug_json=embed_identity(
            {"source_ids": source_ids},
            identity("combined"),
            policy="RUN26_REGRESSION",
        ),
    )
    ranking = RankingResult(
        run_id=raw.run_id,
        raw_row_id=raw.id,
        ticker=ticker,
        ranking_profile=ranking_profile,
        ranking_label=ranking_profile.title(),
        profile_rank=1,
        profile_score=Decimal("80"),
        decision_label="WATCH",
        debug_json=embed_identity({}, identity("ranking"), policy="RUN26_REGRESSION"),
    )
    db.add_all([combined, ranking])
    db.flush()
    _seal(
        db,
        combined,
        CoreEvidenceKind.COMBINED,
        sources={"fundamental": fundamental, "technical": technical},
        effective_configuration=configurations["core.combined"].snapshot,
    )
    _seal(
        db,
        ranking,
        CoreEvidenceKind.RANKING,
        effective_configuration=configurations[ranking_configuration_key].snapshot,
    )


def _seal(
    db: Session,
    row,
    kind: CoreEvidenceKind,
    *,
    sources=None,
    effective_configuration=None,
) -> None:
    evidence = seed_pre_phase5_evidence(
        db,
        kind=kind,
        current_row=row,
        sources=sources or {},
        effective_configuration=effective_configuration,
    )
    assert evidence is not None
