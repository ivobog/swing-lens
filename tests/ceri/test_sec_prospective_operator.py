from __future__ import annotations

import json
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, timedelta
from threading import Barrier

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from test_sec_prospective_capture import _APPROVED, _FixtureTransport
from test_sec_prospective_capture import capture_clock as capture_clock
from test_sec_prospective_capture import capture_db as capture_db

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriChangeEvent,
    CeriEstimateSnapshot,
    CeriIngestionRun,
    CeriProcessingRun,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.models.tables import (
    AcquisitionPlanRecord,
    BackgroundJob,
    RefreshCycleRecord,
    WorkScopeRecord,
)
from app.services.ceri.normalization_service import (
    CeriNormalizationCancelled,
    CeriNormalizationService,
)
from app.services.ceri.providers.eodhd_client import EodhdClientConfig
from app.services.ceri.sec import prospective_capture as capture
from app.services.ceri.sec.client import SecClientConfig
from app.services.scope_refresh_adoption import retained_scope_members
from scripts.ops import ceri_prospective_revenue as operator


def _invoke(operation, engine, runtime, document, transport, **kwargs):
    return operator._run(operation, engine, runtime, document, open_response=transport, **kwargs)


def _counts(db):
    tables = (
        AcquisitionPlanRecord,
        BackgroundJob,
        RefreshCycleRecord,
        WorkScopeRecord,
        CeriAlertEvent,
        CeriChangeEvent,
        CeriEstimateSnapshot,
        CeriIngestionRun,
        CeriProcessingRun,
        CeriRevisionFeature,
        CeriScoreSnapshot,
        CeriSourceRecord,
    )
    return {m.__tablename__: db.scalar(select(func.count()).select_from(m)) for m in tables}


@pytest.fixture
def pilot_engine(capture_db, monkeypatch):
    monkeypatch.setattr(operator, "datetime", capture.datetime)
    engine, cache, submissions = capture_db
    with Session(engine) as db:
        fingerprint, role, head = operator._database(db)
    runtime = operator.Runtime(
        "3" * 40,
        "uid:42",
        "a" * 64,
        head,
        cache,
        "fixture",
        "b" * 64,
        "fixture",
        SecClientConfig(timeout_seconds=1, requests_per_second=1000),
        EodhdClientConfig(api_key="fixture-only", timeout_seconds=1),
    )
    now = operator.datetime.now(UTC)
    approval = operator.Approval(
        operation="reserve",
        ingestion_run_id=None,
        source_grant=operator.GRANT,
        request_key=operator.REQUEST_KEY,
        operator="fixture operator",
        principal=runtime.principal,
        approval_reference="test-owner-grant",
        git_sha=runtime.git_sha,
        runtime_fingerprint=runtime.fingerprint,
        database_fingerprint=fingerprint,
        database_principal=role,
        alembic_head=head,
        starts_at=now - timedelta(seconds=10),
        cutoff_at=now + timedelta(minutes=5),
        symbols=tuple(sorted(_APPROVED)),
        sec_attempts=12,
        eodhd_attempts=6,
        observations=24,
        entitlement_reference="fixture account evidence",
        included_calendar_units=6,
        entitlement_checked_at=now,
        entitlement_valid_until=now + timedelta(minutes=6),
    )
    return engine, runtime, approval, submissions


def _execution(reservation, approval):
    return approval.model_copy(
        update={"operation": "execute", "ingestion_run_id": reservation["ingestion_run_id"]}
    )


def _abbv_approval(approval):
    return approval.model_copy(
        update={
            "source_grant": operator.ABBV_GRANT,
            "request_key": operator.ABBV_REQUEST_KEY,
            "symbols": ("ABBV.US",),
            "sec_attempts": 2,
            "eodhd_attempts": 1,
            "observations": 4,
        }
    )


