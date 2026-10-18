"""Native evidence review: immutable pins, append-only history and rejection rollback."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from test_ceri_evidence_quarantine_postgresql import _upgrade

from app.models.ceri_tables import CeriEvidenceDisposition, CeriScoreSnapshot
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.evidence_eligibility import (
    ELIGIBLE,
    EXCLUDED,
    EvidenceDispositionRequest,
    append_evidence_dispositions,
    eligible_snapshot_select,
)
from app.services.decision_handoff_contract import handoff_artifact_identity
from scripts.forensics.review_resumed_pipeline_evidence import review_resumed_pipeline_evidence


@pytest.fixture
def review_fixture(disposable_postgres_database):
    _upgrade(disposable_postgres_database)
    engine = create_engine(disposable_postgres_database)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into upload_runs(id,filename,status) "
                    "values (41,'review.csv','COMPLETED')"
                )
            )
            connection.execute(
                text(
                    "insert into pipeline_runs(id,upload_run_id,status,current_step) "
                    "values (41,41,'FAILED','EVALUATING_SETUP_LIFECYCLES')"
                )
            )
            connection.execute(
                text(
                    "insert into market_calculation_contexts "
                    "(id,pipeline_run_id,upload_run_id,cutoff_at,exchange_timezone,"
                    "latest_completed_session,calendar_version,"
                    "bar_readiness_version,cutoff_reason) "
                    "values (74,41,41,'2026-10-18 10:51:39+00','America/New_York',"
                    "'2026-10-16','test','test','test')"
                )
            )
            connection.execute(
                text(
                    "insert into ceri_companies(id,ticker,exchange) "
                    "values (1,'AAA','US'),(2,'BBB','US')"
                )
            )
            connection.execute(
                text(
                    "insert into ceri_score_snapshots "
                    "(id,run_id,company_id,ticker,as_of_session,cutoff_at,calculation_context_id,"
                    "opportunity_score,opportunity_coverage_pct,event_risk_score,data_confidence,"
                    "coverage_pct,posture,config_version,config_hash,calculation_version,"
                    "evidence_contract_version,comparison_state,evidence_hash) "
                    "select g,41,g,case g when 1 then 'AAA' else 'BBB' end,'2026-10-16',"
                    "'2026-10-18 10:51:39+00',74,7,100,1,'Normal',100,'Positive',"
                    "'test','test','test','test','COMPARABLE','hash-'||g "
                    "from generate_series(1,2) g"
                )
            )
            connection.execute(
                text(
                    "insert into transition_preflight_plans "
                    "(id,market_calculation_context_id,upload_run_id,pipeline_run_id,status,"
                    "idempotency_key,candidate_classification,evidence_fingerprint,"
                    "technical_reconstruction_fingerprint,expires_at) "
                    "values (1,74,41,41,'CONSUMED','review-test','HIGH','e','t',"
                    "now()+interval '1 hour')"
                )
            )
        with Session(engine) as db:
            pins = [
                handoff_artifact_identity(row)
                for row in db.scalars(select(CeriScoreSnapshot).order_by(CeriScoreSnapshot.ticker))
            ]
        manifest = {
            "run": {"pipeline_run_id": 41, "upload_run_id": 41},
            "ceri_score_snapshots": pins,
        }
        fingerprint = Canonical.fingerprint(manifest)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "insert into transition_decision_handoff_manifests "
                    "(id,preflight_plan_id,market_calculation_context_id,upload_run_id,"
                    "pipeline_run_id,run_start_anchor_fingerprint,manifest_json,"
                    "manifest_fingerprint) values (1,1,74,41,41,'anchor',"
                    "cast(:body as jsonb),:fingerprint)"
                ),
                {"body": json.dumps(manifest), "fingerprint": fingerprint},
            )
        yield engine, fingerprint, pins
    finally:
        engine.dispose()


def _exclude(db, *, manual=False):
    return append_evidence_dispositions(
        db,
        [
            EvidenceDispositionRequest(
                ceri_snapshot_id=value,
                disposition=EXCLUDED,
                reason_code="MANUAL_INTEGRITY_REVIEW" if manual else "PARENT_PIPELINE_UNSUCCESSFUL",
                incident_reference="manual:41" if manual else "pipeline:41",
                actor_source="operator" if manual else "pipeline_terminal_reconciliation",
                metadata_json={"pipeline_run_id": 41, "pipeline_status": "FAILED"},
            )
            for value in (1, 2)
        ],
    )


def _review(db, fingerprint):
    return review_resumed_pipeline_evidence(
        db,
        41,
        expected_handoff_fingerprint=fingerprint,
        incident_reference="run41-remediation-test",
    )


def test_verified_review_is_append_only_idempotent_and_cannot_override_failed_owner(review_fixture):
    engine, fingerprint, pins = review_fixture
    with Session(engine) as db:
        assert _exclude(db) == 2
        db.commit()
        db.execute(text("update pipeline_runs set status='QUEUED' where id=41"))
        result = _review(db, fingerprint)
        assert result["appended_reinstatements"] == 2
        assert _review(db, fingerprint)["appended_reinstatements"] == 0
        db.commit()
        assert [
            handoff_artifact_identity(row)
            for row in db.scalars(select(CeriScoreSnapshot).order_by(CeriScoreSnapshot.ticker))
        ] == pins
        assert list(
            db.scalars(
                eligible_snapshot_select(CeriScoreSnapshot.id).order_by(CeriScoreSnapshot.id)
            )
        ) == [1, 2]
        history = list(
            db.scalars(select(CeriEvidenceDisposition).order_by(CeriEvidenceDisposition.id))
        )
        assert [row.disposition for row in history] == [EXCLUDED, EXCLUDED, ELIGIBLE, ELIGIBLE]
        assert {row.metadata_json["reviewed_exclusion_event_id"] for row in history[2:]} == {
            row.id for row in history[:2]
        }
        db.execute(text("update pipeline_runs set status='FAILED' where id=41"))
        assert list(db.scalars(eligible_snapshot_select(CeriScoreSnapshot.id))) == []
        db.rollback()


@pytest.mark.parametrize("automatic_after_manual", [False, True])
def test_manual_exclusion_survives_review_and_resume_transaction_rolls_back(
    review_fixture, automatic_after_manual
):
    engine, fingerprint, _ = review_fixture
    with Session(engine) as db:
        _exclude(db, manual=True)
        db.commit()
        if automatic_after_manual:
            _exclude(db)
            db.commit()
        db.execute(text("update pipeline_runs set status='QUEUED' where id=41"))
        with pytest.raises(ValueError, match="RESUME_REVIEW_NONAUTOMATIC_EXCLUSION"):
            _review(db, fingerprint)
        db.rollback()
        assert db.scalar(text("select status from pipeline_runs where id=41")) == "FAILED"
        assert (
            db.scalar(
                text("select count(*) from ceri_evidence_dispositions where disposition='ELIGIBLE'")
            )
            == 0
        )


def test_changed_evidence_or_wrong_fingerprint_cannot_be_reinstated(review_fixture):
    engine, fingerprint, _ = review_fixture
    with Session(engine) as db:
        _exclude(db)
        db.commit()
        db.execute(text("update pipeline_runs set status='QUEUED' where id=41"))
        with pytest.raises(ValueError, match="RESUME_REVIEW_FROZEN_HANDOFF_MISMATCH"):
            _review(db, "wrong-fingerprint")
        db.rollback()
        # Simulate drift in a disposable fixture; the native semantic identity
        # must reject it before appending any eligibility event.
        db.execute(text("update pipeline_runs set status='QUEUED' where id=41"))
        db.execute(text("update ceri_score_snapshots set opportunity_score=8 where id=1"))
        db.expire_all()
        with pytest.raises(ValueError, match="RESUME_REVIEW_CERI_EVIDENCE_CHANGED"):
            _review(db, fingerprint)
        db.rollback()
        assert (
            db.scalar(
                text("select count(*) from ceri_evidence_dispositions where disposition='ELIGIBLE'")
            )
            == 0
        )
