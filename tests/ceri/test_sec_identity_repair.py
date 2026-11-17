from app.models.ceri_tables import CeriCompany
from app.services.ceri.sec.client import SecClientConfig
from app.services.ceri.sec.identity_repair import resolve_and_persist_sec_identity
from app.services.ceri.sec.provider import SecCeriProvider


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.added = []

    def scalars(self, _statement):
        return _Scalars(self.rows)

    def add(self, row):
        self.added.append(row)

    def flush(self):
        pass


class _Provider:
    def __init__(self, cik):
        self.cik = cik
        self.calls = []

    def resolve_cik(self, ticker):
        self.calls.append(ticker)
        return self.cik


def test_missing_company_mapping_is_resolved_by_exact_sec_ticker() -> None:
    db = _Db()
    provider = _Provider("123456")

    result = resolve_and_persist_sec_identity(db, provider=provider, ticker=" msft ")

    assert result.status == "RESOLVED"
    assert result.cik == "0000123456"
    assert provider.calls == ["MSFT"]
    assert len(db.added) == 1
    assert db.added[0].ticker == "MSFT"
    assert db.added[0].cik == "0000123456"


def test_ambiguous_persisted_ciks_are_not_guessed_or_overwritten() -> None:
    rows = [
        CeriCompany(ticker="ABC", exchange="US", cik="0000000001"),
        CeriCompany(ticker="ABC", exchange="NYSE", cik="0000000002"),
    ]
    db = _Db(rows)
    provider = _Provider("3")

    result = resolve_and_persist_sec_identity(db, provider=provider, ticker="ABC")

    assert result.status == "AMBIGUOUS"
    assert "Conflicting persisted CIK" in (result.reason or "")
    assert provider.calls == []
    assert db.added == []


def test_missing_exact_sec_ticker_is_reported_without_placeholder_mapping() -> None:
    db = _Db()
    provider = _Provider(None)

    result = resolve_and_persist_sec_identity(db, provider=provider, ticker="NOPE")

    assert result.status == "UNRESOLVED"
    assert result.cik is None
    assert db.added == []


def test_reviewed_fdic_issuer_is_persisted_without_sec_request_or_placeholder_cik() -> None:
    db = _Db()
    provider = _Provider(None)

    result = resolve_and_persist_sec_identity(db, provider=provider, ticker=" town ")

    assert result.status == "NOT_APPLICABLE"
    assert result.resolved
    assert result.cik is None
    assert provider.calls == []
    company = db.added[0]
    assert company.ticker == "TOWN"
    assert company.company_name == "TowneBank"
    assert company.cik is None
    assert company.sec_applicability == "NOT_APPLICABLE"
    assert "FDIC" in company.sec_applicability_reason
    assert "35095" in company.sec_applicability_reason
    assert "https://investor.townebank.com/" in company.sec_applicability_reason
    assert company.sec_applicability_updated_at is not None


def test_reviewed_classification_preserves_existing_other_provider_ids() -> None:
    company = CeriCompany(
        ticker="TOWN", exchange="US", sec_applicability="REQUIRED",
        current_provider_ids_json={"eodhd": "TOWN.US"},
    )
    db = _Db([company])
    provider = _Provider(None)

    result = resolve_and_persist_sec_identity(db, provider=provider, ticker="TOWN")

    assert result.resolved
    assert company.sec_applicability == "NOT_APPLICABLE"
    assert company.current_provider_ids_json == {"eodhd": "TOWN.US"}
    assert not db.added
    assert not provider.calls


def test_explicit_coverage_exemption_is_not_overwritten_by_sec_lookup() -> None:
    company = CeriCompany(
        ticker="EXEMPT", exchange="US", sec_applicability="NOT_APPLICABLE",
        sec_applicability_reason="Reviewed non-SEC issuer",
    )
    provider = _Provider("123456")

    result = resolve_and_persist_sec_identity(_Db([company]), provider=provider, ticker="EXEMPT")

    assert result.status == "NOT_APPLICABLE"
    assert not provider.calls
    assert company.cik is None
    assert company.sec_applicability_reason == "Reviewed non-SEC issuer"


def test_sec_share_class_alias_persists_only_the_canonical_ticker() -> None:
    class Client:
        config = SecClientConfig()
        requests = 0
        failures = 0
        last_success_at = None

        def company_tickers(self):
            return {"0": {"ticker": "MOG-A", "cik_str": 67887}}

    db = _Db()
    provider = SecCeriProvider(client=Client())

    result = resolve_and_persist_sec_identity(db, provider=provider, ticker="MOG.A")

    assert result.status == "RESOLVED"
    assert result.ticker == "MOG.A"
    assert result.cik == "0000067887"
    assert len(db.added) == 1
    assert db.added[0].ticker == "MOG.A"
    assert db.added[0].cik == "0000067887"
