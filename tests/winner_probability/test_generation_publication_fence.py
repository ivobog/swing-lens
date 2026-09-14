from __future__ import annotations

from datetime import UTC, datetime, timedelta
from threading import Barrier, Thread

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, sessionmaker

from app.db import Base
from app.models.tables import (
    WinnerCohortGeneration,
    WinnerCohortRefreshState,
    WinnerEstimatePublicationRequest,
)
from app.services.background_job_service import JobLeaseLost
from app.services.domain_write_fence import fence_domain_commits
from app.services.winner_probability.cohort_generation_service import (
    CohortGenerationService,
    CohortGenerationStatus,
    EvidenceWatermark,
    GenerationPublicationStatus,
    WinnerCohortContract,
    canonical_generation_key,
    canonical_watermark_hash,
)
from app.services.winner_probability.estimate_publication_service import (
    PublicationInvariantViolation,
    WinnerEstimatePublicationService,
    transition_manifest_hash,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(_type, _compiler, **_kwargs) -> str:
    return "JSON"


CONTRACT = {
    "outcome_definition_id": 7,
    "feature_schema_version": "features-v1",
    "calculation_version": "calc-v1",
    "config_hash": "config-v1",
    "eligibility_policy_version": "eligibility-v1",
    "compatibility_policy_version": "compatibility-v1",
    "cohort_algorithm_version": "cohort-v2.2",
}
OBSERVED = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)


