"""Real configuration/receipt/child authority across an explicit gap repair queue."""

from copy import deepcopy
from datetime import date

import pytest
from sqlalchemy.orm import Session
from test_ib_bar_admission_receipts_postgresql import (
    CUTOFF,
    GAP,
    SESSION,
    _bar,
    _execute,
    _owner,
)
from test_ib_bar_admission_receipts_postgresql import (
    receipt_engine as receipt_engine,
)

from app.models.tables import (
    BackgroundJob,
    ExecutionConfigurationBinding,
    IBFetchItem,
    IBFetchRun,
    WorkScopeRecord,
)
from app.services import ib_fetch_job_service as jobs
from app.services.background_worker import execute_job
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.configuration_delivery import (
    binding_reference,
    current_delivery,
    persist_configuration_anchor,
    resolve_pipeline_configurations,
)
from app.services.ib_fetch_plan_service import fetch_plan_from_dict, fetch_plan_to_dict
from app.services.ib_gap_repair_service import (
    prepare_gap_repair,
    submit_gap_repair,
    validate_gap_repair_delivery,
)

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_native_gap_child_retains_configuration_and_rejects_changed_delivery(
    receipt_engine,
    monkeypatch,
):
    with Session(receipt_engine, expire_on_commit=False) as db:
        pipeline, cutoff = _owner(db)
        original = _execute(
            db,
            pipeline,
            monkeypatch,
            [_bar(date(2026, 11, day)) for day in (3, 5, 6, 9)],
        )
        original_item = original.items[0]
        original_metadata = deepcopy(original_item.decision_metadata_json)
        anchor = persist_configuration_anchor(db, resolve_pipeline_configurations(db))
        db.add(
            ExecutionConfigurationBinding(
                binding_key=f"pipeline:{pipeline.id}",
                pipeline_run_id=pipeline.id,
                anchor_id=anchor["anchor_id"],
            )
        )
        pipeline.status = "PARTIAL"
        pipeline.result_json = {"market_calculation_context_id": cutoff.context_id}
        db.commit()
        preview = prepare_gap_repair(db, pipeline_run_id=pipeline.id)
        assert preview.plan.items[0].request_start_date == GAP
        assert preview.plan.items[0].request_end_date == GAP
        assert preview.plan.items[0].duration == "1 D"
        submission = submit_gap_repair(
            db,
            pipeline_run_id=pipeline.id,
            expected_manifest_fingerprint=preview.manifest_fingerprint,
        )
        db.commit()
        job = db.get(BackgroundJob, submission.job_id)
        fetch = db.get(IBFetchRun, submission.fetch_run_id)
        scope = db.get(WorkScopeRecord, fetch.scope_id)
        assert fetch.status == "QUEUED" and fetch.id != original.id
        assert scope.parent_scope_id == pipeline.scope_id
        assert scope.acquisition_plan_id != original.acquisition_plan_id
        assert binding_reference(db, job_id=job.id) == binding_reference(
            db, pipeline_run_id=pipeline.id
        )
        assert job.payload_json["market_calculation_context_id"] == cutoff.context_id
        assert job.payload_json["market_cutoff_at"] == Canonical.canonicalize(CUTOFF)
        assert job.payload_json["input_as_of_session"] == SESSION.isoformat()
        assert job.payload_json["gap_repair_manifest_fingerprint"] == preview.manifest_fingerprint
        plan = fetch_plan_from_dict(job.payload_json["fetch_plan"])
        assert fetch_plan_to_dict(plan) == preview.manifest["fetch_plan"]
        direct_provider_calls = []
        with monkeypatch.context() as direct:

            def unexpected_direct_provider(*args, **kwargs):
                direct_provider_calls.append(True)
                pytest.fail("Direct handler bypass reached provider execution")

            direct.setattr(jobs, "execute_fetch_plan", unexpected_direct_provider)
            with pytest.raises(ValueError, match="GAP_REPAIR_CONFIGURATION_DELIVERY_REQUIRED"):
                jobs.execute_durable_fetch_job(db, job)
        assert direct_provider_calls == []
        delivered = []

        def validate_in_worker(inner_db, inner_job):
            delivered.append(current_delivery().anchor)
            validate_gap_repair_delivery(inner_db, job=inner_job, fetch_run=fetch, plan=plan)
            return {"validated": True}

        assert execute_job(db, job, handlers={jobs.IB_FETCH_JOB_TYPE: validate_in_worker}) == {
            "validated": True,
        }
        assert delivered == [anchor]
        assert db.get(IBFetchItem, original_item.id).status == "PARTIAL"
        assert original_item.decision_metadata_json == original_metadata
        # An operator's stale/altered delivery cannot widen the reviewed historical scope.
        changed = deepcopy(job.payload_json)
        changed["fetch_plan"]["items"][0]["request_end_date"] = "2026-11-05"
        job.payload_json = changed
        provider_calls = []

        def unexpected_provider(*args, **kwargs):
            provider_calls.append(True)
            pytest.fail("Tampered queued delivery reached provider execution")

        monkeypatch.setattr(jobs, "execute_fetch_plan", unexpected_provider)
        with pytest.raises(ValueError, match="GAP_REPAIR_DELIVERY_CHANGED"):
            execute_job(db, job, handlers={jobs.IB_FETCH_JOB_TYPE: jobs.execute_durable_fetch_job})
        assert provider_calls == []
        assert original_item.decision_metadata_json == original_metadata
        db.rollback()
