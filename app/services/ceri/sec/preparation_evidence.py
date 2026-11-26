"""Evidence required to close a verified SEC preparation refusal."""

from __future__ import annotations

import hashlib
import io
import re
import xml.etree.ElementTree as ET
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from types import MappingProxyType

from sqlalchemy import or_, select

from app.models.ceri_tables import CeriGuidanceEvent, CeriSourceRecord
from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.ceri.sec.attribution import (
    CIK_SCHEMES,
    HEADER_CIK,
    INLINE,
    INLINE_MARKERS,
    XBRLI,
    SecAttributionRefused,
    prove_document_attribution,
)
from app.services.ceri.sec.fresh_custody import load_fresh_body
from app.services.entrypoint_authority import require_disposable_tool_target

EXCLUDED_ATTRIBUTION = "COMPLETED_EXCLUDED_ATTRIBUTION"
REVIEWED_QUARANTINED_PRIMARY = ("0001534504", "0001534504-26-000043", "pbf-20261027.htm")
REVIEWED_QUARANTINED_SHA256 = "e75a70173a024f5f691f2edd61736a2a0f77e3ec796fde34ad03ec3312772da8"
REVIEWED_QUARANTINED_POSSESSION = "2026-11-23T18:10:06.844297Z"
EXCLUDABLE_REFUSALS = frozenset({
    "SEC_INLINE_LEGAL_ENTITY_DIMENSION",
    "SEC_INLINE_REGISTRANT_UNPROVED",
    "SEC_FINANCIAL_ISSUER_UNPROVED",
})


@dataclass(frozen=True)
class PreparationAuthorization:
    """Validated operator input. Filing proofs are indexed by the exact primary key."""

    scope_sha256: str
    starts_at: datetime
    cutoff_at: datetime
    issuer_proofs: dict
    expected_custody: dict | None = None
    signed_approval_sha256: str | None = None
    quarantined: tuple[str, dict] | None = None
    recovery: dict | None = None
    archive_root: str | None = None
    fresh_custody: dict | None = None
    _verified: bool = field(default=False, init=False, repr=False)

    def check_window(self) -> None:
        if not self._verified:
            raise ValueError("SEC_PREPARATION_PROTECTED_APPROVAL_REQUIRED")
        if not self.starts_at <= datetime.now(UTC) < self.cutoff_at:
            raise PreparationExpired("SEC_PREPARATION_AUTHORIZATION_EXPIRED")

    def has_filing(self, filing):
        return (filing["cik"], filing["accession"], filing["filename"]) in self.issuer_proofs

    def matches_body(self, filing, body, *, has_fresh_custody: bool = False):
        if self.expected_custody is None:
            return True
        expected = self.expected_custody.get(
            (filing["cik"], filing["accession"], filing["filename"])
        )
        if expected is None and has_fresh_custody:
            # Fresh custody is allowed for a missing historic body, but cannot excuse
            # a discrepancy against previously approved historic custody.
            return True
        return expected == (body["body_sha256"], body["possessed_at"])

    def proof_for(self, filing):
        return self.issuer_proofs.get(
            (filing["cik"], filing["accession"], filing["filename"])
        )

    def fresh_proof_for(self, filing):
        return deepcopy((self.fresh_custody or {}).get(
            (filing["cik"], filing["accession"], filing["filename"])
        ))


class PreparationExpired(RuntimeError):
    """A valid preparation may continue only under a newly signed window."""


def _after_verified_approval(*args, **kwargs) -> PreparationAuthorization:
    authorization = PreparationAuthorization(*args, **kwargs)
    object.__setattr__(
        authorization, "issuer_proofs", MappingProxyType(authorization.issuer_proofs.copy()),
    )
    if authorization.fresh_custody is not None:
        object.__setattr__(
            authorization, "fresh_custody", MappingProxyType(deepcopy(authorization.fresh_custody)),
        )
    object.__setattr__(authorization, "_verified", True)
    return authorization


def disposable_preparation_authorization(db, *args, **kwargs) -> PreparationAuthorization:
    """Exercise the native writer in an explicitly disposable qualification database."""
    bind = db.get_bind()
    require_disposable_tool_target(bind.url if hasattr(bind, "url") else bind.engine.url)
    return _after_verified_approval(*args, **kwargs)


def examine_attribution(document, text: str) -> str | None:
    try:
        if not prove_document_attribution(document, text):
            return "SEC_FINANCIAL_ISSUER_UNPROVED"
    except SecAttributionRefused as error:
        return str(error)
    return None


def archive_exclusion_proof(filing, body, content: str, proof: dict | None, refusal: str):
    """Sealed preparation can only exclude an exact primary with retained wire custody."""
    if not proof or refusal not in EXCLUDABLE_REFUSALS:
        return None
    date = datetime.fromisoformat
    try:
        accepted = date(filing["accepted_at"])
        received = date(proof["received_at"])
        possessed = date(body["possessed_at"])
        expected_url = (
            f'https://www.sec.gov/Archives/edgar/data/{int(filing["cik"])}/'
            f'{filing["accession"].replace("-", "")}/{filing["filename"]}'
        )
        wire = content.encode("utf-8")
        if (
            proof["archive_url"] != expected_url
            or proof["http_status"] != 200
            or proof["redirect_count"] != 0
            or proof["wire_sha256"] != body["body_sha256"]
            or hashlib.sha256(wire).hexdigest() != body["body_sha256"]
            or proof["wire_bytes"] != len(wire)
            or not accepted <= received <= possessed
            or proof["filing"] != filing
            or not re.fullmatch("[a-f0-9]{64}", proof["archive_receipt_sha256"])
            or not re.fullmatch("[a-f0-9]{64}", proof["submissions_sha256"])
        ):
            return None
    except (KeyError, ValueError, TypeError):
        return None
    if not _identity_consistent(content, filing["cik"], filing["form"]):
        return None
    return proof


