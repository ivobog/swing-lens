from __future__ import annotations

import hashlib

DOCUMENT_PARSER_VERSION = "sec-html-text-v1"
GUIDANCE_EXTRACTOR_VERSION = "guidance-regex-v1"
EVIDENCE_LOCATOR_VERSION = "paragraph-locator-v1"
FILING_SELECTION_POLICY_VERSION = "guidance-forms-v1"


def sec_guidance_processor_signature() -> str:
    """Version only inputs that can change SEC guidance extraction output."""
    components = (
        DOCUMENT_PARSER_VERSION,
        GUIDANCE_EXTRACTOR_VERSION,
        EVIDENCE_LOCATOR_VERSION,
        FILING_SELECTION_POLICY_VERSION,
    )
    digest = hashlib.sha256("\n".join(components).encode("utf-8")).hexdigest()[:16]
    return f"sec-guidance:{digest}"
