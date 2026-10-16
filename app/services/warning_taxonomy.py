"""Versioned warning taxonomy used by readiness and consumer policy.

Original warning codes are retained. Unknown codes deliberately fail closed and may
only be made advisory by adding an exact catalog entry under a new catalog version.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

WARNING_TAXONOMY_VERSION = "warning-taxonomy-v1"


class WarningCategory(StrEnum):
    INTEGRITY = "INTEGRITY"
    TEMPORAL = "TEMPORAL"
    DATA_QUALITY = "DATA_QUALITY"
    ANALYTICAL_COMPLETENESS = "ANALYTICAL_COMPLETENESS"
    INVESTMENT_RISK = "INVESTMENT_RISK"
    STRATEGY_RESTRICTION = "STRATEGY_RESTRICTION"
    OPERATIONAL = "OPERATIONAL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class WarningDefinition:
    code: str
    category: WarningCategory
    blocks_integrity_readiness: bool
    description: str


@dataclass(frozen=True)
class ClassifiedWarning:
    original_code: str
    normalized_code: str
    category: WarningCategory
    blocks_integrity_readiness: bool
    catalog_version: str = WARNING_TAXONOMY_VERSION

    @property
    def classified(self) -> bool:
        return self.category is not WarningCategory.UNKNOWN

    def canonical_payload(self) -> dict[str, object]:
        return {
            "original_code": self.original_code,
            "normalized_code": self.normalized_code,
            "category": self.category.value,
            "blocks_integrity_readiness": self.blocks_integrity_readiness,
            "catalog_version": self.catalog_version,
        }


def _definitions(
    category: WarningCategory,
    codes: Iterable[str],
    *,
    blocking: bool,
    description: str,
) -> list[WarningDefinition]:
    return [WarningDefinition(code, category, blocking, description) for code in codes]


_CATALOG_ROWS = [
    *_definitions(
        WarningCategory.INVESTMENT_RISK,
        (
            "asset_growth_without_returns",
            "balance_sheet_stress",
            "capital_efficiency_deterioration",
            "dividend_payout_risk",
            "earnings_high_risk",
            "earnings_medium_risk",
            "earnings_quality_risk",
            "extreme_dilution",
            "extreme_valuation",
            "forward_quality_weak",
            "growth_trap_risk",
            "high_accrual_risk",
            "high_leverage",
            "liquidity_buffer_weak",
            "liquidity_warning",
            "negative_fcf",
            "negative_free_cash_flow",
            "overheated_momentum",
            "poor_cash_conversion",
            "quality_risk",
            "share_dilution",
            "value_trap_risk",
            "weak_liquidity",
        ),
        blocking=False,
        description="Adverse investment observation retained as an analytical feature.",
    ),
    *_definitions(
        WarningCategory.STRATEGY_RESTRICTION,
        (
            "blowoff_top",
            "box_failure",
            "climax_reversal_risk",
            "climax_risk",
            "danger_classification",
            "distribution_risk",
            "earnings_blocked",
            "failed_breakout",
            "fundamental_floor_failed",
            "heavy_distribution",
            "late_stage_extension",
            "market_risk_off",
            "stage_3_distribution",
            "stage_4_downtrend",
            "stage_gate_blocked",
        ),
        blocking=False,
        description="Strategy/trade restriction that does not invalidate research evidence.",
    ),
    *_definitions(
        WarningCategory.ANALYTICAL_COMPLETENESS,
        (
            "analyst_sample_unavailable",
            "catalysts_unavailable",
            "earnings_date_unavailable",
            "guidance_unavailable",
            "incomplete_data",
            "insufficient_history",
            "missing_benchmark_data",
            "missing_fundamental",
            "missing_market_data",
            "missing_technical",
            "opportunity_component_coverage_insufficient",
            "price_response_unavailable",
            "surprise_trend_unavailable",
        ),
        blocking=True,
        description="Required analytical information is absent or incomplete.",
    ),
    *_definitions(
        WarningCategory.DATA_QUALITY,
        (
            "analyst_sample_sparse",
            "baseline_unavailable",
            "catalyst_date_confidence_low",
            "current_snapshot_unavailable",
            "dataset_freshness_unavailable",
            "estimate_coverage_low",
            "guidance_rows_rejected",
            "low_data_quality",
            "low_technical_confidence",
            "revision_component_coverage_low",
        ),
        blocking=True,
        description="Evidence exists but its analytical quality is limited.",
    ),
    *_definitions(
        WarningCategory.TEMPORAL,
        (
            "data_stale",
            "estimate_data_stale",
            "severely_stale_market_data",
            "stale_market_data",
        ),
        blocking=True,
        description="Evidence freshness or temporal validity is not satisfied.",
    ),
    *_definitions(
        WarningCategory.INTEGRITY,
        (
            "catalyst_conflicts_present",
            "identity_coverage_incomplete",
            "source_conflict",
            "technical_error",
            "value_conflict",
        ),
        blocking=True,
        description="Evidence identity, consistency, or calculation integrity is unsafe.",
    ),
    *_definitions(
        WarningCategory.OPERATIONAL,
        ("credentials_missing", "provider_error", "provider_unavailable"),
        blocking=True,
        description="Operational acquisition or provider execution did not complete safely.",
    ),
]

WARNING_CATALOG: dict[str, WarningDefinition] = {row.code: row for row in _CATALOG_ROWS}
if len(WARNING_CATALOG) != len(_CATALOG_ROWS):  # pragma: no cover - import-time invariant
    raise RuntimeError("warning taxonomy contains duplicate codes")


def normalize_warning_code(code: object) -> str:
    return str(code).strip().casefold()


def classify_warning(code: object) -> ClassifiedWarning:
    original = str(code)
    normalized = normalize_warning_code(code)
    definition = WARNING_CATALOG.get(normalized)
    if definition is None:
        return ClassifiedWarning(
            original_code=original,
            normalized_code=normalized,
            category=WarningCategory.UNKNOWN,
            blocks_integrity_readiness=True,
        )
    return ClassifiedWarning(
        original_code=original,
        normalized_code=normalized,
        category=definition.category,
        blocks_integrity_readiness=definition.blocks_integrity_readiness,
    )


def classify_warnings(codes: Iterable[object]) -> tuple[ClassifiedWarning, ...]:
    values = {
        normalize_warning_code(code): classify_warning(code)
        for code in codes
        if str(code).strip()
    }
    return tuple(values[key] for key in sorted(values))


def warnings_preserve_integrity_readiness(codes: Iterable[object]) -> bool:
    classified = classify_warnings(codes)
    return bool(classified) and all(not item.blocks_integrity_readiness for item in classified)
