from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriIngestionRun,
    CeriSecFilingDocument,
    CeriSecScopeEvidence,
    CeriSourceRecord,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.config import load_ceri_config
from app.services.ceri.dtos import GuidanceRequest
from app.services.ceri.enums import CeriDataset
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.preparation_visibility import PREPARATION_REQUEST_PREFIX, PREPARATION_SOURCE
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.sec.content_cache import SecDocumentContentCache
from app.services.ceri.sec.fresh_custody import (
    FRESH_JOURNAL,
    archive_url,
    bundle_reference,
    digest,
    make_proof,
)
from app.services.ceri.sec.preparation_evidence import (
    EXCLUDED_ATTRIBUTION,
    PreparationAuthorization,
    disposable_preparation_authorization,
)
from app.services.ceri.sec.processor_lifecycle import (
    certify_processor,
    promote_processor,
    register_deployed_processor,
)
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import (
    SecCeriProvider,
    SecGuidanceDocument,
    SecGuidanceSelection,
)
from app.services.ceri.sec.scope_evidence import (
    body_version,
    exact_filing,
    generation_ready,
    payload,
)
from app.services.ceri.sec.state_service import SecDocumentIdentity, SecDocumentStateService
from app.services.runtime_mutation_authority import RuntimeMutationAuthority
from app.settings import SecDocumentIncrementalMode, SecReadinessPolicy, Settings

CIK = "0001090872"
SIGNATURE = sec_guidance_processor_signature()
POSITIVE = """<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:dei="http://xbrl.sec.gov/dei/2026">
 <p>Securities and Exchange Commission FORM 8-K</p>
 <xbrli:context id="P"><xbrli:entity>
 <xbrli:identifier scheme="http://www.sec.gov/CIK">0001090872</xbrli:identifier>
 </xbrli:entity></xbrli:context>
 <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="P">0001090872</ix:nonNumeric>
 <p>We expect full year revenue guidance of $100 to $110 million.</p>
 </html>"""
EXCLUSION = """<html><p>Securities and Exchange Commission FORM 8-K</p>
<p>No attributable issuer proof.</p></html>"""


class OfflineSelectedFilings(SecCeriProvider):
    def __init__(self, selection):
        super().__init__(client=object())
        self.selection = selection

    def select_guidance_documents(self, request: GuidanceRequest, *, cik):
        assert cik == CIK
        return self.selection

    def download_guidance_document(self, document):
        raise AssertionError("Fresh archive verification must not call a provider")


def _bundle(root, filing, content, now, submissions_digest):
    received = now - timedelta(days=1)
    approval = {
        "filings": [filing], "max_get_attempts": 1, "archive_root": str(root.resolve()),
        "starts_at": (received - timedelta(seconds=1)).isoformat(),
        "cutoff_at": (received + timedelta(minutes=2)).isoformat(),
    }
    approval_bytes = json.dumps(approval).encode()
    approval_sha = digest(approval_bytes)
    header = {
        "version": FRESH_JOURNAL, "approval_sha256": approval_sha, "verified": 1,
        "attempted": 1, "captured_at": (received + timedelta(seconds=2)).isoformat(),
    }
    body = content.encode()
    receipt = {
        "filing": filing, "submissions_sha256": submissions_digest,
        "archive_url": archive_url(filing), "http_status": 200, "redirect_count": 0,
        "wire_sha256": digest(body), "wire_bytes": len(body),
        "received_at": received.isoformat(),
        "possessed_at": (received + timedelta(seconds=1)).isoformat(),
    }
    line = json.dumps(receipt).encode()
    journal = json.dumps(header).encode() + b"\n" + line + b"\n"
    journal_sha = digest(journal)
    archive = root / bundle_reference(approval_sha, journal_sha)
    (archive / "bodies").mkdir(parents=True)
    (archive / "approval.json").write_bytes(approval_bytes)
    (archive / "journal.jsonl").write_bytes(journal)
    proof = make_proof(receipt, digest(line), approval_sha, journal_sha)
    path = root / proof["body_reference"]
    path.write_bytes(body)
    return path, proof