def fresh_archive_body(archive_root, filing, proof, registered, *, observed_at):
    """Load authenticated late custody without replacing ordinary registry possession."""
    if not archive_root or not isinstance(proof, dict) or registered is None:
        raise ValueError("SEC_FRESH_CUSTODY_REGISTRY_IDENTITY_REQUIRED")
    if (
        (registered.cik, registered.accession_number, registered.document_name,
         registered.form, registered.filing_date)
        != (filing["cik"], filing["accession"], filing["filename"],
            filing["form"], date.fromisoformat(filing["filing_date"]))
    ):
        raise ValueError("SEC_FRESH_CUSTODY_REGISTRY_CONTRADICTION")
    content = load_fresh_body(archive_root, filing, proof)
    possessed = datetime.fromisoformat(proof["possessed_at"])
    if possessed > observed_at:
        raise ValueError("SEC_FRESH_CUSTODY_POSSESSION_AFTER_EXAMINATION")
    return {
        "document_id": registered.id,
        "body_sha256": proof["wire_sha256"],
        "possessed_at": Canonical.canonicalize(possessed),
    }, content


def matches_reviewed_quarantine(filing, custody):
    return (
        (filing["cik"], filing["accession"], filing["filename"]) == REVIEWED_QUARANTINED_PRIMARY
        and custody.get("source") == "verified_quarantine"
        and custody.get("wire_sha256") == REVIEWED_QUARANTINED_SHA256
        and custody.get("possessed_at") == REVIEWED_QUARANTINED_POSSESSION
        and custody.get("wire_bytes") == 34069
        and custody.get("filing") == filing
    )


def _identity_consistent(content: str, cik: str, form: str) -> bool:
    """Check every inline context, including ones after the guard's early refusal."""
    if "\x00" in content or re.search(r"<!ENTITY", content, re.IGNORECASE):
        return False
    if {value.zfill(10) for value in HEADER_CIK.findall(content)} - {cik}:
        return False
    if not INLINE_MARKERS.search(content):
        return bool(re.search(r"<html(?:\s|>)", content, re.IGNORECASE))
    try:
        bindings = {}
        parser = ET.iterparse(io.StringIO(content), events=("start-ns", "end"))
        for event, value in parser:
            if event == "start-ns":
                bindings.setdefault(value[0], {})[value[1]] = None
        root = parser.root
    except (ET.ParseError, ValueError):
        return False
    contexts = {}
    for node in root.iter(XBRLI + "context"):
        ref = node.get("id")
        entities = node.findall(f"./{XBRLI}entity/{XBRLI}identifier")
        if (
            not ref or ref in contexts or len(entities) != 1
            or entities[0].get("scheme") not in CIK_SCHEMES
            or not _is_cik(entities[0].text, cik)
        ):
            return False
        contexts[ref] = entities[0]
    for tag in ("nonNumeric", "nonFraction", "fraction"):
        for fact in root.iter(INLINE + tag):
            if fact.get("contextRef") not in contexts:
                return False
            qname = (fact.get("name") or "").split(":")
            concept = qname[-1]
            if concept in {"EntityCentralIndexKey", "DocumentType"}:
                namespaces = bindings.get(qname[0], set()) if len(qname) == 2 else set()
                if len(namespaces) != 1 or not next(iter(namespaces)).startswith(
                    ("http://xbrl.sec.gov/dei/", "https://xbrl.sec.gov/dei/"),
                ):
                    return False
                if fact.get("continuedAt") or fact.get("format") or len(fact):
                    return False
                if concept == "EntityCentralIndexKey" and not _is_cik(fact.text, cik):
                    return False
                if concept == "DocumentType" and (fact.text or "").strip() != form:
                    return False
    return True


def _is_cik(value, cik):
    value = (value or "").strip()
    return bool(re.fullmatch(r"[0-9]{1,10}", value) and value.zfill(10) == cik)


def has_unresolved_legacy_collision(db, filing) -> bool:
    """Refuse exclusions that could leave body-ambiguous historical guidance in use."""
    locator = f'{filing["accession"]}/{filing["filename"]}#paragraph-'
    return db.scalar(select(CeriGuidanceEvent.id).join(CeriSourceRecord).where(
        CeriSourceRecord.provider == "sec",
        CeriSourceRecord.dataset == "guidance",
        or_(
            CeriSourceRecord.source_reference.startswith(locator, autoescape=True),
            CeriSourceRecord.provider_record_id.startswith(
                f'{filing["accession"]}:', autoescape=True,
            ),
            CeriGuidanceEvent.filing_accession == filing["accession"],
        ),
    ).limit(1)) is not None
