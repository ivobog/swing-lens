from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from alembic import command
from app.database_safety import configure_guarded_alembic
from app.models.ceri_tables import CeriCompany, CeriSecProcessorRelease, CeriSecSyncState
from app.models.tables import RawCompanyRow, UploadRun
from app.services.ceri.sec import processor_lifecycle as lifecycle
from app.services.ceri.sec import processor_rollback as rollback
from app.services.ceri.sec.processor_capability import evaluate_sec_processor_capability
from app.services.pipeline_prerequisites import SecProcessorPromotionRequiredError
from app.services.runtime_mutation_authority import RuntimeMutationAuthority
from scripts.ops import sec_processor_rollback as operator

V3 = "sec-guidance:eed017654682a0c9"
V4 = "sec-guidance:b0bd408b6358d605"
V5 = "sec-guidance:0123456789abcdef"
GIT = "1" * 40
TICKERS = ["ABBV", "AMZN", "NBN", "TOWN"]


def approval(promotion_id):
    now = datetime.now(UTC)
    return rollback.RollbackApproval(
        promotion_id=promotion_id,
        predecessor=V3,
        candidate=V4,
        predecessor_git="2" * 40,
        candidate_git=GIT,
        predecessor_root="previous",
        candidate_root="candidate",
        predecessor_config="3" * 64,
        candidate_config="4" * 64,
        qualification_sha256="5" * 64,
        database_fingerprint="6" * 64,
        database_principal="test",
        alembic_head="0097_sec_scope_evidence",
        operator="uid:test",
        reason="Candidate startup failed after committed promotion",
        starts_at=now - timedelta(minutes=1),
        cutoff_at=now + timedelta(minutes=5),
    )


@pytest.fixture
def rollback_engine(disposable_postgres_database, monkeypatch):
    config = Config("alembic.ini")
    configure_guarded_alembic(config, disposable_postgres_database)
    command.upgrade(config, "head")
    engine = create_engine(disposable_postgres_database)
    now = datetime.now(UTC)
    with Session(engine) as db, db.begin():
        run = UploadRun(filename="disposable-SEC-scope", status="COMPLETED")
        db.add(run)
        db.flush()
        run_id = run.id
        for index, ticker in enumerate(TICKERS, 1):
            cik = None if ticker in {"NBN", "TOWN"} else f"{index:010}"
            db.add(RawCompanyRow(run_id=run_id, row_number=index, ticker=ticker, raw_json={}))
            db.add(
                CeriCompany(
                    ticker=ticker,
                    cik=cik,
                    exchange="US",
                    sec_applicability="REQUIRED" if cik else "NOT_APPLICABLE",
                )
            )
            if cik:
                for signature in (V3, V4):
                    db.add(
                        CeriSecSyncState(
                            cik=cik,
                            processor_signature=signature,
                            dataset="guidance",
                            bootstrap_completed_at=now,
                            last_discovered_at=now,
                            latest_filing_date=now.date(),
                            document_count=5,
                        )
                    )
        # Fail the transaction upon any attempt to write the protected tables.
        for table in (
            "ceri_score_snapshots",
            "ceri_estimate_snapshots",
            "ceri_source_records",
            "winner_prediction_snapshots",
            "ceri_ingestion_runs",
        ):
            db.execute(text(f"LOCK TABLE {table} IN SHARE MODE"))
        db.execute(
            text("""
            CREATE FUNCTION reject_financial_write() RETURNS trigger LANGUAGE plpgsql AS
            $$ BEGIN RAISE EXCEPTION 'financial table changed in rollback'; END $$
        """)
        )
        for table in (
            "ceri_score_snapshots",
            "ceri_estimate_snapshots",
            "ceri_source_records",
            "winner_prediction_snapshots",
            "ceri_ingestion_runs",
        ):
            db.execute(
                text(f"""
                CREATE TRIGGER rollback_test_guard BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE
                ON {table} FOR EACH STATEMENT EXECUTE FUNCTION reject_financial_write()
            """)
            )
        lifecycle.certify_processor(
            db, processor_signature=V3, evidence={"test_run": run_id}, actor="test"
        )
        lifecycle.promote_processor(db, processor_signature=V3, actor="test")
        monkeypatch.setattr(lifecycle, "sec_guidance_processor_signature", lambda: V3)
        lifecycle.require_deployed_processor_active(db)
        monkeypatch.setattr(lifecycle, "sec_guidance_processor_signature", lambda: V4)
        lifecycle.register_deployed_processor(
            db, authority=RuntimeMutationAuthority.normal("test.rollback.register")
        )
        assert lifecycle.lifecycle_state(db).active_signature == V3
        lifecycle.certify_processor(
            db,
            processor_signature=V4,
            actor="test",
            evidence={
                "run_id": run_id,
                "scope_tickers": TICKERS,
                "certification": {"passed": True},
            },
        )
    try:
        yield engine, run_id
    finally:
        engine.dispose()


