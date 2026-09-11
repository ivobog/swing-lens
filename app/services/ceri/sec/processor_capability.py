from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.ceri_tables import CeriSecProcessorRelease
from app.services.ceri.sec.processor_signature import (
    SEC_PROCESSOR_NAME,
    SEC_PROCESSOR_SIGNATURE_ALGORITHM_VERSION,
    sec_guidance_processor_signature,
)


class SecProcessorCapabilityState(StrEnum):
    READY = "SEC_CAPABILITY_READY"
    NOT_REGISTERED = "SEC_PROCESSOR_NOT_REGISTERED"
    NOT_ACTIVE = "SEC_PROCESSOR_NOT_ACTIVE"
    MULTIPLE_ACTIVE = "SEC_PROCESSOR_MULTIPLE_ACTIVE"
    SIGNATURE_MALFORMED = "SEC_PROCESSOR_SIGNATURE_MALFORMED"
    SIGNATURE_MISMATCH = "SEC_PROCESSOR_SIGNATURE_MISMATCH"


@dataclass(frozen=True)
class SecProcessorCapability:
    ready: bool
    state: SecProcessorCapabilityState
    expected_signature: str
    active_signature: str | None
    algorithm_version: str = SEC_PROCESSOR_SIGNATURE_ALGORITHM_VERSION
    processor_name: str = SEC_PROCESSOR_NAME

    def to_dict(self) -> dict[str, str | bool | None]:
        return {
            "ready": self.ready,
            "state": self.state.value,
            "processor_name": self.processor_name,
            "algorithm_version": self.algorithm_version,
            "expected_signature": self.expected_signature,
            "active_signature": self.active_signature,
        }


def evaluate_sec_processor_capability(db: Session) -> SecProcessorCapability:
    expected = sec_guidance_processor_signature()
    releases = list(db.scalars(select(CeriSecProcessorRelease)).all())
    active = [row for row in releases if str(row.status).upper() == "ACTIVE"]
    if len(active) > 1:
        return _blocked(SecProcessorCapabilityState.MULTIPLE_ACTIVE, expected, None)
    if not active:
        return _blocked(SecProcessorCapabilityState.NOT_ACTIVE, expected, None)
    active_signature = str(active[0].processor_signature or "")
    if not re.fullmatch(r"sec-guidance:[0-9a-f]{16}", active_signature):
        return _blocked(
            SecProcessorCapabilityState.SIGNATURE_MALFORMED,
            expected,
            active_signature,
        )
    if not any(str(row.processor_signature) == expected for row in releases):
        return _blocked(
            SecProcessorCapabilityState.NOT_REGISTERED,
            expected,
            active_signature,
        )
    if active_signature != expected:
        return _blocked(
            SecProcessorCapabilityState.SIGNATURE_MISMATCH,
            expected,
            active_signature,
        )
    return SecProcessorCapability(
        ready=True,
        state=SecProcessorCapabilityState.READY,
        expected_signature=expected,
        active_signature=active_signature,
    )


def _blocked(
    state: SecProcessorCapabilityState,
    expected: str,
    active: str | None,
) -> SecProcessorCapability:
    return SecProcessorCapability(
        ready=False,
        state=state,
        expected_signature=expected,
        active_signature=active,
    )


# Conservative capability boundary: a FULL_PIPELINE may reach SEC ingestion,
# every CERI durable job participates in CERI lineage, and readiness repair is
# signature-specific. Non-SEC IB/Winner/SLSE/recovery jobs remain claimable.
SEC_CAPABILITY_JOB_TYPES = frozenset(
    {
        "FULL_PIPELINE",
        "SEC_READINESS_REPAIR",
        "CERI_PROVIDER_INGEST",
        "CERI_NORMALIZE",
        "CERI_REBUILD_FEATURES",
        "CERI_CAPTURE_RUN",
        "CERI_CHANGE_DETECTION",
        "CERI_BACKFILL",
        "CERI_ALERT_REBUILD",
        "CERI_PURGE_LICENSED_DATA",
        "CERI_PROVIDER_INGEST_BATCH",
        "CERI_NORMALIZE_BATCH",
        "CERI_FEATURE_BATCH",
        "CERI_RUN_FINALIZE",
    }
)
