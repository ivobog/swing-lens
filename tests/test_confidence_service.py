from decimal import Decimal

from app.models.tables import FundamentalScore, TechnicalScore
from app.services.confidence_service import (
    MissingSeverity,
    build_combined_warning_flags,
    fundamental_missing_severity,
    technical_confidence_from_score,
)


def test_fundamental_missing_severity_classifies_critical_fields() -> None:
    assert fundamental_missing_severity(set()) == MissingSeverity.NONE
    assert fundamental_missing_severity({"pe_ratio"}) == MissingSeverity.LOW
    assert fundamental_missing_severity({"fcf_ttm"}) == MissingSeverity.CRITICAL
    assert fundamental_missing_severity({"a", "b", "c"}) == MissingSeverity.MEDIUM
    assert fundamental_missing_severity({"a", "b", "c", "d", "e", "f"}) == MissingSeverity.HIGH


def test_technical_confidence_reports_missing_benchmark_data() -> None:
    score = TechnicalScore(
        run_id=1,
        ticker="MSFT",
        technical_confidence="normal",
        insufficient_data=False,
        missing_data_json={"missing_benchmark_data": True},
    )

    assert technical_confidence_from_score(score) == "missing_benchmark_data"


def test_combined_warning_flags_include_sort_bucket() -> None:
    flags = build_combined_warning_flags(
        fundamental=FundamentalScore(
            run_id=1,
            ticker="MSFT",
            fundamental_score=Decimal("8.0"),
        ),
        technical=TechnicalScore(
            run_id=1,
            ticker="MSFT",
            dual_score=Decimal("8.0"),
            insufficient_data=False,
        ),
        decision="Strong candidate",
    )

    assert flags.is_complete
    assert flags.sort_bucket == 10