@pytest.fixture
def publication_sessions(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'winner-publication.db'}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(
        engine,
        tables=[
            WinnerCohortRefreshState.__table__,
            WinnerCohortGeneration.__table__,
            WinnerEstimatePublicationRequest.__table__,
        ],
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE background_jobs (
                id INTEGER PRIMARY KEY,
                status TEXT NOT NULL,
                execution_token TEXT,
                requested_cancel INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        connection.execute(
            text(
                "INSERT INTO background_jobs "
                "(id, status, execution_token, requested_cancel) "
                "VALUES (1, 'RUNNING', 'token-a', 0)"
            )
        )
    monkeypatch.setattr(
        CohortGenerationService,
        "_assert_temporally_clean",
        staticmethod(lambda _db, _generation: None),
    )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    engine.dispose()


def _watermark(value: int) -> EvidenceWatermark:
    return EvidenceWatermark(
        forward_revision_id=value,
        target_stop_revision_id=value,
        eligibility_decision_id=value,
        training_replay_id=value,
        temporal_validity_decision_id=value,
    )


def _seed(publication_sessions):
    with publication_sessions.begin() as db:
        watermark = _watermark(1)
        state = WinnerCohortRefreshState(
            id=1,
            **CONTRACT,
            desired_forward_revision_id=watermark.forward_revision_id,
            desired_target_stop_revision_id=watermark.target_stop_revision_id,
            desired_eligibility_decision_id=watermark.eligibility_decision_id,
            desired_training_replay_id=watermark.training_replay_id,
            desired_temporal_validity_decision_id=watermark.temporal_validity_decision_id,
            desired_watermark_hash=canonical_watermark_hash(watermark),
            updated_at=OBSERVED,
        )
        db.add(state)
        db.flush()
        generations = {
            value: _generation(value=value, state_id=state.id) for value in (1, 2, 3)
        }
        db.add_all(generations.values())
    return generations


def _generation(*, value: int, state_id: int) -> WinnerCohortGeneration:
    watermark = _watermark(value)
    return WinnerCohortGeneration(
        id=value,
        generation_key=canonical_generation_key(WinnerCohortContract(**CONTRACT), watermark),
        refresh_state_id=state_id,
        **CONTRACT,
        watermark_hash=canonical_watermark_hash(watermark),
        watermark_json=watermark.as_dict(),
        status=CohortGenerationStatus.READY,
        training_cutoff_at=OBSERVED + timedelta(seconds=value),
        requested_at=OBSERVED + timedelta(seconds=value),
        started_at=OBSERVED + timedelta(seconds=value),
        ready_at=OBSERVED + timedelta(seconds=value),
        planned_group_count=1,
        completed_group_count=1,
        failed_group_count=0,
        evidence_row_count=1,
        root_manifest_hash=f"manifest-{value}",
        checkpoint_json={"phase": "READY"},
        metrics_json={},
    )


def _set_desired(db: Session, value: int) -> None:
    state = db.get(WinnerCohortRefreshState, 1)
    watermark = _watermark(value)
    state.desired_forward_revision_id = watermark.forward_revision_id
    state.desired_target_stop_revision_id = watermark.target_stop_revision_id
    state.desired_eligibility_decision_id = watermark.eligibility_decision_id
    state.desired_training_replay_id = watermark.training_replay_id
    state.desired_temporal_validity_decision_id = watermark.temporal_validity_decision_id
    state.desired_watermark_hash = canonical_watermark_hash(watermark)
    state.updated_at = OBSERVED + timedelta(seconds=value)


def _publish(db: Session, generation_id: int):
    return CohortGenerationService().publish(
        db,
        generation=db.get(WinnerCohortGeneration, generation_id),
        lease_guard=lambda: None,
        published_at=OBSERVED + timedelta(minutes=generation_id),
    )


def test_first_and_newer_publication_are_monotonic(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        first = _publish(db, 1)
        assert first.status == GenerationPublicationStatus.PUBLISHED
        _set_desired(db, 2)
        newer = _publish(db, 2)
        assert newer.status == GenerationPublicationStatus.PUBLISHED

    with publication_sessions() as db:
        state = db.get(WinnerCohortRefreshState, 1)
        assert state.published_generation_id == 2
        assert db.get(WinnerCohortGeneration, 1).status == CohortGenerationStatus.SUPERSEDED
        assert db.get(WinnerCohortGeneration, 2).status == CohortGenerationStatus.PUBLISHED


def test_older_generation_finishing_last_is_rejected_stale(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        older = db.get(WinnerCohortGeneration, 1)
        older.ready_at = OBSERVED + timedelta(days=1)
        _set_desired(db, 2)
        assert _publish(db, 2).status == GenerationPublicationStatus.PUBLISHED
        stale = _publish(db, 1)
        assert stale.status == GenerationPublicationStatus.REJECTED_STALE
        assert stale.active_generation_id == 2
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 2
        assert db.get(WinnerCohortGeneration, 1).status == CohortGenerationStatus.READY


def test_same_generation_retry_is_side_effect_free(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        first = _publish(db, 1)
        generation = db.get(WinnerCohortGeneration, 1)
        published_at = generation.published_at
        completed_at = generation.completed_at
        retry = _publish(db, 1)
        assert first.status == GenerationPublicationStatus.PUBLISHED
        assert retry.status == GenerationPublicationStatus.ALREADY_ACTIVE
        assert generation.published_at == published_at
        assert generation.completed_at == completed_at


def test_concurrent_adversarial_publishers_converge_to_newest(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        _set_desired(db, 2)

    barrier = Barrier(2)
    statuses: list[str] = []
    errors: list[BaseException] = []

    def attempt(generation_id: int) -> None:
        try:
            with publication_sessions() as db:
                barrier.wait(timeout=5)
                db.connection().exec_driver_sql("BEGIN IMMEDIATE")
                result = _publish(db, generation_id)
                statuses.append(result.status)
                db.commit()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [Thread(target=attempt, args=(generation_id,)) for generation_id in (1, 2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert GenerationPublicationStatus.PUBLISHED in statuses
    with publication_sessions() as db:
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 2


def test_incomplete_and_incompatible_candidates_cannot_replace_active(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        assert _publish(db, 1).status == GenerationPublicationStatus.PUBLISHED
        _set_desired(db, 2)
        incomplete = db.get(WinnerCohortGeneration, 2)
        incomplete.completed_group_count = 0
        assert _publish(db, 2).status == GenerationPublicationStatus.REJECTED_INCOMPLETE
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 1

        incompatible = db.get(WinnerCohortGeneration, 3)
        incompatible.config_hash = "different-contract"
        _set_desired(db, 3)
        assert _publish(db, 3).status == GenerationPublicationStatus.REJECTED_INCOMPATIBLE
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 1


def test_divergent_watermarks_are_not_timestamp_ordered(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        _set_desired(db, 2)
        assert _publish(db, 2).status == GenerationPublicationStatus.PUBLISHED
        candidate = db.get(WinnerCohortGeneration, 3)
        divergent = EvidenceWatermark(
            forward_revision_id=3,
            target_stop_revision_id=1,
            eligibility_decision_id=3,
            training_replay_id=3,
            temporal_validity_decision_id=3,
        )
        candidate.watermark_json = divergent.as_dict()
        candidate.watermark_hash = canonical_watermark_hash(divergent)
        candidate.generation_key = canonical_generation_key(
            WinnerCohortContract(**CONTRACT), divergent
        )
        state = db.get(WinnerCohortRefreshState, 1)
        for field, value in divergent.as_dict().items():
            setattr(state, f"desired_{field}", value)
        state.desired_watermark_hash = candidate.watermark_hash
        result = _publish(db, 3)
        assert result.status == GenerationPublicationStatus.REJECTED_INCOMPATIBLE
        assert state.published_generation_id == 2


def test_publication_rollback_keeps_previous_active(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        assert _publish(db, 1).status == GenerationPublicationStatus.PUBLISHED
    with publication_sessions.begin() as db:
        _set_desired(db, 2)

    with publication_sessions() as db:
        assert _publish(db, 2).status == GenerationPublicationStatus.PUBLISHED
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 2
        db.rollback()

    with publication_sessions() as db:
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 1
        assert db.get(WinnerCohortGeneration, 1).status == CohortGenerationStatus.PUBLISHED
        assert db.get(WinnerCohortGeneration, 2).status == CohortGenerationStatus.READY


def test_committed_response_lost_retry_returns_already_active(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        assert _publish(db, 1).status == GenerationPublicationStatus.PUBLISHED
    with publication_sessions.begin() as db:
        _set_desired(db, 2)
        assert _publish(db, 2).status == GenerationPublicationStatus.PUBLISHED

    with publication_sessions.begin() as db:
        retry = _publish(db, 2)
        assert retry.status == GenerationPublicationStatus.ALREADY_ACTIVE
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 2


def test_manual_reviewed_publication_rejects_pointer_drift(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        assert _publish(db, 1).status == GenerationPublicationStatus.PUBLISHED
        _set_desired(db, 2)
        assert _publish(db, 2).status == GenerationPublicationStatus.PUBLISHED

    manifest = {
        "generation": {
            "id": 3,
            "generation_key": canonical_generation_key(
                WinnerCohortContract(**CONTRACT), _watermark(3)
            ),
            "root_manifest_hash": "manifest-3",
        },
        "previous_generation": {
            "id": 1,
            "generation_key": canonical_generation_key(
                WinnerCohortContract(**CONTRACT), _watermark(1)
            ),
        },
        "candidate_manifest_hash": "candidate-manifest",
        "candidate_count": 1,
        "records": [{"original_estimate_id": 10, "candidate_estimate_id": 11}],
    }
    manifest["artifact_hash"] = transition_manifest_hash(manifest)
    with publication_sessions() as db:
        with pytest.raises(PublicationInvariantViolation, match="generation state drifted"):
            WinnerEstimatePublicationService().publish(
                db,
                manifest=manifest,
                reviewed_manifest_hash=manifest["artifact_hash"],
                candidate_manifest_hash="candidate-manifest",
                actor="admin-test",
                request_key="manual-stale-race",
                approve_write=True,
            )
        db.rollback()
    with publication_sessions() as db:
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 2


def test_reviewed_supersession_uses_canonical_pointer_switch(publication_sessions) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        assert _publish(db, 1).status == GenerationPublicationStatus.PUBLISHED
        result = CohortGenerationService().publish_reviewed_supersession(
            db,
            generation=db.get(WinnerCohortGeneration, 2),
            previous=db.get(WinnerCohortGeneration, 1),
            published_at=OBSERVED + timedelta(minutes=2),
        )
        assert result.status == GenerationPublicationStatus.PUBLISHED
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id == 2
        assert db.get(WinnerCohortGeneration, 1).status == CohortGenerationStatus.SUPERSEDED


def test_t09b_execution_token_fence_still_rejects_background_publication(
    publication_sessions,
) -> None:
    _seed(publication_sessions)
    with publication_sessions.begin() as db:
        db.execute(
            text("UPDATE background_jobs SET execution_token='token-b' WHERE id=1")
        )

    with publication_sessions() as db:
        with fence_domain_commits(job_id=1, execution_token="token-a"):
            assert _publish(db, 1).status == GenerationPublicationStatus.PUBLISHED
            with pytest.raises(JobLeaseLost):
                db.commit()
        db.rollback()

    with publication_sessions() as db:
        assert db.get(WinnerCohortRefreshState, 1).published_generation_id is None
        assert db.get(WinnerCohortGeneration, 1).status == CohortGenerationStatus.READY


def test_pointer_queries_compile_with_row_locks() -> None:
    statement = (
        select(WinnerCohortRefreshState)
        .where(WinnerCohortRefreshState.id == 1)
        .with_for_update()
    )
    assert "FOR UPDATE" in str(statement.compile(dialect=postgresql.dialect()))
