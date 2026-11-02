"""Real preferred-loader regressions for public Technical calendar admission."""

from concurrent.futures import Future
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
from sqlalchemy import JSON, Column, MetaData, Table, create_engine, event, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.models.tables import (
    BackgroundJob,
    MarketCalculationContext,
    PipelineRun,
    PriceBar,
    PriceBarRevision,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services import technical_score_service as technical
from app.services import technical_work
from app.services.background_job_service import record_job_progress
from app.services.calculation_identity import CalculationIdentity
from app.services.core_effective_configuration import (
    CoreEffectiveConfiguration,
    freeze_core_configuration,
    resolve_combined_configuration,
    resolve_technical_configuration,
)
from app.services.market_calculation_context_service import cutoff_from_row
from app.services.market_clock_service import MarketClockService
from app.services.pipeline_executor import _technical_job_checkpoint
from app.services.setup_lifecycle import transition_candidate_service as transition
from app.services.setup_lifecycle.source_loader import RunSourceContext, TickerSourceContext
from app.services.us_market_calendar import (
    CALENDAR_VERSION,
    LEGACY_CALENDAR_VERSION,
    subtract_us_trading_sessions,
)
from app.settings import Settings

NOW = datetime(2025, 1, 10, 22, tzinfo=UTC)
TARGET = date(2025, 1, 10)
CONTEXT_FIELDS = (
    "id", "pipeline_run_id", "upload_run_id", "cutoff_at", "exchange_timezone",
    "latest_completed_session", "daily_bar_ready_at", "calendar_version",
    "bar_readiness_version", "cutoff_reason",
)


def seed_real_cache(path):
    """Use the checkpoint's exact numerical inputs, plus an actual upload owner."""
    engine = create_engine("sqlite:///" + path.as_posix())
    for model in (
        PriceBar, PriceBarRevision, RawCompanyRow, UploadRun, MarketCalculationContext, PipelineRun
    ):
        Table(
            model.__tablename__, MetaData(),
            *[
                Column(
                    c.name, JSON() if isinstance(c.type, JSONB) else c.type,
                    primary_key=c.primary_key,
                )
                for c in model.__table__.columns
            ],
        ).create(engine)
    days = [subtract_us_trading_sessions(TARGET, i) for i in reversed(range(400))]
    assert date(2025, 1, 9) not in days
    rows = []
    for idx, ticker in enumerate(("MSFT", "SPY", "QQQ", "XLK")):
        t = np.arange(len(days))
        close = 100 + (0.05 + idx * 0.01) * t + 2 * np.sin(t / 9)
        for i, day in enumerate(days):
            rows.append(dict(
                id=len(rows) + 1, ticker=ticker, bar_date=day, timeframe="1 day",
                what_to_show="TRADES", source="CHECKPOINT_SYNTHETIC",
                open=float(close[i] - 0.15), high=float(close[i] + 1.1),
                low=float(close[i] - 1.2), close=float(close[i]),
                volume=float(2_000_000 + 50000 * np.cos(i / 4)),
                created_at=NOW - timedelta(minutes=5), first_seen_at=NOW - timedelta(minutes=5),
                last_seen_at=NOW - timedelta(minutes=5), revision_count=0,
            ))
    contexts = []
    for context_id, version in ((88, LEGACY_CALENDAR_VERSION), (89, CALENDAR_VERSION)):
        c = MarketClockService(calendar_version=version).cutoff_for(
            NOW, reason="PUBLIC_ENTRYPOINT_PROBE"
        )
        contexts.append(dict(
            id=context_id, pipeline_run_id=None, upload_run_id=1, cutoff_at=c.cutoff_at,
            exchange_timezone=c.exchange_timezone,
            latest_completed_session=c.latest_completed_session,
            daily_bar_ready_at=c.daily_bar_ready_at, calendar_version=c.calendar_version,
            bar_readiness_version=c.bar_readiness_version, cutoff_reason=c.cutoff_reason,
        ))
    with engine.begin() as db:
        db.execute(UploadRun.__table__.insert(), [
            dict(id=1, filename="checkpoint.csv", status="UPLOADED", uploaded_at=NOW)
        ])
        db.execute(PriceBar.__table__.insert(), rows)
        db.execute(MarketCalculationContext.__table__.insert(), contexts)
    return engine


def retained_cutoff(engine, version):
    with Session(engine) as db:
        row = db.get(MarketCalculationContext, 88 if version == LEGACY_CALENDAR_VERSION else 89)
        # Restore SQLite's omitted UTC offset on a detached read projection only.
        values = {key: getattr(row, key) for key in CONTEXT_FIELDS}
    values["cutoff_at"] = values["cutoff_at"].replace(tzinfo=UTC)
    values["daily_bar_ready_at"] = values["daily_bar_ready_at"].replace(tzinfo=UTC)
    return cutoff_from_row(SimpleNamespace(**values))


def technical_configuration(settings):
    return resolve_technical_configuration(
        pine=technical.load_pine_defaults(),
        v4=technical.load_technical_scoring_v4_config(),
        v5=technical.load_technical_scoring_v5_config(),
        settings=settings,
    )


@pytest.fixture
def cached(tmp_path, monkeypatch):
    path = tmp_path / "technical-calendar.sqlite3"
    engine = seed_real_cache(path)
    settings = Settings(_env_file=None, job_worker_enabled=False, technical_worker_processes=1)
    monkeypatch.setattr(technical, "get_settings", lambda: settings)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda _c, _s, sql, *_a: statements.append(sql))
    yield SimpleNamespace(
        engine=engine, path=path, settings=settings, statements=statements,
        current=retained_cutoff(engine, CALENDAR_VERSION),
        legacy=retained_cutoff(engine, LEGACY_CALENDAR_VERSION),
        configuration=technical_configuration(settings),
    )
    engine.dispose()


