"""A cache hit must retain the exact PIT envelope and intact native payload."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import select, text, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.models.tables import BackgroundJob, TechnicalFeatureArtifact
from app.services.background_job_service import JobLeaseLost
from app.services.domain_write_fence import fence_domain_commits
from app.services.technical_artifact_cache import (
    build_local_artifact_key,
    get_local_artifact,
    record_local_artifact_shadow_validation,
    upsert_local_artifact,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]
contextual_engine = contextual.contextual_engine


def test_cache_rejects_forged_keys_pit_drift_corruption_and_false_shadow(contextual_engine):
    cutoff = datetime(2026, 9, 14, 21, tzinfo=UTC)
    inputs = dict(
        ticker="ACME",
        adjusted_series_version=1,
        trades_series_version=2,
        feature_config_hash="feature-a",
        scoring_config_hash="score-a",
        technical_engine_version="engine-a",
        input_as_of_session=date(2026, 9, 14),
        calculation_cutoff_at=cutoff,
        source_manifest_hash="pit-a",
    )
    key = build_local_artifact_key(**inputs)
    later = build_local_artifact_key(
        **{**inputs, "calculation_cutoff_at": cutoff + timedelta(hours=1)}
    )
    revised = build_local_artifact_key(**{**inputs, "source_manifest_hash": "pit-b"})
    assert len({key.input_signature, later.input_signature, revised.input_signature}) == 3
    with Session(contextual_engine) as db:
        with pytest.raises(ValueError):
            upsert_local_artifact(db, replace(key, input_signature="forged"), artifact_json={})
        db.rollback()
        artifact = upsert_local_artifact(db, key, artifact_json={"feature_result": {"close": 10}})
        db.commit()
        assert get_local_artifact(db, key, usage="shadow") is not None
        assert get_local_artifact(db, key) is None
        with pytest.raises(ValueError):
            record_local_artifact_shadow_validation(
                db,
                key,
                matched=True,
                fresh_fingerprint="different",
                cached_fingerprint="original",
                run_id=None,
            )
        db.rollback()
        record_local_artifact_shadow_validation(
            db,
            key,
            matched=True,
            fresh_fingerprint="equal",
            cached_fingerprint="equal",
            run_id=None,
        )
        db.commit()
        assert get_local_artifact(db, key) is not None
        upsert_local_artifact(db, key, artifact_json={"feature_result": {"close": 11}})
        db.commit()
        assert get_local_artifact(db, key) is None  # A replacement needs new shadow proof.
        assert get_local_artifact(db, later) is None
        assert get_local_artifact(db, revised) is None
        artifact.artifact_json = {"feature_result": {"close": 999}}
        db.commit()
        assert get_local_artifact(db, key) is None

        # Cache authority is distinct from financial CI, but a durable attempt
        # still owns its mutations and keeps the job lock until outer commit.
        key = later  # Pristine key; corrupt artifacts cannot be repaired implicitly.
        upsert_local_artifact(db, key, artifact_json={"feature_result": {"close": 10}})
        job = BackgroundJob(
            job_type="FULL_PIPELINE", status="RUNNING", execution_token="T2", payload_json={}
        )
        db.add(job)
        db.commit()
        job_id = job.id
        with fence_domain_commits(job_id=job_id, execution_token="T1"):
            with pytest.raises(JobLeaseLost):
                upsert_local_artifact(db, key, artifact_json={"feature_result": {"close": 99}})
        db.commit()
        cache_row = select(TechnicalFeatureArtifact).where(
            TechnicalFeatureArtifact.input_signature == key.input_signature
        )
        assert db.scalar(cache_row).artifact_json["feature_result"]["close"] == 10
        with fence_domain_commits(job_id=job_id, execution_token="T2"):
            upsert_local_artifact(db, key, artifact_json={"feature_result": {"close": 11}})
        with Session(contextual_engine) as contender:
            contender.execute(text("SET LOCAL lock_timeout = '100ms'"))
            with pytest.raises(OperationalError):
                contender.execute(
                    update(BackgroundJob)
                    .where(BackgroundJob.id == job_id)
                    .values(execution_token="T3")
                )
            contender.rollback()
        db.commit()
        assert db.scalar(cache_row).artifact_json["feature_result"]["close"] == 11
