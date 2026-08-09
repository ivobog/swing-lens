from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import (
    IBExecutionFill,
    IBHistoricalMetricRevision,
)
from app.services.ib_market_intelligence.dtos import HistoricalMetricBarDTO
from app.services.ib_market_intelligence.flex import import_flex_report
from app.services.ib_market_intelligence.repository import persist_historical_metric_bar

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_metric_revision_and_flex_import_are_idempotent(
    disposable_postgres_database: str,
) -> None:
    env = {**os.environ, "DATABASE_URL": disposable_postgres_database}
    upgrade = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert upgrade.returncode == 0, upgrade.stdout + upgrade.stderr
    engine = create_engine(disposable_postgres_database)
    dto = HistoricalMetricBarDTO(
        ticker="XYZ",
        ib_conid=123,
        session_date=date(2026, 8, 7),
        timeframe="1 day",
        metric_type="FEE_RATE",
        open_value=1.0,
        high_value=1.5,
        low_value=0.8,
        close_value=1.2,
        requested_range="60 D",
        source_semantic_type="BORROW_FEE_RATE",
    )
    changed = HistoricalMetricBarDTO(**{**dto.__dict__, "close_value": 2.2})
    with Session(engine) as db:
        row, outcome = persist_historical_metric_bar(db, dto)
        assert outcome == "INSERTED"
        _, outcome = persist_historical_metric_bar(db, dto)
        assert outcome == "UNCHANGED"
        row, outcome = persist_historical_metric_bar(db, changed)
        assert outcome == "REVISED" and row.revision_count == 1
        db.commit()
        revisions = db.scalars(select(IBHistoricalMetricRevision)).all()
        assert len(revisions) == 1

        report = (
            "AccountId,TradeID,TradeDate,TradeTime,Symbol,Buy/Sell,Quantity,"
            "TradePrice,IBCommission,Fees,Currency\n"
            "U123,E1,20260807,093000,XYZ,BUY,10,25,1,0.1,USD\n"
        )
        first = import_flex_report(
            db,
            content=report,
            query_type="TRADE_CONFIRMATIONS",
            query_id="QUERY",
            reference_code="REF",
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )
        assert first["inserted"] == 1
        db.commit()
        second = import_flex_report(
            db,
            content=report,
            query_type="TRADE_CONFIRMATIONS",
            query_id="QUERY",
            reference_code="REF2",
            now=datetime(2026, 8, 9, tzinfo=UTC),
        )
        assert second["status"] == "DUPLICATE_REPORT"
        assert len(db.scalars(select(IBExecutionFill)).all()) == 1
    engine.dispose()
