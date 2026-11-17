from datetime import date

import pytest
from pydantic import ValidationError

from app.services.ceri.batched_workflow import _ensure_ceri_companies
from app.services.ceri.filing_authority import (
    FilingAuthority,
    FilingAuthorityRegistry,
    ReviewedFilingAuthority,
    reviewed_filing_authority,
)


def test_unknown_ticker_does_not_inherit_an_exemption() -> None:
    assert reviewed_filing_authority("UNKNOWN") is None
    assert reviewed_filing_authority("TOWN.A") is None


def test_reviewed_bank_reports_its_authority_and_coverage_limit() -> None:
    entry = reviewed_filing_authority(" town ")
    assert entry.authority is FilingAuthority.FDIC
    assert not entry.sec_required
    assert entry.reviewed_on == date(2026, 11, 7)
    assert "not currently supported" in entry.applicability_reason


def test_nbn_retains_reviewed_fdic_authority_instead_of_legacy_sec_identity() -> None:
    entry = reviewed_filing_authority(" nbn ")
    assert entry.authority is FilingAuthority.FDIC
    assert entry.authority_id == "19690"
    assert not entry.sec_required
    assert entry.reviewed_on == date(2026, 11, 9)
    assert "former holding company" in entry.applicability_reason
    assert "not currently supported" in entry.applicability_reason
    assert any("financial-information/fdic" in str(url) for url in entry.sources)


@pytest.mark.parametrize(
    "change",
    [
        {"sources": []},
        {"authority": "UNKNOWN"},
        {"authority_id": ""},
        {"reason": ""},
        {"reviewed_on": None},
        {"ticker": "town"},
    ],
)
def test_unreviewed_or_incomplete_exemption_is_rejected(change) -> None:
    entry = reviewed_filing_authority("TOWN").model_dump()
    with pytest.raises(ValidationError):
        ReviewedFilingAuthority.model_validate({**entry, **change})


def test_conflicting_registry_entries_are_rejected() -> None:
    entry = reviewed_filing_authority("TOWN")
    with pytest.raises(ValidationError, match="duplicate tickers"):
        FilingAuthorityRegistry(schema_version=1, companies=(entry, entry))


def test_onboarding_retains_non_sec_classification_and_other_provider_mapping() -> None:
    class Db:
        def __init__(self):
            self.rows = []

        def scalars(self, _statement):
            return self.rows

        def add(self, row):
            self.rows.append(row)

        def flush(self):
            pass

    db = Db()
    _ensure_ceri_companies(db, ["TOWN", "UNKNOWN"])
    town, unknown = db.rows
    assert town.sec_applicability == "NOT_APPLICABLE"
    assert "FDIC" in town.sec_applicability_reason
    assert town.current_provider_ids_json == {"eodhd": "TOWN.US"}
    assert unknown.sec_applicability != "NOT_APPLICABLE"


def test_nbn_onboarding_preserves_other_provider_scope_and_current_reporting_limit() -> None:
    class Db:
        def __init__(self):
            self.rows = []

        def scalars(self, _statement):
            return self.rows

        def add(self, row):
            self.rows.append(row)

        def flush(self):
            pass

    db = Db()
    _ensure_ceri_companies(db, ["NBN"])
    nbn = db.rows[0]
    assert nbn.ticker == "NBN"
    assert nbn.sec_applicability == "NOT_APPLICABLE"
    assert "FDIC reporting issuer; authority ID 19690" in nbn.sec_applicability_reason
    assert nbn.cik is None
    assert nbn.current_provider_ids_json == {"eodhd": "NBN.US"}
