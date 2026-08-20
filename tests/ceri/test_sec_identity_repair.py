from app.models.ceri_tables import CeriCompany
from app.services.ceri.sec.identity_repair import resolve_and_persist_sec_identity


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
