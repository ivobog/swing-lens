"""Frozen instrument-availability policy, separate from gateway and integrity gates."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

CONTRACT = "market-data-availability-policy-v1"
RESULT_KEY = "market_data_availability_policy"
CONFIGURATION_NAMESPACE = "execution.market_data"


class MarketDataAvailabilityMode(StrEnum):
    STRICT_V1 = "STRICT_V1"
    CONTINUE_KNOWN_DATA_GAPS_V1 = "CONTINUE_KNOWN_DATA_GAPS_V1"


@dataclass(frozen=True)
class MarketDataAvailabilityPolicy:
    mode: MarketDataAvailabilityMode

    @property
    def allow_history_gaps(self) -> bool:
        return self.mode is MarketDataAvailabilityMode.CONTINUE_KNOWN_DATA_GAPS_V1

    def as_dict(self) -> dict:
        return {"contract_version": CONTRACT, "mode": self.mode.value}


def parse_availability_policy(marker=None) -> MarketDataAvailabilityPolicy:
    # Absence is deliberately historical strict behavior, never today's default.
    if marker is None:
        return MarketDataAvailabilityPolicy(MarketDataAvailabilityMode.STRICT_V1)
    if (not isinstance(marker, dict) or set(marker) != {"contract_version", "mode"}
            or marker["contract_version"] != CONTRACT):
        raise ValueError("MARKET_DATA_AVAILABILITY_POLICY_INVALID")
    try:
        return MarketDataAvailabilityPolicy(MarketDataAvailabilityMode(marker["mode"]))
    except ValueError as exc:
        raise ValueError("MARKET_DATA_AVAILABILITY_POLICY_INVALID") from exc


def new_availability_policy(mode=None, *, certification=False) -> MarketDataAvailabilityPolicy:
    selected = MarketDataAvailabilityMode(mode) if mode is not None else (
        MarketDataAvailabilityMode.STRICT_V1 if certification
        else MarketDataAvailabilityMode.CONTINUE_KNOWN_DATA_GAPS_V1
    )
    if certification and selected is not MarketDataAvailabilityMode.STRICT_V1:
        raise ValueError("CERTIFICATION_REQUIRES_STRICT_MARKET_DATA")
    return MarketDataAvailabilityPolicy(selected)


def pipeline_availability_policy(pipeline) -> MarketDataAvailabilityPolicy:
    from app.services.configuration_delivery import current_delivery

    marker = (pipeline.result_json or {}).get(RESULT_KEY)
    policy = parse_availability_policy(marker)
    delivery = current_delivery()
    if delivery is not None:
        frozen = delivery.configurations.get(CONFIGURATION_NAMESPACE)
        if frozen is not None:
            if dict(frozen.values) != policy.as_dict():
                raise ValueError("MARKET_DATA_AVAILABILITY_CONFIGURATION_MISMATCH")
        elif marker is not None:
            raise ValueError("MISSING_FROZEN_MARKET_DATA_AVAILABILITY_POLICY")
    return policy


def known_instrument_unavailability(row) -> bool:
    """Only a proven instrument no-data response is continuable, not any failure."""
    if row.disposition != "REQUIRED_DATA_UNAVAILABLE":
        return False
    evidence = getattr(row, "evidence_json", None) or {}
    if evidence.get("integrity_failure"):
        return False
    if evidence.get("validated_coverage_gap") is True and evidence.get("admission_receipt_hashes"):
        return not evidence.get("integrity_failure")
    if evidence.get("validated_empty_response") is True and evidence.get("empty_response_item_ids"):
        return not evidence.get("integrity_failure")
    failures = evidence.get("fetch_failures") or ()
    return bool(failures) and all(
        failure.get("provider_error_category") == "HISTORICAL_DATA_UNAVAILABLE"
        and failure.get("retryable") is False
        and "BENCHMARK" not in (failure.get("dependency_roles") or ())
        and "BENCHMARK" not in str(failure.get("data_role") or "")
        for failure in failures
    )


def hard_readiness_blockers(rows, policy: MarketDataAvailabilityPolicy) -> tuple:
    return tuple(row for row in rows if row.disposition not in {"READY", "TERMINAL_INACTIVE"}
                 and not (policy.allow_history_gaps and known_instrument_unavailability(row)))


def processing_tickers(rows, policy: MarketDataAvailabilityPolicy) -> tuple[str, ...]:
    # Keep known-unavailable requested members for explicit null/ineligible outputs.
    return tuple(row.ticker for row in rows if row.downstream_eligible
                 or (policy.allow_history_gaps and known_instrument_unavailability(row)))


def required_dependency(item) -> bool:
    roles = getattr(item, "dependency_roles", ()) or ()
    role = str(getattr(item, "data_role", "") or "").upper()
    return any("BENCHMARK" in str(value).upper() or "SECTOR" in str(value).upper()
               for value in (*roles, role))


def fetch_execution_hazards(run, plan, items) -> tuple[str, ...]:
    if run is None:
        return ()
    hazards = []
    if run.status not in {"COMPLETED", "PARTIAL", "FAILED"}:
        hazards.append("FETCH_EXECUTION_NOT_TERMINAL")
    if (getattr(run, "decision_counts_json", None) or {}).get("execution_failure"):
        hazards.append("FETCH_EXECUTION_FAILURE")
    if (run.status == "FAILED" and (run.planned_request_count or 0) > 0
            and (run.executed_request_count or 0) == 0):
        hazards.append("FETCH_BOOTSTRAP_NOT_EXECUTED")
    if any(item.status not in {"SUCCESS", "PARTIAL", "FAILED", "SKIPPED"} for item in items):
        hazards.append("FETCH_ITEMS_NOT_TERMINAL")
    actual = {(item.ticker, item.what_to_show): item for item in items}
    expected_requests = {(item.ticker, item.what_to_show) for item in plan.items
                         if getattr(item, "estimated_request_count", 0) > 0}
    if expected_requests - actual.keys():
        hazards.append("FETCH_ITEM_DIAGNOSTICS_MISSING")
    for expected in plan.items:
        if not required_dependency(expected):
            continue
        item = actual.get((expected.ticker, expected.what_to_show))
        if ((str(expected.action) != "SKIP" and (item is None or item.status != "SUCCESS"))
                or (item is not None and item.status not in {"SUCCESS", "SKIPPED"})):
            hazards.append(f"REQUIRED_DEPENDENCY_FAILED:{expected.ticker}:{expected.what_to_show}")
    return tuple(hazards)


def availability_diagnostics(rows, policy: MarketDataAvailabilityPolicy) -> dict:
    unavailable = [row.ticker for row in rows if known_instrument_unavailability(row)]
    gaps = [row.ticker for row in rows
            if (getattr(row, "evidence_json", None) or {}).get("history_gap_count", 0)]
    hard = hard_readiness_blockers(rows, policy)
    return {
        **policy.as_dict(), "requested_tickers": [row.ticker for row in rows],
        "ready_tickers": [row.ticker for row in rows if row.disposition == "READY"],
        "unavailable_tickers": unavailable, "history_gap_tickers": gaps,
        "hard_blockers": [row.ticker for row in hard],
        "continued_with_gaps": bool(
            policy.allow_history_gaps and (unavailable or gaps) and not hard
        ),
    }