def _seed(db, document):
    db.add(CeriCompany(ticker="A", exchange="US", cik=CIK))
    register_deployed_processor(db, authority=RuntimeMutationAuthority.normal("fresh-custody-test"))
    state = SecDocumentStateService()
    registered = state.register_document(db, SecDocumentIdentity(
        CIK, document.accession_number, document.document_name, "A", document.form,
        document.accepted_at.date(),
    ))
    db.commit()
    return registered.id


def _selection(document, *, prepared, now):
    time_key = ("submissions_possessed_at" if prepared else "observed_at")
    sha_key = ("submissions_wire_sha256" if prepared else "submissions_sha256")
    discovery = {
        "source": f"SEC/submissions/CIK{CIK}.json",
        time_key: document.accepted_at if prepared else now,
        sha_key: "d" * 64 if prepared else "e" * 64,
    }
    if prepared:
        discovery["frozen_selection_cutoff_at"] = document.accepted_at
    return SecGuidanceSelection((document,), discovery)


def _ingest(db, tmp_path, document, *, key, now, preparation=None, degraded=False):
    config = load_ceri_config()
    service = CeriIngestionService(
        config=config,
        registry=CeriProviderRegistry(
            providers={"sec": OfflineSelectedFilings(
                _selection(document, prepared=preparation is not None, now=now),
            )},
            config=config,
        ),
        settings=Settings(
            _env_file=None, cache_dir=tmp_path / "cache",
            sec_readiness_policy=(
                SecReadinessPolicy.ALLOW_DEGRADED if degraded else SecReadinessPolicy.REQUIRE_READY
            ),
            sec_document_incremental_mode=(
                SecDocumentIncrementalMode.SHADOW if preparation is not None
                else SecDocumentIncrementalMode.ACTIVE
            ),
        ),
    )
    result = service.ingest(db, CeriIngestionRequest(
        provider="sec", dataset=CeriDataset.GUIDANCE, ticker="A", request_key=key,
        end=now.date(),
    ), preparation=preparation)
    db.commit()
    return result


def _run_errors(db, run_id):
    table = CeriIngestionRun.__table__
    return db.scalar(select(table.c.errors_json).where(table.c.id == run_id))


def _events(db, run_id):
    return [
        payload(row) for row in db.scalars(select(CeriSecScopeEvidence).where(
            CeriSecScopeEvidence.ingestion_run_id == run_id,
            CeriSecScopeEvidence.ordinal > 1,
        ).order_by(CeriSecScopeEvidence.ordinal))
    ]


@pytest.fixture
def fresh_engine(disposable_postgres_database):
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "0097_sec_scope_evidence"],
        env={**os.environ, "DATABASE_URL": disposable_postgres_database},
        capture_output=True, text=True, check=True,
    )
    engine = create_engine(disposable_postgres_database)
    try:
        yield engine
    finally:
        engine.dispose()


def _approval(db, now, tmp_path, proof, filing, *, original=None):
    # This test fixture exercises the explicitly disposable authority, with no live capture.
    exact_key = (filing["cik"], filing["accession"], filing["filename"])
    custody = {} if original is None else {
        exact_key: (original["body_sha256"], original["possessed_at"]),
    }
    return disposable_preparation_authorization(
        db, "a" * 64, now - timedelta(minutes=1), now + timedelta(minutes=10),
        {exact_key: None}, custody, "b" * 64, archive_root=str(tmp_path.resolve()),
        fresh_custody={exact_key: proof},
    )


def _document(now):
    accepted = now - timedelta(days=30)
    return SecGuidanceDocument(
        "A", CIK, "0001090872-26-000064", "fresh-immutable.htm", "8-K",
        accepted.date().isoformat(), accepted,
    )


