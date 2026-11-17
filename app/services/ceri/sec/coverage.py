"""Retained SEC coverage gaps are distinct from successfully prepared evidence.

The pipeline owns this small JSON contract. It records a completed exact-identity
lookup, so resumed execution can keep an unavailable SEC source unavailable
without repeating repair or excluding the ticker from other providers.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.services.ceri.sec.readiness_diagnostics import SecUniverseReadiness

SEC_COVERAGE_CONTRACT_VERSION = "sec-source-coverage-v1"
SEC_IDENTITY_GAP_REASON = "SEC_IDENTITY_UNRESOLVED"
SEC_IDENTITY_SOURCE = "SEC/company_tickers"
_IDENTITY_CATEGORIES = frozenset({"UNRESOLVED_MAPPING", "CIK_MISSING"})


def _symbols(tickers: Iterable[str]) -> list[str]:
    return sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()})


def _fingerprint(coverage: Mapping[str, Any]) -> str:
    body = {key: value for key, value in coverage.items() if key != "fingerprint"}
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _aware_timestamp(value: datetime | str) -> str:
    stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("SEC_COVERAGE_AWARE_TIMESTAMP_REQUIRED")
    return stamp.astimezone(UTC).isoformat()


def _value(result: Any, name: str) -> Any:
    return result.get(name) if isinstance(result, Mapping) else getattr(result, name, None)


def build_sec_coverage(
    *,
    pipeline: Any,
    readiness: SecUniverseReadiness,
    identity_results: Mapping[str, Any],
    attempted_at: datetime | str,
) -> dict[str, Any]:
    """Retain only explicit no-match outcomes, never an ambiguous identity.

    ``identity_results`` contains the exact resolver's results, rather than a
    dictionary of free-form failures. A failed request or conflicting CIK must
    not be mistaken for a completed no-match lookup.
    """
    timestamp = _aware_timestamp(attempted_at)
    results = {str(ticker).strip().upper(): value for ticker, value in identity_results.items()}
    gaps = []
    for item in sorted(readiness.tickers, key=lambda entry: entry.ticker):
        result = results.get(item.ticker)
        if (
            str(item.category) not in _IDENTITY_CATEGORIES
            or item.ciks
            or _value(result, "status") != "UNRESOLVED"
            or _value(result, "cik")
        ):
            continue
        reason = _value(result, "reason")
        if not isinstance(reason, str) or not reason.strip():
            continue
        item_attempted_at = _aware_timestamp(_value(result, "attempted_at") or timestamp)
        if datetime.fromisoformat(item_attempted_at) > datetime.fromisoformat(timestamp):
            raise ValueError("SEC_COVERAGE_ATTEMPT_AFTER_RECORDING")
        gaps.append(
            {
                "ticker": item.ticker,
                "category": str(item.category),
                "reason_code": SEC_IDENTITY_GAP_REASON,
                "reason": reason,
                "resolution_status": "UNRESOLVED",
                "attempted_at": item_attempted_at,
                "source": SEC_IDENTITY_SOURCE,
            }
        )
    coverage = {
        "contract_version": SEC_COVERAGE_CONTRACT_VERSION,
        "pipeline_id": getattr(pipeline, "id", None),
        "scope_id": getattr(pipeline, "scope_id", None),
        "refresh_cycle_id": getattr(pipeline, "refresh_cycle_id", None),
        "acquisition_plan_id": getattr(pipeline, "acquisition_plan_id", None),
        "processor_signature": readiness.processor_signature,
        "requested_tickers": _symbols(item.ticker for item in readiness.tickers),
        "ready_tickers": sum(str(item.category) == "READY" for item in readiness.tickers),
        "exempt_tickers": sum(
            str(item.category) == "SEC_NOT_APPLICABLE" for item in readiness.tickers
        ),
        "unresolved_tickers": len(gaps),
        "gaps": gaps,
        "recorded_at": timestamp,
    }
    coverage["fingerprint"] = _fingerprint(coverage)
    return coverage


def _valid_contract(coverage: Any) -> bool:
    if not isinstance(coverage, dict):
        return False
    if (
        coverage.get("contract_version") != SEC_COVERAGE_CONTRACT_VERSION
        or not isinstance(coverage.get("pipeline_id"), int)
        or isinstance(coverage["pipeline_id"], bool)
        or coverage["pipeline_id"] <= 0
        or not isinstance(coverage.get("scope_id"), str)
        or not coverage["scope_id"]
        or not isinstance(coverage.get("processor_signature"), str)
        or not coverage["processor_signature"]
    ):
        return False
    requested = coverage.get("requested_tickers")
    gaps = coverage.get("gaps")
    if (
        not isinstance(requested, list)
        or any(not isinstance(ticker, str) or not ticker for ticker in requested)
        or requested != _symbols(requested)
        or not isinstance(gaps, list)
    ):
        return False
    try:
        if coverage.get("fingerprint") != _fingerprint(coverage):
            return False
        recorded_at = _aware_timestamp(coverage["recorded_at"])
    except (KeyError, TypeError, ValueError, OverflowError):
        return False
    gap_tickers = set()
    for gap in gaps:
        if not isinstance(gap, dict):
            return False
        ticker = gap.get("ticker")
        if (
            not isinstance(ticker, str)
            or ticker not in requested
            or ticker in gap_tickers
            or not isinstance(gap.get("category"), str)
            or gap["category"] not in _IDENTITY_CATEGORIES
            or gap.get("resolution_status") != "UNRESOLVED"
            or gap.get("reason_code") != SEC_IDENTITY_GAP_REASON
            or gap.get("source") != SEC_IDENTITY_SOURCE
            or not isinstance(gap.get("reason"), str)
            or not gap["reason"].strip()
        ):
            return False
        try:
            gap_time = datetime.fromisoformat(_aware_timestamp(gap["attempted_at"]))
            if gap_time > datetime.fromisoformat(recorded_at):
                return False
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        gap_tickers.add(ticker)
    counts = [
        coverage.get(key)
        for key in ("ready_tickers", "exempt_tickers", "unresolved_tickers")
    ]
    if any(not isinstance(count, int) or isinstance(count, bool) or count < 0 for count in counts):
        return False
    return counts[2] == len(gaps) and sum(counts) <= len(requested)


def load_sec_coverage(
    pipeline: Any,
    *,
    processor_signature: str,
    tickers: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Load only a valid disposition retained by this exact pipeline authority."""
    result = getattr(pipeline, "result_json", None)
    return validate_sec_coverage(
        result.get("sec_coverage") if isinstance(result, dict) else None,
        pipeline=pipeline,
        processor_signature=processor_signature,
        tickers=tickers,
    )