def test_preflight_reserve_and_exact_six_issuer_execute(pilot_engine, monkeypatch):
    engine, runtime, approval, submissions = pilot_engine
    transport = _FixtureTransport(submissions, rows=4)
    original_cache_check = operator._cache_checks

    def assert_read_only(db, *args):
        assert db.scalar(text("SHOW transaction_read_only")) == "on"
        return original_cache_check(db, *args)

    monkeypatch.setattr(operator, "_cache_checks", assert_read_only)
    inspection = _invoke("inspect", engine, runtime, approval, transport)
    assert inspection["ready"] and set(inspection["issuers"]) == _APPROVED
    assert not _invoke("inspect", engine, runtime, None, transport)["ready"]
    with Session(engine) as db:
        assert all(count == 0 for count in _counts(db).values())
    reservation = _invoke("reserve", engine, runtime, approval, transport)
    assert transport.calls == []
    with Session(engine) as db:
        owner = db.get(CeriIngestionRun, reservation["ingestion_run_id"])
        assert owner.status == "RUNNING" and owner.checkpoint_json is None
        for field in ("scope_id", "refresh_cycle_id", "acquisition_plan_id"):
            assert getattr(owner, field) == reservation[field]
        assert {
            member.subject_id for member in retained_scope_members(db, owner.scope_id)
        } == _APPROVED
        counts = _counts(db)
        assert counts.pop("ceri_ingestion_runs") == counts.pop("work_scope_records") == 1
        assert counts.pop("refresh_cycle_records") == counts.pop("acquisition_plan_records") == 1
        assert all(count == 0 for count in counts.values())
    with pytest.raises(operator.Unavailable, match="already_reserved"):
        _invoke("reserve", engine, runtime, approval, transport)
    assert transport.calls == []
    execution = _execution(reservation, approval)
    outcome = _invoke("execute", engine, runtime, execution, transport)
    assert outcome["status"] == "COMPLETED"
    assert outcome["requests"] == {"sec": 12, "eodhd": 6} and outcome["normalized"] == 24
    spot = next(issuer for issuer in outcome["issuers"] if issuer["symbol"] == "SPOT.US")
    assert {row["currency"] for row in spot["observations"]} == {"EUR"}
    with Session(engine) as db:
        counts = _counts(db)
        assert counts["ceri_source_records"] == 30 and counts["ceri_estimate_snapshots"] == 24
        for table in (
            "background_jobs",
            "ceri_score_snapshots",
            "ceri_alert_events",
            "ceri_revision_features",
            "ceri_change_events",
        ):
            assert counts[table] == 0
    assert CeriNormalizationService().sec_currency_capture is None
    with pytest.raises(ValueError, match="already claimed"):
        _invoke("execute", engine, runtime, execution, transport)
    assert len(transport.calls) == 18


def test_rejected_approval_context_has_no_provider_access(pilot_engine):
    engine, runtime, approval, submissions = pilot_engine
    transport = _FixtureTransport(submissions)
    for field, value in (
        ("principal", "uid:99"),
        ("git_sha", "2" * 40),
        ("runtime_fingerprint", "c" * 64),
        ("database_fingerprint", "d" * 64),
        ("database_principal", "different-role"),
        ("alembic_head", "wrong-schema"),
        ("symbols", ("MSFT.US",)),
        ("symbols", ("AAPL.US", "AAPL.US")),
        ("sec_attempts", 13),
        ("eodhd_attempts", 7),
        ("observations", 25),
        ("included_calendar_units", 5),
        ("entitlement_valid_until", approval.starts_at),
        ("cutoff_at", approval.starts_at + timedelta(minutes=31)),
        ("starts_at", approval.cutoff_at - timedelta(seconds=1)),
    ):
        with pytest.raises(ValueError):
            _invoke(
                "reserve", engine, runtime, approval.model_copy(update={field: value}), transport
            )
    expired = approval.model_copy(
        update={
            "starts_at": approval.starts_at - timedelta(hours=1),
            "cutoff_at": approval.cutoff_at - timedelta(hours=1),
        }
    )
    with pytest.raises(ValueError):
        _invoke("reserve", engine, runtime, expired, transport)
    with pytest.raises(ValueError):
        _invoke("reserve", engine, runtime, None, transport)
    with Session(engine) as db:
        assert all(count == 0 for count in _counts(db).values())
    reservation = _invoke("reserve", engine, runtime, approval, transport)
    execution = _execution(reservation, approval)
    for field, value in (
        ("ingestion_run_id", reservation["ingestion_run_id"] + 1),
        ("operator", "other operator"),
        ("approval_reference", "different approval"),
        ("symbols", ("AAPL.US",)),
        ("cutoff_at", approval.cutoff_at - timedelta(seconds=1)),
        ("entitlement_reference", "changed evidence"),
        ("observations", 12),
    ):
        with pytest.raises(ValueError, match="outside the approved"):
            _invoke(
                "execute", engine, runtime, execution.model_copy(update={field: value}), transport
            )
    with pytest.raises(ValueError, match="operation_or_window"):
        _invoke("execute", engine, runtime, approval, transport)
    assert transport.calls == []


