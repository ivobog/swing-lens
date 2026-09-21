"""A delayed worker cannot advance frozen maturation business authority."""

from dataclasses import replace
from datetime import timedelta

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from test_ceri_batched_workflow_v2 import _claim_native_fixture_job, _execute_handler
from test_winner_maturation_canary_postgresql import (
    _native_operation_at,
    _seed_native_ready_outcome,
)

from app.models.tables import (
    WinnerForwardOutcome,
    WinnerPredictionSnapshot,
    WinnerProcessingRun,
    WinnerTargetStopOutcome,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.configuration_delivery import ANCHOR_KEY
from app.services.domain_write_fence import current_domain_write_ownership
from app.services.winner_probability import config as native_config
from app.services.winner_probability import outcome_authority
from app.services.winner_probability.job_handlers import (
    enqueue_outcome_maturation_workflow,
    execute_outcome_maturation_job,
)
from app.services.winner_probability.outcome_authority import PROOF_KEY

contextual_engine = contextual.contextual_engine
pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def test_delayed_worker_preserves_cutoff_and_new_operation_seals_exact_lineage(
    contextual_engine, monkeypatch, record_property
):
    original_loader = native_config.load_winner_probability_config
    enabled = original_loader()
    enabled = replace(enabled, engine=replace(enabled.engine, enabled=True))
    monkeypatch.setattr(native_config, "load_winner_probability_config", lambda: enabled)
    with Session(contextual_engine) as db:
        outcome_id = _seed_native_ready_outcome(db, ticker="T14DCLOCK", run_suffix="clock")
        outcome = db.get(WinnerForwardOutcome, outcome_id)
        prediction = db.get(WinnerPredictionSnapshot, outcome.prediction_id)
        prediction_id = prediction.id
        prediction_before = Canonical.fingerprint(prediction.lineage_json)
        c1 = prediction.captured_at + timedelta(seconds=1)
        c2 = _native_operation_at(db, outcome_id)
        worker_now = c2 + timedelta(days=30)
        assert c1.date() < outcome.due_session <= c2.date()

        def enqueue(cutoff, key):
            job = enqueue_outcome_maturation_workflow(
                db,
                payload={"operation_cutoff_at": cutoff.isoformat()},
                trigger_source="TEST",
                request_key=key,
            )
            _claim_native_fixture_job(db, job)
            return job

        early = enqueue(c1, "t14d-maturation-frozen-C1")
        early_id, early_token = early.id, early.execution_token
        frozen_anchor = dict(early.payload_json[ANCHOR_KEY])
        result = _execute_handler(
            db,
            early,
            lambda session, job: execute_outcome_maturation_job(session, job, now=worker_now),
        )
        assert result["matured_h5"] == result["target_stop_matured"] == 0
        assert db.get(WinnerForwardOutcome, outcome_id).status == "PENDING"
        assert early.payload_json["operation_cutoff_at"] == c1.isoformat()
        assert (
            early.execution_token == early_token and early.payload_json[ANCHOR_KEY] == frozen_anchor
        )
        early_processing = db.get(WinnerProcessingRun, result["processing_run_id"])
        assert early_processing.background_job_id == early_id

        later = enqueue(c2, "t14d-maturation-explicit-C2")
        job_id, token = later.id, later.execution_token
        writes = []
        sealed = []
        real_seal = outcome_authority.seal_outcome

        def observe_seal(session, row, *, context, configuration, prices):
            sealed.append((row.id, context.canonical_payload(), context.execution))
            return real_seal(
                session, row, context=context, configuration=configuration, prices=prices
            )

        monkeypatch.setattr(outcome_authority, "seal_outcome", observe_seal)

        def observe(_conn, _cursor, sql, _params, _context, _many):
            if sql.lstrip().upper().startswith("UPDATE WINNER_FORWARD_OUTCOMES"):
                owner = current_domain_write_ownership()
                writes.append((owner.job_id, owner.execution_token))

        event.listen(contextual_engine, "after_cursor_execute", observe)
        try:
            result = _execute_handler(
                db,
                later,
                lambda session, job: execute_outcome_maturation_job(session, job, now=worker_now),
            )
        finally:
            event.remove(contextual_engine, "after_cursor_execute", observe)
        assert writes and set(writes) == {(job_id, token)}
        assert result["matured_h5"] == result["target_stop_matured"] == 1, result
        db.expire_all()
        outcome = db.get(WinnerForwardOutcome, outcome_id)
        assert outcome.prediction_id == prediction_id and outcome.revision == 1
        assert outcome.status == "MATURED" and outcome.matured_at == c2
        proof = outcome.metadata_json[PROOF_KEY]
        authority, execution = next(
            (context, execution) for row_id, context, execution in sealed if row_id == outcome_id
        )
        assert authority["durable"] is True
        assert (execution.job_id, execution.execution_token) == (job_id, token)
        assert authority["temporal"]["cutoff_at"] == Canonical.canonicalize(c2)
        assert proof["prices"] and all(price["id"] for price in proof["prices"])
        processing = db.get(WinnerProcessingRun, result["processing_run_id"])
        assert processing.background_job_id == job_id
        target = db.scalar(
            select(WinnerTargetStopOutcome).where(
                WinnerTargetStopOutcome.forward_outcome_id == outcome_id,
                WinnerTargetStopOutcome.is_current_revision.is_(True),
            )
        )
        assert (
            target.prediction_id == prediction_id
            and target.revision == 1
            and target.status == "MATURED"
        )
        assert (
            Canonical.fingerprint(db.get(WinnerPredictionSnapshot, prediction_id).lineage_json)
            == prediction_before
        )
        for name, value in {
            "prediction_id": prediction_id,
            "job_id": job_id,
            "execution_token": token,
            "operation_cutoff": c2.isoformat(),
            "worker_now": worker_now.isoformat(),
            "outcome_id": outcome_id,
            "outcome_revision": outcome.revision,
            "target_stop_id": target.id,
            "processing_run_id": processing.id,
            "price_lineage_ids": [price["id"] for price in proof["prices"]],
        }.items():
            record_property(name, value)
