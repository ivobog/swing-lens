from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import IBFetchItem, IBFetchRun
from app.services.ib_fetch_recovery_service import (
    apply_interrupted_canary_finalization,
    build_interrupted_canary_finalization_manifest,
)


def test_interrupted_canary_finalization_is_reviewed_atomic_and_idempotent(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    started = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
    with Session(engine) as db:
        run = IBFetchRun(
            requested_tickers=["AAL", "AIR"],
            symbols_including_benchmarks=["AAL", "AIR"],
            include_benchmarks=False,
            force_refresh=False,
            force_full_backfill=False,
            decision_counts_json={},
            planned_request_count=4,
            status="RUNNING",
            started_at=started,
        )
        db.add(run)
        db.flush()
        db.add_all(
            [
                _item(run, "AAL", "TRADES", "SUCCESS", 1, fetched=20, inserted=12),
                _item(run, "AAL", "ADJUSTED_LAST", "SUCCESS", 1),
                _item(run, "AIR", "TRADES", "RUNNING", 0),
            ]
        )
        db.commit()
        run_id = run.id

    with Session(engine) as db:
        manifest = build_interrupted_canary_finalization_manifest(db, fetch_run_id=run_id)
        assert manifest["planned_request_count"] == 4
        assert manifest["materialized_item_count"] == 3
        assert manifest["unmaterialized_request_count"] == 1
        assert manifest["expected_totals"] == {
            "executed_request_count": 2,
            "failure_count": 1,
            "fetched_count": 20,
            "inserted_count": 12,
            "revised_count": 0,
            "skipped_count": 1,
            "success_count": 1,
            "unchanged_count": 0,
            "updated_count": 0,
        }
        reviewed_hash = manifest["manifest_hash"]

    with Session(engine) as db:
        result = apply_interrupted_canary_finalization(
            db,
            manifest=manifest,
            reviewed_manifest_hash=reviewed_hash,
            actor="pytest",
            request_key="pytest-run-finalization",
            approve_write=True,
            now=datetime(2026, 9, 5, 13, 0, tzinfo=UTC),
        )
        db.commit()
        assert result.changed_items == 2
        assert result.run_status == "PARTIAL"

    with Session(engine) as db:
        run = db.get(IBFetchRun, run_id)
        by_key = {(item.ticker, item.what_to_show): item for item in run.items}
        assert by_key[("AAL", "TRADES")].status == "SUCCESS"
        rejected = by_key[("AAL", "ADJUSTED_LAST")]
        assert rejected.status == "FAILED"
        assert rejected.decision_metadata_json["provider_result"] == "PROVIDER_REJECTED"
        assert (
            rejected.decision_metadata_json["controlled_finalization"]["manifest_hash"]
            == reviewed_hash
        )
        skipped = by_key[("AIR", "TRADES")]
        assert skipped.status == "SKIPPED"
        assert skipped.decision_metadata_json["provider_result"] == "NOT_ATTEMPTED"
        assert run.status == "PARTIAL"
        assert run.executed_request_count == 2
        assert run.success_count == 1
        assert run.failure_count == 1
        assert run.skipped_count == 1

        repeated = apply_interrupted_canary_finalization(
            db,
            manifest=manifest,
            reviewed_manifest_hash=reviewed_hash,
            actor="pytest",
            request_key="pytest-run-finalization",
            approve_write=True,
            now=datetime(2026, 9, 5, 14, 0, tzinfo=UTC),
        )
        assert repeated.changed_items == 0


def test_interrupted_canary_finalization_rejects_unreviewed_hash(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    with Session(engine) as db:
        run = IBFetchRun(
            requested_tickers=["AAL"],
            symbols_including_benchmarks=["AAL"],
            include_benchmarks=False,
            force_refresh=False,
            force_full_backfill=False,
            decision_counts_json={},
            planned_request_count=1,
            status="RUNNING",
        )
        db.add(run)
        db.flush()
        db.add(_item(run, "AAL", "ADJUSTED_LAST", "SUCCESS", 1))
        db.commit()
        manifest = build_interrupted_canary_finalization_manifest(db, fetch_run_id=run.id)
        with pytest.raises(ValueError, match="hash"):
            apply_interrupted_canary_finalization(
                db,
                manifest=manifest,
                reviewed_manifest_hash="0" * 64,
                actor="pytest",
                request_key="pytest-bad-hash",
                approve_write=True,
            )


def _item(
    run: IBFetchRun,
    ticker: str,
    basis: str,
    status: str,
    attempt_count: int,
    *,
    fetched: int = 0,
    inserted: int = 0,
) -> IBFetchItem:
    return IBFetchItem(
        fetch_run=run,
        ticker=ticker,
        what_to_show=basis,
        action="TOP_UP_RECENT",
        duration="25 D",
        bar_size="1 day",
        status=status,
        reason="test",
        decision_metadata_json={"request_end_datetime": "20260904-23:59:59"},
        current_bar_count=100,
        fetched=fetched,
        inserted=inserted,
        updated=0,
        revised=0,
        unchanged=0,
        attempt_count=attempt_count,
    )


def _upgrade(database_url: str) -> None:
    env = os.environ.copy()
    env["DATABASE_URL"] = database_url
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )
