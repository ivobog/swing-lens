from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.ceri_tables import (
    CeriCompany,
    CeriEstimateSnapshot,
    CeriIngestionRun,
    CeriProcessingRun,
    CeriSecFilingDocument,
    CeriSourceRecord,
)
from app.services.ceri.dtos import RawProviderRecord
from app.services.ceri.enums import CeriDataset
from app.services.ceri.estimate_normalizer import CeriEstimateNormalizer
from app.services.ceri.normalization_service import CeriNormalizationService
from app.services.ceri.sec.content_cache import SecDocumentContentCache
from app.services.ceri.sec.provider import SecGuidanceDocument
from app.services.ceri.sec.reporting_currency import (
    DATASET,
    SecRevenueCurrencyCapture,
    StatementReview,
    _PreparedAuthority,
    project_statement,
    register_statement,
)
from app.services.ceri.sec.statement_currency import extract_presentation_currency
from app.services.ceri.source_record_service import CeriSourceRecordService

CASES = {
    "AAPL": ("0000320193", "0000320193-26-000026", "10-K", "USD"),
    "MSFT": ("0000789019", "0001193125-26-436133", "10-Q", None),
    "AMZN": ("0001018724", "0001018724-26-000040", "10-Q", "USD"),
    "ABBV": ("0001551152", "0001551152-26-000037", "10-Q", "USD"),
    "SPOT": ("0001639920", "0001628280-26-066889", "6-K", "EUR"),
    "LIN": ("0001707925", "0001628280-26-068079", "10-Q", "USD"),
    "HG": ("0001593275", "0001593275-26-000128", "10-Q", "USD"),
}
NOW = datetime(2026, 11, 20, 15, tzinfo=UTC)


def statement(ticker, currency="USD", *, ambiguous=False):
    cik, _, form, _ = CASES[ticker]
    end = "2026-09-26" if form == "10-K" else "2026-09-30"
    start = "2025-09-28" if form == "10-K" else "2026-07-01"
    heading = "Consolidated Statements of Operations"
    if ticker == "MSFT":
        heading = "Management discussion: results"
    labels = "in USD millions"
    if form == "6-K":
        labels = "September 30, 2026 (in € millions)"
    if ambiguous:
        labels += " US$ convenience column"
    return f"""
        <xbrli:context id="period">
          <xbrli:entity><xbrli:identifier>{cik}</xbrli:identifier></xbrli:entity>
          <xbrli:period><xbrli:startDate>{start}</xbrli:startDate>
            <xbrli:endDate>{end}</xbrli:endDate></xbrli:period>
        </xbrli:context>
        <xbrli:unit id="currency"><xbrli:measure>iso4217:{currency}</xbrli:measure></xbrli:unit>
        <h2>{heading}</h2>
        <table><tr><th>{labels}</th></tr>
          <tr><td>{"Premiums Earned" if ticker == "HG" else "Revenue"}</td>
            <td><ix:nonFraction contextRef="period" unitRef="currency" scale="6">
            40</ix:nonFraction></td></tr>
          <tr><td>Net income</td><td>10</td></tr>
        </table>
    """


def fixture(tmp_path, ticker="AAPL", clock=NOW):
    cik, accession, form, currency = CASES[ticker]
    end = date(2026, 9, 26) if form == "10-K" else date(2026, 9, 30)
    accepted = clock - timedelta(minutes=8)
    run = CeriIngestionRun(
        id=5,
        dataset="estimates",
        provider="eodhd",
        status="RUNNING",
        started_at=clock - timedelta(minutes=6),
    )
    company = CeriCompany(id=2, ticker=ticker, cik=cik, sec_applicability="REQUIRED")
    document = SecGuidanceDocument(ticker, cik, accession, "statement.htm", form, "2026-11-20")
    body = statement(ticker, currency or "USD")
    SecDocumentContentCache(tmp_path).store(document, body)
    filing = CeriSecFilingDocument(
        id=8,
        cik=cik,
        accession_number=accession,
        document_name=document.document_name,
        form=form,
        filing_date=accepted.date(),
        first_seen_at=accepted,
        last_downloaded_at=clock - timedelta(minutes=7),
        last_content_hash=sha256(body.encode()).hexdigest(),
        last_content_bytes=len(body.encode()),
    )
    recent = {
        "accessionNumber": [accession],
        "primaryDocument": [document.document_name],
        "form": [form],
        "acceptanceDateTime": [accepted.isoformat()],
        "reportDate": [end.isoformat()],
    }
    review = StatementReview(
        filing,
        end,
        "ANNUAL" if form == "10-K" else "QUARTERLY",
        {
            "cik": cik,
            "name": f"{ticker} reporting entity",
            "tickers": [ticker],
            "filings": {"recent": recent},
        },
        clock - timedelta(minutes=4),
    )
    return run, company, review


