"""Reject SEC filings whose financial prose cannot be attributed to one issuer."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# These exact accessions have independently reviewed joint filing indexes in
# scripts/ops/validate_sec_cofiler.py and its retained source fixture.
REVIEWED_JOINT_ACCESSIONS = frozenset(
    {
        "0001193125-26-470295",
        "0001534504-26-000039",
        "0001534504-26-000043",
    }
)
XBRLI = "{http://www.xbrl.org/2003/instance}"
XBRLDI = "{http://xbrl.org/2006/xbrldi}"
INLINE = "{http://www.xbrl.org/2013/inlineXBRL}"
CIK_SCHEMES = {"http://www.sec.gov/CIK", "https://www.sec.gov/CIK"}
INLINE_MARKERS = re.compile(
    r"inlineXBRL|(?:EntityCentralIndexKey|EntityRegistrantName|LegalEntityAxis)|"
    r"xmlns:[^\s=]+\s*=\s*['\"]http://www\.xbrl\.org/2003/instance",
    re.IGNORECASE,
)
HEADER_CIK = re.compile(r"CENTRAL INDEX KEY:\s*([0-9]{1,10})\b", re.IGNORECASE)


class SecAttributionRefused(ValueError):
    """Document identity or financial issuer attribution is unresolved."""


def prove_document_attribution(document, body: str) -> bool:
    """Recognize one positive SEC entity or a complete submission's filer CIK."""

    if document.accession_number in REVIEWED_JOINT_ACCESSIONS:
        raise SecAttributionRefused("SEC_REVIEWED_JOINT_FILING")
    header_ciks = {cik.zfill(10) for cik in HEADER_CIK.findall(body)}
    if header_ciks and header_ciks != {document.cik}:
        raise SecAttributionRefused("SEC_SUBMISSION_REGISTRANTS_CONFLICT")
    if not INLINE_MARKERS.search(body):
        header = re.search(r"<SEC-HEADER>(.*?)</SEC-HEADER>", body, re.I | re.S)
        return bool(
            header and {cik.zfill(10) for cik in HEADER_CIK.findall(header[1])} == {document.cik}
        )
    if re.search(r"<!ENTITY", body, re.I):
        raise SecAttributionRefused("SEC_INLINE_ENTITY_DECLARATION")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, ValueError) as exc:
        raise SecAttributionRefused("SEC_INLINE_REGISTRANT_CONTEXT_MALFORMED") from exc
    contexts = {}
    for context in root.iter(XBRLI + "context"):
        identifier = context.findall(f"./{XBRLI}entity/{XBRLI}identifier")
        ref = context.get("id")
        if (
            not ref
            or ref in contexts
            or len(identifier) != 1
            or identifier[0].get("scheme") not in CIK_SCHEMES
            or not _matches_cik(identifier[0].text, document.cik)
        ):
            raise SecAttributionRefused("SEC_INLINE_FOREIGN_OR_MALFORMED_CONTEXT")
        for member in (
            *context.iter(XBRLDI + "explicitMember"),
            *context.iter(XBRLDI + "typedMember"),
        ):
            if member.get("dimension", "").rsplit(":", 1)[-1].lower() == "legalentityaxis":
                raise SecAttributionRefused("SEC_INLINE_LEGAL_ENTITY_DIMENSION")
        contexts[ref] = context
    proved = False
    for fact in root.iter(INLINE + "nonNumeric"):
        concept = fact.get("name", "").rsplit(":", 1)[-1]
        if concept != "EntityCentralIndexKey":
            continue
        if (
            fact.get("contextRef") not in contexts
            or fact.get("continuedAt")
            or fact.get("format")
            or len(fact)
            or not _matches_cik(fact.text, document.cik)
        ):
            raise SecAttributionRefused("SEC_INLINE_REGISTRANT_FACT_CONFLICT")
        proved = True
    if not proved:
        raise SecAttributionRefused("SEC_INLINE_REGISTRANT_UNPROVED")
    return True


def _matches_cik(value: str | None, cik: str) -> bool:
    value = (value or "").strip()
    return bool(re.fullmatch(r"[0-9]{1,10}", value) and value.zfill(10) == cik)