def test_fresh_custody_does_not_relax_known_historic_registry_binding():
    now = datetime.now(UTC)
    filing = exact_filing(_document(now), CIK)
    key = (filing["cik"], filing["accession"], filing["filename"])
    possessed_at = Canonical.canonicalize(now - timedelta(days=2))
    original = {"body_sha256": "c" * 64, "possessed_at": possessed_at}
    bound = PreparationAuthorization(
        "a" * 64, now, now + timedelta(minutes=1), {}, {key: ("c" * 64, possessed_at)},
    )
    missing = PreparationAuthorization(
        "a" * 64, now, now + timedelta(minutes=1), {}, {},
    )
    assert bound.matches_body(filing, original, has_fresh_custody=True)
    assert not bound.matches_body(
        filing, {**original, "body_sha256": "d" * 64}, has_fresh_custody=True,
    )
    assert missing.matches_body(filing, original, has_fresh_custody=True)
    assert not missing.matches_body(filing, original)


def test_native_fresh_exclusion_reexamines_archive_in_active_generations(
    fresh_engine, tmp_path: Path, monkeypatch,
):
    now = datetime.now(UTC)
    document = _document(now)
    filing = exact_filing(document, CIK)
    archive, proof = _bundle(tmp_path, filing, EXCLUSION, now, "d" * 64)
    with Session(fresh_engine) as db:
        document_id = _seed(db, document)
        cache = SecDocumentContentCache(tmp_path / "cache")
        cache.store(document, POSITIVE)
        state = SecDocumentStateService()
        state.record_downloaded_content(
            db, document_id=document_id, content_hash=digest(POSITIVE.encode()),
            content_bytes=len(POSITIVE.encode()),
            possessed_at=now - timedelta(days=20),
        )
        db.commit()
        stored = db.get(CeriSecFilingDocument, document_id)
        initial = (body_version(stored), stored.last_seen_at)
        authority = _approval(db, now, tmp_path, proof, filing, original=initial[0])
        result = _ingest(
            db, tmp_path, document, key=f"{PREPARATION_REQUEST_PREFIX}{authority.scope_sha256}:A",
            now=now, preparation=authority,
        )
        assert result.run_evidence_status == "READY", (
            _run_errors(db, result.ingestion_run_id), _events(db, result.ingestion_run_id),
        )
        receipt, = _events(db, result.ingestion_run_id)
        assert receipt["status"] == EXCLUDED_ATTRIBUTION and receipt["sources"] == []
        assert receipt["fresh_custody"] == proof
        assert receipt["fresh_registry_baseline"] == initial[0]
        assert receipt["body"]["body_sha256"] != initial[0]["body_sha256"]
        assert receipt["body"]["possessed_at"] == Canonical.canonicalize(
            datetime.fromisoformat(proof["possessed_at"]),
        )
        assert generation_ready(db, CIK, SIGNATURE)
        certify_processor(
            db, processor_signature=SIGNATURE, evidence={"disposable_fresh_scope_control": True},
            actor="fresh-scope-test",
        )
        promote_processor(db, processor_signature=SIGNATURE, actor="fresh-scope-test")
        db.commit()
        for key in ("fresh-native-active-1", "fresh-native-active-2"):
            active = _ingest(db, tmp_path, document, key=key, now=now)
            assert active.run_evidence_status == "READY"
            record, = _events(db, active.ingestion_run_id)
            assert record["exclusion_origin"]
            assert record["fresh_custody"] == proof and record["sources"] == []
        retained = db.get(CeriSecFilingDocument, document_id, populate_existing=True)
        assert (body_version(retained), retained.last_seen_at) == initial
        assert cache.load(document, expected_hash=initial[0]["body_sha256"]) == POSITIVE
        assert db.scalars(select(CeriSourceRecord.__table__.c.id)).all() == []
        newer = state.claim(
            db, document_id=document_id, dataset="guidance", processor_signature=SIGNATURE,
            worker_id="new-attempt-control", lease_seconds=60, reprocess_completed=True,
        )
        assert newer.acquired
        assert not generation_ready(db, CIK, SIGNATURE)
        db.rollback()
        assert generation_ready(db, CIK, SIGNATURE)
        archive.write_text("changed", encoding="utf-8")
        assert not generation_ready(db, CIK, SIGNATURE)
        archive.write_bytes(EXCLUSION.encode())
        assert generation_ready(db, CIK, SIGNATURE)
        state.record_downloaded_content(
            db, document_id=document_id, content_hash=proof["wire_sha256"],
            content_bytes=len(EXCLUSION.encode()), possessed_at=now,
        )
        db.commit()
        assert not generation_ready(db, CIK, SIGNATURE)
        state.record_downloaded_content(
            db, document_id=document_id, content_hash=initial[0]["body_sha256"],
            content_bytes=len(POSITIVE.encode()),
            possessed_at=datetime.fromisoformat(initial[0]["possessed_at"]),
        )
        db.commit()
        assert generation_ready(db, CIK, SIGNATURE)
        with monkeypatch.context() as patch:
            patch.setattr(
                "app.services.ceri.sec.incremental_ingestion.examine_attribution",
                lambda *_: None,
            )
            failed = _ingest(db, tmp_path, document, key="fresh-fenced-failure", now=now)
        attempted, = _events(db, failed.ingestion_run_id)
        assert attempted["status"] == "FAILED_RETRYABLE"
        assert attempted["reason"] == "SEC_ACTIVE_FRESH_CUSTODY_REFUSAL_CHANGED"
        next_generation = _ingest(
            db, tmp_path, document, key="fresh-fenced-no-fallback", now=now, degraded=True,
        )
        assert next_generation.run_evidence_status != "READY"
        next_run = db.get(CeriIngestionRun, next_generation.ingestion_run_id)
        assert next_run.errors_json["documents"][0]["error"] == (
            "SEC_ACTIVE_FRESH_CUSTODY_PREVIOUS_EXAMINATION_BLOCKED"
        )
        assert db.scalars(select(CeriSourceRecord.__table__.c.id)).all() == []
        assert cache.load(document, expected_hash=initial[0]["body_sha256"]) == POSITIVE


