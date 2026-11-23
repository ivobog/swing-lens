"""Verify the three reviewed SEC joint filings offline, without cache admission."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DISCOVERY_SHA256 = "4df298cd9c55fdfa3bdb96cada76568c3d60f873f6c8f64037137a8de1aed525"
SELECTION_CUTOFF = "2026-11-23T16:51:34.338252+00:00"
ARCHIVES = "https://www.sec.gov/Archives/edgar/data"
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
INLINE = "{http://www.xbrl.org/2013/inlineXBRL}"
DEI_NAMESPACE = re.compile(r"https?://xbrl\.sec\.gov/dei/[0-9]{4}")
CIK_SCHEMES = {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
MAX_WIRE_BYTES = 4 * 1024 * 1024
PBF_NAMES = {
    "0001534504": ("PBF Energy Inc.",),
    "0001566011": ("PBF Holding Co LLC", "PBF Holding Company LLC"),
}


class FilingIdentityError(ValueError):
    """The supplied evidence cannot establish the joint filing identity."""


@dataclass(frozen=True)
class ReviewedFiling:
    ticker: str
    cik: str
    accession: str
    filename: str
    filing_date: str
    accepted_at: str
    index_cik: str
    registrants: dict[str, tuple[str, ...]]
    retained_wire_sha256: str | None = None

    @property
    def archive_url(self) -> str:
        return f"{ARCHIVES}/{int(self.cik)}/{self.accession.replace('-', '')}/{self.filename}"

    @property
    def index_url(self) -> str:
        archive_accession = self.accession.replace("-", "")
        index_dir = f"{ARCHIVES}/{int(self.index_cik)}/{archive_accession}"
        return f"{index_dir}/{self.accession}-index.htm"


# Filing-level authority is independently reviewed and bound to exact accessions.
# These co-filer identities have no authority outside these SEC filing indexes.
APPROVED = {
    filing.accession: filing
    for filing in (
        ReviewedFiling(
            "EVRG",
            "0001711269",
            "0001193125-26-470295",
            "d114993d8k.htm",
            "2026-11-20",
            "2026-11-20T21:18:53+00:00",
            "0001711269",
            {"0001711269": ("Evergy, Inc.",), "0000054476": ("Evergy Metro, Inc.",)},
        ),
        ReviewedFiling(
            "PBF",
            "0001534504",
            "0001534504-26-000039",
            "pbf-20261029.htm",
            "2026-10-29",
            "2026-10-29T10:46:58+00:00",
            "0001566011",
            PBF_NAMES,
        ),
        ReviewedFiling(
            "PBF",
            "0001534504",
            "0001534504-26-000043",
            "pbf-20261027.htm",
            "2026-10-30",
            "2026-10-30T20:15:34+00:00",
            "0001566011",
            PBF_NAMES,
            "e75a70173a024f5f691f2edd61736a2a0f77e3ec796fde34ad03ec3312772da8",
        ),
    )
}


def reviewed_filing(cik: str, accession: str, filename: str, form: str) -> ReviewedFiling:
    filing = APPROVED.get(accession)
    if filing is None or (cik, filename, form) != (filing.cik, filing.filename, "8-K"):
        raise FilingIdentityError(
            "CIK, accession, primary filename or form is outside the reviewed scope"
        )
    return filing


def check_frozen_selection(manifest: bytes, filing: ReviewedFiling) -> None:
    if hashlib.sha256(manifest).hexdigest() != DISCOVERY_SHA256:
        raise FilingIdentityError("Discovery manifest digest changed")
    data = json.loads(manifest)
    matches = [
        (
            d["ticker"],
            d["cik"],
            d["document_name"],
            d["form"],
            d["filing_date"],
            d["sec_filing_accepted_at_utc"],
        )
        for issuer in data["issuer_results"]
        if issuer["authoritative_manifest"]
        for d in issuer["selected_documents"] or []
        if d["accession_number"] == filing.accession
    ]
    if matches != [
        (filing.ticker, filing.cik, filing.filename, "8-K", filing.filing_date, filing.accepted_at)
    ]:
        raise FilingIdentityError("The filing has no unique exact record in frozen SEC discovery")


def _qname(value: str, namespaces: dict[str, str]) -> tuple[str, str]:
    if not re.fullmatch(r"[A-Za-z_][\w.-]*:[A-Za-z_][\w.-]*", value):
        raise FilingIdentityError("Malformed XBRL QName")
    prefix, local = value.split(":", 1)
    if prefix not in namespaces:
        raise FilingIdentityError("Unbound XBRL QName prefix")
    return namespaces[prefix], local


def _one(parent: ET.Element, tag: str) -> ET.Element:
    matches = parent.findall(tag)
    if len(matches) != 1:
        raise FilingIdentityError("Missing or ambiguous XBRL " + tag.rsplit("}", 1)[-1])
    return matches[0]


def _scalar(element: ET.Element) -> str:
    if len(element):
        raise FilingIdentityError("A registrant identity scalar contains nested markup")
    return (element.text or "").strip()


def _cik(value: str) -> str:
    if not re.fullmatch(r"[0-9]{1,10}", value) or not int(value):
        raise FilingIdentityError("Malformed SEC CIK")
    return value.zfill(10)


def _name(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _period(context: ET.Element) -> tuple[date, ...]:
    parts = list(_one(context, XBRLI + "period"))
    if [p.tag for p in parts] not in (
        [XBRLI + "instant"],
        [XBRLI + "startDate", XBRLI + "endDate"],
    ):
        raise FilingIdentityError("Unsupported or duplicate registrant context period")
    try:
        values = tuple(date.fromisoformat(_scalar(p)) for p in parts)
    except ValueError as exc:
        raise FilingIdentityError("Invalid registrant context date") from exc
    if tuple(sorted(values)) != values:
        raise FilingIdentityError("Reversed registrant context period")
    return values


Role = tuple[str, tuple[str, str] | None]


def _context_role(context: ET.Element, namespaces: dict[str, str], filing: ReviewedFiling) -> Role:
    entity = _one(context, XBRLI + "entity")
    identifier = _one(entity, XBRLI + "identifier")
    cik = _cik(_scalar(identifier))
    if identifier.get("scheme") not in CIK_SCHEMES or cik not in filing.registrants:
        raise FilingIdentityError("Foreign or unverified SEC context entity")
    segments = entity.findall(XBRLI + "segment")
    if context.findall(XBRLI + "scenario") or len(segments) > 1 or len(entity) != 1 + len(segments):
        raise FilingIdentityError("Ambiguous registrant dimensions")
    if not segments:
        return cik, None
    member = _one(segments[0], XBRLDI + "explicitMember")
    if len(segments[0]) != 1 or cik != filing.cik:
        raise FilingIdentityError("Unsupported co-filer context structure")
    dimension_uri, dimension_name = _qname(member.get("dimension", ""), namespaces)
    if not DEI_NAMESPACE.fullmatch(dimension_uri) or dimension_name != "LegalEntityAxis":
        raise FilingIdentityError("Unverified registrant dimension")
    member_name = _qname(_scalar(member), namespaces)
    if DEI_NAMESPACE.fullmatch(member_name[0]):
        raise FilingIdentityError("Legal-entity member must identify a filer extension")
    return cik, member_name


def _parse(body: bytes) -> tuple[ET.Element, dict[str, str]]:
    if (
        not body
        or len(body) > MAX_WIRE_BYTES
        or b"\0" in body
        or re.search(rb"<!DOCTYPE|<!ENTITY", body, re.I)
    ):
        raise FilingIdentityError("Unsupported or oversized Inline XBRL wire body")
    namespaces = {}
    try:
        parsed = ET.iterparse(io.BytesIO(body), events=("start-ns",))
        for _, (prefix, uri) in parsed:
            if prefix in namespaces and namespaces[prefix] != uri:
                raise FilingIdentityError("Ambiguous Inline XBRL namespace binding")
            namespaces[prefix] = uri
    except ET.ParseError as exc:
        raise FilingIdentityError("Malformed Inline XBRL wire body") from exc
    if parsed.root.tag != "{http://www.w3.org/1999/xhtml}html":
        raise FilingIdentityError("Expected an SEC XHTML primary document")
    return parsed.root, namespaces


def _registrant_contexts(body: bytes, filing: ReviewedFiling) -> dict[str, str]:
    root, namespaces = _parse(body)
    contexts = {}
    for context in root.iter(XBRLI + "context"):
        ref = context.get("id")
        if not ref or ref in contexts:
            raise FilingIdentityError("Missing or duplicate XBRL context ID")
        contexts[ref] = context

    role_facts: dict[Role, dict[str, str]] = defaultdict(dict)
    used: dict[str, Role] = {}
    periods: set[tuple[Role, tuple[date, ...]]] = set()
    concepts = {"EntityCentralIndexKey", "EntityRegistrantName", "DocumentType"}
    for fact in root.iter(INLINE + "nonNumeric"):
        name = fact.get("name", "")
        if name.rsplit(":", 1)[-1] not in concepts:
            continue
        uri, concept = _qname(name, namespaces)
        if not DEI_NAMESPACE.fullmatch(uri):
            raise FilingIdentityError("Registrant fact is outside the SEC DEI namespace")
        ref = fact.get("contextRef", "")
        if ref not in contexts or fact.get("continuedAt") or fact.get("format"):
            raise FilingIdentityError("Missing or transformed DEI registrant context")
        if ref not in used:
            role = _context_role(contexts[ref], namespaces, filing)
            period_role = role, _period(contexts[ref])
            if period_role in periods:
                raise FilingIdentityError(
                    "Duplicate registrant context for the same role and period"
                )
            periods.add(period_role)
            used[ref] = role
        facts = role_facts[used[ref]]
        if concept in facts:
            raise FilingIdentityError("Repeated or conflicting DEI fact for one registrant role")
        facts[concept] = _scalar(fact)

    # Even a context with no DEI facts must not assert an unrelated entity.
    for context in contexts.values():
        identifier = _one(_one(context, XBRLI + "entity"), XBRLI + "identifier")
        if (
            identifier.get("scheme") not in CIK_SCHEMES
            or _cik(_scalar(identifier)) not in filing.registrants
        ):
            raise FilingIdentityError("Foreign or unverified SEC context entity")

    role_ciks = {}
    seen_ciks = set()
    for role, facts in role_facts.items():
        identifier, member = role
        cik = _cik(facts.get("EntityCentralIndexKey", ""))
        if cik not in filing.registrants or cik in seen_ciks:
            raise FilingIdentityError("Unverified or multiply bound DEI CIK")
        if member is None and identifier != cik or member is not None and cik == filing.cik:
            raise FilingIdentityError(
                "DEI CIK conflicts with its entity context or legal-entity member"
            )
        if _name(facts.get("EntityRegistrantName", "")) not in {
            _name(n) for n in filing.registrants[cik]
        }:
            raise FilingIdentityError(
                "DEI registrant name does not match the approved context role"
            )
        if facts.get("DocumentType", "8-K") != "8-K":
            raise FilingIdentityError("Body form differs from the official primary filing")
        role_ciks[role] = cik
        seen_ciks.add(cik)

    if (
        seen_ciks != set(filing.registrants)
        or role_facts.get((filing.cik, None), {}).get("DocumentType") != "8-K"
    ):
        raise FilingIdentityError(
            "Expected primary filer, form or independent co-filer context is missing"
        )
    return {ref: role_ciks[role] for ref, role in sorted(used.items())}


def verify_response(
    filing: ReviewedFiling, body: bytes, *, archive_url: str, wire_sha256: str
) -> dict[str, object]:
    if APPROVED.get(filing.accession) is not filing or archive_url != filing.archive_url:
        raise FilingIdentityError("Body does not have the exact reviewed primary archive identity")
    if hashlib.sha256(body).hexdigest() != wire_sha256:
        raise FilingIdentityError("Body bytes differ from the acquisition receipt SHA-256")
    if filing.retained_wire_sha256 is None:
        raise FilingIdentityError("No authenticated retained wire response for this accession")
    if wire_sha256 != filing.retained_wire_sha256:
        raise FilingIdentityError(
            "The original quarantined body must retain its exact wire SHA-256"
        )
    return {
        "cik": filing.cik,
        "accession": filing.accession,
        "filename": filing.filename,
        "form": "8-K",
        "filing_acceptance_utc": filing.accepted_at,
        "source_selection_cutoff_utc": SELECTION_CUTOFF,
        "archive_url": archive_url,
        "cofiler_authority_index": filing.index_url,
        "wire_sha256": wire_sha256,
        "wire_bytes": len(body),
        "registrant_contexts": _registrant_contexts(body, filing),
        "document_identity": "VERIFIED",
        "financial_attribution": "BLOCKED",
        "production_cache_admission": "BLOCKED",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("cik", "accession", "filename", "form", "archive-url", "wire-sha256"):
        parser.add_argument("--" + flag, required=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--body", required=True, type=Path)
    args = parser.parse_args()
    try:
        filing = reviewed_filing(args.cik, args.accession, args.filename, args.form)
        check_frozen_selection(args.manifest.read_bytes(), filing)
        with args.body.open("rb") as stream:
            body = stream.read(MAX_WIRE_BYTES + 1)
        result = verify_response(
            filing, body, archive_url=args.archive_url, wire_sha256=args.wire_sha256
        )
    except (FilingIdentityError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
