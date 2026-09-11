from __future__ import annotations

import hashlib

SEC_PROCESSOR_SIGNATURE_ALGORITHM_VERSION = "sec-processor-signature-v1"
SEC_PROCESSOR_NAME = "sec-guidance"
DOCUMENT_PARSER_VERSION = "sec-html-text-v1"
GUIDANCE_EXTRACTOR_VERSION = "guidance-regex-visible-text-v3"
EVIDENCE_LOCATOR_VERSION = "paragraph-locator-v1"
FILING_SELECTION_POLICY_VERSION = "guidance-forms-v1"


def sec_guidance_processor_signature() -> str:
    """Version only inputs that can change SEC guidance extraction output."""
    components = sec_guidance_processor_identity_inputs()
    digest = hashlib.sha256("\n".join(components).encode("utf-8")).hexdigest()[:16]
    return f"{SEC_PROCESSOR_NAME}:{digest}"


def sec_guidance_processor_identity_inputs() -> tuple[str, ...]:
    """Ordered semantic identity contract for the v1 persisted signature."""

    return (
        DOCUMENT_PARSER_VERSION,
        GUIDANCE_EXTRACTOR_VERSION,
        EVIDENCE_LOCATOR_VERSION,
        FILING_SELECTION_POLICY_VERSION,
    )