def validate_sec_coverage(
    coverage: Any,
    *,
    pipeline: Any,
    processor_signature: str,
    tickers: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    """Validate retained child or parent JSON against its execution owner."""
    if pipeline is None:
        return None
    if not _valid_contract(coverage):
        return None
    if (
        coverage["pipeline_id"] != getattr(pipeline, "id", None)
        or coverage["scope_id"] != getattr(pipeline, "scope_id", None)
        or coverage.get("refresh_cycle_id") != getattr(pipeline, "refresh_cycle_id", None)
        or coverage.get("acquisition_plan_id") != getattr(pipeline, "acquisition_plan_id", None)
        or coverage["processor_signature"] != processor_signature
        or (tickers is not None and coverage["requested_tickers"] != _symbols(tickers))
    ):
        return None
    # Callers can project this JSON into their own transaction without sharing
    # mutable ORM payload state with the original pipeline record.
    return json.loads(json.dumps(coverage))


def _terminal_gap_tickers(
    readiness: SecUniverseReadiness, coverage: dict[str, Any] | None
) -> frozenset[str]:
    if (
        not _valid_contract(coverage)
        or coverage["processor_signature"] != readiness.processor_signature
        or coverage["requested_tickers"] != _symbols(item.ticker for item in readiness.tickers)
    ):
        return frozenset()
    recorded = {gap["ticker"] for gap in coverage["gaps"]}
    return frozenset(
        item.ticker
        for item in readiness.tickers
        if item.ticker in recorded
        and str(item.category) in _IDENTITY_CATEGORIES
        and not item.ciks
    )


def sec_coverage_blocking_tickers(
    readiness: SecUniverseReadiness, coverage: dict[str, Any] | None
) -> tuple[str, ...]:
    terminal = _terminal_gap_tickers(readiness, coverage)
    return tuple(
        item.ticker
        for item in readiness.tickers
        if not item.accepted and item.ticker not in terminal
    )


def sec_coverage_current_gaps(
    readiness: SecUniverseReadiness, coverage: dict[str, Any] | None
) -> tuple[dict[str, Any], ...]:
    """Project still-unavailable sources without rewriting retained history."""
    terminal = _terminal_gap_tickers(readiness, coverage)
    if not terminal:
        return ()
    return tuple(
        json.loads(json.dumps(gap))
        for gap in coverage["gaps"]
        if gap["ticker"] in terminal
    )


def sec_coverage_can_continue(
    readiness: SecUniverseReadiness, coverage: dict[str, Any] | None
) -> bool:
    return not sec_coverage_blocking_tickers(readiness, coverage)


def sec_coverage_ticker_scope(
    readiness: SecUniverseReadiness, coverage: dict[str, Any] | None
) -> tuple[str, ...]:
    """Scope only SEC acquisition; leave other provider scopes untouched.

    Bootstrap/signature/configuration problems stay in scope and must still be
    handled by the caller's readiness checks. They are not coverage exemptions.
    """
    terminal = _terminal_gap_tickers(readiness, coverage)
    return tuple(
        item.ticker
        for item in readiness.tickers
        if str(item.category) != "SEC_NOT_APPLICABLE" and item.ticker not in terminal
    )
