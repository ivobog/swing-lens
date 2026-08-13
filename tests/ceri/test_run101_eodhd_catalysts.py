from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models.ceri_tables import CeriCatalystEvent, CeriCatalystEventRevision, CeriSourceRecord
from app.services.ceri.catalyst_feature_service import CeriCatalystFeatureService
from app.services.ceri.catalyst_taxonomy import CeriCatalystTaxonomy
from app.services.ceri.dtos import CatalystRequest
from app.services.ceri.providers.eodhd_provider import EodhdCeriProvider


@pytest.mark.parametrize(
    ("symbols", "expected_relevance", "reason"),
    [
        (["TEST.US", "SPY.US"], True, "PROVIDER_RELATED_TICKER_MATCH"),
        (["OTHER.US"], False, "ISSUER_RELEVANCE_MISMATCH"),
        (None, None, "ISSUER_RELEVANCE_UNVERIFIED"),
    ],
)
def test_structured_related_symbols_control_issuer_relevance(
    symbols, expected_relevance, reason
) -> None:
    record = _record(
        {
            "id": "article",
            "title": "Regulatory update",
            "content": "A regulatory decision is expected.",
            "date": "2026-08-13T10:00:00+00:00",
            "relatedTickers": symbols,
        }
    )

    assert record.payload["issuer_relevance"] is expected_relevance
    assert record.payload["issuer_relevance_reason"] == reason


def test_completed_result_article_is_not_pending_binary_risk() -> None:
    source = _record(
        {
            "id": "completed",
            "title": "TEST reports completed quarterly results",
            "content": "The company reported earnings results today.",
            "date": "2026-08-13T10:00:00+00:00",
            "relatedTickers": ["TEST.US"],
            "materiality": 8,
        }
    )

    feature = _feature(source)

    assert source.payload["status"] in {"COMPLETED", "OUTCOME_KNOWN"}
    assert feature.binary_eligible is False
    assert feature.binary_risk_score == 0.0


def test_scheduled_future_binary_event_is_eligible() -> None:
    source = _record(
        {
            "id": "scheduled",
            "title": "Regulatory decision scheduled",
            "content": "The regulator scheduled its decision for September 15.",
            "date": "2026-08-13T10:00:00+00:00",
            "expectedDate": "2026-09-15",
            "relatedTickers": ["TEST.US"],
            "materiality": 7,
            "sentiment": -0.9,
        }
    )

    feature = _feature(source)

    assert source.payload["status"] == "SCHEDULED"
    assert feature.selected is True
    assert feature.binary_eligible is True
    assert feature.binary_risk_score > 0
    assert feature.category == "REGULATORY"


def test_sentiment_cannot_override_evidence_classification() -> None:
    positive = _record(
        {
            "id": "positive-sentiment",
            "title": "Lawsuit settlement completed",
            "content": "The legal settlement was completed.",
            "date": "2026-08-13T10:00:00+00:00",
            "relatedTickers": ["TEST.US"],
            "sentiment": 1.0,
            "materiality": 9,
        }
    )

    feature = _feature(positive)

    assert feature.category == "LEGAL"
    assert feature.binary_eligible is False


def _record(row):
    provider = EodhdCeriProvider(
        client=CatalystClient(row),
        clock=lambda: datetime(2026, 8, 13, 12, tzinfo=UTC),
    )
    return next(provider.fetch_catalysts(CatalystRequest(None, "TEST")))


def _feature(record):
    source = CeriSourceRecord(
        id=1,
        provider="eodhd",
        dataset="catalysts",
        provider_record_id=record.provider_record_id,
        restricted_normalized_json=record.payload,
        observed_at=record.observed_at,
        published_at=record.published_at,
        content_hash="hash",
        idempotency_key="key",
    )
    normalized = CeriCatalystTaxonomy().normalize(source, company_id=42)
    event = CeriCatalystEvent(
        id=1,
        company_id=42,
        category=normalized.category.value,
        subtype=normalized.subtype,
        subject_key=normalized.subject_key,
    )
    revision = CeriCatalystEventRevision(
        id=1,
        catalyst_event_id=1,
        source_record_id=1,
        revision_number=1,
        status=normalized.status.value,
        direction=normalized.direction.value,
        materiality=normalized.materiality,
        date_confidence=normalized.date_confidence.value,
        expected_date=normalized.expected_date,
        issuer_relevance=normalized.issuer_relevance,
        relevance_reason=normalized.relevance_reason,
        binary_eligible=True,
    )
    return CeriCatalystFeatureService().calculate(
        event=event, revision=revision, as_of_session=date(2026, 8, 13)
    )


class CatalystClient:
    def __init__(self, row):
        self.row = row

    def get_json(self, *_args, **_kwargs):
        return [self.row]
