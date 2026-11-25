"""Explicit owner-authorized, offline SEC cache preparation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriIngestionRun,
    CeriSecProcessorRelease,
    CeriSecScopeEvidence,
    CeriSourceRecord,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.config import load_ceri_config
from app.services.ceri.enums import CeriDataset
from app.services.ceri.orchestration import CeriIngestionRequest, CeriIngestionService
from app.services.ceri.preparation_visibility import PREPARATION_REQUEST_PREFIX, PREPARATION_SOURCE
from app.services.ceri.provider_registry import CeriProviderRegistry
from app.services.ceri.sec.preparation_evidence import (
    REVIEWED_QUARANTINED_POSSESSION,
    REVIEWED_QUARANTINED_PRIMARY,
    REVIEWED_QUARANTINED_SHA256,
    _after_verified_approval,
)
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from app.services.ceri.sec.provider import (
    SecCeriProvider,
    SecGuidanceDocument,
    SecGuidanceSelection,
)
from app.services.ceri.sec.scope_evidence import exact_filing
from app.services.entrypoint_authority import require_disposable_tool_target
from app.settings import RuntimeMode, SecDocumentIncrementalMode, get_settings
from scripts.ops.ceri_prospective_revenue import (
    OWNER,
    _database,
    _principal,
    _trust,
    _unique_fields,
)
from scripts.ops.sec_processor_rollback import (
    _assert_windows_trust_acl,
    _checkout_identity,
    _hold_launcher_lock,
)
from scripts.ops.validate_sec_cofiler import (
    check_frozen_selection,
    reviewed_filing,
    verify_response,
)

ROOT = Path(__file__).resolve().parents[2]
NAMESPACE = "swinglens-sec-cache-preparation"
SCHEMA = "0097_sec_scope_evidence"
SHA = r"^[0-9a-f]{64}$"
COMMIT = r"^[0-9a-f]{40}$"
INCUMBENT = "sec-guidance:eed017654682a0c9"
FROZEN_COHORT_SHA256 = "be92e76753bb2b707f2014de3657f2257f5b0ecbef479c3247f442f7243d0eba"
FROZEN_SELECTION_SHA256 = "faeb8f8da74f3a87f13c1f8bb622517323809f5fb4854623a6467b40cc91d671"
REVIEWED_WIRE_JOURNALS = {
    "SEC_V4_FILINGS_ACQUISITION/body-events.jsonl":
        "c1e1fe6306292a274920f0ea038171c21268c3fc173e51198a12fe9c1f95c0db",
    "SEC_V4_FILINGS_RECOVERY/body-events.jsonl":
        "dfc5bb21a5a825a59a82aff7dd6cf2d387ef1161dc39a848602d091668c8c07c",
    "SEC_V4_FILINGS_FINAL/body-events.jsonl":
        "a62a1412c977435c23bc0db0ae09fd5e42e6f8ed4535b168ec77a9192cc40bd4",
    "SEC_V4_FILINGS_REMAINING_145/body-events.jsonl":
        "7a4a141d0fb1bef72876544a7044b0c7de77e465d7d6ce41523eedacbad213a8",
}


class CachePreparationApproval(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: Literal["sec-cache-preparation-v2"]
    preparation_id: str = Field(pattern=SHA)
    runtime_git: str = Field(pattern=COMMIT)
    recovery_git: str = Field(pattern=COMMIT)
    recovery_root: str
    recovery_config_sha256: str = Field(pattern=SHA)
    processor_signature: str
    database_fingerprint: str = Field(pattern=SHA)
    database_role: str
    runtime_config_sha256: str = Field(pattern=SHA)
    operator: str
    manifest_sha256: str = Field(pattern=SHA)
    wire_proofs_sha256: str = Field(pattern=SHA)
    archive_root: str
    provider_requests: Literal[0]
    starts_at: AwareDatetime
    cutoff_at: AwareDatetime


def _read_json(path, limit, expected=None):
    with path.open("rb") as file:
        content = file.read(limit + 1)
    fingerprint = hashlib.sha256(content).hexdigest()
    if len(content) > limit or (expected is not None and fingerprint != expected):
        raise ValueError("SEC_PREPARATION_INPUT_DIGEST_INVALID")
    return json.loads(content, object_pairs_hook=_unique_fields), content, fingerprint


def _verify_approval(path, signature):
    _, signed, digest = _read_json(path, 16384)
    executable, signers = _trust()
    _assert_windows_trust_acl(executable, signers)
    result = subprocess.run(
        [str(executable), "-Y", "verify", "-f", str(signers), "-I", OWNER,
         "-n", NAMESPACE, "-s", str(signature)],
        input=signed, capture_output=True, timeout=5, check=False,
    )
    if result.returncode:
        raise ValueError("SEC_PREPARATION_OWNER_SIGNATURE_INVALID")
    approval = CachePreparationApproval.model_validate_json(signed)
    if not timedelta(0) < approval.cutoff_at - approval.starts_at <= timedelta(minutes=30):
        raise ValueError("SEC_PREPARATION_AUTHORIZATION_WINDOW_INVALID")
    return approval, digest


def _read_archive(root, relative, digest, limit=16_000_000):
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError("SEC_PREPARATION_ARCHIVE_PATH_INVALID")
    return _read_json(target, limit, digest)[0]


def _scope_from_manifest(manifest, archive_root):
    cutoff = datetime.fromisoformat(manifest["selection_cutoff_at_utc"])
    issuers = manifest["issuers"]
    hidden = {row["ticker"] for row in issuers if row["sec_applicability"] == "NOT_APPLICABLE"}
    required = [row for row in issuers if row["sec_applicability"] == "REQUIRED"]
    if (
        cutoff.tzinfo is None
        or len(issuers) != 516 or hidden != {"NBN", "TOWN"} or len(required) != 514
        or len({row["ticker"] for row in issuers}) != 516
        or len({row["cik"] for row in required}) != 514
        or sum(len(row["selected_documents"]) for row in required) != 2570
    ):
        raise ValueError("SEC_PREPARATION_REQUIRED_COHORT_MISMATCH")
    cohort = sorted((row["ticker"], row["cik"]) for row in required)
    if Canonical.fingerprint(cohort) != FROZEN_COHORT_SHA256:
        raise ValueError("SEC_PREPARATION_FROZEN_COHORT_CHANGED")
    selections, custody, filings = {}, {}, {}
    for row in required:
        cik = row["cik"]
        raw = _read_archive(
            archive_root, f"SEC_V4_DISCOVERY_EXECUTION/raw/CIK{cik}.body",
            row["sec_submissions_sha256"],
        )
        if str(raw["cik"]).zfill(10) != cik:
            raise ValueError("SEC_PREPARATION_SUBMISSIONS_CIK_MISMATCH")
        recent = raw["filings"]["recent"]
        documents, selected_keys = [], set()
        for selected in row["selected_documents"]:
            accession, primary = selected["accession_number"], selected["document_name"]
            key = (cik, accession, primary)
            if key in selected_keys or recent["accessionNumber"].count(accession) != 1:
                raise ValueError("SEC_PREPARATION_SELECTED_PRIMARY_AMBIGUOUS")
            index = recent["accessionNumber"].index(accession)
            acceptance = datetime.fromisoformat(recent["acceptanceDateTime"][index])
            if (
                selected["cik"] != cik or recent["primaryDocument"][index] != primary
                or recent["form"][index] != selected["form"]
                or recent["filingDate"][index] != selected["filing_date"]
                or acceptance != datetime.fromisoformat(selected["sec_filing_accepted_at_utc"])
                or acceptance > cutoff
                or not (cutoff.date() - timedelta(days=730) <= acceptance.date() <= cutoff.date())
            ):
                raise ValueError("SEC_PREPARATION_SELECTED_PRIMARY_MISMATCH")
            doc = SecGuidanceDocument(
                row["ticker"], cik, accession, primary, selected["form"],
                selected["filing_date"], acceptance,
            )
            filing = exact_filing(doc, cik)
            filings[key] = (filing, row["sec_submissions_sha256"])
            selected_keys.add(key)
            documents.append(doc)
            if selected["verified"]:
                custody[key] = (
                    selected["cache_sha256"],
                    Canonical.canonicalize(datetime.fromisoformat(selected["registry_possession_utc"])),
                )
        selections[row["ticker"]] = SecGuidanceSelection(tuple(documents), {
            "source": f"SEC/submissions/CIK{cik}.json",
            "submissions_wire_sha256": row["sec_submissions_sha256"],
            "submissions_possessed_at": datetime.fromisoformat(
                row["sec_submissions_possession_utc"],
            ),
            "frozen_selection_cutoff_at": cutoff,
        })
    selection_digest = Canonical.fingerprint([filings[key][0] for key in sorted(filings)])
    if selection_digest != FROZEN_SELECTION_SHA256:
        raise ValueError("SEC_PREPARATION_FROZEN_SELECTION_CHANGED")
    return cutoff, selections, custody, filings


def _wire_custody(raw, filings, archive_root):
    proofs = dict.fromkeys(filings)
    journals = {}
    for entry in raw:
        key = (entry["cik"], entry["accession"], entry["primary"])
        if key not in proofs or proofs[key] is not None:
            raise ValueError("SEC_PREPARATION_WIRE_PROOF_SCOPE_INVALID")
        name = entry["journal"]
        journal = (archive_root / name).resolve()
        digest = REVIEWED_WIRE_JOURNALS.get(name)
        if not journal.is_relative_to(archive_root) or digest is None:
            raise ValueError("SEC_PREPARATION_WIRE_PROOF_PATH_INVALID")
        if name not in journals:
            data = journal.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError("SEC_PREPARATION_WIRE_JOURNAL_CHANGED")
            journals[name] = data.splitlines()
        lines = journals[name]
        line = entry["line"]
        if not isinstance(line, int) or not 1 <= line <= len(lines):
            raise ValueError("SEC_PREPARATION_WIRE_LINE_INVALID")
        event = json.loads(lines[line - 1], object_pairs_hook=_unique_fields)
        if (
            event["event"] != "RESULT" or event.get("disposition") != "DOWNLOADED_AND_VERIFIED"
            or event.get("http_status") != 200
            or (event["cik"], event["accession_number"], event["document_name"]) != key
            or "HTTPS_SEC_EXACT_ARCHIVE_IDENTITY" not in event["body_identity_proof"]
            or "AUTHENTICATED_SUBMISSIONS_PRIMARY_DOCUMENT" not in event["body_identity_proof"]
            or not {"SEC_COMMISSION_AND_FORM_COVER", "INLINE_DEI_DOCUMENT_TYPE"}.intersection(
                event["body_identity_proof"]
            )
        ):
            raise ValueError("SEC_PREPARATION_ARCHIVE_RECEIPT_INVALID")
        filing, submissions_sha256 = filings[key]
        proofs[key] = {
            "filing": filing, "submissions_sha256": submissions_sha256,
            "archive_url": event["archive_url"], "redirect_count": 0, "http_status": 200,
            "wire_sha256": event["wire_body_sha256"], "wire_bytes": event["wire_body_bytes"],
            "received_at": event["received_at_utc"],
            "archive_receipt_sha256": hashlib.sha256(lines[line - 1]).hexdigest(),
        }
    return proofs


class PreparedCacheProvider(SecCeriProvider):
    def __init__(self, selections):
        super().__init__(client=object())
        self.selections = selections

    def select_guidance_documents(self, request, *, cik):
        selection = self.selections[request.ticker]
        if any(document.cik != cik for document in selection.documents):
            raise ValueError("SEC_PREPARATION_SELECTION_CIK_MISMATCH")
        return selection

    def resolve_cik_candidates(self, ticker):
        raise ValueError("SEC_PREPARATION_IDENTITY_LOOKUP_FORBIDDEN")

    def download_guidance_document(self, document):
        raise ValueError("SEC_PREPARATION_NETWORK_FORBIDDEN")


def _quarantined_custody(archive_root, filings):
    key = REVIEWED_QUARANTINED_PRIMARY
    cik, accession, primary = key
    target = (
        archive_root / "SEC_V4_FILINGS_FINAL/quarantine" / f"{cik}-{accession}.body"
    ).resolve()
    retained_manifest = (
        archive_root / "SEC_V4_DISCOVERY_EXECUTION/authoritative-discovery-manifest.json"
    ).resolve()
    if (
        key not in filings or not target.is_relative_to(archive_root)
        or not target.is_file() or not retained_manifest.is_relative_to(archive_root)
    ):
        return None
    filing = reviewed_filing(cik, accession, primary, "8-K")
    check_frozen_selection(retained_manifest.read_bytes(), filing)
    content = target.read_bytes()
    custody = verify_response(
        filing, content, archive_url=filing.archive_url, wire_sha256=REVIEWED_QUARANTINED_SHA256,
    )
    return content.decode("utf-8"), {
        **custody, "source": "verified_quarantine", "filing": filings[key][0],
        "possessed_at": REVIEWED_QUARANTINED_POSSESSION,
    }


def _inspect(db, settings):
    fingerprint, role, head = _database(db)
    return {
        "runtime_git": _checkout_identity(ROOT),
        "runtime_config_sha256": Canonical.fingerprint(settings.model_dump(mode="json")),
        "database_fingerprint": fingerprint, "database_role": role, "schema": head,
        "operator": _principal(), "processor_signature": sec_guidance_processor_signature(),
    }


def _scope_digest(approval):
    return Canonical.fingerprint(approval.model_dump(
        exclude={"starts_at", "cutoff_at"}, mode="json",
    ))


def _inspect_preparation(db, identity, approval, ticker):
    if (
        identity["schema"] != SCHEMA
        or any(
            getattr(approval, key) != value for key, value in identity.items() if key != "schema"
        )
        or not approval.starts_at <= datetime.now(UTC) < approval.cutoff_at
    ):
        raise ValueError("SEC_PREPARATION_INSPECTION_AUTHORITY_MISMATCH")
    scope = _scope_digest(approval)
    runs = CeriIngestionRun.__table__
    evidence = CeriSecScopeEvidence.__table__
    sources = CeriSourceRecord.__table__
    run = db.execute(select(runs.c.id, runs.c.status).where(
        runs.c.request_key == f"{PREPARATION_REQUEST_PREFIX}{scope}:{ticker}",
    )).mappings().one_or_none()
    if run is None:
        return {"scope_sha256": scope, "ticker": ticker, "run": None}
    events = db.execute(select(
        evidence.c.kind, evidence.c.generation, evidence.c.ordinal, evidence.c.evidence_sha256,
    ).where(
        evidence.c.ingestion_run_id == run["id"],
        evidence.c.processor_signature == approval.processor_signature,
    ).order_by(evidence.c.generation, evidence.c.ordinal)).mappings()
    records = db.execute(select(sources.c.id, sources.c.content_hash).where(
        sources.c.ingestion_run_id == run["id"], sources.c.dataset == PREPARATION_SOURCE,
    ).order_by(sources.c.id)).mappings()
    return {
        "scope_sha256": scope, "ticker": ticker, "run": dict(run),
        "receipts": [dict(row) for row in events], "projections": [dict(row) for row in records],
    }


def _compatible_runtime(db, approval):
    root = Path(approval.recovery_root).resolve(strict=True)
    if _checkout_identity(root) != approval.recovery_git:
        raise ValueError("SEC_PREPARATION_RECOVERY_GIT_MISMATCH")
    recovery = {
        "git": approval.recovery_git, "root": str(root), "config": approval.recovery_config_sha256,
    }
    if os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT") == "DISPOSABLE_TEST":
        require_disposable_tool_target(db.get_bind().url)
        return {**recovery, "native_verified": False}
    script = """
