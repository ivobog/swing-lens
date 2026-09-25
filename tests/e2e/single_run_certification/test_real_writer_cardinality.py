"""Disposable full-pipeline cardinality gates with real internal writers."""

import csv
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.models.tables import IBContract
from app.services.market_clock_service import MarketClockService
from single_run_certification.fixtures import _seed_ceri_manual_evidence
from single_run_certification.reporting import CertificationRecorder
from single_run_certification.test_single_run_certification import (
    _launch_run_through_gui,
    _run_pipeline_through_gui,
    certification_environment,
    certification_page,
)


EXACT_TEN = ("BHE", "BLLN", "KLIC", "LSCC", "PDFS", "ACMR", "RDVT", "AVT", "DVN", "JNJ")


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.destructive
@pytest.mark.parametrize("size", (10, 100))
def test_frozen_real_writer_cardinality(certification_environment, certification_page, size):
    env = certification_environment
    tickers = EXACT_TEN + tuple(f"F{index:04d}" for index in range(size - len(EXACT_TEN)))
    with env.csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames
        exemplar = next(reader)
    with env.csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for ticker in tickers:
            row = {**exemplar, "Symbol": ticker, "Description": f"{ticker} frozen fixture"}
            writer.writerow(row)

    engine = create_engine(env.database_url)
    try:
        with Session(engine) as db:
            db.add_all(
                IBContract(
                    ticker=ticker,
                    ib_conid=9_500_000 + index,
                    symbol=ticker,
                    exchange="SMART",
                    primary_exchange="NASDAQ",
                    currency="USD",
                    sec_type="STK",
                    resolution_status="RESOLVED",
                )
                for index, ticker in enumerate(tickers)
            )
            db.commit()
            session = MarketClockService().cutoff_for(
                datetime.now(UTC), reason="FROZEN_REAL_WRITER_CARDINALITY"
            ).latest_completed_session
            _seed_ceri_manual_evidence(
                db, as_of_session=session, tickers=tickers, baseline_tickers=()
            )
            db.commit()

        recorder = CertificationRecorder(execution_id=env.execution_id)
        run_id = _launch_run_through_gui(certification_page, env, recorder)
        pipeline_id, status = _run_pipeline_through_gui(
            certification_page,
            env,
            recorder,
            run_id,
            absolute_deadline_seconds=2400 if size == 100 else 900,
            progress_deadline_seconds=900 if size == 100 else 180,
        )
        assert status == "COMPLETED", (size, status, env.artifact_dir)
        with engine.connect() as db:
            members = tuple(
                db.scalars(
                    text("select ticker from raw_company_rows where run_id=:run_id order by row_number"),
                    {"run_id": run_id},
                )
            )
            assert members == tickers
            stages = db.execute(
                text(
                    "select step_name, status from pipeline_steps "
                    "where pipeline_run_id=:pipeline_id order by step_order"
                ),
                {"pipeline_id": pipeline_id},
            ).all()
            assert stages and all(row.status == "COMPLETED" for row in stages), stages
            counts = {
                name: db.scalar(
                    text(f"select count(*) from {table} where run_id=:run_id"),
                    {"run_id": run_id},
                )
                for name, table in (
                    ("fundamental", "fundamental_scores"),
                    ("technical", "technical_scores"),
                    ("combined", "combined_results"),
                    ("ceri", "ceri_score_snapshots"),
                    ("setup", "setup_signal_snapshots"),
                    ("winner", "winner_prediction_snapshots"),
                )
            }
            assert counts["fundamental"] == size, counts
            assert counts["technical"] == size, counts
            assert counts["combined"] == size, counts
            assert counts["ceri"] > 0 and counts["setup"] > 0 and counts["winner"] > 0, counts
    finally:
        engine.dispose()