def test_competing_spot_filing_blocks_existing_guard(pilot_engine):
    engine, runtime, approval, submissions = pilot_engine
    transport = _FixtureTransport(submissions, rows=4)
    proof = capture._REVIEWED["SPOT.US"]
    with Session(engine) as db, db.begin():
        reviewed = db.get(CeriSecFilingDocument, proof.registry_id)
        db.add(
            CeriSecFilingDocument(
                cik=proof.cik,
                accession_number="0001140361-26-040435",
                document_name="other.htm",
                form="6-K",
                filing_date=reviewed.filing_date,
                first_seen_at=operator.datetime.now(UTC) - timedelta(seconds=10),
            )
        )
    report = _invoke("inspect", engine, runtime, approval, transport)
    assert report["issuers"]["SPOT.US"] == "locally_competing_filing"
    assert not report["ready"]
    with pytest.raises(operator.Unavailable, match="cache_evidence_unavailable"):
        _invoke("reserve", engine, runtime, approval, transport)
    assert transport.calls == []
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CeriIngestionRun)) == 0


def test_competitor_appearing_after_preflight_rolls_back_spot(pilot_engine):
    engine, runtime, approval, submissions = pilot_engine
    approval = approval.model_copy(update={"symbols": ("SPOT.US",)})
    transport = _FixtureTransport(submissions, rows=4)
    reservation = _invoke("reserve", engine, runtime, approval, transport)
    proof = capture._REVIEWED["SPOT.US"]

    def late_competitor(request, *, timeout):
        if len(transport.calls) == 2:
            with Session(engine) as db, db.begin():
                reviewed = db.get(CeriSecFilingDocument, proof.registry_id)
                db.add(
                    CeriSecFilingDocument(
                        cik=proof.cik,
                        accession_number="0001140361-26-040435",
                        document_name="other.htm",
                        form="6-K",
                        filing_date=reviewed.filing_date,
                        first_seen_at=operator.datetime.now(UTC) - timedelta(minutes=1),
                    )
                )
        return transport(request, timeout=timeout)

    result = _invoke("execute", engine, runtime, _execution(reservation, approval), late_competitor)
    assert result["status"] == "PARTIAL"
    assert result["requests"] == {"sec": 2, "eodhd": 1}
    assert result["issuers"] == [
        {
            "symbol": "SPOT.US",
            "status": "UNAVAILABLE",
            "reason": "new_owned_observation_cannot_bind",
        }
    ]
    with Session(engine) as db:
        assert not db.scalar(select(func.count()).select_from(CeriEstimateSnapshot))
        assert not db.scalar(select(func.count()).select_from(CeriSourceRecord))