def _promote(engine, run_id):
    with Session(engine) as db, db.begin():
        candidate = lifecycle.promote_processor(
            db,
            processor_signature=V4,
            actor="operator",
            rollback_run_id=run_id,
            rollback_candidate_git=GIT,
        )
        return approval(candidate.certification_evidence_json[rollback.PROMOTION_RECEIPT]["id"])


def _reverse(db, signed):
    return rollback.rollback_committed_promotion(
        db,
        approval=signed,
        approval_sha256="a" * 64,
        actor=signed.operator,
        authority=RuntimeMutationAuthority.normal("test.rollback"),
    )


def _sync(db):
    return [
        (
            r.id,
            r.cik,
            r.processor_signature,
            r.bootstrap_completed_at,
            r.last_discovered_at,
            r.latest_filing_date,
            r.document_count,
        )
        for r in db.scalars(select(CeriSecSyncState).order_by(CeriSecSyncState.id))
    ]


def _snapshot(engine):
    with Session(engine) as db:
        releases = {
            r.processor_signature: (
                r.status,
                r.activated_at,
                r.retired_at,
                r.certification_evidence_json,
            )
            for r in db.scalars(select(CeriSecProcessorRelease))
        }
        return releases, _sync(db)


def test_exact_rollback_survives_interruption_concurrency_and_lost_ack(
    rollback_engine, monkeypatch
):
    engine, run_id = rollback_engine
    signed = _promote(engine, run_id)
    committed = _snapshot(engine)
    assert committed[0][V3][0] == "RETIRED" and committed[0][V4][0] == "ACTIVE"
    monkeypatch.setattr(lifecycle, "sec_guidance_processor_signature", lambda: V3)
    monkeypatch.setattr(
        "app.services.ceri.sec.processor_capability.sec_guidance_processor_signature", lambda: V3
    )
    with Session(engine) as db, pytest.raises(SecProcessorPromotionRequiredError):
        lifecycle.require_deployed_processor_active(db)
    with Session(engine) as db:
        _reverse(db, signed)
        with Session(engine) as second, pytest.raises(OperationalError):
            _reverse(second, signed)
        db.close()  # Connection loss before acknowledgment and commit.
    assert _snapshot(engine) == committed
    with Session(engine) as db, db.begin():
        result = _reverse(db, signed)
        assert result["rollback"]["actor"] == signed.operator
        assert result["rollback"]["reason"] == signed.reason
    # Read durable audit after simulating loss of the post-commit acknowledgment.
    final = _snapshot(engine)
    assert final[0][V3][0] == "ACTIVE" and final[0][V4][0] == "RETIRED"
    assert [key for key, state in final[0].items() if state[0] == "ACTIVE"] == [V3]
    assert final[0][V4][3][rollback.PROMOTION_RECEIPT] == result
    assert final[1] == committed[1]
    with Session(engine) as db:
        assert lifecycle.require_deployed_processor_active(db).deployed_is_active
        assert evaluate_sec_processor_capability(db).ready
        with pytest.raises(ValueError, match="no longer reversible"):
            _reverse(db, signed)
    assert _snapshot(engine) == final



def test_promotion_commit_boundary_and_stale_certification(rollback_engine):
    engine, run_id = rollback_engine
    original = _snapshot(engine)
    with Session(engine) as first:
        candidate = lifecycle.promote_processor(
            first,
            processor_signature=V4,
            actor="operator",
            rollback_run_id=run_id,
            rollback_candidate_git=GIT,
        )
        uncommitted = candidate.certification_evidence_json[rollback.PROMOTION_RECEIPT]
        assert _snapshot(engine) == original
        with Session(engine) as competing, pytest.raises(OperationalError):
            _reverse(competing, approval(uncommitted["id"]))
        # Controller disconnect before commit must also discard the promotion receipt.
    assert _snapshot(engine) == original

    signed = _promote(engine, run_id)
    committed = _snapshot(engine)
    assert committed[0][V4][3][rollback.PROMOTION_RECEIPT]["id"] == signed.promotion_id
    with Session(engine) as db, db.begin():
        lifecycle.certify_processor(
            db, processor_signature=V4, evidence={"changed": True}, actor="new-certifier"
        )
    changed = _snapshot(engine)
    with Session(engine) as db, pytest.raises(ValueError, match="no longer reversible"):
        _reverse(db, signed)
    assert _snapshot(engine) == changed