def test_native_fresh_positive_sources_are_preparation_only_and_bind_frozen_selection(
    fresh_engine, tmp_path: Path,
):
    now = datetime.now(UTC)
    document = _document(now)
    filing = exact_filing(document, CIK)
    _, invalid_proof = _bundle(tmp_path, filing, POSITIVE, now, "c" * 64)
    _, proof = _bundle(tmp_path, filing, POSITIVE, now, "d" * 64)
    with Session(fresh_engine) as db:
        document_id = _seed(db, document)
        initial = body_version(db.get(CeriSecFilingDocument, document_id))
        authority = _approval(db, now, tmp_path, invalid_proof, filing)
        denied = _ingest(
            db, tmp_path, document, key=f"{PREPARATION_REQUEST_PREFIX}{authority.scope_sha256}:A",
            now=now, preparation=authority,
        )
        assert denied.failed == 1
        failed, = _events(db, denied.ingestion_run_id)
        assert failed["reason"] == "SEC_PREPARATION_FRESH_CUSTODY_INVALID"
        assert not generation_ready(db, CIK, SIGNATURE)
        authority = _approval(db, now, tmp_path, proof, filing)
        key = f"{PREPARATION_REQUEST_PREFIX}{authority.scope_sha256}:A:next"
        result = _ingest(db, tmp_path, document, key=key, now=now, preparation=authority)
        assert result.run_evidence_status == "READY", (
            _run_errors(db, result.ingestion_run_id), _events(db, result.ingestion_run_id),
        )
        valid, = _events(db, result.ingestion_run_id)
        assert valid["status"] == "COMPLETED_WITH_RECORDS" and valid["sources"]
        assert valid["fresh_custody"] == proof and valid["fresh_registry_baseline"] == initial
        assert set(db.scalars(select(CeriSourceRecord.__table__.c.dataset))) == {PREPARATION_SOURCE}
        assert db.scalars(select(CeriSourceRecord)).all() == []
        assert body_version(db.get(CeriSecFilingDocument, document_id, populate_existing=True)) == (
            initial
        )
        assert not list((tmp_path / "cache").rglob("*.gz"))
