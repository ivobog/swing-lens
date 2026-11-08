"""Disposable NORMAL workers with real handlers and an external IB replay only."""

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path


def native_worker(url, role, environment, events, evidence):
    os.environ.update(
        environment,
        DATABASE_URL=url,
        JOB_WORKER_PROFILE=role,
        JOB_WORKER_ID="native-" + role,
        RUNTIME_MODE="NORMAL",
    )
    if environment.get("NATIVE_RUNTIME_DIR"):
        os.chdir(environment["NATIVE_RUNTIME_DIR"])
    from app.settings import get_settings

    get_settings.cache_clear()
    from single_run_certification.frozen_ceri import frozen_ceri_registry
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session, sessionmaker

    from app.models.tables import PipelineRun
    from app.services import (
        ib_connection,
        ib_fetch_executor,
        ib_gateway_health_service,
        ib_historical_capability,
    )
    from app.services.background_worker import default_job_handlers, run_worker_once
    from app.services.ceri import batched_job_handlers, batched_workflow

    registry = frozen_ceri_registry()
    batched_job_handlers.CeriProviderRegistry = lambda *_args, **_kwargs: registry
    batched_workflow.CeriProviderRegistry = lambda *_args, **_kwargs: registry
    log = Path(evidence) / (role + ".jsonl")

    def record(kind, **values):
        with log.open("a", encoding="utf-8") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": kind,
                        "at": datetime.now(UTC).isoformat(),
                        "pid": os.getpid(),
                        **values,
                    },
                    default=str,
                )
                + "\n"
            )

    class ReplayIB:
        connected = False

        def __init__(self):
            from types import SimpleNamespace

            self.client = SimpleNamespace(
                connect=self.handshake, isReady=lambda: self.connected, serverVersion=lambda: 180
            )

        def handshake(self, *_args, **kwargs):
            self.connected = True
            record("health_handshake", client_id=kwargs["clientId"])

        def reqCurrentTime(self):  # noqa: N802
            return datetime.now(UTC)

        def connect(self, *_args, **kwargs):
            assert kwargs.get("readonly") is True
            self.connected = True
            self.client_id = kwargs["clientId"]
            record("connect", client_id=kwargs["clientId"])
            return self

        def disconnect(self):
            self.connected = False
            record("disconnect")

        def isConnected(self):  # noqa: N802
            return self.connected

        def qualifyContracts(self, contract):  # noqa: N802
            return [contract]

        def reqHistoricalData(self, contract, **kwargs):  # noqa: N802
            from types import SimpleNamespace

            from single_run_certification.fixtures import _ohlcv, certification_as_of_session

            record("provider_enter", symbol=contract.symbol, basis=kwargs["whatToShow"])
            if (
                role == "general"
                and self.client_id == get_settings().ib_client_id
                and not events["acquired"].is_set()
            ):
                events["acquired"].set()
                assert events["release"].wait(90), "pipeline acquisition release missing"
            if role == "sector_refresh" and not events["sector_provider"].is_set():
                events["sector_provider"].set()
                assert events["calculating"].wait(90), "native calculation boundary missing"
            rows = _ohlcv(contract.symbol, 1000, as_of_session=certification_as_of_session())
            if role == "sector_refresh":
                rows = [
                    {**row, **{key: row[key] + 7 for key in ("open", "high", "low", "close")}}
                    for row in rows
                ]
            record(
                "provider_return",
                symbol=contract.symbol,
                basis=kwargs["whatToShow"],
                bars=len(rows),
            )
            return [SimpleNamespace(**row) for row in rows]

    native_factory = ib_connection.create_ib_client

    def factory(_factory=None):
        return native_factory(ReplayIB)

    for module in (
        ib_connection,
        ib_fetch_executor,
        ib_gateway_health_service,
        ib_historical_capability,
    ):
        module.create_ib_client = factory
    # A transport escape fails even when a new native path appears.
    import ib_insync

    ib_insync.IB.connect = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("Live IB forbidden")
    )
    engine = create_engine(url, connect_args={"application_name": "sector-native-" + role})
    worker_id = "native-" + role
    try:
        handlers = default_job_handlers()
        record(
            "handlers",
            handlers={
                key: value.__module__ + "." + value.__qualname__ for key, value in handlers.items()
            },
        )
        publication_job = None
        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            worked = run_worker_once(
                worker_id=worker_id,
                worker_instance_id=worker_id + "-instance",
                operation_profile=role,
                queues=("interactive", "broker", "background"),
                stale_after_seconds=180,
                heartbeat_interval_seconds=0.25,
                session_factory=sessionmaker(bind=engine),
                handlers=handlers,
            )
            if role == "sector_refresh" and worked:
                break
            with Session(engine) as db:
                pipeline = db.scalar(
                    select(PipelineRun).where(PipelineRun.requested_by == "native-sector-followup")
                )
                if pipeline and pipeline.status in (
                    "COMPLETED",
                    "PARTIAL",
                    "FAILED",
                    "BLOCKED",
                    "CANCELLED",
                ):
                    record(
                        "pipeline_terminal",
                        pipeline_id=pipeline.id,
                        status=pipeline.status,
                        result=pipeline.result_json,
                        error=pipeline.error_message,
                    )
                    if pipeline.status not in ("COMPLETED", "PARTIAL") or not environment.get(
                        "NATIVE_PUBLICATION"
                    ):
                        break
                    if publication_job is None:
                        publication_job = prepare_native_publication(engine, pipeline.upload_run_id)
                        record("publication_admitted", job_id=publication_job)
                    else:
                        from app.models.tables import BackgroundJob

                        published_job = db.get(BackgroundJob, publication_job)
                        if published_job.status in ("COMPLETED", "PARTIAL", "FAILED", "CANCELLED"):
                            record(
                                "publication_terminal",
                                job_id=publication_job,
                                status=published_job.status,
                                result=published_job.result_json,
                            )
                            assert published_job.status == "COMPLETED", published_job.result_json
                            assert published_job.result_json["publication_status"] == "PUBLISHED", (
                                published_job.result_json
                            )
                            break
            if not worked:
                time.sleep(0.05)
        else:
            raise AssertionError("native worker deadline exceeded")
        record("worker_finished")
    except BaseException as exc:
        record("worker_error", error=repr(exc))
        raise
    finally:
        engine.dispose()


