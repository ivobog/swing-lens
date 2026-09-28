"""Actual entry-point and durable authority delivery on task-owned PostgreSQL."""

from datetime import UTC, datetime

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from fastapi import HTTPException, Request
from native_mutation_support import seed_native_core
from sqlalchemy import event, func, select, text, update
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriCatalystEvent, CeriCatalystEventRevision, CeriCompany
from app.models.ib_market_intelligence_tables import IBExecutionFill, IBTradeEpisode
from app.models.tables import (
    BackgroundJob,
    CoreCalculationEvidence,
    MarketCalculationContext,
    RawCompanyRow,
    UploadRun,
)
from app.routers import ceri_routes, market_regime_routes, run_routes, sector_rotation_routes
from app.services.background_job_service import enqueue_job
from app.services.background_worker import execute_job
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.manual_review_service import CeriManualReviewService
from app.services.ceri.sec.identity_repair import resolve_and_persist_sec_identity
from app.services.configuration_delivery import ANCHOR_KEY, anchored_job_configuration
from app.services.domain_write_fence import detached_control_plane_scope
from app.services.entrypoint_authority import EntryPointAuthorityError
from app.services.ib_market_intelligence.flex import import_flex_report
from app.services.ib_market_intelligence.journal import rebuild_trade_episodes
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.sector_rotation_service import SectorRotationService
from app.services.setup_lifecycle.caller_authority import setup_run_cutoff
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotCaptureService
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader
from app.services.technical_score_service import score_run_technicals

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_all_regime_sector_missing_state_reads_have_no_dml_or_enqueue(contextual_engine):
    statements = []

    def record(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            statements.append(sql)

    from app.main import app

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "app": app,
            "router": app.router,
            "scheme": "http",
            "server": ("localhost", 80),
            "query_string": b"",
        }
    )
    with Session(contextual_engine) as db:
        db.add(UploadRun(id=7, filename="t14d-read-only.csv", status="COMPLETED"))
        db.commit()
        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            for operation in (
                lambda: market_regime_routes.market_regime_page(request, db),
                lambda: market_regime_routes.run_market_regime_page(request, 7, db),
            ):
                assert operation().status_code == 200
            for operation in (
                lambda: sector_rotation_routes.sector_rotation_dashboard(request, 7, db),
                lambda: sector_rotation_routes.api_sector_rotation(7, db),
                lambda: sector_rotation_routes.api_sector_rotation_drilldown(7, "technology", db),
                lambda: sector_rotation_routes.sector_rotation_drilldown(
                    request, 7, "technology", db
                ),
            ):
                with pytest.raises(HTTPException) as error:
                    operation()
                assert error.value.status_code == 404
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)
        assert statements == []
        assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 0


