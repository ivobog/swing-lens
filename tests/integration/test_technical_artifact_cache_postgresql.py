from __future__ import annotations

import os
import subprocess
import sys

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models.tables import TechnicalFeatureArtifact
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
        indicator_config_hash="indicator-a",
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
            indicator_config_hash="indicator-a",
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