def invoke(kind, db, cutoff, cached):
    arguments = dict(market_cutoff=cutoff)
    if kind == "preview":
        return technical.preview_run_technicals(db, 1, ["MSFT"], **arguments)
    if kind == "finalize":
        return technical.finalize_technical_scores(db, 1, [], persist=False, **arguments)
    arguments.update(pipeline_run_id=37, effective_configuration=cached.configuration)
    if kind == "score":
        return technical.score_run_technicals(db, 1, ["MSFT"], **arguments)
    coordinator = technical.TechnicalScoringOverlapCoordinator(
        db, run_id=1, tickers=["MSFT"], settings=cached.settings, **arguments
    )
    coordinator.abort()
    return coordinator


@pytest.mark.parametrize("kind", ["preview", "score", "overlap", "finalize"])
@pytest.mark.parametrize("fault", ["legacy", "unknown", "inconsistent"])
def test_public_execution_rejects_invalid_calendar_before_reads_or_pending_flush(
    cached, monkeypatch, kind, fault
):
    cutoff, error = {
        "legacy": (cached.legacy, "LEGACY_CALENDAR_EXECUTION_NOT_SUPPORTED"),
        "unknown": (replace(cached.current, calendar_version="unknown-retained-calendar"),
                    "unsupported US equity calendar"),
        "inconsistent": (replace(cached.current, latest_completed_session=date(2025, 1, 8)),
                         "MARKET_CUTOFF_CALENDAR_MISMATCH"),
    }[fault]
    frozen = deepcopy(cutoff)
    forbidden = Mock(side_effect=AssertionError("calendar admission must precede work"))
    for module, name in (
        (technical, "_call_price_frame"), (technical, "_build_work_item"),
        (technical, "execute_technical_work_item"), (technical, "calculate_technical_features"),
        (technical, "rank_technical_universe"), (technical, "ProcessPoolExecutor"),
        (technical_work, "calculate_technical_features"),
    ):
        monkeypatch.setattr(module, name, forbidden)
    before = sha256(cached.path.read_bytes()).hexdigest()
    cached.statements.clear()
    with Session(cached.engine) as db:
        pending = UploadRun(id=999, filename="unrelated-pending.csv", status="UPLOADED")
        db.add(pending)
        with pytest.raises(ValueError, match=error):
            invoke(kind, db, cutoff, cached)
        assert pending in db.new
        assert cached.statements == []
    assert forbidden.call_count == 0
    assert cutoff == frozen
    assert sha256(cached.path.read_bytes()).hexdigest() == before
    assert retained_cutoff(cached.engine, LEGACY_CALENDAR_VERSION) == cached.legacy


