from __future__ import annotations

from datetime import date

from app.models.ceri_tables import CeriRevisionFeature
from app.services.ceri.confidence_service import CeriConfidenceService


def test_high_staleness_prevents_high_confidence() -> None:
    feature = _feature(as_of_session=date(2026, 7, 1), upward_count=6, downward_count=2)

    result = CeriConfidenceService().calculate(
        as_of_session=date(2026, 8, 1),
        revision_features=[feature],
    )

    assert result.label.value != "High"
    assert "estimate_data_stale" in result.warnings


def test_sparse_analyst_coverage_lowers_confidence() -> None:
    feature = _feature(upward_count=1, downward_count=0)

    result = CeriConfidenceService().calculate(
        as_of_session=date(2026, 8, 1),
        revision_features=[feature],
    )

    assert result.label.value in {"Low", "Normal"}
    assert "analyst_sample_sparse" in result.warnings


def _feature(
    *,
    as_of_session: date = date(2026, 8, 1),
    upward_count: int,
    downward_count: int,
) -> CeriRevisionFeature:
    return CeriRevisionFeature(
        company_id=42,
        metric="EPS_DILUTED",
        period_key="key",
        as_of_session=as_of_session,
        window_days=30,
        actual_elapsed_days=30,
        pct_change=0.1,
        upward_count=upward_count,
        downward_count=downward_count,
        config_version="2026-07-31",
        config_hash="hash",
        calculation_version="ceri-1.0.0",
    )