@pytest.mark.parametrize("policy", ["REQUIRE_IB", "ALLOW_CACHE_FALLBACK"])
def test_reduced_pipeline_rejects_without_sql_or_provider_side_effects(
    contextual_engine, monkeypatch, policy
):

    from app.models.tables import CoreCalculationEvidence, PipelineRun
    from app.settings import get_settings

    writes = []

    def record(_conn, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")):
            writes.append(sql)

    settings = get_settings().model_copy(update={"use_durable_pipeline": False})
    monkeypatch.setattr(run_routes, "get_settings", lambda: settings)
    monkeypatch.setattr(
        run_routes, "check_status", lambda **_: pytest.fail("retired option contacted broker")
    )
    monkeypatch.setattr(
        run_routes, "start_pipeline", lambda *_a, **_k: pytest.fail("retired option enqueued")
    )
    with Session(contextual_engine) as db:
        seed_native_core(db)
        db.commit()
        models = (BackgroundJob, PipelineRun, CoreCalculationEvidence)
        before = tuple(db.scalar(select(func.count()).select_from(m)) for m in models)
        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            with pytest.raises(HTTPException) as error:
                run_routes.run_full_pipeline_action(7, db, market_data_policy=policy)
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "REDUCED_PIPELINE_RETIRED"
        assert writes == []
        assert tuple(db.scalar(select(func.count()).select_from(m)) for m in models) == before


def test_native_canonical_sources_remain_unchanged_when_standalone_is_rejected(contextual_engine):
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(db)
        db.commit()
        before = Canonical.fingerprint(
            [
                (row.id, row.evidence_key, row.payload_json, row.calculation_identity_json)
                for row in db.scalars(
                    select(CoreCalculationEvidence).order_by(CoreCalculationEvidence.id)
                )
            ]
        )
        for operation in (
            run_routes.recalculate_fundamentals_action,
            run_routes.refresh_technicals_action,
            run_routes.refresh_combined_results_action,
            run_routes.refresh_all_ranking_profiles_action,
        ):
            with pytest.raises(HTTPException) as error:
                operation(7, db)
            assert error.value.status_code == 409
        after = Canonical.fingerprint(
            [
                (row.id, row.evidence_key, row.payload_json, row.calculation_identity_json)
                for row in db.scalars(
                    select(CoreCalculationEvidence).order_by(CoreCalculationEvidence.id)
                )
            ]
        )
        assert before == after
        for unbound in (
            lambda: score_run_technicals(db, 7),
            lambda: SetupLifecycleSnapshotCaptureService().capture_snapshots_for_run(db, 7),
            lambda: MarketRegimeCommandCenterService().build_snapshot(db, run_id=7),
            lambda: SectorRotationService().build_sector_rotation_snapshot(db, run_id=7),
        ):
            with pytest.raises(EntryPointAuthorityError):
                unbound()
        pipeline_id = db.get(MarketCalculationContext, cutoff.context_id).pipeline_run_id
        assert setup_run_cutoff(db, run_id=7, pipeline_run_id=pipeline_id) == cutoff
        with pytest.raises(EntryPointAuthorityError, match="SETUP_PIPELINE_AUTHORITY_REQUIRED"):
            setup_run_cutoff(db, run_id=7, pipeline_run_id=None)


def test_setup_source_loader_never_substitutes_newest_context_for_missing_authority(
    contextual_engine,
):
    from app.models.tables import PipelineRun
    from app.services.market_calculation_context_service import create_pipeline_market_context

    with Session(contextual_engine) as db:
        c1, _, _ = seed_native_core(
            db,
            cutoff_at=datetime(2026, 9, 16, 21, tzinfo=UTC),
        )
        newer_pipeline = PipelineRun(upload_run_id=7, status="RUNNING")
        db.add(newer_pipeline)
        db.flush()
        c2 = create_pipeline_market_context(
            db,
            newer_pipeline,
            cutoff_at=datetime(2026, 9, 17, 21, tzinfo=UTC),
        )
        db.commit()
        assert c1.context_id != c2.context_id

        with pytest.raises(
            EntryPointAuthorityError,
            match="SETUP_EXPLICIT_CALCULATION_AUTHORITY_REQUIRED",
        ):
            SetupLifecycleSourceLoader().load_run_context(db, 7)


def test_new_root_and_child_preserve_frozen_rules_mode_and_operation_time(
    contextual_engine, monkeypatch
):
    with Session(contextual_engine) as db:
        root = enqueue_job(db, "WINNER_OUTCOME_MATURATION", {"limit": 3}, request_key="t14d:root")
        db.commit()
        root_id = root.id
        frozen = dict(root.payload_json)
        assert frozen["semantic_mode"] == "OUTCOME_MATURATION"
        assert datetime.fromisoformat(frozen["operation_cutoff_at"]).tzinfo is not None
        monkeypatch.setattr(
            "app.services.configuration_delivery.resolve_pipeline_configurations",
            lambda *_a, **_k: pytest.fail("child resolved C2 instead of inheriting C1"),
        )
        child = enqueue_job(
            db,
            "WINNER_OUTCOME_MATURATION",
            {"limit": 3, "continuation": True},
            request_key="t14d:child",
            parent_job_id=root_id,
        )
        db.commit()
        assert child.payload_json[ANCHOR_KEY] == frozen[ANCHOR_KEY]
        assert child.payload_json["operation_cutoff_at"] == frozen["operation_cutoff_at"]
        assert child.payload_json["semantic_mode"] == frozen["semantic_mode"]
        db.expire_all()
        assert db.get(BackgroundJob, root_id).payload_json == frozen
        with pytest.raises(ValueError, match="MUTATION_CHILD_OPERATION_CUTOFF_MISMATCH"):
            enqueue_job(
                db,
                "WINNER_OUTCOME_MATURATION",
                {"operation_cutoff_at": "2099-01-01T00:00:00+00:00"},
                parent_job_id=root_id,
            )
        db.rollback()


def test_direct_durable_rejection_rolls_back_preliminary_state(contextual_engine):
    @anchored_job_configuration
    def handler(db, job):
        pytest.fail("unsupported direct delivery reached business writer")

    with Session(contextual_engine) as db:
        row = UploadRun(filename="t14d-rollback.csv", status="COMPLETED")
        db.add(row)
        db.flush()
        row_id = row.id
        with pytest.raises(ValueError, match="MISSING_CONFIGURATION_ANCHOR"):
            handler(db, BackgroundJob(payload_json={}))
        assert db.get(UploadRun, row_id) is None


def test_direct_delivery_cannot_replace_retained_job_scope(contextual_engine):
    @anchored_job_configuration
    def handler(db, job):
        pytest.fail("altered durable scope reached business writer")

    with Session(contextual_engine) as db:
        job = enqueue_job(db, "WINNER_OUTCOME_MATURATION", {"limit": 3})
        job.status = "RUNNING"
        job.execution_token = "t14d-owned-attempt"
        db.commit()
        original = dict(job.payload_json)
        job.payload_json = {**original, "operation_cutoff_at": "2099-01-01T00:00:00+00:00"}
        with pytest.raises(ValueError, match="MUTATION_DURABLE_RETAINED_SCOPE_MISMATCH"):
            handler(db, job)
        assert job.payload_json == original


def test_detached_control_plane_can_update_job_during_anchored_calculation(contextual_engine):
    statements: list[str] = []

    def record(_connection, _cursor, sql, _parameters, _context, _many):
        statements.append(sql)

    @anchored_job_configuration
    def handler(db, job):
        with db.begin_nested():
            db.add(UploadRun(filename="t14d-detached-control-flush.csv", status="COMPLETED"))
            db.flush()
        return job._control_plane_progress()

    def detached_control_progress():
        with Session(contextual_engine) as control_db:
            with detached_control_plane_scope():
                control_db.execute(text("SET LOCAL lock_timeout = '500ms'"))
                changed = control_db.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == job.id)
                    .values(heartbeat_at=datetime.now(UTC))
                ).rowcount
                control_db.commit()
        return changed

    with Session(contextual_engine) as db:
        job = enqueue_job(db, "CERI_CHANGE_DETECTION", {"batch_size": 1})
        job.status = "RUNNING"
        job.execution_token = "t14d-detached-control-attempt"
        db.commit()
        job._control_plane_progress = detached_control_progress

        event.listen(contextual_engine, "before_cursor_execute", record)
        try:
            assert (
                execute_job(
                    db,
                    job,
                    {"CERI_CHANGE_DETECTION": handler},
                    execution_token=job.execution_token,
                )
                == 1
            )
        finally:
            event.remove(contextual_engine, "before_cursor_execute", record)

    assert not [
        sql
        for sql in statements
        if "background_jobs" in sql.lower() and "FOR UPDATE" in sql.upper()
    ]


