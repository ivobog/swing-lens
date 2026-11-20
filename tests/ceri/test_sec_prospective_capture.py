from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from test_sec_reporting_currency import fixture as filing_fixture

from app.models.ceri_tables import (
    CeriAlertEvent,
    CeriCompany,
    CeriEstimateSnapshot,
    CeriIngestionRun,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import BackgroundJob
from app.services.alembic_heads import cached_repository_alembic_heads
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.providers.eodhd_client import EodhdClientConfig
from app.services.ceri.sec import prospective_capture as candidate
from app.services.ceri.sec.client import SecClientConfig
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.services.scope_refresh_adoption import admit_frozen_operation
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember

_APPROVED = {"AAPL.US", "ABBV.US", "AMZN.US", "HG.US", "LIN.US", "SPOT.US"}


class _Response(BytesIO):
    status = 200


class _FixtureTransport:
    def __init__(self, filings, *, mode="ok", rows=1, value=100):
        self.filings = filings
        self.mode = mode
        self.rows = rows
        self.value = value
        self.calls = []

    def __call__(self, request, *, timeout):
        assert timeout == 1
        url = urlsplit(request.full_url)
        if url.netloc == "data.sec.gov":
            assert url.path.startswith("/submissions/CIK") and not url.query
            cik = url.path.rsplit("CIK", 1)[1].split(".")[0]
            self.calls.append(("sec", cik))
            submission = deepcopy(self.filings[cik])
            if self.mode == "wrong_cik":
                submission["cik"] = "0000789019"
            if self.mode == "wrong_entity":
                submission["name"] = "Another issuer"
            if self.mode == "superseded" and self.calls.count(("sec", cik)) == 2:
                recent = submission["filings"]["recent"]
                for key, value in {
                    "accessionNumber": "0000000001-26-999999",
                    "primaryDocument": "later.htm",
                    "form": "10-Q",
                    "reportDate": recent["reportDate"][0],
                    "acceptanceDateTime": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
                }.items():
                    recent[key].append(value)
            return _Response(json.dumps(submission).encode())
        assert url.netloc == "eodhd.com" and url.path == "/api/calendar/trends"
        (ticker,) = parse_qs(url.query)["symbols"]
        assert ticker in _APPROVED
        self.calls.append(("eodhd", ticker))
        if self.mode == "partial_http_failure" and ticker == "ABBV.US":
            raise HTTPError(request.full_url, 503, "fixture failure", {}, None)
        today = datetime.now(UTC).date()
        values = []
        for index, period in enumerate(("0q", "+1q", "0y", "+1y")[: self.rows]):
            row = {
                "code": "MSFT.US" if self.mode == "extra_response_issuer" else ticker,
                "date": (today + timedelta(days=12 + index * 95)).isoformat(),
                "period": period,
                "revenueEstimateAvg": str(self.value + index),
                "earningsEstimateAvg": "3.00",
            }
            if self.mode == "late_authority":
                row["observedAt"] = (datetime.now(UTC) - timedelta(minutes=4)).isoformat()
            if self.mode == "unexpected_currency":
                row["currency"] = "GBP"
            values.append(row)
        return _Response(json.dumps(values).encode())


@pytest.fixture
def capture_db(disposable_postgres_database, tmp_path, monkeypatch):
    engine = create_engine(disposable_postgres_database)
    CeriCompany.metadata.create_all(engine)
    with engine.begin() as connection:
        (head,) = cached_repository_alembic_heads(Path(__file__).resolve().parents[2])
        connection.execute(text("create table alembic_version (version_num text not null)"))
        connection.execute(
            text("insert into alembic_version values (:version)"),
            {"version": head},
        )
    proofs = {}
    submissions = {}
    with Session(engine) as db, db.begin():
        for index, (symbol, proof) in enumerate(sorted(candidate._REVIEWED.items()), 1):
            _, company, review = filing_fixture(tmp_path, symbol[:-3], clock=datetime.now(UTC))
            company.id = index
            review.document.id = proof.registry_id
            assert (company.cik, review.document.accession_number) == (proof.cik, proof.accession)
            proofs[symbol] = replace(
                proof,
                entity=review.submissions["name"],
                sha256=review.document.last_content_hash,
                accepted_at=datetime.fromisoformat(
                    review.submissions["filings"]["recent"]["acceptanceDateTime"][0]
                ),
            )
            submissions[proof.cik] = review.submissions
            db.add_all((company, review.document))
    # Fixture evidence uses smaller synthetic statement bytes. The real cache, SEC parser,
    # approved source writer, and all caller guards run unchanged.
    monkeypatch.setattr(candidate, "_REVIEWED", proofs)
    yield engine, tmp_path, submissions
    engine.dispose()


def _owned_run(engine, key, symbols, **limits):
    now = datetime.now(UTC)
    scope = candidate.RevenueCaptureScope(
        ingestion_run_id=1,
        request_key=key,
        operator="fixture-operator",
        approval_reference="fixture-approval",
        starts_at=now - timedelta(seconds=10),
        cutoff_at=now + timedelta(minutes=5),
        symbols=symbols,
        sec_attempts=12,
        eodhd_attempts=6,
        observations=24,
    )
    scope = replace(scope, **limits)
    with Session(engine) as db, db.begin():
        ownership = admit_frozen_operation(
            db,
            operation_kind=candidate.CAPTURE_SCOPE_KIND,
            subject_kind="SYMBOL",
            members=tuple(ScopeMember("SYMBOL", symbol) for symbol in symbols),
            cycle_key=key,
            business_cutoff=scope.cutoff_at,
            provider_source_class="eodhd",
            request_type="estimates",
            requirements=(AcquisitionRequirement("REVENUE"),),
            policy_identity=candidate.POLICY,
            scope_definition=scope.envelope(),
        )
        run = CeriSourceRecordService().create_ingestion_run(
            db,
            provider="eodhd",
            provider_terms_version="fixture",
            dataset="estimates",
            scope=scope.envelope(),
            request_key=key,
            config_version="fixture",
            config_hash="fixture",
            semantic_authority=ownership,
        )
        return replace(scope, ingestion_run_id=run.id)


def _execute(engine, cache, scope, transport, cancelled=lambda: False):
    with Session(engine) as db:
        return candidate.capture_prospective_revenue(
            db,
            scope,
            cache_dir=cache,
            sec_config=SecClientConfig(
                timeout_seconds=1,
                requests_per_second=1000,
            ),
            eodhd_config=EodhdClientConfig(api_key="fixture-only", timeout_seconds=1),
            open_response=transport,
            should_cancel=cancelled,
        )


def _count(db, table):
    return db.scalar(select(func.count()).select_from(table))


def test_six_owned_issuers_and_no_downstream_output(capture_db, record_property):
    engine, cache, submissions = capture_db
    scope = _owned_run(engine, "f10-test-success", tuple(sorted(_APPROVED)))
    transport = _FixtureTransport(submissions, rows=4)
    result = _execute(engine, cache, scope, transport)
    assert result["status"] == "COMPLETED", result
    assert result["requests"] == {"sec": 12, "eodhd": 6}
    assert result["normalized"] == 24
    record_property("capture_receipt", json.dumps(result, sort_keys=True))
    assert {issuer["symbol"] for issuer in result["issuers"]} == _APPROVED
    assert len({issuer["authority_source_id"] for issuer in result["issuers"]}) == 6
    assert CeriNormalizationService().sec_currency_capture is None
    with Session(engine) as db:
        assert db.get(CeriIngestionRun, scope.ingestion_run_id).checkpoint_json == result
        assert _count(db, CeriSourceRecord) == 30
        assert _count(db, CeriEstimateSnapshot) == 24
        for downstream in (BackgroundJob, CeriRevisionFeature, CeriScoreSnapshot, CeriAlertEvent):
            assert _count(db, downstream) == 0
        for issuer in result["issuers"]:
            symbol = issuer["symbol"]
            sec = db.get(CeriSourceRecord, issuer["authority_source_id"])
            possession = datetime.fromisoformat(issuer["authority_possessed_at"])
            assert sec.provider == "sec" and sec.retrieved_at == possession
            assert sec.ingestion_run_id is None and sec.raw_json is None
            assert (
                sec.restricted_normalized_json["eodhd_ingestion_run_id"] == scope.ingestion_run_id
            )
            assert transport.calls.count(("sec", issuer["cik"])) == 2
            for link in issuer["observations"]:
                row = db.get(CeriEstimateSnapshot, link["estimate_id"])
                eodhd = db.get(CeriSourceRecord, link["source_record_id"])
                assert eodhd.ingestion_run_id == scope.ingestion_run_id
                assert possession < eodhd.observed_at <= eodhd.retrieved_at < scope.cutoff_at
                assert row.conversion_source_record_id == sec.id
                assert row.currency_basis == "filing_currency"
                assert row.canonical_currency == ("EUR" if symbol == "SPOT.US" else "USD")
                assert row.source_scale == row.canonical_scale == Decimal(1)
                assert row.consensus == Decimal(eodhd.restricted_normalized_json["consensus"])
    recorded = len(transport.calls)
    with pytest.raises(ValueError, match="already claimed"):
        _execute(engine, cache, scope, transport)
    assert len(transport.calls) == recorded


@pytest.mark.parametrize(
    ("mode", "reason", "attempts"),
    [
        ("wrong_cik", "reviewed_filing_stale_or_entity_changed", {"sec": 1, "eodhd": 0}),
        ("wrong_entity", "reviewed_filing_stale_or_entity_changed", {"sec": 1, "eodhd": 0}),
        ("superseded", "reviewed_filing_stale_or_entity_changed", {"sec": 2, "eodhd": 1}),
        ("late_authority", "estimate_identity_or_observation_time_invalid", {"sec": 2, "eodhd": 1}),
        ("extra_response_issuer", "empty_or_duplicate_provider_rows", {"sec": 1, "eodhd": 1}),
        ("unexpected_currency", "new_owned_observation_cannot_bind", {"sec": 2, "eodhd": 1}),
    ],
)
def test_rejected_evidence_rolls_back_issuer(capture_db, mode, reason, attempts):
    engine, cache, submissions = capture_db
    scope = _owned_run(engine, "f10-reject", ("AAPL.US",))
    result = _execute(engine, cache, scope, _FixtureTransport(submissions, mode=mode))
    assert result["status"] == "PARTIAL"
    assert result["issuers"] == [{"symbol": "AAPL.US", "status": "UNAVAILABLE", "reason": reason}]
    assert result["requests"] == attempts
    with Session(engine) as db:
        assert _count(db, CeriSourceRecord) == _count(db, CeriEstimateSnapshot) == 0


@pytest.mark.parametrize("returned_id", [None, 999999])
def test_missing_or_invalid_persisted_authority(capture_db, monkeypatch, returned_id):
    engine, cache, submissions = capture_db
    scope = _owned_run(engine, "f10-missing", ("AAPL.US",))
    monkeypatch.setattr(candidate, "register_statement", lambda *_args, **_kw: returned_id)
    result = _execute(engine, cache, scope, _FixtureTransport(submissions))
    assert result["issuers"][0]["reason"] == "approved_authority_missing_or_mismatched"
    assert result["requests"] == {"sec": 1, "eodhd": 0}


def test_partial_failure_and_cancel_preserve_claims(capture_db):
    engine, cache, submissions = capture_db
    scope = _owned_run(engine, "f10-partial", ("AAPL.US", "ABBV.US"))
    partial = _execute(
        engine,
        cache,
        scope,
        _FixtureTransport(submissions, mode="partial_http_failure"),
    )
    assert (partial["status"], partial["normalized"]) == ("PARTIAL", 1)
    assert partial["requests"] == {"sec": 3, "eodhd": 2}
    assert partial["issuers"][1]["reason"] == "provider_unavailable"
    scope = _owned_run(engine, "f10-cancel", ("AAPL.US", "ABBV.US"))
    transport = _FixtureTransport(submissions, value=200)
    result = _execute(engine, cache, scope, transport, cancelled=lambda: len(transport.calls) >= 4)
    assert (result["status"], result["normalized"]) == ("CANCELLED", 0)
    assert result["requests"] == {"sec": 3, "eodhd": 1}
    with Session(engine) as db:
        assert db.get(CeriIngestionRun, scope.ingestion_run_id).checkpoint_json == result
        assert _count(db, CeriSourceRecord) == 2
        assert _count(db, CeriEstimateSnapshot) == 1
    with pytest.raises(ValueError, match="already claimed"):
        _execute(engine, cache, scope, transport)
    assert len(transport.calls) == 4


@pytest.mark.parametrize(
    ("limits", "reason", "attempts"),
    [
        ({"sec_attempts": 1}, "sec_attempt_budget_exhausted", {"sec": 1, "eodhd": 1}),
        ({"eodhd_attempts": 1}, "eodhd_attempt_budget_exhausted", {"sec": 3, "eodhd": 1}),
        ({"observations": 1}, "observation_budget_exhausted", {"sec": 1, "eodhd": 1}),
    ],
)
def test_budget_failure_is_atomic(capture_db, limits, reason, attempts):
    engine, cache, submissions = capture_db
    scope = _owned_run(engine, "f10-budget", ("AAPL.US", "ABBV.US"), **limits)
    result = _execute(engine, cache, scope, _FixtureTransport(submissions, rows=2))
    assert (result["status"], result["reason"]) == ("FAILED", reason)
    assert result["requests"] == attempts
    with Session(engine) as db:
        assert _count(db, CeriSourceRecord) == _count(db, CeriEstimateSnapshot) == 0


def test_unapproved_scopes_never_make_requests(capture_db):
    engine, cache, submissions = capture_db
    scope = _owned_run(engine, "f10-scope", ("AAPL.US",))
    transport = _FixtureTransport(submissions)
    for invalid in (
        replace(scope, symbols=("MSFT.US",)),
        replace(scope, symbols=("AAPL.US", "AAPL.US")),
        replace(scope, sec_attempts=13),
        replace(scope, eodhd_attempts=7),
        replace(scope, observations=25),
        replace(scope, cutoff_at=scope.starts_at + timedelta(minutes=31)),
    ):
        with pytest.raises(ValueError, match="Invalid prospective revenue capture approval"):
            _execute(engine, cache, invalid, transport)
    with pytest.raises(ValueError, match="outside the approved scope"):
        _execute(engine, cache, replace(scope, operator="different-operator"), transport)
    expired = replace(
        scope,
        starts_at=scope.starts_at - timedelta(minutes=6),
        cutoff_at=scope.cutoff_at - timedelta(minutes=6),
    )
    with pytest.raises(RuntimeError, match="capture_window_exhausted"):
        _execute(engine, cache, expired, transport)
    assert transport.calls == []
