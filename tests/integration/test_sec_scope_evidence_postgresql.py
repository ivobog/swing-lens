from __future__ import annotations

import gzip
import hashlib
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriGuidanceEvent,
    CeriIngestionRun,
    CeriSecFilingDocument,
    CeriSecScopeEvidence,
    CeriSecSyncState,
    CeriSourceRecord,
)
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.sec.attribution import SecAttributionRefused
from app.services.ceri.sec.content_cache import SecDocumentContentCache
from app.services.ceri.sec.pipeline_preflight import validate_sec_pipeline_preflight
from app.services.ceri.sec.processor_lifecycle import certify_processor, promote_processor
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import SecCeriProvider, SecGuidanceDocument
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
from app.services.ceri.sec.scope_evidence import (
    ScopeEvidenceError,
    generation_ready,
    locked_events,
    open_generation,
    payload,
    selected_event,
)
from app.services.ceri.sec.state_service import SecDocumentIdentity, SecDocumentStateService
from app.services.ceri.source_record_service import CeriSourceRecordService
from app.services.pipeline_prerequisites import CeriBootstrapRequiredError
from app.services.scope_refresh_adoption import admit_frozen_operation
from app.services.work_scope_identity import AcquisitionRequirement, ScopeMember
from app.settings import SecDocumentIncrementalMode, Settings
from scripts.ops import validate_sec_cofiler as verifier

CIK = "0001534504"
V3 = "sec-guidance:eed017654682a0c9"
SIGNATURE = sec_guidance_processor_signature()
FILING_DATE = "2026-10-30"
CONTROL = "0001534504-26-999991"
JOINT = "0001534504-26-000043"
NOW = datetime(2026, 11, 23, 18, 10, 6, 844297, tzinfo=UTC)
CONTROL_BODY = """<html xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"
 xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:dei="http://xbrl.sec.gov/dei/2026">
 <xbrli:context id="P"><xbrli:entity>
 <xbrli:identifier scheme="http://www.sec.gov/CIK">0001534504</xbrli:identifier>
 </xbrli:entity></xbrli:context>
 <ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="P">0001534504</ix:nonNumeric>
 <p>We expect full year revenue guidance of $100 to $110 million.</p>
 </html>"""


class OfflineSEC:
    def __init__(self):
        from app.services.ceri.sec.client import SecClientConfig

        self.config = SecClientConfig()
        self.accession = CONTROL
        self.downloads = []

    def company_tickers(self):
        return {"0": {"ticker": "PBF", "cik_str": 1534504}}

    def submissions(self, cik):
        assert cik == CIK
        return {
            "cik": cik,
            "filings": {
                "recent": {
                    "form": ["8-K"],
                    "accessionNumber": [self.accession],
                    "primaryDocument": [_document(self.accession).document_name],
                    "filingDate": [FILING_DATE],
                }
            },
        }

    def archive_document(self, cik, accession, filename):
        assert (cik, accession, filename) == (CIK, CONTROL, _document(CONTROL).document_name)
        self.downloads.append(accession)
        return CONTROL_BODY


def _document(accession):
    filename = (
        verifier.APPROVED[accession].filename if accession in verifier.APPROVED else "control.htm"
    )
    return SecGuidanceDocument("PBF", CIK, accession, filename, "8-K", FILING_DATE)


def _alembic(url, *args, check=True):
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        env={**os.environ, "DATABASE_URL": url},
        capture_output=True,
        text=True,
        check=check,
    )


@pytest.fixture
def scope_engine(disposable_postgres_database):
    url = disposable_postgres_database
    _alembic(url, "upgrade", "0096_ceri_native_context")
    engine = create_engine(url)
    with Session(engine) as db:
        db.add(CeriCompany(ticker="PBF", exchange="US", cik=CIK))
        SecDocumentStateService().certify_bootstrap(
            db,
            cik=CIK,
            dataset="guidance",
            processor_signature=V3,
            document_count=1,
            latest_filing_date=datetime.fromisoformat(FILING_DATE).date(),
        )
        db.commit()
    _alembic(url, "upgrade", "head")
    try:
        yield engine, url
    finally:
        engine.dispose()


