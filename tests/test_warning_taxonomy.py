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

    assert WARNING_TAXONOMY_VERSION == "warning-taxonomy-v4"
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


def test_ranking_native_warning_vocabulary_is_explicitly_classified() -> None:
    emitted_codes = {
        "climax_risk",
        "danger_classification",
        "distribution_risk",
        "earnings_blocked",
        "earnings_high_risk",
        "earnings_medium_risk",
        "extreme_dilution",
        "failed_breakout",
        "fundamental_floor_failed",
        "growth_trap_risk",
        "ibkr_tradeability_penalty",
        "late_stage_extension",
        "liquidity_warning",
        "low_data_quality",
        "missing_fundamental",
        "missing_technical",
        "negative_fcf",
        "overheated_momentum",
        "quality_risk",
        "value_trap_risk",
    }

    assert all(classify_warning(code).classified for code in emitted_codes)


def test_run33_and_run36_native_warning_vocabularies_are_explicitly_classified() -> None:
    emitted_codes = {
        "earnings_date_missing",
        "earnings_date_unparseable",
        "low_confidence_sector",
        "missing_ranking_profile_results",
        "sector_risk_off",
        "missing_optional_context",
        "missing_sector_rotation",
        "missing_sector_rotation_context",
        "no_eligible_estimate_input",
    }

    assert all(classify_warning(code).classified for code in emitted_codes)
    for code in {
        "earnings_date_missing",
        "earnings_date_unparseable",
        "low_confidence_sector",
        "missing_ranking_profile_results",
        "no_eligible_estimate_input",
    }:
        assert classify_warning(code).blocks_integrity_readiness is True
    for code in {
        "sector_risk_off",
        "missing_optional_context",
        "missing_sector_rotation",
        "missing_sector_rotation_context",
    }:
        assert classify_warning(code).blocks_integrity_readiness is False


def test_unknown_warning_is_retained_and_fails_closed() -> None:
    value = classify_warning("new_unreviewed_warning")

    assert value.original_code == "new_unreviewed_warning"
    assert value.category is WarningCategory.UNKNOWN
    assert value.blocks_integrity_readiness is True
    assert not warnings_preserve_integrity_readiness(["new_unreviewed_warning"])


def test_run36_historical_value_trap_warning_cases_remain_valid_winner_evidence() -> None:
    cases = {
        "ASC": [
            "liquidity_warning",
            "market_risk_off",
            "poor_cash_conversion",
            "value_trap_risk",
            "value_trap",
        ],
        "AUPH": [
            "earnings_quality_risk",
            "liquidity_warning",
            "market_risk_off",
            "poor_cash_conversion",
            "value_trap_risk",
            "value_trap",
        ],
        "DVN": [
            "liquidity_buffer_weak",
            "market_risk_off",
            "poor_cash_conversion",
            "value_trap_risk",
            "value_trap",
        ],
        "FRO": [
            "distribution_risk",
            "failed_breakout",
            "market_risk_off",
            "poor_cash_conversion",
            "value_trap_risk",
            "value_trap",
            "danger_classification",
        ],
        "IQV": [
            "balance_sheet_stress",
            "liquidity_buffer_weak",
            "market_risk_off",
            "value_trap_risk",
            "value_trap",
        ],
    }

    for ticker, warning_codes in cases.items():
        readiness = normalize_producer_readiness(
            "RANKING",
            {"is_complete": True, "warning_flags_json": warning_codes},
            identity_fingerprint=(ticker.casefold() * 64)[:64],
        )
        assert readiness.status is ReadinessStatus.READY
        assert RANKING_TO_WINNER.evaluate(readiness).status is ConsumerEligibilityStatus.ELIGIBLE
        assert all(classify_warning(code).classified for code in warning_codes)

    historical = classify_warning("value_trap")
    assert historical.category is WarningCategory.INVESTMENT_RISK
    assert historical.blocks_integrity_readiness is False


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
