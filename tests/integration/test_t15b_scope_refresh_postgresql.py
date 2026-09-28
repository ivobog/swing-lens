from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from alembic import command
from app.models.ceri_tables import CeriIngestionRun, CeriProcessingRun, CeriSourceRecord
from app.models.tables import BackgroundJob, PipelineRun, UploadRun, WorkScopeRecord
from app.services.ceri.enums import CeriDataset
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.providers.manual_provider import ManualCeriProvider
from app.services.scope_refresh_adoption import (
    LegacySemanticAuthorityError,
    SemanticAuthorityError,
    admit_frozen_operation,
    bind_semantic_authority,
    continuation_remainder,
    record_zero_progress,
    require_children_terminal,
    require_semantic_authority,
    retained_scope_members,
)
from app.services.work_scope_identity import (
    AcquisitionPlanRevisionReason,
    AcquisitionRequirement,
    ContinuationDecision,
    ScopeMember,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def _admit(
    db: Session,
    *,
    members: tuple[str, ...] = ("A", "B", "C"),
    cycle: str = "R1",
    policy: str = "policy-v1",
    parent_scope_id: str | None = None,
    previous_plan_id: str | None = None,
    revision_reason: AcquisitionPlanRevisionReason | None = None,
):
    return admit_frozen_operation(
        db,
        operation_kind="t15b-synthetic-pipeline",
        subject_kind="ticker",
        members=tuple(ScopeMember("TICKER", member) for member in members),
        cycle_key=cycle,
        business_cutoff=date(2026, 9, 20),
        provider_source_class="SYNTHETIC",
        request_type="PIPELINE",
        requirements=(AcquisitionRequirement("DAILY_BARS"),),
        policy_identity=policy,
        scope_definition={"selection": "synthetic", "policy": policy},
        parent_scope_id=parent_scope_id,
        previous_plan_id=previous_plan_id,
        revision_reason=revision_reason,
        refresh_reason="T15B_CERTIFICATION",
    )


def _ceri_service(eps: int) -> CeriIngestionService:
    provider = ManualCeriProvider(
        {
            CeriDataset.ESTIMATES: [
                {
                    "provider_record_id": "stable-source-X",
                    "ticker": "MSFT",
                    "eps": eps,
                }
            ]
        }
    )
    return CeriIngestionService(registry=CeriProviderRegistry(providers={"manual": provider}))


def test_t15b_postgresql_scope_refresh_and_acquisition_certification(
    disposable_postgres_database: str,
) -> None:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", disposable_postgres_database.replace("%", "%%"))
    assert ScriptDirectory.from_config(config).get_heads() == ["0086_ceri_feature_source_manifest"]
    command.upgrade(config, "head")
    command.check(config)
    engine = create_engine(disposable_postgres_database)

    try:
        with Session(engine, expire_on_commit=False) as db:
            r1 = _admit(db)
            same_retry = _admit(db)
            assert same_retry == r1

            upload = UploadRun(filename="t15b.csv", row_count=4, status="READY")
            db.add(upload)
            db.flush()
            pipeline = PipelineRun(upload_run_id=upload.id, status="RUNNING")
            bind_semantic_authority(pipeline, r1)
            db.add(pipeline)

            parent_job = BackgroundJob(
                job_type="FULL_PIPELINE",
                status="RUNNING",
                payload_json={},
                execution_token="T1",
            )
            bind_semantic_authority(parent_job, r1)
            db.add(parent_job)
            db.flush()

            stage = _admit(
                db,
                members=("B", "C"),
                cycle="R1-market-stage",
                parent_scope_id=r1.scope_id,
            )
            stage_scope = db.get(WorkScopeRecord, stage.scope_id)
            assert stage_scope is not None
            assert stage_scope.parent_scope_id == r1.scope_id

            # D becomes eligible after admission; retained continuation remains B,C.
            remaining, decision = continuation_remainder(
                db,
                authority=r1,
                completed_members=(ScopeMember("TICKER", "A"),),
                processed_this_attempt=1,
            )
            assert tuple(member.subject_id for member in remaining) == ("B", "C")
            assert decision is ContinuationDecision.ENQUEUE_REMAINDER

            blocked_child = BackgroundJob(
                job_type="PIPELINE_STAGE_CHILD",
                status="RUNNING",
                payload_json={"checkpoint": {"scope_id": r1.scope_id}},
                parent_job_id=parent_job.id,
                execution_token="child-T1",
            )
            bind_semantic_authority(blocked_child, r1, required_for_parent_completion=True)
            db.add(blocked_child)
            db.flush()
            with pytest.raises(SemanticAuthorityError, match="CHILDREN_INCOMPLETE"):
                require_children_terminal(db, r1.scope_id)

            no_progress_remaining, no_progress_decision = continuation_remainder(
                db,
                authority=r1,
                completed_members=(ScopeMember("TICKER", "A"),),
                processed_this_attempt=0,
            )
            assert no_progress_decision is ContinuationDecision.STOP_ZERO_PROGRESS
            record_zero_progress(
                blocked_child,
                remaining_members=no_progress_remaining,
                reason="WAITING_FOR_EXTERNAL_REQUIREMENT",
            )
            assert blocked_child.status == "BLOCKED"
            assert require_children_terminal(db, r1.scope_id)["failed"] == 1

            # A reclaim changes only execution ownership, never semantic authority.
            before_reclaim = require_semantic_authority(blocked_child)
            blocked_child.execution_token = "child-T2"
            blocked_child.status = "COMPLETED"
            assert require_semantic_authority(blocked_child) == before_reclaim
            assert require_children_terminal(db, r1.scope_id)["completed"] == 1

            # A policy/membership change is an explicit P2 and R2, not a retry of P1/R1.
            r2 = _admit(
                db,
                members=("A", "B", "C", "D"),
                cycle="R2",
                policy="policy-v2",
                previous_plan_id=r1.acquisition_plan_id,
                revision_reason=AcquisitionPlanRevisionReason.POLICY_CHANGE,
            )
            assert r2.scope_id != r1.scope_id
            assert r2.refresh_cycle_id != r1.refresh_cycle_id
            assert r2.acquisition_plan_id != r1.acquisition_plan_id
            assert tuple(
                member.subject_id for member in retained_scope_members(db, r1.scope_id)
            ) == (
                "A",
                "B",
                "C",
            )
            assert tuple(
                member.subject_id for member in retained_scope_members(db, r2.scope_id)
            ) == (
                "A",
                "B",
                "C",
                "D",
            )

            # Checkpoint progress is explicitly scoped and cannot be reused by R2.
            checkpoint_r1 = CeriProcessingRun(
                job_type="T15B_CHECKPOINT",
                status="PARTIAL",
                deterministic_request_key=f"checkpoint:{r1.scope_id}:{r1.refresh_cycle_id}",
                checkpoint_json={"completed": ["A"], **r1.as_dict()},
            )
            bind_semantic_authority(checkpoint_r1, r1)
            checkpoint_r2 = CeriProcessingRun(
                job_type="T15B_CHECKPOINT",
                status="PENDING",
                deterministic_request_key=f"checkpoint:{r2.scope_id}:{r2.refresh_cycle_id}",
                checkpoint_json={"completed": [], **r2.as_dict()},
            )
            bind_semantic_authority(checkpoint_r2, r2)
            db.add_all((checkpoint_r1, checkpoint_r2))
            db.commit()

        # The database trigger is the final defense against semantic rebinding.
        with Session(engine) as db:
            pipeline_id = db.scalar(select(PipelineRun.id))
            r2_scope = db.scalar(
                select(CeriProcessingRun.scope_id).where(CeriProcessingRun.status == "PENDING")
            )
            with pytest.raises(DBAPIError, match="semantic work scope cannot be rebound"):
                db.execute(
                    text("UPDATE pipeline_runs SET scope_id = :scope WHERE id = :id"),
                    {"scope": r2_scope, "id": pipeline_id},
                )
                db.flush()
            db.rollback()

            legacy = CeriIngestionRun(
                provider="manual",
                dataset="estimates",
                status="PENDING",
                request_key="legacy-without-authority",
            )
            db.add(legacy)
            db.flush()
            with pytest.raises(LegacySemanticAuthorityError, match="LEGACY_UNKNOWN"):
                require_semantic_authority(legacy)
            db.rollback()

        # Stable provider key: retry R1 coalesces, explicit R2 remains processable.
        with Session(engine, expire_on_commit=False) as db:
            request_r1 = CeriIngestionRequest(
                provider="manual",
                dataset=CeriDataset.ESTIMATES,
                ticker="MSFT",
                request_key="stable-source-X",
                refresh_cycle_key="CERI-R1",
                business_cutoff=date(2026, 9, 20),
            )
            first = _ceri_service(1).ingest(db, request_r1)
            db.commit()
            retry = _ceri_service(1).ingest(db, request_r1)
            db.commit()
            assert retry.ingestion_run_id == first.ingestion_run_id

            request_r2 = CeriIngestionRequest(
                provider="manual",
                dataset=CeriDataset.ESTIMATES,
                ticker="MSFT",
                request_key="stable-source-X",
                refresh_cycle_key="CERI-R2",
                business_cutoff=date(2026, 9, 21),
            )
            later = _ceri_service(2).ingest(db, request_r2)
            db.commit()
            assert later.ingestion_run_id != first.ingestion_run_id
            runs = list(
                db.scalars(
                    select(CeriIngestionRun)
                    .where(CeriIngestionRun.request_key.like("stable-source-X:refresh:%"))
                    .order_by(CeriIngestionRun.id)
                )
            )
            assert len(runs) == 2
            assert runs[0].refresh_cycle_id != runs[1].refresh_cycle_id
            records = list(
                db.scalars(
                    select(CeriSourceRecord).where(
                        CeriSourceRecord.provider_record_id == "stable-source-X"
                    )
                )
            )
            assert len(records) == 2
            assert records[1].supersedes_id == records[0].id

        # Concurrent identical admissions converge; distinct cycle keys stay distinct.
        def concurrent_admit(cycle: str):
            with Session(engine) as db:
                authority = _admit(db, cycle=cycle)
                db.commit()
                return authority

        with ThreadPoolExecutor(max_workers=2) as pool:
            converged = list(pool.map(concurrent_admit, ("CONCURRENT", "CONCURRENT")))
        assert converged[0] == converged[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            distinct = list(pool.map(concurrent_admit, ("CONCURRENT-R1", "CONCURRENT-R2")))
        assert distinct[0].refresh_cycle_id != distinct[1].refresh_cycle_id
    finally:
        engine.dispose()