def test_trade_episode_native_population_and_altered_source_rejection(contextual_engine):
    report = (
        "AccountId,TradeID,TradeDate,TradeTime,Symbol,Buy/Sell,Quantity,"
        "TradePrice,IBCommission,Fees,Currency\n"
        "U123,E1,20260807,093000,XYZ,BUY,10,25,1,0.1,USD\n"
    )
    at = datetime(2026, 8, 9, tzinfo=UTC)
    with Session(contextual_engine) as db:
        import_flex_report(
            db,
            content=report,
            query_type="TRADE_CONFIRMATIONS",
            query_id="T14D",
            reference_code="T14D",
            now=at,
        )
        db.commit()
        fills = list(db.scalars(select(IBExecutionFill)))
        rows = rebuild_trade_episodes(
            db, fills=fills, operation_at=at, episode_policy="FIFO_POSITION_V1"
        )
        assert len(rows) == 1 and rows[0].status == "OPEN"
        assert rows[0].fill_ids_json == [fills[0].id]
        db.commit()
        original_quantity = fills[0].quantity
        fills[0].quantity += 1
        with pytest.raises(ValueError, match="MUTATION_SOURCE"):
            rebuild_trade_episodes(
                db, fills=fills, operation_at=at, episode_policy="FIFO_POSITION_V1"
            )
        assert fills[0].quantity == original_quantity
        assert db.scalar(select(func.count()).select_from(IBTradeEpisode)) == 1