def _ingest(db, cache, client, key, mode):
    config = load_ceri_config()
    service = CeriIngestionService(
        config=config,
        registry=CeriProviderRegistry(
            providers={"sec": SecCeriProvider(client=client)}, config=config
        ),
        settings=Settings(
            _env_file=None,
            cache_dir=cache,
            sec_document_incremental_mode=mode,
            sec_document_retry_base_seconds=3600,
        ),
    )
    result = service.ingest(
        db,
        CeriIngestionRequest(
            provider="sec",
            dataset=CeriDataset.GUIDANCE,
            ticker="PBF",
            request_key=key,
            end=NOW.date(),
        ),
    )
    db.commit()
    return result


def _retain_joint(db, cache):
    body = gzip.decompress(
        (Path(__file__).parents[1] / "ceri/fixtures/pbf-000043-sec-primary.body.gz").read_bytes()
    )
    wire_hash = hashlib.sha256(body).hexdigest()
    filing = verifier.APPROVED[JOINT]
    receipt = verifier.verify_response(
        filing, body, archive_url=filing.archive_url, wire_sha256=wire_hash
    )
    assert receipt["financial_attribution"] == "BLOCKED"
    doc = _document(JOINT)
    SecDocumentContentCache(cache).store(doc, body.decode("utf-8"))
    state = SecDocumentStateService()
    registered = state.register_document(
        db,
        SecDocumentIdentity(
            CIK,
            JOINT,
            doc.document_name,
            "PBF",
            doc.form,
            NOW.date().replace(month=10, day=30),
        ),
    )
    state.record_downloaded_content(
        db,
        document_id=registered.id,
        content_hash=wire_hash,
        content_bytes=len(body),
        possessed_at=NOW,
    )
    db.commit()
    return wire_hash


def _ready(db, signature=SIGNATURE):
    return diagnose_sec_readiness(db, tickers=["PBF"], processor_signature=signature).complete


def _events(db, kind):
    return list(
        db.scalars(
            select(CeriSecScopeEvidence)
            .where(CeriSecScopeEvidence.kind == kind)
            .order_by(CeriSecScopeEvidence.generation, CeriSecScopeEvidence.ordinal)
        )
    )


