from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriEstimateSnapshot
from app.services.ceri.estimate_deduplicator import CeriEstimateDeduplicator


def test_exact_and_near_estimate_observations_are_grouped_conservatively() -> None:
    first = _estimate(1, Decimal("10.00000"))
    second = _estimate(2, Decimal("10.00005"))
    different = _estimate(3, Decimal("10.50"))

    groups = CeriEstimateDeduplicator().group([first, second, different])

    assert len(groups) == 2
    assert groups[0].canonical.source_record_id == 1
    assert groups[0].duplicate_snapshot_ids == (None,)
    assert len(groups[0].observations) == 2


def _estimate(source_record_id: int, consensus: Decimal) -> CeriEstimateSnapshot:
    return CeriEstimateSnapshot(
        source_record_id=source_record_id,
        company_id=42,
        metric="EPS_DILUTED",
        period_type="ANNUAL",
        fiscal_period_end=date(2026, 12, 31),
        consensus=consensus,
        effective_at=datetime(2026, 8, 3, 12, tzinfo=ZoneInfo("UTC")),
        effective_session=date(2026, 8, 3),
        canonical_observation_key="42:EPS_DILUTED:ANNUAL:2026-12-31:USD:1:2026-08-03",
    )
