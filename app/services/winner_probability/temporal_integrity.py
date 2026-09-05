from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

ENTRY_TIMING_VALIDATION_VERSION = "winner-temporal-integrity-1.0"


class TemporalValidityStatus:
    VALID = "VALID"
    EXECUTION_INVALID = "EXECUTION_INVALID"
    LOOKAHEAD_INVALID = "LOOKAHEAD_INVALID"
    TEMPORAL_LINEAGE_UNRESOLVED = "TEMPORAL_LINEAGE_UNRESOLVED"


class TemporalValidityReason:
    ENTRY_NOT_AFTER_DECISION = "ENTRY_NOT_AFTER_DECISION"
    SEMANTIC_INPUT_TIME_UNRESOLVED = "SEMANTIC_INPUT_TIME_UNRESOLVED"
    SOURCE_AFTER_DECISION = "SOURCE_AFTER_DECISION"


@dataclass(frozen=True)
class TemporalValidationResult:
    status: str
    entry_timing_valid: bool
    source_cutoff_valid: bool
    semantic_input_time_valid: bool | None
    evidence_eligible: bool
    reason_codes: tuple[str, ...]
    validation_version: str = ENTRY_TIMING_VALIDATION_VERSION


def validate_next_open_timing(
    decision_at: datetime,
    entry_open_at: datetime,
    *,
    source_data_cutoff_at: datetime | None = None,
    semantic_input_time_valid: bool | None = True,
) -> TemporalValidationResult:
    """Evaluate the fail-closed temporal contract without changing source rows."""
    decision = _aware_utc(decision_at, "decision_at")
    entry_open = _aware_utc(entry_open_at, "entry_open_at")
    source_cutoff = (
        _aware_utc(source_data_cutoff_at, "source_data_cutoff_at")
        if source_data_cutoff_at is not None
        else None
    )
    entry_valid = decision < entry_open
    source_valid = source_cutoff is None or source_cutoff <= decision
    reasons: list[str] = []
    if not entry_valid:
        reasons.append(TemporalValidityReason.ENTRY_NOT_AFTER_DECISION)
    if not source_valid:
        reasons.append(TemporalValidityReason.SOURCE_AFTER_DECISION)
    if semantic_input_time_valid is not True:
        reasons.append(TemporalValidityReason.SEMANTIC_INPUT_TIME_UNRESOLVED)

    if not entry_valid:
        status = TemporalValidityStatus.EXECUTION_INVALID
    elif not source_valid:
        status = TemporalValidityStatus.LOOKAHEAD_INVALID
    elif semantic_input_time_valid is not True:
        status = TemporalValidityStatus.TEMPORAL_LINEAGE_UNRESOLVED
    else:
        status = TemporalValidityStatus.VALID
    eligible = entry_valid and source_valid and semantic_input_time_valid is True
    return TemporalValidationResult(
        status=status,
        entry_timing_valid=entry_valid,
        source_cutoff_valid=source_valid,
        semantic_input_time_valid=semantic_input_time_valid,
        evidence_eligible=eligible,
        reason_codes=tuple(reasons),
    )


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)