def test_trade_episode_retry_order_time_population_and_partial_failure(contextual_engine):
    report = (
        "AccountId,TradeID,TradeDate,TradeTime,Symbol,Buy/Sell,Quantity,"
        "TradePrice,IBCommission,Fees,Currency\n"
        "U123,E1,20260807,093000,XYZ,BUY,10,25,1,0.1,USD\n"
        "U123,E2,20260807,100000,XYZ,SELL,4,30,1,0.1,USD\n"
    )
    at = datetime(2026, 8, 9, tzinfo=UTC)
    with Session(contextual_engine) as db:
        import_flex_report(
            db,
            content=report,
            query_type="TRADE_CONFIRMATIONS",
            query_id="T14D_ORDER",
            reference_code="T14D_ORDER",
            now=at,
        )
        db.commit()
        fills = list(db.scalars(select(IBExecutionFill).order_by(IBExecutionFill.id)))
        kwargs = {"operation_at": at, "episode_policy": "FIFO_POSITION_V1"}
        first = rebuild_trade_episodes(db, fills=fills, **kwargs)[0]
        db.commit()
        original = (first.id, first.fill_ids_json, first.exit_quantity, first.status)
        reversed_result = rebuild_trade_episodes(db, fills=list(reversed(fills)), **kwargs)[0]
        db.commit()
        assert (
            reversed_result.id,
            reversed_result.fill_ids_json,
            reversed_result.exit_quantity,
            reversed_result.status,
        ) == original
        assert first.exit_quantity == 4
        for missing_time, reason in (
            (None, "AWARE_OPERATION_TIME_REQUIRED"),
            (at.replace(tzinfo=None), "must be timezone-aware"),
        ):
            with pytest.raises(ValueError, match=reason):
                rebuild_trade_episodes(db, fills=fills, **{**kwargs, "operation_at": missing_time})
        with pytest.raises(ValueError, match="DUPLICATE_FILL"):
            rebuild_trade_episodes(db, fills=fills + fills[:1], **kwargs)

        # Failure after a real mutable projection UPDATE must discard it.
        def interrupt(_conn, _cursor, sql, _parameters, _context, _many):
            if sql.lstrip().upper().startswith("UPDATE IB_TRADE_EPISODES"):
                raise RuntimeError("T14D_INJECTED_PROJECTION_FAILURE")

        event.listen(contextual_engine, "after_cursor_execute", interrupt)
        try:
            with pytest.raises(RuntimeError, match="T14D_INJECTED"):
                rebuild_trade_episodes(db, fills=fills[:1], **kwargs)
        finally:
            event.remove(contextual_engine, "after_cursor_execute", interrupt)
        retained = db.get(IBTradeEpisode, original[0])
        assert (
            retained.id,
            retained.fill_ids_json,
            retained.exit_quantity,
            retained.status,
        ) == original
        changed = rebuild_trade_episodes(db, fills=fills[:1], **kwargs)[0]
        db.commit()
        assert changed.fill_ids_json == [fills[0].id] and changed.exit_quantity == 0
        assert db.scalar(select(func.count()).select_from(IBTradeEpisode)) == 1