def prepare_native_publication(engine, run_id):
    """Reuse the real-writer fixture's later receipts; publish via the native queue."""
    from datetime import time as day_time
    from datetime import timedelta
    from decimal import Decimal

    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from app.models.tables import (
        WinnerForwardOutcome,
        WinnerMarketDataObligation,
        WinnerOutcomeDefinition,
        WinnerPredictionSnapshot,
        WinnerTargetStopOutcome,
    )
    from app.services.bar_cache_service import cache_bars
    from app.services.ib_data_fetcher import HistoricalBar
    from app.services.winner_probability.cohort_refresh_planner import CohortRefreshPlanner
    from app.services.winner_probability.config import load_winner_probability_config
    from app.services.winner_probability.market_data_obligation_service import (
        MarketDataObligationService,
        required_outcome_sessions,
    )
    from app.services.winner_probability.outcome_service import OutcomeMaturationService

    config = load_winner_probability_config()
    with Session(engine) as db:
        prediction = db.scalar(
            select(WinnerPredictionSnapshot).where(
                WinnerPredictionSnapshot.run_id == run_id, WinnerPredictionSnapshot.ticker == "BHE"
            )
        )
        assert prediction is not None, "native pipeline did not publish a BHE prediction"
        forward = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id == prediction.id,
                WinnerForwardOutcome.entry_model == "NEXT_OPEN",
                WinnerForwardOutcome.horizon_sessions == 5,
                WinnerForwardOutcome.is_current_revision.is_(True),
            )
        )
        assert forward is not None
        sessions = required_outcome_sessions(forward.entry_session, forward.horizon_sessions)
        operation_at = datetime.combine(forward.due_session, day_time(22), tzinfo=UTC) + timedelta(
            days=1
        )

        def operation_now():
            nonlocal operation_at
            operation_at = max(operation_at + timedelta(microseconds=1), datetime.now(UTC))
            return operation_at

        for basis in ("ADJUSTED_LAST", "TRADES"):
            bars = []
            for index, session in enumerate(sessions):
                opening = Decimal("100") + index
                bars.append(
                    HistoricalBar(
                        ticker=prediction.ticker,
                        bar_date=session,
                        timeframe="1 day",
                        open=float(opening),
                        high=float(opening + 4),
                        low=float(opening - 1),
                        close=float(opening + 2),
                        volume=2000000 + index,
                        source="QA_LATER_MARKET_BARS",
                        what_to_show=basis,
                        adjustment_type="adjusted" if basis == "ADJUSTED_LAST" else "raw",
                    )
                )
            cache_bars(db, bars)
        db.flush()
        obligations = list(
            db.scalars(
                select(WinnerMarketDataObligation).where(
                    WinnerMarketDataObligation.forward_outcome_id == forward.id
                )
            )
        )
        evaluated = MarketDataObligationService().evaluate(
            db, obligations=obligations, now=operation_now()
        )
        assert evaluated.satisfied == len(obligations), evaluated.as_dict()
        matured = OutcomeMaturationService().process_forward_outcome(
            db, forward, now=operation_now()
        )
        assert matured.matured == 1 and matured.target_stop_matured > 0, matured.as_dict()
        db.commit()
        target = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.forward_outcome_id == forward.id,
                WinnerTargetStopOutcome.is_current_revision.is_(True),
            )
        )
        definition = db.get(WinnerOutcomeDefinition, target.outcome_definition_id)
        request = CohortRefreshPlanner().request_for_current_evidence(
            db, outcome_definition=definition, config=config, observed_at=operation_now()
        )
        assert request.job is not None
        job_id = request.job.id
        db.commit()
        return job_id
