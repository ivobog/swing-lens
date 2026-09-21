from __future__ import annotations

from datetime import UTC, date, datetime

from alembic.config import Config
from sqlalchemy import create_engine, insert, inspect, select
from sqlalchemy.orm import Session

from alembic import command
from app.models.tables import (
    BackgroundJob,
    UploadRun,
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
)
from app.services.scope_refresh_adoption import bind_semantic_authority
from app.services.winner_probability.config import load_winner_probability_config
from app.services.winner_probability.scope_refresh import (
    WINNER_MATURATION_MEMBER,
    admit_maturation,
    maturation_prediction_ids,
)
from app.services.work_scope_identity import ScopeMember


def _upgrade(database_url: str) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def _downgrade(database_url: str, revision: str) -> None:
    config = Config("alembic.ini")
    config.attributes["database_url"] = database_url
    command.downgrade(config, revision)


def _seed_pending(db: Session, *, due_sessions: tuple[date, ...]) -> tuple[int, ...]:
    config = load_winner_probability_config()
    upload = UploadRun(filename="t15c-scope.csv", status="COMPLETED")
    db.add(upload)
    db.flush()
    prediction_ids = tuple(
        db.scalars(
            insert(WinnerPredictionSnapshot).returning(WinnerPredictionSnapshot.id),
            [
                {
                    "run_id": upload.id,
                    "ticker": f"T15C{index}",
                    "prediction_as_of_date": date(2026, 8, 3),
                    "source_data_cutoff_at": datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
                    "decision_at": datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
                    "captured_at": datetime(2026, 7, 31, 20, 0, tzinfo=UTC),
                    "planned_entry_session": date(2026, 8, 3),
                    "entry_schedule_status": "RESOLVED",
                    "entry_data_status": "AVAILABLE",
                    "eligibility_status": "ELIGIBLE",
                    "feature_schema_version": config.feature_schema.version,
                    "feature_vector_hash": f"t15c-{index}",
                    "config_hash": config.config_hash,
                    "calculation_version": config.engine.calculation_version,
                    "feature_json": {},
                    "source_ids_json": {},
                    "warning_flags_json": [],
                    "lineage_json": {
                        "point_in_time_validated": True,
                        "point_in_time_validation": {"semantic_input_time": "VALID"},
                    },
                }
                for index in range(len(due_sessions))
            ],
        )
    )
    db.execute(
        insert(WinnerForwardOutcome),
        [
            {
                "prediction_id": prediction_id,
                "entry_model": "NEXT_OPEN",
                "horizon_sessions": 5,
                "entry_session": date(2026, 8, 3),
                "due_session": due_session,
                "status": "PENDING",
                "revision": 1,
                "is_current_revision": True,
                "metadata_json": {},
            }
            for prediction_id, due_session in zip(prediction_ids, due_sessions, strict=True)
        ],
    )
    db.flush()
    return prediction_ids


def test_t15c_maturation_admission_retry_and_later_refresh(
    disposable_postgres_database: str,
) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    config = load_winner_probability_config()
    r1_at = datetime(2026, 8, 7, 22, 0, tzinfo=UTC)
    r2_at = datetime(2026, 8, 10, 22, 0, tzinfo=UTC)
    with Session(engine) as db:
        prediction_ids = _seed_pending(
            db,
            due_sessions=(date(2026, 8, 7),) * 3 + (date(2026, 8, 10),),
        )
        r1 = admit_maturation(
            db,
            cycle_key="winner:t15c:R1",
            operation_cutoff=r1_at,
            due_session=date(2026, 8, 7),
            configuration_identity=config.config_hash,
        )
        assert maturation_prediction_ids(db, r1) == prediction_ids[:3]

        retry = admit_maturation(
            db,
            cycle_key="winner:t15c:R1",
            operation_cutoff=r1_at,
            due_session=date(2026, 8, 7),
            configuration_identity=config.config_hash,
        )
        assert retry == r1

        fourth = db.scalar(
            select(WinnerForwardOutcome).where(
                WinnerForwardOutcome.prediction_id == prediction_ids[3]
            )
        )
        fourth.due_session = date(2026, 8, 7)
        db.flush()
        assert maturation_prediction_ids(db, r1) == prediction_ids[:3]

        r2 = admit_maturation(
            db,
            cycle_key="winner:t15c:R2",
            operation_cutoff=r2_at,
            due_session=date(2026, 8, 10),
            configuration_identity=config.config_hash,
        )
        assert r2.refresh_cycle_id != r1.refresh_cycle_id
        assert maturation_prediction_ids(db, r2) == prediction_ids

        job = BackgroundJob(job_type="WINNER_OUTCOME_MATURATION", status="QUEUED")
        bind_semantic_authority(job, r1)
        assert job.scope_id == r1.scope_id
        assert ScopeMember(WINNER_MATURATION_MEMBER, prediction_ids[3]) not in tuple(
            ScopeMember(WINNER_MATURATION_MEMBER, value)
            for value in maturation_prediction_ids(db, r1)
        )
        db.commit()
    engine.dispose()


def test_t15c_migration_downgrade_and_reupgrade(disposable_postgres_database: str) -> None:
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    for table in (
        "winner_cohort_generations",
        "winner_estimate_publication_requests",
        "winner_processing_runs",
    ):
        assert {"scope_id", "refresh_cycle_id", "acquisition_plan_id"} <= {
            column["name"] for column in inspect(engine).get_columns(table)
        }
    engine.dispose()

    _downgrade(disposable_postgres_database, "0082_pipeline_ceri_scope")
    engine = create_engine(disposable_postgres_database)
    for table in (
        "winner_cohort_generations",
        "winner_estimate_publication_requests",
        "winner_processing_runs",
    ):
        assert not {"scope_id", "refresh_cycle_id", "acquisition_plan_id"} & {
            column["name"] for column in inspect(engine).get_columns(table)
        }
    engine.dispose()

    _upgrade(disposable_postgres_database)
