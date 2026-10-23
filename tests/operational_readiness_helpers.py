from datetime import UTC, datetime

from core_readiness_helpers import seal_core

from app.models.tables import FundamentalScore


def certified_fundamental(**values):
    from app.services.fundamental_admission_policy import FUNDAMENTAL_COMPONENTS

    values.setdefault("data_coverage_score", 10)
    values.setdefault("run_id", 1)
    values.setdefault("fundamental_label", "High-quality quant")
    values.setdefault("scoring_model_version", "fundamentals_v2.1")
    values.setdefault("missing_data_penalty", 0)
    values.setdefault("v2_warning_flags_json", {"flags": []})
    debug = dict(values.get("debug_json") or {})
    debug.setdefault(
        "coverage",
        {
            "available_scoring_fields": 79,
            "total_scoring_fields": 79,
            "coverage_ratio": 1.0,
            "data_coverage_score": 10.0,
            "missing_fields_by_priority": {
                "critical": [],
                "high": [],
                "medium": [],
                "low": [],
            },
        },
    )
    debug.setdefault("component_coverage", dict.fromkeys(FUNDAMENTAL_COMPONENTS, 1.0))
    debug.setdefault("parse_diagnostics", {"failed_field_count": 0, "failed_fields": []})
    values["debug_json"] = debug
    row = seal_core(FundamentalScore(**values))
    row.calculation_evidence.calculated_at = datetime(2026, 9, 11, 18, tzinfo=UTC)
    return row