@pytest.mark.parametrize(
    ("limits", "rows", "calls_before_cancel", "status", "reason", "attempts"),
    [
        ({"sec_attempts": 1}, 1, None, "FAILED", "sec_attempt_budget_exhausted", 2),
        ({"eodhd_attempts": 1}, 1, None, "FAILED", "eodhd_attempt_budget_exhausted", 4),
        ({"observations": 1}, 2, None, "FAILED", "observation_budget_exhausted", 2),
        ({}, 1, 4, "CANCELLED", None, 4),
    ],
)
def test_hard_caps_and_cancellation_rollback(
    pilot_engine, limits, rows, calls_before_cancel, status, reason, attempts
):
    engine, runtime, approval, submissions = pilot_engine
    approval = approval.model_copy(update=limits)
    transport = _FixtureTransport(submissions, rows=rows)
    reservation = _invoke("reserve", engine, runtime, approval, transport)
    execution = _execution(reservation, approval)
    with pytest.raises(CeriNormalizationCancelled):
        _invoke("execute", engine, runtime, execution, transport, cancelled=lambda: True)
    assert transport.calls == []
    outcome = _invoke(
        "execute",
        engine,
        runtime,
        execution,
        transport,
        cancelled=lambda: (
            calls_before_cancel is not None and len(transport.calls) >= calls_before_cancel
        ),
    )
    assert outcome["status"] == status and len(transport.calls) == attempts
    if reason:
        assert outcome["reason"] == reason
    with Session(engine) as db:
        owner = db.get(CeriIngestionRun, reservation["ingestion_run_id"])
        assert owner.status == status and owner.checkpoint_json == outcome
        counts = _counts(db)
        for table in (
            "ceri_estimate_snapshots",
            "ceri_source_records",
            "ceri_processing_runs",
            "background_jobs",
            "ceri_score_snapshots",
            "ceri_alert_events",
            "ceri_revision_features",
            "ceri_change_events",
        ):
            assert counts[table] == 0
    with pytest.raises(ValueError, match="already claimed"):
        _invoke("execute", engine, runtime, execution, transport)
    assert len(transport.calls) == attempts


@pytest.fixture
def approval_signer(tmp_path, monkeypatch):
    ssh = shutil.which("ssh-keygen")
    if not ssh:
        pytest.skip("OpenSSH is unavailable")
    key = tmp_path / "test-owner"
    subprocess.run([ssh, "-q", "-t", "ed25519", "-N", "", "-f", str(key)], check=True)
    signers = tmp_path / "test-signers"
    signers.write_text(f"{operator.OWNER} {key.with_suffix('.pub').read_text()}", encoding="utf-8")
    monkeypatch.setattr(operator, "_trust", lambda: (ssh, signers))
    manifest = tmp_path / "approval.json"
    signature = tmp_path / "approval.json.sig"

    def sign(value):
        if signature.is_file():
            signature.unlink()
        manifest.write_text(value, encoding="utf-8")
        subprocess.run(
            [ssh, "-Y", "sign", "-f", str(key), "-n", operator.NAMESPACE, str(manifest)],
            check=True,
        )
        return manifest, signature

    return sign


def test_authenticate_owner_and_reject_tampered_or_duplicate_fields(pilot_engine, approval_signer):
    _, _, approval, _ = pilot_engine
    approved = approval.model_dump_json()
    manifest, signature = approval_signer(approved)
    assert operator._approval(manifest, signature) == approval
    execution = approval.model_copy(update={"operation": "execute", "ingestion_run_id": 9})
    approval_signer(execution.model_dump_json())
    assert operator._approval(manifest, signature) == execution
    manifest.write_text(approved.replace("fixture operator", "other operator"), encoding="utf-8")
    with pytest.raises(operator.Unavailable, match="owner_signature_invalid"):
        operator._approval(manifest, signature)
    approval_signer(approved[:-1] + ', "operator": "different"}')
    with pytest.raises(operator.Unavailable, match="duplicate_approval_field"):
        operator._approval(manifest, signature)
    approval_signer(json.dumps({**json.loads(approved), "source_grant": "different-grant"}))
    with pytest.raises(ValidationError):
        operator._approval(manifest, signature)


