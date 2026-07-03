from dataclasses import dataclass
from enum import StrEnum

from app.models.tables import FundamentalScore, TechnicalScore
from app.services.warning_flag_service import warning_flags_for_row


class MissingSeverity(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class WarningFlags:
    flags: list[str]
    is_complete: bool
    has_warning: bool
    has_fundamental: bool
    has_technical: bool
    sort_bucket: int


def fundamental_missing_severity(missing_fields: set[str]) -> MissingSeverity:
    count = len(missing_fields)
    if count == 0:
        return MissingSeverity.NONE
    if {"fcf_ttm", "market_cap"} & missing_fields:
        return MissingSeverity.CRITICAL
    if count >= 6:
        return MissingSeverity.HIGH
    if count >= 3:
        return MissingSeverity.MEDIUM
    return MissingSeverity.LOW


def technical_confidence_from_score(score: TechnicalScore | None) -> str:
    if score is None:
        return "missing_price_data"
    if score.technical_confidence == "error":
        return "error"

    missing_data = score.missing_data_json or {}
    if missing_data.get("missing_market_data"):
        return "missing_market_data"
    if missing_data.get("missing_benchmark_data"):
        return "missing_benchmark_data"
    if score.insufficient_data or missing_data.get("insufficient_history"):
        return "insufficient_history"
    if score.technical_confidence:
        return score.technical_confidence
    return "normal"


def build_combined_warning_flags(
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    decision: str,
) -> WarningFlags:
    flags = warning_flags_for_row(fundamental, technical)
    has_fundamental = fundamental is not None and fundamental.fundamental_score is not None
    has_technical = technical is not None and technical.dual_score is not None
    is_complete = has_fundamental and has_technical
    return WarningFlags(
        flags=flags,
        is_complete=is_complete,
        has_warning=bool(flags),
        has_fundamental=has_fundamental,
        has_technical=has_technical,
        sort_bucket=_sort_bucket(decision, is_complete),
    )


def _sort_bucket(decision: str, is_complete: bool) -> int:
    if not is_complete:
        return 50
    if decision == "Strong candidate":
        return 10
    if decision == "Candidate":
        return 20
    if decision == "Watchlist":
        return 30
    if decision == "Avoid":
        return 40
    return 60