@pytest.mark.parametrize("population", [1, 50])
def test_ceri_review_changes_metadata_and_override_appends_a_source_revision(
    contextual_engine, monkeypatch, population
):
    monkeypatch.setattr(ceri_routes, "_require_local_admin", lambda *_: None)
    with Session(contextual_engine) as db:
        company = CeriCompany(ticker="T14D")
        db.add(company)
        db.flush()
        event = CeriCatalystEvent(company_id=company.id, category="TEST", subject_key="T14D")
        db.add(event)
        db.flush()
        revision = CeriCatalystEventRevision(
            catalyst_event_id=event.id,
            revision_number=1,
            is_current=True,
            status="ANNOUNCED",
            direction="POSITIVE",
            materiality=1.0,
        )
        db.add(revision)
        for index in range(population - 1):
            extra = CeriCatalystEvent(
                company_id=company.id, category="TEST", subject_key=f"T14D-{index}"
            )
            db.add(extra)
            db.flush()
            db.add(
                CeriCatalystEventRevision(
                    catalyst_event_id=extra.id,
                    revision_number=1,
                    is_current=True,
                    status="ANNOUNCED",
                    direction="POSITIVE",
                    materiality=1.0,
                )
            )
        db.commit()
        with pytest.raises(HTTPException) as error:
            ceri_routes.review_ceri_event(
                event.id, object(), db, {"new_value": {"materiality": 99.0}}
            )
        assert error.value.status_code == 422
        assert revision.materiality == 1.0
        statements = []

        def count_selects(_conn, _cursor, sql, _parameters, _context, _many):
            if sql.lstrip().upper().startswith("SELECT"):
                statements.append(sql)

        event_id = event.id
        from sqlalchemy import event as sql_events

        sql_events.listen(contextual_engine, "before_cursor_execute", count_selects)
        try:
            first = ceri_routes.review_ceri_event(event_id, object(), db, None)
        finally:
            sql_events.remove(contextual_engine, "before_cursor_execute", count_selects)
        assert len(statements) <= 12, len(statements)
        second = ceri_routes.review_ceri_event(event.id, object(), db, None)
        assert first["id"] == second["id"]
        assert revision.review_state == "REVIEWED"
        old_id = revision.id
        _, appended = CeriManualReviewService().create_catalyst_override(
            db,
            current_revision=revision,
            new_values={"materiality": 2.0},
            reviewer="t14d-reviewer",
            reason="Explicit source correction",
        )
        db.commit()
        assert appended.prior_revision_id == old_id
        assert appended.revision_number == 2 and appended.materiality == 2.0
        assert db.get(CeriCatalystEventRevision, old_id).materiality == 1.0


def test_sec_cli_and_application_service_share_source_operation_and_dry_run_boundary(
    contextual_engine, monkeypatch, capsys
):
    import sys
    from types import SimpleNamespace

    from scripts import resolve_sec_ciks as cli

    # Provider observation double, not a fabricated financial proof.
    provider = SimpleNamespace(resolve_cik_candidates=lambda symbol: ("12345",))
    with Session(contextual_engine) as db:
        db.add(UploadRun(id=7, filename="t14d-source-universe.csv", status="COMPLETED"))
        db.flush()
        db.add(RawCompanyRow(run_id=7, row_number=1, ticker="T14D", raw_json={}))
        db.add(CeriCompany(ticker="T14D", exchange="US"))
        db.commit()
        direct = resolve_and_persist_sec_identity(db, provider=provider, ticker="T14D")
        assert direct.status == "RESOLVED" and direct.cik == "0000012345"
        db.rollback()
    monkeypatch.setattr(cli, "engine", contextual_engine)
    monkeypatch.setattr(cli, "SecCeriProvider", lambda **_: provider)
    monkeypatch.setattr(sys, "argv", ["resolve_sec_ciks", "--run-id", "7", "--dry-run"])
    assert cli.main() == 0
    assert '"T14D": "0000012345"' in capsys.readouterr().out
    with Session(contextual_engine) as db:
        assert db.scalar(select(CeriCompany).where(CeriCompany.ticker == "T14D")).cik is None
    monkeypatch.setattr(sys, "argv", ["resolve_sec_ciks", "--run-id", "7"])
    assert cli.main() == 0
    with Session(contextual_engine) as db:
        assert db.scalar(select(CeriCompany).where(CeriCompany.ticker == "T14D")).cik == direct.cik