def test_abbv_grant_reserves_and_executes_without_reopening_27601(pilot_engine, approval_signer):
    engine, runtime, approval, submissions = pilot_engine
    abbv = _abbv_approval(approval)
    original = abbv.model_copy(
        update={
            "source_grant": operator.GRANT,
            "request_key": operator.REQUEST_KEY,
        }
    )

    def signed(envelope):
        return operator._approval(*approval_signer(envelope.model_dump_json()))

    # Exercise the real reservation guard against a terminal fixture with the original ID.
    with engine.begin() as db:
        db.execute(
            text("SELECT setval(pg_get_serial_sequence('ceri_ingestion_runs', 'id'), 27600)")
        )
    empty = _FixtureTransport(submissions, abbv_candidates=())
    original_reservation = _invoke("reserve", engine, runtime, signed(original), empty)
    assert original_reservation["ingestion_run_id"] == 27601
    assert original_reservation["request_key"] == operator.REQUEST_KEY
    original_execution = _execution(original_reservation, original)
    closed = _invoke("execute", engine, runtime, signed(original_execution), empty)
    assert (closed["status"], closed["normalized"]) == ("PARTIAL", 0)
    with Session(engine) as db:
        historical = dict(
            db.execute(select(CeriIngestionRun.__table__).where(CeriIngestionRun.id == 27601))
            .mappings()
            .one()
        )

    transport = _FixtureTransport(submissions, rows=4)
    fresh_original = original.model_copy(update={"approval_reference": "fresh original signature"})
    with pytest.raises(operator.Unavailable, match="source_grant_already_reserved"):
        _invoke("reserve", engine, runtime, signed(fresh_original), transport)
    with pytest.raises(ValueError, match="already claimed"):
        _invoke("execute", engine, runtime, signed(original_execution), transport)

    reservation = _invoke("reserve", engine, runtime, signed(abbv), transport)
    assert reservation["ingestion_run_id"] != 27601
    assert reservation["request_key"] == operator.ABBV_REQUEST_KEY
    execution = _execution(reservation, abbv)
    wrong_id = execution.model_copy(update={"ingestion_run_id": 27601})
    changed_budget = execution.model_copy(update={"observations": 3})
    wrong_grant = _execution(reservation, original)
    for changed in (wrong_id, changed_budget, wrong_grant):
        with pytest.raises(ValueError, match="outside the approved scope"):
            _invoke("execute", engine, runtime, signed(changed), transport)
    assert transport.calls == []
    with Session(engine) as db:
        owner = db.get(CeriIngestionRun, reservation["ingestion_run_id"])
        assert owner.scope_json == abbv.scope().envelope() and owner.checkpoint_json is None
        assert {member.subject_id for member in retained_scope_members(db, owner.scope_id)} == {
            "ABBV.US"
        }

    result = _invoke("execute", engine, runtime, signed(execution), transport)
    assert result["status"] == "COMPLETED" and result["normalized"] == 4
    assert result["requests"] == {"sec": 2, "eodhd": 1}
    assert [row["symbol"] for row in result["issuers"]] == ["ABBV.US"]
    assert {row["currency"] for row in result["issuers"][0]["observations"]} == {"USD"}
    with pytest.raises(ValueError, match="already claimed"):
        _invoke("execute", engine, runtime, signed(execution), transport)
    fresh_abbv = abbv.model_copy(update={"approval_reference": "fresh ABBV signature"})
    with pytest.raises(operator.Unavailable, match="source_grant_already_reserved"):
        _invoke("reserve", engine, runtime, signed(fresh_abbv), transport)
    assert len(transport.calls) == 3
    with Session(engine) as db:
        assert (
            dict(
                db.execute(select(CeriIngestionRun.__table__).where(CeriIngestionRun.id == 27601))
                .mappings()
                .one()
            )
            == historical
        )
        counts = _counts(db)
        assert counts["ceri_ingestion_runs"] == 2
        assert counts["ceri_source_records"] == 5 and counts["ceri_estimate_snapshots"] == 4
        assert counts["ceri_processing_runs"] == 1
        for table in (
            "background_jobs",
            "ceri_score_snapshots",
            "ceri_alert_events",
            "ceri_revision_features",
            "ceri_change_events",
        ):
            assert counts[table] == 0


