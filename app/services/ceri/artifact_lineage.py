from __future__ import annotations

from datetime import datetime
from enum import StrEnum


class CeriArtifactOwnership(StrEnum):
    PIPELINE = "PIPELINE"
    STANDALONE = "STANDALONE"
    LEGACY_UNKNOWN = "LEGACY_UNKNOWN"


def validate_ceri_artifact_lineage(
    *,
    ownership_mode: str,
    calculation_context_id: int | None,
    calculation_cutoff_at: datetime | None,
    calendar_version: str | None,
) -> None:
    try:
        ownership = CeriArtifactOwnership(ownership_mode)
    except ValueError as exc:
        raise ValueError(f"Unsupported CERI artifact ownership mode: {ownership_mode}") from exc
    if ownership is not CeriArtifactOwnership.PIPELINE:
        return
    missing = [
        name
        for name, value in (
            ("calculation_context_id", calculation_context_id),
            ("calculation_cutoff_at", calculation_cutoff_at),
            ("calendar_version", calendar_version),
        )
        if value in (None, "")
    ]
    if missing:
        raise ValueError(
            "Pipeline-owned CERI artifact is missing frozen context lineage: " + ", ".join(missing)
        )
    if calculation_cutoff_at.tzinfo is None or calculation_cutoff_at.utcoffset() is None:
        raise ValueError("Pipeline-owned CERI calculation_cutoff_at must be timezone-aware")
