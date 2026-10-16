import json
from pathlib import Path

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from app.services.producer_readiness import (
    ConsumerEligibilityStatus,
    ReadinessStatus,
    normalize_producer_readiness,
)
from app.services.warning_taxonomy import (
    WARNING_TAXONOMY_VERSION,
    WarningCategory,
    classify_warning,
    classify_warnings,
    warnings_preserve_integrity_readiness,
)
from app.services.winner_probability.consumer_eligibility import (
    COMBINED_TO_WINNER,
    RANKING_TO_WINNER,
)

FIXTURE = Path(__file__).parent / "fixtures" / "readiness_phase2" / "run_33.json"


def test_investment_and_strategy_warnings_preserve_integrity_readiness() -> None:
    values = classify_warnings(["value_trap_risk", "stage_gate_blocked"])

    assert [item.category for item in values] == [
        WarningCategory.STRATEGY_RESTRICTION,
        WarningCategory.INVESTMENT_RISK,
    ]
    assert warnings_preserve_integrity_readiness(["value_trap_risk", "stage_gate_blocked"])
    assert {item.catalog_version for item in values} == {WARNING_TAXONOMY_VERSION}


def test_market_distribution_warnings_are_visible_nonblocking_strategy_restrictions() -> None:
    values = classify_warnings(["spy_distribution", "qqq_distribution"])

    assert WARNING_TAXONOMY_VERSION == "warning-taxonomy-v2"
    assert {item.original_code for item in values} == {
        "spy_distribution",
        "qqq_distribution",
    }
    assert all(item.category is WarningCategory.STRATEGY_RESTRICTION for item in values)
    assert all(item.blocks_integrity_readiness is False for item in values)
    assert warnings_preserve_integrity_readiness(
        ["spy_distribution", "qqq_distribution", "market_risk_off"]
    )


def test_market_regime_warning_vocabulary_is_explicitly_classified() -> None:
    emitted_codes = {
        "market_risk_off",
        "low_market_confidence",
        "mixed_benchmark_sessions",
        "missing_qqq_market_data",
        "missing_spy_market_data",
        "qqq_distribution",
        "severely_stale_market_data",
        "spy_distribution",
        "stale_market_data",
        "stale_qqq_market_data",
        "stale_spy_market_data",
    }

    assert all(classify_warning(code).classified for code in emitted_codes)
    assert classify_warning("missing_qqq_market_data").blocks_integrity_readiness is True
    assert classify_warning("stale_qqq_market_data").blocks_integrity_readiness is True


def test_unknown_warning_is_retained_and_fails_closed() -> None:
    value = classify_warning("new_unreviewed_warning")

    assert value.original_code == "new_unreviewed_warning"
    assert value.category is WarningCategory.UNKNOWN
    assert value.blocks_integrity_readiness is True
    assert not warnings_preserve_integrity_readiness(["new_unreviewed_warning"])


def test_completeness_and_integrity_warnings_remain_blocking() -> None:
    for code in ("missing_fundamental", "technical_error", "stale_market_data"):
        assert classify_warning(code).blocks_integrity_readiness is True


def test_run_33_warning_fixture_proves_prospective_fix_without_reinterpretation() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    expected_fingerprint = payload.pop("fixture_fingerprint")
    assert Canonical.fingerprint(payload) == expected_fingerprint

    complete_with_warnings = [
        row for row in payload["combined"] if row["is_complete"] and row["has_warning"]
    ]
    prospective = [
        normalize_producer_readiness(
            "COMBINED",
            {
                "is_complete": row["is_complete"],
                "warning_flags_json": row["warning_flags_json"],
            },
            identity_fingerprint=row["calculation_identity_fingerprint"],
        )
        for row in complete_with_warnings
    ]

    assert len(complete_with_warnings) == 51
    assert all(row["producer_readiness"]["status"] == "DEGRADED" for row in complete_with_warnings)
    assert all(item.status is ReadinessStatus.READY for item in prospective)
    assert sum(not row["is_complete"] for row in payload["combined"]) == 53


def test_complete_risk_warning_sources_remain_consumable_by_winner() -> None:
    combined = normalize_producer_readiness(
        "COMBINED",
        {"is_complete": True, "warning_flags_json": ["high_leverage"]},
        identity_fingerprint="a" * 64,
    )
    ranking = normalize_producer_readiness(
        "RANKING",
        {"is_complete": True, "warning_flags_json": ["earnings_blocked"]},
        identity_fingerprint="b" * 64,
    )

    assert COMBINED_TO_WINNER.evaluate(combined).status is ConsumerEligibilityStatus.ELIGIBLE
    assert RANKING_TO_WINNER.evaluate(ranking).status is ConsumerEligibilityStatus.ELIGIBLE