def estimate(ticker="AAPL", **overrides):
    return CeriSourceRecord(
        provider="eodhd",
        dataset="estimates",
        provider_record_id=f"{ticker}.US:row",
        id=11,
        ingestion_run_id=5,
        retrieved_at=NOW + timedelta(seconds=1),
        restricted_normalized_json={
            "provider_company_id": f"{ticker}.US",
            "consensus": "123.456",
            "metric": "REVENUE",
            "period_type": "NEXT_QUARTER",
            "fiscal_period_end": "2027-03-31",
            **overrides,
        },
    )


@pytest.mark.parametrize("ticker", CASES)
def test_retained_accession_contract(tmp_path, ticker):
    run, company, review = fixture(tmp_path, ticker)
    projected = project_statement(
        run=run,
        company=company,
        review=review,
        cache_dir=tmp_path,
        derived_at=NOW,
    )
    expected = CASES[ticker][3]
    observation = estimate(ticker)
    if expected is None:
        assert projected is None
        assert CeriEstimateNormalizer().normalize(observation, company_id=2).consensus is None
        return
    proof = CeriSourceRecord(
        id=42,
        provider="sec",
        dataset=DATASET,
        published_at=projected.published_at,
        retrieved_at=NOW,
        restricted_normalized_json=projected.payload,
    )
    capture = SecRevenueCurrencyCapture(ingestion_run_id=5, authority_source_ids=(42,))
    binding = capture.resolve(observation, _PreparedAuthority(proof, None))
    assert binding.currency == expected
    assert projected.payload["filing_accession"] == CASES[ticker][1]
    assert projected.payload["cik"] == CASES[ticker][0]
    normalized = CeriEstimateNormalizer().normalize(observation, company_id=2, sec_currency=binding)
    assert normalized.consensus == Decimal("123.456")
    assert normalized.canonical_currency == expected and normalized.canonical_scale == Decimal(1)
    assert normalized.currency_basis == "filing_currency"
    assert normalized.conversion_source_record_id == 42
    assert "source_currency" not in observation.restricted_normalized_json


@pytest.mark.parametrize(
    "case",
    [
        "wrong_cik",
        "wrong_ticker",
        "hash",
        "late_acceptance",
        "late_receipt",
        "newer",
        "amendment",
        "foreign_listing",
        "family_collision",
    ],
)
def test_projection_fails_closed(tmp_path, case):
    run, company, review = fixture(tmp_path)
    recent = review.submissions["filings"]["recent"]
    if case == "wrong_cik":
        review.submissions["cik"] = "0000789019"
    elif case == "wrong_ticker":
        review.submissions["tickers"] = ["MSFT"]
    elif case == "hash":
        review.document.last_content_hash = "0" * 64
    elif case == "late_acceptance":
        recent["acceptanceDateTime"][0] = (NOW + timedelta(minutes=1)).isoformat()
    elif case == "late_receipt":
        review.document.last_downloaded_at = NOW + timedelta(minutes=1)
    elif case in {"newer", "amendment"}:
        for key, value in {
            "accessionNumber": "different",
            "primaryDocument": "different.htm",
            "form": "10-Q" if case == "newer" else "10-K/A",
            "acceptanceDateTime": (NOW - timedelta(minutes=5)).isoformat(),
            "reportDate": "2026-09-26",
        }.items():
            recent[key].append(value)
    elif case == "foreign_listing":
        company.sec_applicability = "NOT_REQUIRED"
    else:
        review = replace(review, period_family="QUARTERLY")
    assert (
        project_statement(
            run=run,
            company=company,
            review=review,
            cache_dir=tmp_path,
            derived_at=NOW,
        )
        is None
    )


@pytest.mark.parametrize(("unit", "expected"), [("CNY", "CNY"), ("XXX", None), ("USD", "USD")])
def test_explicit_iso_unit(unit, expected):
    assert (
        extract_presentation_currency(
            statement("HG", unit),
            cik=CASES["HG"][0],
            fiscal_end=date(2026, 9, 30),
            annual=False,
            reviewed_interim=False,
        )
        == expected
    )


def test_foreign_interim_rejects_ambiguous_convenience_columns():
    cik = CASES["SPOT"][0]
    for body in (statement("SPOT", "EUR", ambiguous=True), statement("SPOT", "USD")):
        assert (
            extract_presentation_currency(
                body,
                cik=cik,
                fiscal_end=date(2026, 9, 30),
                annual=False,
                reviewed_interim=True,
            )
            is None
        )


def test_other_xbrl_entity_cannot_establish_reporting_currency():
    assert (
        extract_presentation_currency(
            statement("HG"),
            cik=CASES["LIN"][0],
            fiscal_end=date(2026, 9, 30),
            annual=False,
            reviewed_interim=False,
        )
        is None
    )