def test_native_certified_outcome_is_unchanged_when_incident_canary_is_retired(contextual_engine):
    from dataclasses import replace
    from datetime import time, timedelta

    from test_t14c_decision_writer_postgresql import _native_liquidity_source

    from app.models.tables import (
        IBContract,
        PriceBar,
        WinnerForwardOutcome,
    )
    from app.services.bar_cache_service import price_bar_data_hash
    from app.services.decision_effective_configuration import (
        resolve_setup_configuration,
        resolve_winner_configuration,
    )
    from app.services.transition_preflight_plan_service import (
        freeze_transition_decision_handoff_manifest,
    )
    from app.services.winner_probability.capture_service import WinnerPredictionCaptureService
    from app.services.winner_probability.config import load_winner_probability_config
    from app.services.winner_probability.market_data_obligation_service import (
        required_outcome_sessions,
    )
    from app.services.winner_probability.maturation_canary_service import (
        build_maturation_canary_manifest,
        canonical_canary_hash,
        execute_reviewed_maturation_canary,
    )

    config = load_winner_probability_config()
    config = replace(config, engine=replace(config.engine, enabled=True))
    configurations = tuple(
        resolve_winner_configuration(config, family=family)
        for family in ("prediction", "outcome", "cohort", "generation")
    ) + (resolve_setup_configuration(),)
    with Session(contextual_engine) as db:
        cutoff, _, _ = seed_native_core(
            db,
            extra_configurations=configurations,
            raw_values={**_native_liquidity_source(), "upcoming_earnings_date": "2026-11-10"},
        )
        db.commit()
        handoff = freeze_transition_decision_handoff_manifest(
            db, upload_run_id=7, market_cutoff=cutoff
        )
        db.add(
            IBContract(
                ticker="ACME",
                ib_conid=140014,
                symbol="ACME",
                exchange="SMART",
                primary_exchange="NASDAQ",
                currency="USD",
                sec_type="STK",
                resolution_status="RESOLVED",
            )
        )
        db.commit()
        capture = WinnerPredictionCaptureService().capture_run(
            db,
            run_id=7,
            config=config,
            market_cutoff=cutoff,
            decision_handoff_manifest_id=handoff.id,
            decision_at=datetime.now(UTC),
        )
        assert capture.inserted == 1 and capture.failed == 0, capture.as_dict()
        db.commit()
        outcome = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.entry_model == "NEXT_OPEN",
                WinnerForwardOutcome.horizon_sessions == 5,
            )
        )
        assert outcome is not None
        for basis in ("ADJUSTED_LAST", "TRADES"):
            for day in required_outcome_sessions(outcome.entry_session, outcome.horizon_sessions):
                bar = PriceBar(
                    ticker="ACME",
                    bar_date=day,
                    timeframe="1 day",
                    what_to_show=basis,
                    open=100,
                    high=110,
                    low=99,
                    close=108,
                    volume=100000,
                    source="T14D_EXPLICIT_OBSERVATION",
                )
                bar.data_hash = price_bar_data_hash(bar)
                db.add(bar)
        db.commit()
        operation_at = datetime.combine(
            outcome.due_session + timedelta(days=1), time(22), tzinfo=UTC
        )
        from app.models.tables import WinnerMarketDataObligation
        from app.services.winner_probability.market_data_obligation_service import (
            MarketDataObligationService,
        )

        obligations = list(
            db.scalars(
                select(WinnerMarketDataObligation).where(
                    WinnerMarketDataObligation.forward_outcome_id == outcome.id
                )
            )
        )
        evaluated = MarketDataObligationService().evaluate(
            db, obligations=obligations, now=operation_at
        )
        assert evaluated.satisfied == 2, evaluated.as_dict()
        db.commit()
        manifest = build_maturation_canary_manifest(db, [outcome.id])
        outcome_id = outcome.id
    with pytest.raises(EntryPointAuthorityError, match="LEGACY_MUTATION_RETIRED"):
        execute_reviewed_maturation_canary(
            lambda: pytest.fail("retired write must not open a transaction"),
            manifest,
            reviewed_manifest_hash=canonical_canary_hash(manifest),
            approve_write=True,
            actor="native-test",
            request_key="t14d-native-canary",
            now=operation_at,
        )

    with Session(contextual_engine) as db:
        retained = db.get(WinnerForwardOutcome, outcome_id)
        assert retained.status == "PENDING"
        assert retained.close_return_pct is None
        assert retained.matured_at is None
        assert retained.metadata_json.get("native_pending_proof") is not None


