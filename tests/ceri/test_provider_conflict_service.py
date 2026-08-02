from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.models.ceri_tables import CeriEstimateSnapshot, CeriSourceRecord
from app.services.ceri.provider_conflict_service import CeriProviderConflictService


def test_provider_priority_selection_is_deterministic_and_preserves_competitors() -> None:
    manual = _source(1, "manual")
    primary = _source(2, "primary")
    manual_observation = _estimate(1, Decimal("11.00"))
    primary_observation = _estimate(2, Decimal("12.00"))

    result = CeriProviderConflictService().resolve_estimate(
        [primary_observation, manual_observation],
        {1: manual, 2: primary},
    )

    assert result.selected is manual_observation
    assert result.competing == (primary_observation,)
    assert result.conflict_type == "VALUE_CONFLICT"
    assert result.resolution_reason == "provider_priority_then_quality_then_freshness"


def _source(source_record_id: int, provider: str) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=source_record_id,
        provider=provider,
        dataset="estimates",
        provider_record_id=f"{provider}-1",
        content_hash="hash",
        idempotency_key=f"{provider}-key",
    )


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
        canonical_observation_key="key",
    )