def test_exact_scope_versioned_ingestion_and_historical_preservation(scope_engine, tmp_path):
    engine, url = scope_engine
    client = OfflineSEC()
    with Session(engine) as db:
        prior = db.scalar(select(CeriSecSyncState))
        retained = (prior.id, prior.bootstrap_completed_at, prior.document_count)
        assert _ready(db, V3)
        assert not _ready(db)
        wire_hash = _retain_joint(db, tmp_path)
        client.accession = JOINT
        cold = _ingest(db, tmp_path, client, "scope-cold", SecDocumentIncrementalMode.SHADOW)
        assert cold.status == "PARTIAL" and cold.run_evidence_status != "READY"
        assert not _ready(db)
        assert db.query(CeriSourceRecord).count() == db.query(CeriGuidanceEvent).count() == 0
        refusal = payload(_events(db, "ATTRIBUTION_REFUSAL")[0])
        assert refusal["filing"]["accession"] == JOINT
        assert refusal["body"]["body_sha256"] == wire_hash
        assert refusal["body"]["possessed_at"] == "2026-11-23T18:10:06.844297Z"
        replay = _ingest(
            db, tmp_path, client, "scope-cold-replay", SecDocumentIncrementalMode.SHADOW
        )
        assert replay.status == "PARTIAL" and replay.run_evidence_status != "READY"
        assert replay.documents_downloaded == 0 and not _ready(db)
        assert len(_events(db, "ATTRIBUTION_REFUSAL")) == 1

        client.accession = CONTROL
        shadow = _ingest(db, tmp_path, client, "scope-shadow", SecDocumentIncrementalMode.SHADOW)
        assert shadow.status == "COMPLETED" and shadow.run_evidence_status == "READY"
        assert shadow.documents_downloaded == 1 and _ready(db)
        assert db.query(CeriSourceRecord).count() == 1
        fact = db.scalar(select(CeriSourceRecord))
        company_id = db.scalar(select(CeriCompany.id).where(CeriCompany.cik == CIK))
        assert fact.source_reference.startswith(f"{CONTROL}/control.htm#paragraph-")
        assert CeriNormalizationService()._normalize_record(db, fact, company_id=company_id) == 1
        db.commit()
        reuse = _ingest(db, tmp_path, client, "scope-reuse", SecDocumentIncrementalMode.ACTIVE)
        assert reuse.status == "COMPLETED" and reuse.documents_skipped == 1
        assert len(client.downloads) == 1 and db.query(CeriSourceRecord).count() == 1
        assert _ready(db)

        registered = db.scalar(
            select(CeriSecFilingDocument).where(
                CeriSecFilingDocument.accession_number == CONTROL,
            )
        )
        changed = CONTROL_BODY.replace("$110", "$111")
        SecDocumentContentCache(tmp_path).store(_document(CONTROL), changed)
        SecDocumentStateService().record_downloaded_content(
            db,
            document_id=registered.id,
            content_hash=hashlib.sha256(changed.encode()).hexdigest(),
            content_bytes=len(changed.encode()),
        )
        db.commit()
        assert not _ready(db) and _ready(db, V3)
        repair = _ingest(db, tmp_path, client, "scope-new-body", SecDocumentIncrementalMode.SHADOW)
        assert repair.status == "COMPLETED" and repair.documents_skipped == 0
        assert repair.documents_cache_reused == 1 and _ready(db)
        approved_generation = payload(_events(db, "SELECTED")[-1])

        client.accession = JOINT
        warm = _ingest(db, tmp_path, client, "scope-warm", SecDocumentIncrementalMode.ACTIVE)
        assert warm.status == "PARTIAL" and warm.run_evidence_status != "READY"
        assert not _ready(db) and _ready(db, V3)
        refused_generation = payload(_events(db, "SELECTED")[-1])
        assert len(approved_generation["required"]) == len(refused_generation["required"]) == 1
        assert approved_generation["required"][0]["filing_date"] == FILING_DATE
        assert refused_generation["required"][0]["filing_date"] == FILING_DATE
        assert approved_generation["required"] != refused_generation["required"]
        assert db.query(CeriSourceRecord).count() == 2
        assert db.query(CeriGuidanceEvent).count() == 1
        assert db.query(CeriSecSyncState).count() == 1
        assert (prior.id, prior.bootstrap_completed_at, prior.document_count) == retained
        certify_processor(db, processor_signature=SIGNATURE, evidence={}, actor="disposable-test")
        promote_processor(db, processor_signature=SIGNATURE, actor="disposable-test")
        with pytest.raises(CeriBootstrapRequiredError):
            validate_sec_pipeline_preflight(db, tickers=["PBF"])
        db.rollback()

    result = _alembic(url, "downgrade", "0096_ceri_native_context", check=False)
    assert result.returncode != 0 and "SEC scope evidence must be retained" in result.stderr


def _owned_run(db, key, *, scope_kind="ceri-source-acquisition"):
    authority = admit_frozen_operation(
        db,
        operation_kind=scope_kind,
        subject_kind="ticker",
        members=(ScopeMember("TICKER", "PBF"),),
        cycle_key=key,
        business_cutoff=NOW.date(),
        provider_source_class="sec",
        request_type="guidance",
        requirements=(AcquisitionRequirement("guidance"),),
        policy_identity="sec-test",
        scope_definition={"provider": "sec", "dataset": "guidance"},
    )
    return (
        CeriSourceRecordService()
        .create_ingestion_run(
            db,
            provider="sec",
            provider_terms_version=None,
            dataset="guidance",
            scope={"ticker": "PBF"},
            request_key=key,
            config_version=None,
            config_hash=None,
            semantic_authority=authority,
        )
        .id
    )