def test_current_real_preview_matches_checkpoint_scores_and_keeps_evidence_read_only(
    cached, monkeypatch
):
    original = technical.execute_technical_work_item
    observed = []

    def execute(item):
        result = original(item)
        observed.append(result)
        return result

    monkeypatch.setattr(technical, "execute_technical_work_item", execute)
    before = sha256(cached.path.read_bytes()).hexdigest()
    cached.statements.clear()
    with Session(cached.engine) as db:
        scores = technical.preview_run_technicals(db, 1, ["MSFT"], market_cutoff=cached.current)
    assert len(observed) == len(scores) == 1
    assert observed[0].error is None and observed[0].score is not None
    assert observed[0].feature_result["insufficient_data"] is False
    score = scores[0]
    assert score.calendar_version == CALENDAR_VERSION
    assert score.dual_score == Decimal("7.2211")  # original independent pre-fix v2 control
    assert score.classification == "No trade"  # pre-fix v2 control export
    assert score.action_bias == "No clear trade"
    assert score.technical_confidence == "high"
    assert any("price_bars" in sql for sql in cached.statements)
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                   for sql in cached.statements)
    assert sha256(cached.path.read_bytes()).hexdigest() == before
    assert retained_cutoff(cached.engine, LEGACY_CALENDAR_VERSION) == cached.legacy


class ImmediateExecutor:
    """Exercise the native overlap path without spawning subprocesses."""

    def __init__(self, max_workers):
        self.max_workers = max_workers

    def submit(self, fn, item):
        future = Future()
        try:
            future.set_result(fn(item))
        except Exception as exc:
            future.set_exception(exc)
        return future

    def shutdown(self, wait=True, cancel_futures=False):
        pass


def test_current_overlap_constructs_and_executes_real_native_work(cached, monkeypatch):
    monkeypatch.setattr(technical, "ProcessPoolExecutor", ImmediateExecutor)
    observed = []
    original = technical.execute_technical_work_item

    def execute(item):
        result = original(item)
        observed.append(result)
        return result

    monkeypatch.setattr(technical, "execute_technical_work_item", execute)
    with Session(cached.engine) as db:
        coordinator = technical.TechnicalScoringOverlapCoordinator(
            db, run_id=1, tickers=["MSFT"], settings=cached.settings,
            market_cutoff=cached.current, pipeline_run_id=37,
            effective_configuration=cached.configuration,
        )
        try:
            coordinator.on_ticker_ready(technical.TickerReadyEvent(
                ticker="MSFT", statuses=("SUCCESS",), failed=False, completed_at=NOW
            ))
            coordinator.mark_fetch_complete()
            assert observed and observed[0].score is not None
            assert observed[0].error is None
            assert coordinator.market_cutoff == cached.current
        finally:
            coordinator.abort()
    assert any("price_bars" in sql for sql in cached.statements)
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                   for sql in cached.statements)


def test_current_direct_finalization_performs_native_conversion_without_writes(cached):
    price = None
    with Session(cached.engine) as db:
        price, volume = technical.load_preferred_ohlcv_frames(
            db, "MSFT", max_session=TARGET, as_of=NOW, calculation_context_id=89
        )
        features = technical.calculate_technical_features(
            price, volume, ticker="MSFT", expected_session=TARGET,
        )
        base = technical.score_from_feature_result(
            features, params=technical.load_pine_defaults(),
            v4_params=technical.load_technical_scoring_v4_config(),
        )
        scores = technical.finalize_technical_scores(
            db, 1, [base], settings=cached.settings, market_cutoff=cached.current, persist=False
        )
        assert len(scores) == 1
        assert isinstance(scores[0], TechnicalScore)
        assert scores[0].calendar_version == CALENDAR_VERSION
        assert scores[0].dual_score is not None
        assert not db.new and not db.dirty
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                   for sql in cached.statements)


