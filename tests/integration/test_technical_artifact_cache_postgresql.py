from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime

import numpy as np
import pandas as pd
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import TechnicalFeatureArtifact, TechnicalScore, UploadRun
from app.services import technical_score_service
from app.services.ib_fetch_executor import TickerReadyEvent
from app.services.operational_metrics import operational_metrics
from app.services.technical_artifact_cache import (
    SHADOW_MATCH,
    SHADOW_MISMATCH,
    SHADOW_UNVALIDATED,
    build_local_artifact_key,
    get_local_artifact,
    record_local_artifact_shadow_validation,
    upsert_local_artifact,
)
from app.services.technical_score_service import TechnicalScoringOverlapCoordinator
from app.settings import Settings


def test_only_shadow_certified_artifacts_can_be_active_hits(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    operational_metrics.reset()
    key = build_local_artifact_key(
        ticker="MSFT",
        adjusted_series_version=12,
        trades_series_version=15,
        feature_config_hash="feature-a",
        scoring_config_hash="scoring-a",
        technical_engine_version="3.2.0",
    )
    with Session(engine) as db:
        artifact = upsert_local_artifact(
            db,
            key,
            artifact_json={"feature_result": {"ticker": "MSFT"}, "htf_features": {}},
        )
        db.commit()
        artifact_id = artifact.id
        assert artifact.shadow_validation_status == SHADOW_UNVALIDATED

    with Session(engine) as db:
        assert get_local_artifact(db, key, usage="active") is None
        candidate = get_local_artifact(db, key, usage="shadow")
        assert candidate is not None
        record_local_artifact_shadow_validation(
            db,
            key,
            matched=True,
            fresh_fingerprint="same",
            cached_fingerprint="same",
            run_id=7,
        )
        db.commit()

    with Session(engine) as db:
        active = get_local_artifact(db, key, usage="active")
        assert active is not None
        assert active.id == artifact_id
        assert active.shadow_validation_status == SHADOW_MATCH
        assert active.shadow_validation_count == 1

        invalidated_key = build_local_artifact_key(
            ticker="MSFT",
            adjusted_series_version=13,
            trades_series_version=15,
            feature_config_hash="feature-a",
            scoring_config_hash="scoring-a",
            technical_engine_version="3.2.0",
        )
        assert get_local_artifact(db, invalidated_key, usage="active") is None
        db.rollback()

    with Session(engine) as db:
        record_local_artifact_shadow_validation(
            db,
            key,
            matched=False,
            fresh_fingerprint="fresh",
            cached_fingerprint="cached",
            run_id=8,
        )
        db.commit()

    with Session(engine) as db:
        artifact = db.get(TechnicalFeatureArtifact, artifact_id)
        assert artifact is not None
        assert artifact.shadow_validation_status == SHADOW_MISMATCH
        assert artifact.shadow_validation_count == 2
        assert artifact.shadow_mismatch_count == 1
        assert artifact.last_shadow_mismatch_json["run_id"] == 8
        assert get_local_artifact(db, key, usage="active") is None

    assert operational_metrics.total(
        "swinglens_technical_artifact_cache_total", result="hit"
    ) == 1
    engine.dispose()


def test_overlap_workers_publish_final_scores_only_in_parent_postgresql_session(
    disposable_postgres_database: str,
    monkeypatch,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    frame = _synthetic_frame()
    monkeypatch.setattr(
        technical_score_service,
        "load_preferred_ohlcv_frames",
        lambda _db, _ticker: (frame, frame),
    )
    with Session(engine) as db:
        run = UploadRun(filename="phase8-overlap.csv", row_count=2, status="COMPLETED")
        db.add(run)
        db.commit()
        run_id = run.id

        coordinator = TechnicalScoringOverlapCoordinator(
            db,
            run_id=run_id,
            tickers=["BBB", "AAA"],
            settings=Settings(
                _env_file=None,
                technical_process_pool_enabled=True,
                technical_worker_processes=1,
                technical_max_in_flight=2,
            ),
        )
        coordinator.on_ticker_ready(_ready_event("AAA"))
        coordinator.on_ticker_ready(_ready_event("BBB"))

        # Workers return database-free values; no score row exists until the
        # parent session performs deterministic finalization.
        assert db.query(TechnicalScore).filter_by(run_id=run_id).count() == 0
        scores = coordinator.finalize()
        db.commit()

        assert [score.ticker for score in scores] == ["BBB", "AAA"]
        persisted = db.query(TechnicalScore).filter_by(run_id=run_id).all()
        assert {score.ticker for score in persisted} == {"AAA", "BBB"}
        assert all(score.technical_confidence != "error" for score in persisted)
    engine.dispose()


def _upgrade(database_url: str) -> None:
    env = {**os.environ, "DATABASE_URL": database_url}
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        cwd=os.getcwd(),
        env=env,
        capture_output=True,
        text=True,
    )


def _ready_event(ticker: str) -> TickerReadyEvent:
    return TickerReadyEvent(
        ticker=ticker,
        statuses=("SUCCESS", "SUCCESS"),
        failed=False,
        completed_at=datetime.now(UTC),
    )


def _synthetic_frame() -> pd.DataFrame:
    dates = pd.date_range("2020-01-01", periods=320, freq="D")
    close = np.linspace(100.0, 180.0, len(dates))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 1.0,
            "high": close + 2.0,
            "low": close - 2.0,
            "close": close,
            "volume": np.linspace(100_000.0, 120_000.0, len(dates)),
        }
    )