def test_rejects_unrelated_expired_changed_or_intervening_transition(rollback_engine):
    engine, run_id = rollback_engine
    signed = _promote(engine, run_id)
    committed = _snapshot(engine)
    for update in (
        {"predecessor": "sec-guidance:948beb114caa8da9"},
        {"candidate_git": "7" * 40},
        {"promotion_id": "0" * 32},
        {"cutoff_at": signed.starts_at - timedelta(seconds=1)},
    ):
        with Session(engine) as db, pytest.raises(ValueError):
            _reverse(db, signed.model_copy(update=update))
    assert _snapshot(engine) == committed
    with Session(engine) as db, db.begin():
        db.execute(
            text(
                "UPDATE ceri_sec_sync_states SET document_count=4 "
                "WHERE processor_signature=:signature"
            ),
            {"signature": V3},
        )
    changed = _snapshot(engine)
    with Session(engine) as db, pytest.raises(ValueError, match="readiness evidence changed"):
        _reverse(db, signed)
    assert _snapshot(engine) == changed
    with Session(engine) as db, db.begin():
        lifecycle.certify_processor(db, processor_signature=V5, evidence={}, actor="test")
        lifecycle.promote_processor(db, processor_signature=V5, actor="test")
    intervened = _snapshot(engine)
    with Session(engine) as db, pytest.raises(ValueError, match="no longer reversible"):
        _reverse(db, signed)
    assert _snapshot(engine) == intervened


def test_incomplete_scope_blocks_recoverable_promotion(rollback_engine):
    engine, run_id = rollback_engine
    with Session(engine) as db, db.begin():
        db.execute(
            text("DELETE FROM ceri_sec_sync_states WHERE processor_signature=:signature"),
            {"signature": V4},
        )
    before = _snapshot(engine)
    with pytest.raises(ValueError, match="readiness scope is incomplete"):
        _promote(engine, run_id)
    assert _snapshot(engine) == before


def test_running_lease_denies_rollback(rollback_engine):
    from app.services.background_job_service import enqueue_job

    engine, run_id = rollback_engine
    signed = _promote(engine, run_id)
    with Session(engine) as db, db.begin():
        job = enqueue_job(db, "WORKER_RECOVERY_PROBE", {"fixture": True})
        job.status = "RUNNING"
    before = _snapshot(engine)
    with Session(engine) as db, pytest.raises(ValueError, match="Active durable jobs"):
        _reverse(db, signed)
    assert _snapshot(engine) == before


def test_signed_operator_rejects_modified_or_prospective_approval(tmp_path, monkeypatch):
    ssh = shutil.which("ssh-keygen")
    if ssh is None:
        pytest.skip("OpenSSH ssh-keygen unavailable in this test environment")
    key = tmp_path / "owner"
    subprocess.run([ssh, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    signers = tmp_path / "allowed"
    public = key.with_suffix(".pub").read_text(encoding="utf-8").strip()
    signers.write_text(f'{operator.OWNER} namespaces="{operator.NAMESPACE}" {public}\n')
    monkeypatch.setattr(operator, "_trust", lambda: (ssh, signers))
    monkeypatch.setattr(operator, "_assert_windows_trust_acl", lambda *_: None)
    path = tmp_path / "owner.json"
    path.write_text(approval("1" * 32).model_dump_json(), encoding="utf-8")
    signature = path.with_suffix(".json.sig")
    subprocess.run(
        [ssh, "-Y", "sign", "-f", str(key), "-n", operator.NAMESPACE, str(path)], check=True
    )
    verified, digest = operator._verify_approval(path, signature)
    assert verified.candidate == V4 and digest == hashlib.sha256(path.read_bytes()).hexdigest()
    path.write_text(path.read_text().replace("startup failed", "startup passed"), encoding="utf-8")
    with pytest.raises(ValueError, match="signature is invalid"):
        operator._verify_approval(path, signature)


@pytest.mark.skipif(os.name != "nt", reason="Native Windows file sharing contract")
def test_native_launcher_lock_rejects_second_handle(tmp_path):
    (tmp_path / "data/cache").mkdir(parents=True)
    with operator._hold_launcher_lock(tmp_path):
        with pytest.raises(ValueError, match="native runtime launcher lock"):
            with operator._hold_launcher_lock(tmp_path):
                pytest.fail("A second launcher handle was admitted.")
    with operator._hold_launcher_lock(tmp_path):
        assert (tmp_path / "data/cache/swinglens-lifecycle.lock").exists()
