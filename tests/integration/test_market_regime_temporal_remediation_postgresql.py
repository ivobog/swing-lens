from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pandas as pd
import pytest
from alembic.config import Config
from native_mutation_support import bound_pipeline, seed_price_frame
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    AcquisitionPlanRecord,
    IBFetchItem,
    IBFetchRun,
    MarketRegimeSnapshot,
    PipelineRun,
    PriceBar,
    UploadRun,
)
from app.services.bar_cache_service import price_bar_data_hash
from app.services.contextual_effective_configuration import resolve_regime_configuration
from app.services.market_regime_command_center import MarketRegimeCommandCenterService
from app.services.market_regime_repository import MarketRegimeRepository

pytestmark = [pytest.mark.integration, pytest.mark.destructive]

RUN_ID = 164
PIPELINE_ID = 155
CUTOFF = datetime(2026, 9, 24, 22, 43, 46, 618832, tzinfo=UTC)
SESSION = date(2026, 9, 24)
PRIOR_SESSION = date(2026, 9, 23)
PLAN_ID = "market-regime-retained-155".ljust(64, "0")


@pytest.fixture
def regime_recovery_engine(disposable_postgres_database):
    config = Config("alembic.ini")
    config.attributes["database_url"] = disposable_postgres_database
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def test_pipeline_owned_regime_accepts_older_effective_source_and_rejects_future(
    regime_recovery_engine,
):
    configuration = resolve_regime_configuration()
    with Session(regime_recovery_engine) as db:
        db.add(
            UploadRun(
                id=RUN_ID,
                filename="retained-run-164.csv",
                row_count=10,
                status="COMPLETED",
            )
        )
        db.add(
            AcquisitionPlanRecord(
                plan_id=PLAN_ID,
                plan_kind="REGIME_TEMPORAL_REMEDIATION",
                plan_version="v1",
                payload_json={"symbols": ["SPY", "QQQ"]},
            )
        )
        db.flush()
        cutoff, pipeline_id = bound_pipeline(
            db,
            RUN_ID,
            [configuration],
            pipeline_id=PIPELINE_ID,
            cutoff_at=CUTOFF,
        )
        assert pipeline_id == PIPELINE_ID
        assert cutoff.latest_completed_session == SESSION
        pipeline = db.get(PipelineRun, PIPELINE_ID)
        pipeline.acquisition_plan_id = PLAN_ID

        frame = _market_frame(PRIOR_SESSION)
        for ticker in ("SPY", "QQQ"):
            seed_price_frame(db, ticker, frame, cutoff.cutoff_at)
        # A normal calculation session may legitimately consume the most
        # recent prior-session close for one benchmark.  SPY is current while
        # QQQ remains on the prior completed session.
        _seed_pipeline_owned_session(db, cutoff.context_id, tickers=("SPY",))
        db.commit()

    with Session(regime_recovery_engine) as db:
        snapshot_dto = MarketRegimeCommandCenterService().build_snapshot(
            db,
            run_id=RUN_ID,
            market_cutoff=cutoff,
            effective_configuration=configuration,
        )
        snapshot = db.scalar(
            select(MarketRegimeSnapshot).where(MarketRegimeSnapshot.run_id == RUN_ID)
        )
        assert snapshot is not None
        assert snapshot_dto.as_of_date == PRIOR_SESSION
        assert snapshot.as_of_date == PRIOR_SESSION
        assert snapshot.input_as_of_session == SESSION
        assert snapshot.calculation_context_id == cutoff.context_id
        assert snapshot.calculation_cutoff_at == cutoff.cutoff_at
        assert snapshot.debug_json["temporal_lineage"]["source_latest_sessions"] == {
            "QQQ": PRIOR_SESSION.isoformat(),
            "SPY": SESSION.isoformat(),
        }
        assert snapshot.evidence_id is not None

        valid_write = MarketRegimeCommandCenterService()._snapshot_write(
            snapshot_dto,
            snapshot_dto.debug["input_symbols"],
        )
        object.__setattr__(
            valid_write,
            "_effective_configuration",
            configuration.snapshot,
        )
        future_write = replace(valid_write, as_of_date=SESSION + timedelta(days=1))
        object.__setattr__(
            future_write,
            "_effective_configuration",
            configuration.snapshot,
        )
        with pytest.raises(ValueError, match="MUTATION_ARTIFACT_TEMPORAL_MISMATCH"):
            MarketRegimeRepository().upsert_snapshot(
                db,
                future_write,
                run_id=RUN_ID,
            )
        db.rollback()


def _market_frame(end: date) -> pd.DataFrame:
    dates = pd.bdate_range(end=end, periods=300)
    return pd.DataFrame(
        {
            "date": dates,
            "open": range(100, 400),
            "high": range(102, 402),
            "low": range(99, 399),
            "close": range(101, 401),
            "volume": [1_000_000 + index for index in range(300)],
        }
    )


def _seed_pipeline_owned_session(
    db: Session,
    context_id: int,
    *,
    tickers: tuple[str, ...] = ("SPY", "QQQ"),
) -> None:
    fetch_run = IBFetchRun(
        acquisition_plan_id=PLAN_ID,
        run_id=RUN_ID,
        requested_tickers=["SPY", "QQQ"],
        symbols_including_benchmarks=["SPY", "QQQ"],
        status="COMPLETED",
        completed_at=CUTOFF + timedelta(minutes=10),
    )
    db.add(fetch_run)
    db.flush()
    for ticker in tickers:
        for feed in ("ADJUSTED_LAST", "TRADES"):
            item = IBFetchItem(
                fetch_run_id=fetch_run.id,
                ticker=ticker,
                what_to_show=feed,
                bar_size="1 day",
                status="SUCCESS",
                fetched=1,
                inserted=1,
                attempt_count=1,
                completed_at=CUTOFF + timedelta(minutes=10),
            )
            db.add(item)
            db.flush()
            row = PriceBar(
                ticker=ticker,
                bar_date=SESSION,
                timeframe="1 day",
                open=Decimal("399"),
                high=Decimal("402"),
                low=Decimal("398"),
                close=Decimal("401"),
                volume=Decimal("1000300"),
                source="IBKR",
                what_to_show=feed,
                created_at=CUTOFF + timedelta(minutes=5),
                first_seen_at=CUTOFF + timedelta(minutes=5),
                last_seen_at=CUTOFF + timedelta(minutes=5),
                first_fetch_run_id=fetch_run.id,
                first_fetch_item_id=item.id,
                revision_count=0,
            )
            row.data_hash = price_bar_data_hash(row)
            db.add(row)
    db.flush()
    assert context_id is not None
