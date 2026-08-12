from __future__ import annotations

from datetime import date

from app.models.ceri_tables import CeriCatalystEvent, CeriCatalystEventRevision
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService


def test_catalyst_feature_calculates_materiality_binary_risk_and_conflicts() -> None:
    event = CeriCatalystEvent(
        id=9,
        company_id=42,
        category="REGULATORY",
        subtype="approval",
        subject_key="pdufa-date",
    )
    revision = CeriCatalystEventRevision(
        id=11,
        catalyst_event_id=9,
        revision_number=1,
        status="SCHEDULED",
        direction="POSITIVE",
        materiality=4.0,
        expected_date=date(2026, 8, 20),
        date_confidence="DATE_RANGE",
        source_confidence="Normal",
        conflict_flags_json=["conflicting_event_dates"],
        issuer_relevance=True,
        relevance_reason="PROVIDER_RELATED_TICKER_MATCH",
    )

    feature = CeriCatalystFeatureService().calculate(
        event=event,
        revision=revision,
        as_of_session=date(2026, 8, 1),
    )

    assert feature.opportunity_component > 0
    assert feature.binary_risk_score > 0
    assert feature.conflict_penalty > 0
    assert "catalyst_conflicts_present" in feature.warnings
    assert "catalyst_date_confidence_low" in feature.warnings