def test_model_retirement_api_delivers_exact_new_governance_and_maps_rejections(
    contextual_engine, monkeypatch
):
    from datetime import timedelta
    from types import SimpleNamespace

    from test_t14c_winner_writer_postgresql import _model_context
    from test_winner_maturation_canary_postgresql import _seed_native_ready_outcome

    from app.models.tables import PipelineRun, WinnerModelVersion, WinnerOutcomeDefinition
    from app.routers import winner_probability_routes
    from app.services.configuration_delivery import binding_reference, load_configuration_delivery
    from app.services.winner_probability.api_service import (
        WinnerProbabilityApiError,
        WinnerProbabilityApiService,
    )
    from app.services.winner_probability.model_artifact_service import artifact_hash
    from app.services.winner_probability.model_authority import baseline_artifact
    from app.services.winner_probability.model_registry import ModelRegistry

    with Session(contextual_engine) as db:
        _seed_native_ready_outcome(db, ticker="T14DGOV", run_suffix="t14d-governance")
        pipeline = db.scalar(select(PipelineRun))
        delivery = load_configuration_delivery(
            db, binding_reference(db, pipeline_run_id=pipeline.id)
        )
        config = delivery.configurations["decision.winner.generation"].winner_config()
        definition = db.scalar(
            select(WinnerOutcomeDefinition).where(WinnerOutcomeDefinition.is_primary)
        )
        artifact = baseline_artifact(config, definition.id)
        arguments = {
            "model_key": "t14d-native-admin-governance",
            "algorithm": "cohort",
            "outcome_definition_id": definition.id,
            "entry_model": definition.entry_model,
            "training_cutoff_at": datetime.now(UTC) - timedelta(days=1),
            "artifact_hash": artifact_hash(artifact),
            "artifact_format": "cohort_baseline",
            "artifact_schema_version": "winner-model-artifact-v1",
            "feature_schema_version": config.feature_schema.version,
            "calculation_version": config.engine.calculation_version,
            "config_hash": config.config_hash,
        }
        prototype = WinnerModelVersion(
            **arguments,
            hyperparameters_json={},
            metrics_json={},
            preprocessing_json={"feature_order": ["setup_family"]},
            calibration_json={"method": "beta_binomial", "version": "1"},
            dependency_versions_json={},
        )
        model = ModelRegistry().register_model(
            db,
            **arguments,
            actor="native-test",
            reason="create",
            config=config,
            artifact_payload=artifact,
            preprocessing=prototype.preprocessing_json,
            calibration=prototype.calibration_json,
            mutation_context=_model_context(prototype, config, "register_model", "create"),
        )
        db.commit()
        model_id = model.id
        # The governance operation is later than the persisted model birth.
        # A wall-clock sample taken before registration races PostgreSQL's
        # server-side created_at and can make this positive test fail.
        at = model.created_at + timedelta(seconds=1)
        service = WinnerProbabilityApiService()
        with pytest.raises(WinnerProbabilityApiError) as before_birth:
            service.retire_model(
                db,
                model_id=model_id,
                actor="native-test",
                reason="retire",
                operation_at=model.created_at - timedelta(microseconds=1),
            )
        assert before_birth.value.code == "MUTATION_WINNER_MODEL_NOT_KNOWN_AT_OPERATION"
        assert db.get(WinnerModelVersion, model_id).status == "SHADOW"
        for invalid_time in (None, datetime.now()):
            db.add(UploadRun(id=1993, filename="rejected-governance.csv", status="COMPLETED"))
            with pytest.raises(WinnerProbabilityApiError) as error:
                service.retire_model(
                    db,
                    model_id=model_id,
                    actor="native-test",
                    reason="retire",
                    operation_at=invalid_time,
                )
            assert error.value.status_code == 409
            assert error.value.code == "MUTATION_WINNER_MODEL_AWARE_OPERATION_TIME_REQUIRED"
            assert db.get(UploadRun, 1993) is None
            assert db.get(WinnerModelVersion, model_id).status == "SHADOW"
        retained_key = model.model_key
        model.model_key = "unstored-model"
        with pytest.raises(WinnerProbabilityApiError) as error:
            service.retire_model(
                db, model_id=model_id, actor="native-test", reason="retire", operation_at=at
            )
        assert error.value.status_code == 409
        assert db.get(WinnerModelVersion, model_id).model_key == retained_key
        result = service.retire_model(
            db, model_id=model_id, actor="native-test", reason="retire", operation_at=at
        )
        db.commit()
        assert result["model"]["status"] == "RETIRED"
        monkeypatch.setattr(winner_probability_routes, "_require_local_admin", lambda *_: None)
        request = SimpleNamespace(client=SimpleNamespace(host="native-test"))
        delivered = winner_probability_routes.retire_winner_probability_model(
            request, model_id, db, reason="Explicit new API governance request"
        )
        assert delivered["model"]["status"] == "RETIRED"
        with pytest.raises(HTTPException) as error:
            winner_probability_routes.retire_winner_probability_model(request, 987654321, db)
        assert error.value.status_code == 404
