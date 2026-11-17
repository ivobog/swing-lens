from datetime import date
from types import SimpleNamespace

import pytest
from test_sec_identity_repair import _Db, _Provider

from app.models.ceri_tables import CeriCompany
from app.services.ceri.identity_resolver import CeriIdentityResolver
from app.services.ceri.sec.identity_repair import resolve_and_persist_sec_identity
from app.services.ceri.sec.readiness_diagnostics import diagnose_sec_readiness
from app.services.pipeline_service import _ceri_child_failure_detail


def company(i, ticker, cik="0001636519"):
    return CeriCompany(id=i, ticker=ticker, cik=cik, exchange="US")


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 10, 26), [171]),
        (date(2026, 10, 27), [793]),
        (None, [171, 793]),
    ],
)
def test_sec_symbol_as_of_filing_even_with_current_ticker_hint(day, expected):
    resolver = CeriIdentityResolver(
        companies=[company(171, "MSGS"), company(793, "MSGK")], aliases=[]
    )
    hints = {"ticker": "MSGK", "cik": "0001636519", "provider_company_id": "0001636519"}
    assert [row.id for row in resolver._candidate_companies(None, "sec", hints, day)] == expected


@pytest.mark.parametrize("tickers", [("AAA.A", "AAA.B"), ("MSGS", "MSGK", "MSGR")])
def test_shared_cik_or_third_candidate_remains_ambiguous(tickers):
    rows = [company(i, ticker) for i, ticker in enumerate(tickers, 1)]
    resolver = CeriIdentityResolver(companies=rows, aliases=[])
    matches = resolver._candidate_companies(
        None, "sec", {"ticker": tickers[0], "cik": "0001636519"}, date(2026, 11, 6)
    )
    assert len(matches) == len(rows)


def test_readiness_reports_collision_outside_requested_scope():
    from test_sec_pipeline_preflight import SequentialDb

    rows = [company(1, "AAA.A"), company(2, "AAA.B")]
    result = diagnose_sec_readiness(
        SequentialDb(rows, []), tickers=["AAA.A"], processor_signature="test"
    )
    assert result.blocking_tickers == ("AAA.A",)
    assert "AAA.B (2)" in result.tickers[0].reason


def test_new_ticker_cannot_claim_an_unreviewed_cik():
    class WriterDb(_Db):
        def scalars(self, statement):
            if "WHERE ceri_companies.cik" in str(statement):
                return SimpleNamespace(all=lambda: [company(1, "OTHER")])
            return super().scalars(statement)

    db = WriterDb()
    result = resolve_and_persist_sec_identity(db, provider=_Provider("1636519"), ticker="NEW")
    assert result.status == "AMBIGUOUS" and not db.added


def test_pipeline_retains_identity_failure_provenance():
    record = {"source_record_id": 5, "candidate_ids": [1, 2], "reason": "identity_ambiguous"}
    job = SimpleNamespace(
        id=9,
        payload_json={},
        operational_metadata_json={},
        job_type="CERI_NORMALIZE_BATCH",
        status="PARTIAL",
        related_run_id=62,
        error_message=None,
        progress_stage=None,
        result_json={
            "provider": "sec",
            "dataset": "guidance",
            "results": {"ABC": {"processing_run_id": 8, "identity_rejections": [record]}},
        },
    )
    detail = _ceri_child_failure_detail(job)
    assert detail["reason"] == "CERI_IDENTITY_REJECTED"
    assert detail["classification"]["identity_failures"] == [
        {
            "ticker": "ABC",
            "processing_run_id": 8,
            "provider": "sec",
            "dataset": "guidance",
            "source_count": 1,
            "records": [record],
        }
    ]