@pytest.mark.parametrize("version", [LEGACY_CALENDAR_VERSION, CALENDAR_VERSION])
def test_transition_discovery_uses_guarded_real_previewer(cached, monkeypatch, version):
    cutoff = cached.legacy if version == LEGACY_CALENDAR_VERSION else cached.current
    source = Mock()
    ticker_context = TickerSourceContext(
        raw_row=RawCompanyRow(id=1, run_id=1, ticker="MSFT"), market_cutoff=cutoff,
    )
    context = RunSourceContext(
        upload_run=UploadRun(id=1), market_regime_snapshot=None,
        sector_rotation_snapshot=None, tickers=(ticker_context,), market_cutoff=cutoff,
    )
    source.load_run_context.return_value = context
    built = Mock(return_value=[])
    monkeypatch.setattr(transition, "build_run_context_snapshots", built)
    service = transition.TransitionCandidateDiscoveryService(
        source_loader=source, full_universe_technical_preview=False
    )
    assert service.technical_previewer is technical.preview_run_technicals
    cached.statements.clear()
    with Session(cached.engine) as db:
        if version == LEGACY_CALENDAR_VERSION:
            with pytest.raises(ValueError, match="LEGACY_CALENDAR_EXECUTION_NOT_SUPPORTED"):
                service.discover_for_run(db, 1, market_cutoff=cutoff, tickers={"MSFT"})
            assert cached.statements == []
            built.assert_not_called()
        else:
            assert service.discover_for_run(db, 1, market_cutoff=cutoff, tickers={"MSFT"}) == []
            assert any("price_bars" in sql for sql in cached.statements)
            built.assert_called_once()
            reconstructed = built.call_args.args[1].tickers[0].technical_score
            assert reconstructed.ticker == "MSFT"
            assert reconstructed.calendar_version == CALENDAR_VERSION
            assert reconstructed.dual_score == Decimal("7.2211")
    assert ticker_context.technical_score is None
    assert source.load_run_context.call_args.kwargs["market_cutoff"] == cutoff
    assert not any(sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
                   for sql in cached.statements)


@pytest.fixture
def progress_cached(cached):
    Table(
        BackgroundJob.__tablename__, MetaData(),
        *[
            Column(c.name, JSON() if isinstance(c.type, JSONB) else c.type,
                   primary_key=c.primary_key)
            for c in BackgroundJob.__table__.columns
        ],
    ).create(cached.engine)
    with cached.engine.begin() as db:
        db.execute(BackgroundJob.__table__.insert(), [dict(
            id=1, job_type="CALENDAR_TEST_FIXTURE_ONLY", status="RUNNING",
            execution_token="local-disposable-fixture", progress_sequence=0,
        )])
    return cached


def real_progress_callback(db, events):
    from app.services.pipeline_executor import (
        close_pipeline_read_transaction,
        commit_pipeline_mutation_checkpoint,
    )

    def progress(progress_db, *, close_read_transaction=False,
                 commit_mutation_transaction=False, **values):
        # Use main's worker adapter contract, including its actual ownership guards.
        if commit_mutation_transaction:
            commit_pipeline_mutation_checkpoint(progress_db)
        if close_read_transaction:
            close_pipeline_read_transaction(progress_db)
        return record_job_progress(
            progress_db, job_id=1, execution_token="local-disposable-fixture", **values
        )

    def checkpoint(**values):
        events.append(values)
        _technical_job_checkpoint(db, progress_callback=progress, lease_guard=None, **values)

    return checkpoint


def invalid_configuration(cached, fault):
    if fault == "wrong_family":
        return resolve_combined_configuration(), "core configuration family mismatch"
    values = cached.configuration.values
    if fault == "incomplete":
        values.pop("v5")
        error = "incomplete core effective configuration"
    else:
        values[fault.removeprefix("empty_")] = {}
        error = "Technical requires resolved Pine and v4 parameters"
    return freeze_core_configuration("core.technical", values), error


