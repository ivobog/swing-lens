from __future__ import annotations

from datetime import date

from app.models.ceri_tables import CeriCompany, CeriCompanyAlias, CeriSourceRecord
from app.services.ceri.identity_resolver import CeriIdentityResolver


def test_identity_ambiguity_quarantines_record() -> None:
    source = _source({"ticker": "ABC"})
    resolver = CeriIdentityResolver(
        companies=[
            CeriCompany(id=1, ticker="ABC", exchange="NYSE"),
            CeriCompany(id=2, ticker="ABC", exchange="NASDAQ"),
        ]
    )

    result = resolver.resolve_source_record(FakeDb(), source)

    assert result.status == "AMBIGUOUS"
    assert source.quarantine_reason == "identity_ambiguous"


def test_historical_alias_validity_dates_are_respected() -> None:
    source = _source({"ticker": "OLD"}, source_date=date(2025, 6, 15))
    resolver = CeriIdentityResolver(
        companies=[
            CeriCompany(id=1, ticker="NEW", exchange="NYSE"),
            CeriCompany(id=2, ticker="OTHER", exchange="NYSE"),
        ],
        aliases=[
            CeriCompanyAlias(
                company_id=1,
                provider="manual",
                alias_type="ticker",
                alias_value="OLD",
                valid_from=date(2020, 1, 1),
                valid_to=date(2025, 12, 31),
            ),
            CeriCompanyAlias(
                company_id=2,
                provider="manual",
                alias_type="ticker",
                alias_value="OLD",
                valid_from=date(2026, 1, 1),
            ),
        ],
    )

    result = resolver.resolve_source_record(FakeDb(), source)

    assert result.resolved is True
    assert result.company_id == 1


def test_prepare_prefetches_companies_and_aliases_only_once() -> None:
    company = CeriCompany(id=1, ticker="ABC", exchange="NYSE")
    alias = CeriCompanyAlias(
        id=2,
        company_id=1,
        provider="manual",
        alias_type="ticker",
        alias_value="OLD",
        confidence="High",
    )
    db = PrefetchDb([[company], [alias]])
    resolver = CeriIdentityResolver()

    resolver.prepare(db)
    resolver.prepare(db)
    result = resolver.resolve_source_record(db, _source({"ticker": "OLD"}))

    assert result.company_id == 1
    assert db.select_count == 2


def _source(payload: dict, source_date: date | None = None) -> CeriSourceRecord:
    return CeriSourceRecord(
        id=10,
        provider="manual",
        dataset="estimates",
        provider_record_id="record-1",
        company_hint_json={"ticker": payload.get("ticker")},
        raw_json={**payload, "source_date": source_date.isoformat() if source_date else None},
        content_hash="hash",
        idempotency_key="key",
    )


class FakeDb:
    pass


class PrefetchRows:
    def __init__(self, rows) -> None:
        self._rows = rows

    def all(self):
        return self._rows


class PrefetchDb:
    def __init__(self, rows) -> None:
        self._rows = list(rows)
        self.select_count = 0

    def scalars(self, _statement):
        self.select_count += 1
        return PrefetchRows(self._rows.pop(0))
