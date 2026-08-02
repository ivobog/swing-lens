from __future__ import annotations

from datetime import date

from app.models.ceri_tables import CeriSourceRecord
from app.services.ceri.guidance_normalizer import CeriGuidanceNormalizer


def test_guidance_action_and_values_are_normalized_with_effective_session() -> None:
    source = CeriSourceRecord(
        id=31,
        provider="manual",
        dataset="guidance",
        provider_record_id="guidance-1",
        raw_json={
            "action": "raise",
            "metric": "EPS_DILUTED",
            "period_type": "ANNUAL",
            "low": "10.50",
            "high": "11.25",
            "announced_at": "2026-08-03T16:30:00-04:00",
        },
        content_hash="hash",
        idempotency_key="key",
    )

    guidance = CeriGuidanceNormalizer().normalize(source, company_id=42)

    assert guidance.action == "RAISED"
    assert guidance.metric == "EPS_DILUTED"
    assert guidance.low_value is not None
    assert guidance.high_value is not None
    assert guidance.effective_session == date(2026, 8, 4)