@pytest.mark.parametrize("fault", [
    "legacy", "unknown", "inconsistent", "missing_cutoff", "missing_pipeline",
    "missing_configuration", "wrong_family", "retry", "incomplete", "empty_pine", "empty_v4",
])
def test_score_admission_precedes_real_progress_flush(progress_cached, monkeypatch, fault):
    cached = progress_cached
    arguments = dict(market_cutoff=cached.current, pipeline_run_id=37,
                     effective_configuration=cached.configuration)
    error = "TECHNICAL_EXPLICIT_CALCULATION_AUTHORITY_REQUIRED"
    if fault == "legacy":
        arguments["market_cutoff"] = cached.legacy
        error = "LEGACY_CALENDAR_EXECUTION_NOT_SUPPORTED"
    elif fault == "unknown":
        arguments["market_cutoff"] = replace(cached.current, calendar_version="unknown")
        error = "unsupported US equity calendar"
    elif fault == "inconsistent":
        arguments["market_cutoff"] = replace(
            cached.current, latest_completed_session=date(2025, 1, 8)
        )
        error = "MARKET_CUTOFF_CALENDAR_MISMATCH"
    elif fault == "retry":
        other = technical_configuration(cached.settings.model_copy(update={
            "technical_v5_enabled": not cached.settings.technical_v5_enabled,
        }))
        arguments["expected_calculation_identity"] = other.bind(
            CalculationIdentity.legacy_unknown(run_id=1, ticker="MSFT")
        )
        error = "CORE_CONFIGURATION_RETRY_MISMATCH"
    elif fault in {"wrong_family", "incomplete", "empty_pine", "empty_v4"}:
        arguments["effective_configuration"], error = invalid_configuration(cached, fault)
    else:
        arguments[{
            "missing_cutoff": "market_cutoff", "missing_pipeline": "pipeline_run_id",
            "missing_configuration": "effective_configuration",
        }[fault]] = None
    frozen = deepcopy(arguments["market_cutoff"])
    frozen_configuration = deepcopy(arguments["effective_configuration"])
    native = Mock(side_effect=AssertionError("native work before admission"))
    monkeypatch.setattr(technical, "execute_technical_work_item", native)
    callbacks, transaction_events = [], []
    before = sha256(cached.path.read_bytes()).hexdigest()
    cached.statements.clear()
    with Session(cached.engine) as db:
        event.listen(db, "before_flush", lambda *_a: transaction_events.append("flush"))
        event.listen(db, "after_begin", lambda *_a: transaction_events.append("begin"))
        pending = UploadRun(id=999, filename="unrelated-pending.csv", status="UPLOADED")
        db.add(pending)
        with pytest.raises(ValueError, match=error):
            technical.score_run_technicals(
                db, 1, **arguments,
                checkpoint_callback=real_progress_callback(db, callbacks),
            )
        assert callbacks == []
        assert transaction_events == []
        assert cached.statements == []
        assert pending in db.new
        native.assert_not_called()
        assert arguments["market_cutoff"] == frozen
        assert arguments["effective_configuration"] == frozen_configuration
        # Diagnostic reads happen only after asserting the zero-SQL boundary.
        with db.no_autoflush:
            assert db.execute(select(
                BackgroundJob.progress_stage, BackgroundJob.progress_current_item,
                BackgroundJob.progress_sequence,
            ).where(BackgroundJob.id == 1)).one() == (None, None, 0)
        assert pending in db.new
        db.rollback()
    assert sha256(cached.path.read_bytes()).hexdigest() == before
    assert retained_cutoff(cached.engine, LEGACY_CALENDAR_VERSION) == cached.legacy