def test_generation_lock_interruption_replay_and_append_only(scope_engine):
    engine, _ = scope_engine
    with Session(engine) as db:
        old = _owned_run(db, "older")
        new = _owned_run(db, "newer")
        wrong = _owned_run(db, "wrong", scope_kind="unrelated-repair")
        db.commit()
        with pytest.raises(ScopeEvidenceError, match="AUTHORITY_REQUIRED"):
            open_generation(db, wrong, "PBF", CIK, SIGNATURE)
        db.rollback()
        interrupted = open_generation(db, old, "PBF", CIK, SIGNATURE)
        db.rollback()
        assert not _events(db, "OPEN")
        first = open_generation(db, old, "PBF", CIK, SIGNATURE)
        assert first == interrupted
        opened = Event()

        def compete():
            with Session(engine) as other:
                opened.set()
                second = open_generation(other, new, "PBF", CIK, SIGNATURE)
                other.commit()
                return second

        with ThreadPoolExecutor(max_workers=1) as pool:
            result = pool.submit(compete)
            assert opened.wait(5)
            db.commit()
            second = result.result(timeout=10)
        assert second.number == first.number + 1
        with pytest.raises(ScopeEvidenceError, match="SUPERSEDED"):
            locked_events(db, first)
        db.rollback()
        assert open_generation(db, new, "PBF", CIK, SIGNATURE) == second
        assert selected_event(db, second) is None
        assert not generation_ready(db, CIK, SIGNATURE)
        db.commit()
        assert len(_events(db, "OPEN")) == 2
        assert db.get(CeriIngestionRun, old).scope_id == first.parent_id
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text("UPDATE ceri_sec_scope_evidence SET evidence = evidence"))
        db.rollback()
        with pytest.raises(DBAPIError, match="immutable"):
            db.execute(text("TRUNCATE ceri_sec_scope_evidence"))
        db.rollback()
        assert len(_events(db, "OPEN")) == 2


@pytest.mark.parametrize(
    "text_body",
    (
        CONTROL_BODY.replace(">0001534504<", ">0001566011<"),
        CONTROL_BODY.replace('contextRef="P"', 'contextRef="missing"'),
        CONTROL_BODY.replace("</html>", ""),
        CONTROL_BODY.replace(
            "</html>",
            '<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="P">'
            "0001566011</ix:nonNumeric></html>",
        ),
    ),
)
def test_provider_refuses_unsupported_inline_issuer(text_body):
    with pytest.raises(SecAttributionRefused):
        SecCeriProvider(client=OfflineSEC()).extract_guidance_document(
            _document(CONTROL), text=text_body
        )


def test_populated_migration_preserves_held_legacy_source(disposable_postgres_database):
    url = disposable_postgres_database
    _alembic(url, "upgrade", "0096_ceri_native_context")
    engine = create_engine(url)
    try:
        with Session(engine) as db:
            company = CeriCompany(ticker="PBF", exchange="US", cik=CIK)
            held = CeriSourceRecord(
                provider="sec",
                dataset="guidance",
                provider_record_id=f"{JOINT}:held",
                company_hint_json={"ticker": "PBF", "cik": CIK},
                source_reference=f"{JOINT}/pbf-20261027.htm#paragraph-1",
                content_hash="held-fixture-hash",
                idempotency_key="held-fixture-key",
                retrieved_at=NOW,
            )
            db.add_all((company, held))
            db.flush()
            company_id, source_id = company.id, held.id
            db.commit()
        _alembic(url, "upgrade", "head")
        with Session(engine) as db:
            held = db.get(CeriSourceRecord, source_id)
            assert held.content_hash == "held-fixture-hash" and held.retrieved_at == NOW
            with pytest.raises(ValueError, match="ATTRIBUTION_EVIDENCE_REQUIRED"):
                CeriNormalizationService()._normalize_record(db, held, company_id=company_id)
            db.rollback()
            assert db.query(CeriGuidanceEvent).count() == 0
            assert db.get(CeriSourceRecord, source_id).content_hash == "held-fixture-hash"
        _alembic(url, "downgrade", "0096_ceri_native_context")
        _alembic(url, "upgrade", "head")
        with Session(engine) as db:
            assert db.query(CeriSecScopeEvidence).count() == 0
            assert db.get(CeriSourceRecord, source_id).retrieved_at == NOW
    finally:
        engine.dispose()


def test_untagged_financial_claim_cannot_inherit_selected_archive_issuer():
    body = "<p>PBF Holding expects annual revenue of $100 to $110 million.</p>"
    with pytest.raises(SecAttributionRefused, match="SEC_FINANCIAL_ISSUER_UNPROVED"):
        SecCeriProvider(client=OfflineSEC()).extract_guidance_document(
            _document(CONTROL),
            text=body,
            require_financial_attribution=True,
        )
