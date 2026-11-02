from copy import deepcopy

import pytest

from app.services.ib_market_intelligence.activation_assessment import (
    REQUIRED_STAGES,
    ZERO_COUNTERS,
    assess_activation_manifest,
)


def retained_advisory_manifest():
    return {
        "business_status": "PARTIAL",
        "tickers": ["AAPL"],
        "stages": [
            {"name": name, "status": "COMPLETED", "error": None} for name in REQUIRED_STAGES
        ],
        "counters": dict.fromkeys(ZERO_COUNTERS, 0),
        "checks": dict.fromkeys(
            (
                "certified_sources",
                "scope_complete",
                "fundamental_technical_admitted",
                "ranking_complete",
                "ibmi_included_fresh_compatible",
                "arithmetic_reconciled",
                "native_data_complete",
                "policy_exclusions_accounted",
                "partial_reasons_accounted",
            ),
            True,
        ),
        "warnings": [
            {"kind": "TECHNICAL", "code": "market_risk_off"},
            {"kind": "FUNDAMENTAL", "code": "balance_sheet_stress"},
        ],
        "evidence": [{"id": 123, "fingerprint": "a" * 64}],
        "unexplained_reasons": [],
    }


def test_advisory_acceptance_keeps_partial_and_grants_no_trading_authority():
    manifest = retained_advisory_manifest()
    original = deepcopy(manifest)
    decision = assess_activation_manifest(manifest)
    assert decision["accepted"] and decision["business_status"] == "PARTIAL"
    assert decision["trading_authority"] is False
    assert manifest == original
    assert decision["configuration"]["semantic_hash"]


@pytest.mark.parametrize(
    "warning",
    [
        "new_unknown_warning",
        "stale_market_data",
        "missing_technical",
        "low_technical_confidence",
        "insufficient_data",
        "integrity_error",
    ],
)
def test_unknown_or_mixed_integrity_data_warnings_block(warning):
    manifest = retained_advisory_manifest()
    manifest["warnings"].append({"kind": "TECHNICAL", "code": warning})
    assert not assess_activation_manifest(manifest)["accepted"]


@pytest.mark.parametrize("counter", ZERO_COUNTERS)
def test_any_real_failure_or_incompleteness_counter_blocks(counter):
    manifest = retained_advisory_manifest()
    manifest["counters"][counter] = 1
    assert not assess_activation_manifest(manifest)["accepted"]
    manifest["counters"].pop(counter)
    assert not assess_activation_manifest(manifest)["accepted"]


@pytest.mark.parametrize("check", retained_advisory_manifest()["checks"])
def test_missing_required_proof_cannot_be_replaced_by_partial_label(check):
    manifest = retained_advisory_manifest()
    manifest["checks"][check] = False
    assert not assess_activation_manifest(manifest)["accepted"]
    manifest["checks"].pop(check)
    assert not assess_activation_manifest(manifest)["accepted"]


@pytest.mark.parametrize(
    "change",
    ["failed_stage", "missing_stage", "error_on_completed", "unexplained", "failed_business"],
)
def test_execution_and_unexplained_reasons_block(change):
    manifest = retained_advisory_manifest()
    if change == "failed_stage":
        manifest["stages"][0]["status"] = "FAILED"
    elif change == "missing_stage":
        manifest["stages"].pop()
    elif change == "error_on_completed":
        manifest["stages"][0]["error"] = "provider failure"
    elif change == "unexplained":
        manifest["unexplained_reasons"].append("unclassified exclusion")
    else:
        manifest["business_status"] = "FAILED"
    assert not assess_activation_manifest(manifest)["accepted"]