def test_current_score_retains_real_progress_order_and_native_result(progress_cached, monkeypatch):
    cached = progress_cached
    cached.settings.technical_pure_boundary_enabled = True
    order, callbacks, native_results = [], [], []
    original_guard = technical.require_current_execution_calendar
    original_family = CoreEffectiveConfiguration.require_family
    original_retry = CoreEffectiveConfiguration.require_retry_identity
    original_loader = technical._call_price_frame
    original_execute = technical.execute_technical_work_item
    original_finalize = technical.finalize_technical_scores

    def admit(cutoff):
        original_guard(cutoff)
        order.append("CALENDAR_ADMITTED")

    def admit_configuration(configuration, namespace):
        original_family(configuration, namespace)
        order.append("CONFIG_ADMITTED")

    def admit_retry(configuration, identity):
        original_retry(configuration, identity)
        order.append("RETRY_ADMITTED")

    def load(*args, **kwargs):
        order.append("INPUT")
        return original_loader(*args, **kwargs)

    def execute(item):
        order.append("NATIVE")
        result = original_execute(item)
        native_results.append(result)
        return result

    def finalize(*args, **kwargs):
        # Exercise actual conversion without fabricating pipeline publication authority.
        kwargs.update(persist=False, pipeline_run_id=None)
        return original_finalize(*args, **kwargs)

    monkeypatch.setattr(technical, "require_current_execution_calendar", admit)
    monkeypatch.setattr(CoreEffectiveConfiguration, "require_family", admit_configuration)
    monkeypatch.setattr(CoreEffectiveConfiguration, "require_retry_identity", admit_retry)
    resolver = Mock(side_effect=AssertionError("supplied frozen configuration must be reused"))
    monkeypatch.setattr(technical, "resolve_technical_configuration", resolver)
    monkeypatch.setattr(technical, "_call_price_frame", load)
    monkeypatch.setattr(technical, "execute_technical_work_item", execute)
    monkeypatch.setattr(technical, "finalize_technical_scores", finalize)
    with Session(cached.engine) as db:
        progress = real_progress_callback(db, callbacks)

        def checkpoint(**values):
            order.append(values["phase"])
            progress(**values)

        pending = UploadRun(id=999, filename="admitted-pending.csv", status="UPLOADED")
        db.add(pending)
        cached.statements.clear()
        scores = technical.score_run_technicals(
            db, 1, ["MSFT"], market_cutoff=cached.current, pipeline_run_id=37,
            effective_configuration=cached.configuration, checkpoint_callback=checkpoint,
            expected_calculation_identity=cached.configuration.bind(
                CalculationIdentity.legacy_unknown(run_id=1, ticker="MSFT")
            ),
        )
        assert order[:5] == [
            "CALENDAR_ADMITTED", "CONFIG_ADMITTED", "RETRY_ADMITTED", "PREPARING", "INPUT",
        ]
        resolver.assert_not_called()
        assert [c["phase"] for c in callbacks] == [
            "PREPARING", "CALCULATING", "CALCULATING", "CALCULATED",
            "FINALIZING", "FINALIZING", "PUBLISHED",
        ]
        assert [c["processed"] for c in callbacks] == [0, 0, 1, 1, 0, 1, 1]
        assert [c["total"] for c in callbacks] == [0, 1, 1, 1, 1, 1, 1]
        assert order.index("NATIVE") > order.index("PREPARING")
        assert len(native_results) == len(scores) == 1
        assert native_results[0].error is None
        assert native_results[0].feature_result["insufficient_data"] is False
        assert scores[0].dual_score == Decimal("7.2211")
        assert scores[0].classification == "No trade"
        assert scores[0].action_bias == "No clear trade"
        assert scores[0].technical_confidence == "high"
        assert scores[0].calendar_version == CALENDAR_VERSION
        assert pending not in db.new
        updates = [s for s in cached.statements
                   if s.lstrip().upper().startswith("UPDATE BACKGROUND_JOBS")]
        assert len(updates) == 7
        assert db.execute(select(
            BackgroundJob.progress_stage, BackgroundJob.progress_current_item,
            BackgroundJob.progress_sequence, BackgroundJob.progress_processed,
            BackgroundJob.progress_total, BackgroundJob.checkpoint_version,
        ).where(BackgroundJob.id == 1)).one() == (
            "SCORING_TECHNICALS", "PUBLISHED:-", 7, 1, 1, "technical-recovery-v1",
        )
    assert retained_cutoff(cached.engine, LEGACY_CALENDAR_VERSION) == cached.legacy


def test_current_cancellation_keeps_first_real_progress_checkpoint(progress_cached, monkeypatch):
    cached = progress_cached
    forbidden = Mock(side_effect=AssertionError("cancelled before inputs/native work"))
    monkeypatch.setattr(technical, "_call_price_frame", forbidden)
    monkeypatch.setattr(technical, "execute_technical_work_item", forbidden)
    callbacks = []
    with Session(cached.engine) as db:
        with pytest.raises(
            technical.TechnicalScoringError, match="cancelled at a durable checkpoint"
        ):
            technical.score_run_technicals(
                db, 1, ["MSFT"], market_cutoff=cached.current, pipeline_run_id=37,
                effective_configuration=cached.configuration, should_cancel=lambda: True,
                checkpoint_callback=real_progress_callback(db, callbacks),
            )
        assert [c["phase"] for c in callbacks] == ["PREPARING"]
        assert db.execute(select(
            BackgroundJob.progress_current_item, BackgroundJob.progress_sequence,
        ).where(BackgroundJob.id == 1)).one() == ("PREPARING:-", 1)
        forbidden.assert_not_called()