def test_point_in_time_and_replay_guards(tmp_path):
    run, company, review = fixture(tmp_path)
    projected = project_statement(
        run=run,
        company=company,
        review=review,
        cache_dir=tmp_path,
        derived_at=NOW,
    )
    proof = CeriSourceRecord(
        id=42,
        retrieved_at=NOW,
        restricted_normalized_json=projected.payload,
    )
    authorized = _PreparedAuthority(proof, None)
    capture = SecRevenueCurrencyCapture(ingestion_run_id=5, authority_source_ids=(42,))
    old_run = estimate()
    old_run.ingestion_run_id = 4
    early = estimate()
    early.retrieved_at = NOW - timedelta(seconds=1)
    for observation in (
        old_run,
        early,
        estimate("LIN"),
        estimate(source_scale="1000000"),
        estimate(source_currency="EUR"),
        estimate(trend_baseline_window_days=7),
        estimate(fiscal_period_end="2026-09-26"),
        estimate(fiscal_period_end="2026-06-30"),
    ):
        assert capture.resolve(observation, authorized) is None
    annual = estimate(period_type="CURRENT_FISCAL_YEAR", fiscal_period_end="2026-09-26")
    assert capture.resolve(annual, authorized).currency == "USD"
    assert capture.resolve(estimate(), replace(authorized, earliest_competing_filing=NOW)) is None
    assert capture.resolve(estimate(), replace(authorized, superseded_at=NOW)) is None
    eps = estimate(metric="EPS_DILUTED", current_observation_reference="same-response")
    assert capture.resolve(eps, authorized) is None
    assert CeriEstimateNormalizer().normalize(eps, company_id=2).consensus == Decimal("123.456")


def test_protected_writer_and_persisted_authority(tmp_path, disposable_postgres_database):
    engine = create_engine(disposable_postgres_database)
    CeriCompany.metadata.create_all(engine)
    with Session(engine) as db:
        clock = datetime.now(UTC)
        run, company, review = fixture(tmp_path, clock=clock)
        run.request_key = "f7-test"
        run.config_version = "test"
        run.config_hash = "test"
        db.add_all((run, company, review.document))
        db.flush()
        source_id = register_statement(
            db,
            run_id=run.id,
            company_id=company.id,
            review=review,
            cache_dir=tmp_path,
        )
        assert source_id is not None
        db.commit()
        source = db.scalar(select(CeriSourceRecord))
        assert source.id == source_id and source.raw_json is None
        assert source.ingestion_run_id is None and not source.redistribution_allowed
        assert (
            source.restricted_normalized_json["document_sha256"]
            == review.document.last_content_hash
        )
        capture = SecRevenueCurrencyCapture(
            ingestion_run_id=run.id, authority_source_ids=(source_id,)
        )
        writer = CeriSourceRecordService()

        def add_estimate(key):
            record = RawProviderRecord(
                provider="eodhd",
                dataset=CeriDataset.ESTIMATES,
                provider_record_id=key,
                payload={
                    "ticker": "AAPL",
                    **estimate(
                        fiscal_period_end=(clock.date() + timedelta(days=120)).isoformat()
                    ).restricted_normalized_json,
                },
                published_at=None,
                observed_at=None,
                retrieved_at=datetime.now(UTC),
            )
            return writer.store_source_record(
                db,
                ingestion_run_id=run.id,
                record=record,
                raw_payload_allowed=False,
            ).source_record

        old = add_estimate("AAPL.US:already-normalized")
        old_snapshot = CeriEstimateNormalizer().normalize(old, company_id=company.id)
        db.add(old_snapshot)
        observation = add_estimate("AAPL.US:new-observation")
        processing = CeriProcessingRun(
            job_type="NORMALIZE",
            status="RUNNING",
            deterministic_request_key="f7-normalize",
        )
        db.add(processing)
        db.flush()
        normalized = CeriNormalizationService(sec_currency_capture=capture).normalize(
            db,
            processing_run=processing,
            ingestion_run_id=run.id,
        )
        assert normalized.failed == 0 and normalized.quarantined == 0, processing.errors_json
        assert normalized.normalized == 1
        db.commit()
        assert old_snapshot.conversion_source_record_id is None and old_snapshot.consensus is None
        new = db.scalar(
            select(CeriEstimateSnapshot).where(
                CeriEstimateSnapshot.source_record_id == observation.id
            )
        )
        assert new.consensus == Decimal("123.456")
        assert (
            new.conversion_source_record_id == source_id and new.currency_basis == "filing_currency"
        )

        db.add(CeriCompany(ticker="AAPL-ADR", cik=company.cik, sec_applicability="REQUIRED"))
        db.flush()
        assert capture.prepare(db) == {}
    engine.dispose()