def test_concurrent_abbv_reservations_consume_grant_once(
    pilot_engine, approval_signer, monkeypatch
):
    engine, runtime, approval, submissions = pilot_engine
    abbv = _abbv_approval(approval)
    transport = _FixtureTransport(submissions)
    reserve = operator._reserve
    ready = Barrier(2, timeout=15)

    def simultaneous_reserve(*args):
        ready.wait()
        return reserve(*args)

    monkeypatch.setattr(operator, "_reserve", simultaneous_reserve)

    def attempt(envelope):
        try:
            return _invoke("reserve", engine, runtime, envelope, transport)
        except operator.Unavailable as exc:
            return str(exc)

    approvals = [
        operator._approval(
            *approval_signer(
                abbv.model_copy(update={"approval_reference": reference}).model_dump_json()
            )
        )
        for reference in ("first signed scope", "second signed scope")
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, approvals))

    receipts = [row for row in outcomes if isinstance(row, dict)]
    assert len(receipts) == 1 and receipts[0]["request_key"] == operator.ABBV_REQUEST_KEY
    assert outcomes.count("source_grant_already_reserved") == 1
    assert transport.calls == []
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CeriIngestionRun)) == 1


def test_abbv_grant_rejects_changed_scope_or_binding_before_provider_access(pilot_engine):
    engine, runtime, approval, submissions = pilot_engine
    abbv = _abbv_approval(approval)
    transport = _FixtureTransport(submissions)
    for updates in (
        {"source_grant": "unregistered-grant"},
        {"request_key": operator.REQUEST_KEY},
        {"source_grant": operator.GRANT},
        {"symbols": ("AMZN.US",)},
        {"symbols": ("ABBV.US", "AAPL.US")},
        {"sec_attempts": 3},
        {"eodhd_attempts": 2},
        {"observations": 5},
        {"cutoff_at": abbv.starts_at + timedelta(minutes=31)},
        {"operation": "execute", "ingestion_run_id": 27601},
        {"principal": "uid:99"},
        {"git_sha": "2" * 40},
        {"runtime_fingerprint": "c" * 64},
        {"database_fingerprint": "d" * 64},
        {"database_principal": "other-role"},
    ):
        with pytest.raises(ValueError):
            _invoke("reserve", engine, runtime, abbv.model_copy(update=updates), transport)
    assert transport.calls == []
    with Session(engine) as db:
        assert all(count == 0 for count in _counts(db).values())


def test_abbv_forged_approval_is_rejected_by_entry_point(
    pilot_engine, approval_signer, monkeypatch, capsys
):
    _, _, approval, _ = pilot_engine
    abbv = _abbv_approval(approval)
    manifest, signature = approval_signer(abbv.model_dump_json())
    manifest.write_text(
        abbv.model_dump_json().replace("fixture operator", "forged"), encoding="utf-8"
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ceri_prospective_revenue",
            "reserve",
            "--approval",
            str(manifest),
            "--signature",
            str(signature),
        ],
    )
    monkeypatch.setattr(operator, "get_settings", lambda: pytest.fail("runtime must not open"))
    assert operator.main() == 2
    assert json.loads(capsys.readouterr().out) == {
        "status": "BLOCKED",
        "reason": "owner_signature_invalid",
    }


@pytest.mark.parametrize("status", (302, 503))
def test_abbv_calendar_failure_uses_one_attempt_and_commits_no_evidence(pilot_engine, status):
    engine, runtime, approval, submissions = pilot_engine
    abbv = _abbv_approval(approval)
    transport = _FixtureTransport(submissions)

    def unsuccessful_calendar(request, *, timeout):
        response = transport(request, timeout=timeout)
        if request.full_url.startswith("https://eodhd.com/api/calendar/trends?"):
            response.status = status
        return response

    reservation = _invoke("reserve", engine, runtime, abbv, transport)
    outcome = _invoke(
        "execute", engine, runtime, _execution(reservation, abbv), unsuccessful_calendar
    )
    assert (outcome["status"], outcome["normalized"]) == ("PARTIAL", 0)
    assert outcome["requests"] == {"sec": 1, "eodhd": 1}
    assert outcome["issuers"] == [
        {"symbol": "ABBV.US", "status": "UNAVAILABLE", "reason": "provider_unavailable"}
    ]
    assert [provider for provider, _ in transport.calls] == ["sec", "eodhd"]
    with Session(engine) as db:
        counts = _counts(db)
        assert counts["ceri_source_records"] == counts["ceri_estimate_snapshots"] == 0
        assert counts["ceri_processing_runs"] == counts["background_jobs"] == 0