@pytest.mark.parametrize("fault", [
    "cutoff", "pipeline", "configuration", "readonly_configuration", "readonly_cutoff",
    "wrong_family", "incomplete", "empty_pine", "empty_v4",
])
def test_finalization_authority_precedes_transaction_flush(progress_cached, monkeypatch, fault):
    cached = progress_cached
    arguments = dict(market_cutoff=cached.current, pipeline_run_id=37,
                     effective_configuration=cached.configuration)
    if fault in {"wrong_family", "incomplete", "empty_pine", "empty_v4"}:
        arguments["effective_configuration"], error = invalid_configuration(cached, fault)
    else:
        arguments[{
            "cutoff": "market_cutoff", "pipeline": "pipeline_run_id",
            "configuration": "effective_configuration",
            "readonly_configuration": "effective_configuration", "readonly_cutoff": "market_cutoff",
        }[fault]] = None
        error = "TECHNICAL_MUTATION_AUTHORITY_REQUIRED"
        if fault.startswith("readonly_"):
            arguments["persist"] = False
            error = (
                "certified Technical finalization requires configuration frozen "
                "before feature construction"
                if fault == "readonly_configuration"
                else "Technical identity production requires an explicit market_cutoff"
            )
    frozen = deepcopy(cached.current)
    frozen_configuration = deepcopy(arguments["effective_configuration"])
    native = Mock(side_effect=AssertionError("native work before finalization admission"))
    monkeypatch.setattr(technical, "rank_technical_universe", native)
    events, callbacks = [], []
    cached.statements.clear()
    with Session(cached.engine) as db:
        event.listen(db, "before_flush", lambda *_a: events.append("flush"))
        event.listen(db, "after_begin", lambda *_a: events.append("begin"))
        pending = UploadRun(id=999, filename="unrelated-pending.csv", status="UPLOADED")
        db.add(pending)
        with pytest.raises(ValueError, match=error):
            technical.finalize_technical_scores(
                db, 1, [], **arguments, checkpoint_callback=real_progress_callback(db, callbacks)
            )
        assert cached.statements == []
        assert events == []
        assert pending in db.new
        assert callbacks == []
        native.assert_not_called()
        assert cached.current == frozen
        assert arguments["effective_configuration"] == frozen_configuration
        with db.no_autoflush:
            assert db.execute(select(
                BackgroundJob.progress_stage, BackgroundJob.progress_sequence,
            ).where(BackgroundJob.id == 1)).one() == (None, 0)
        assert pending in db.new


@pytest.mark.parametrize("body_failure", [False, True])
def test_valid_finalization_retains_caller_transaction_ownership(cached, monkeypatch, body_failure):
    if body_failure:
        def fail_after_admission(*_args, **_kwargs):
            raise RuntimeError("injected finalization body failure")

        monkeypatch.setattr(technical, "rank_technical_universe", fail_after_admission)
    before = sha256(cached.path.read_bytes()).hexdigest()
    with Session(cached.engine) as db:
        db.add(UploadRun(id=999, filename="admitted-pending.csv", status="UPLOADED"))
        arguments = dict(
            settings=cached.settings, market_cutoff=cached.current,
            effective_configuration=cached.configuration, persist=False,
        )
        if body_failure:
            with pytest.raises(RuntimeError, match="injected finalization body failure"):
                technical.finalize_technical_scores(db, 1, [], **arguments)
            assert not db.in_transaction()
        else:
            assert technical.finalize_technical_scores(db, 1, [], **arguments) == []
            assert db.in_transaction()
            # Releasing the writer savepoint must not commit the caller's transaction.
            with Session(cached.engine) as observer:
                assert observer.get(UploadRun, 999) is None
            db.rollback()
    with Session(cached.engine) as observer:
        assert observer.get(UploadRun, 999) is None
    assert sha256(cached.path.read_bytes()).hexdigest() == before
