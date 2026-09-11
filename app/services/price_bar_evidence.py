from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.models.tables import PriceBar
from app.services.canonical_evidence import CanonicalEvidenceSerializer

PRICE_BAR_FIELD_CLASSIFICATION: dict[str, str] = {
    "id": "ADMINISTRATIVE_METADATA",
    "ticker": "IMMUTABLE_MARKET_EVIDENCE",
    "bar_date": "IMMUTABLE_MARKET_EVIDENCE",
    "timeframe": "IMMUTABLE_MARKET_EVIDENCE",
    "open": "IMMUTABLE_MARKET_EVIDENCE",
    "high": "IMMUTABLE_MARKET_EVIDENCE",
    "low": "IMMUTABLE_MARKET_EVIDENCE",
    "close": "IMMUTABLE_MARKET_EVIDENCE",
    "volume": "IMMUTABLE_MARKET_EVIDENCE",
    "source": "IMMUTABLE_MARKET_EVIDENCE",
    "what_to_show": "IMMUTABLE_MARKET_EVIDENCE",
    "adjustment_type": "IMMUTABLE_MARKET_EVIDENCE",
    "created_at": "IMMUTABLE_PIT_PROVENANCE",
    "first_seen_at": "IMMUTABLE_PIT_PROVENANCE",
    "last_seen_at": "MUTABLE_OPERATIONAL_METADATA",
    "revised_at": "IMMUTABLE_PIT_PROVENANCE",
    "revision_count": "IMMUTABLE_PIT_PROVENANCE",
    "data_hash": "IMMUTABLE_PIT_PROVENANCE",
}

PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS = tuple(
    name
    for name, classification in PRICE_BAR_FIELD_CLASSIFICATION.items()
    if classification in {"IMMUTABLE_MARKET_EVIDENCE", "IMMUTABLE_PIT_PROVENANCE"}
)


def price_bar_immutable_evidence_manifest(row: PriceBar) -> dict[str, Any]:
    """Return the evidence whose mutation can change historical/PIT meaning.

    ``last_seen_at`` is deliberately absent: the cache advances it when a provider
    re-observes an identical bar.  Identity, values, first-known time, and every
    revision marker remain covered.
    """

    return {
        "contract": "price-bar-immutable-evidence-v1",
        "fields": {name: getattr(row, name, None) for name in PRICE_BAR_IMMUTABLE_EVIDENCE_FIELDS},
    }


def price_bar_immutable_evidence_hash(row: PriceBar) -> str:
    return CanonicalEvidenceSerializer.fingerprint(price_bar_immutable_evidence_manifest(row))


def price_bar_full_row_diagnostic_hash(row: PriceBar) -> str:
    """Diagnostic only; never use this as the historical-integrity pass/fail gate."""

    return CanonicalEvidenceSerializer.fingerprint(
        {
            "contract": "price-bar-full-row-diagnostic-v1",
            "fields": {
                column.name: getattr(row, column.name, None)
                for column in PriceBar.__table__.columns
            },
        }
    )


def price_bar_immutable_evidence_set_hash(rows: Iterable[PriceBar]) -> str:
    """Hash a bar set canonically without mutable operational metadata."""

    manifests = [price_bar_immutable_evidence_manifest(row) for row in rows]
    manifests.sort(
        key=lambda item: tuple(
            str(item["fields"].get(name))
            for name in ("ticker", "bar_date", "timeframe", "what_to_show", "source")
        )
    )
    return CanonicalEvidenceSerializer.fingerprint(manifests)