import json, sys
sys.path.insert(0, sys.argv[1])
from sqlalchemy.orm import Session
from app.db import engine
from app.services.ceri.sec.processor_signature import sec_guidance_processor_signature
from scripts.ops.ceri_prospective_revenue import _database
from scripts.ops.lifecycle_probe import _runtime_state_report
with Session(engine) as session:
    database, role, head = _database(session)
print(json.dumps({"runtime": _runtime_state_report(None), "database": database,
                  "role": role, "schema": head, "processor": sec_guidance_processor_signature()}))
"""
    system_names = {
        "SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "USERPROFILE", "PROGRAMDATA",
        "COMPUTERNAME", "USERNAME", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES",
    }
    child = subprocess.run(
        [str(root / ".venv/Scripts/python.exe"), "-I", "-c", script, str(root)],
        cwd=root, env={k: v for k, v in os.environ.items() if k.upper() in system_names},
        capture_output=True, timeout=20, check=True,
    )
    observed = json.loads(child.stdout)
    runtime = observed["runtime"]
    state = runtime.get("state") or {}
    if (
        observed["database"] != approval.database_fingerprint
        or observed["role"] != approval.database_role
        or observed["schema"] != SCHEMA or observed["processor"] != INCUMBENT
        or runtime.get("classification") != "ACTIVE_VALID"
        or state.get("gitCommit") != approval.recovery_git
        or state.get("runtimeConfigFingerprint") != approval.recovery_config_sha256
    ):
        raise ValueError("SEC_PREPARATION_COMPATIBLE_INCUMBENT_NOT_VERIFIED")
    return {**recovery, "native_verified": True}


def _verify_target(db, settings, approval, selections):
    result = _inspect(db, settings)
    if (
        result.pop("schema") != SCHEMA
        or any(getattr(approval, key) != value for key, value in result.items())
        or settings.runtime_mode is not RuntimeMode.NORMAL
        or Path.cwd().resolve() != ROOT
    ):
        raise ValueError("SEC_PREPARATION_RUNTIME_AUTHORITY_MISMATCH")
    active = db.scalar(select(CeriSecProcessorRelease).where(
        CeriSecProcessorRelease.status == "ACTIVE",
    ))
    candidate = db.get(CeriSecProcessorRelease, approval.processor_signature)
    if (
        active is None or active.processor_signature != INCUMBENT
        or candidate is None or candidate.status != "DEPLOYED"
        or candidate.deployed_git_sha != approval.runtime_git
    ):
        raise ValueError("SEC_PREPARATION_RELEASE_IDENTITY_INVALID")
    companies = db.execute(select(
        CeriCompany.ticker, CeriCompany.cik, CeriCompany.sec_applicability,
    ).where(CeriCompany.ticker.in_(set(selections) | {"NBN", "TOWN"}))).all()
    expected = {(ticker, selection.documents[0].cik, "REQUIRED")
                for ticker, selection in selections.items()}
    mapped = {(ticker, cik, status) for ticker, cik, status in companies if status == "REQUIRED"}
    if mapped != expected:
        raise ValueError("SEC_PREPARATION_DB_ISSUER_SCOPE_MISMATCH")
    if {ticker for ticker, _, status in companies if status == "NOT_APPLICABLE"} != {"NBN", "TOWN"}:
        raise ValueError("SEC_PREPARATION_DB_EXEMPTIONS_MISMATCH")
    return _compatible_runtime(db, approval)


def _prepare(session, settings, approval, approval_digest, manifest, proofs):
    archive_root = Path(approval.archive_root).resolve(strict=True)
    cutoff, selections, custody, filings = _scope_from_manifest(manifest, archive_root)
    wire = _wire_custody(proofs, filings, archive_root)
    recovery = _verify_target(session, settings, approval, selections)
    scope = _scope_digest(approval)
    authority = _after_verified_approval(
        scope, approval.starts_at, approval.cutoff_at, wire, custody, approval_digest,
        _quarantined_custody(archive_root, filings), recovery,
    )
    authority.check_window()
    config = load_ceri_config()
    provider = PreparedCacheProvider(selections)
    service = CeriIngestionService(
        config=config,
        registry=CeriProviderRegistry(providers={"sec": provider}, config=config),
        settings=settings.model_copy(update={
            "sec_document_incremental_mode": SecDocumentIncrementalMode.SHADOW,
        }),
    )
    for ticker in sorted(selections):
        authority.check_window()
        result = service.ingest(session, CeriIngestionRequest(
            provider="sec", dataset=CeriDataset.GUIDANCE, ticker=ticker,
            start=cutoff.date() - timedelta(days=730), end=cutoff.date(),
            request_key=f"{PREPARATION_REQUEST_PREFIX}{scope}:{ticker}",
            scope={"ticker": ticker},
        ), preparation=authority)
        session.commit()
        print(json.dumps({
            "ticker": ticker, "ingestion_run_id": result.ingestion_run_id,
            "status": result.status, "readiness": result.run_evidence_status,
        }), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("inspect", "prepare"))
    parser.add_argument("--approval", type=Path)
    parser.add_argument("--signature", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--wire-proofs", type=Path)
    parser.add_argument("--ticker")
    args = parser.parse_args()
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        with Session(engine) as db:
            if args.operation == "inspect":
                db.execute(text("SET TRANSACTION READ ONLY"))
                identity = _inspect(db, settings)
                if any((args.approval, args.signature, args.ticker)):
                    if not all((args.approval, args.signature, args.ticker)):
                        parser.error("signed inspection requires approval, signature and ticker")
                    approval, _ = _verify_approval(args.approval, args.signature)
                    identity = _inspect_preparation(db, identity, approval, args.ticker)
                print(json.dumps(identity))
                return
            if any(value is None for value in (
                args.approval, args.signature, args.manifest, args.wire_proofs,
            )):
                parser.error("prepare requires approval, signature, manifest and wire proofs")
            approval, receipt = _verify_approval(args.approval, args.signature)
            manifest, _, _ = _read_json(args.manifest, 8_000_000, approval.manifest_sha256)
            wire, _, _ = _read_json(args.wire_proofs, 1_000_000, approval.wire_proofs_sha256)
            if os.environ.get("SWINGLENS_DATABASE_SAFETY_CONTEXT") == "DISPOSABLE_TEST":
                require_disposable_tool_target(engine.url)
                _prepare(db, settings, approval, receipt, manifest, wire)
            else:
                recovery_root = Path(approval.recovery_root).resolve(strict=True)
                with _hold_launcher_lock(recovery_root):
                    _prepare(db, settings, approval, receipt, manifest, wire)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
