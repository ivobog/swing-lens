from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from test_sec_prospective_capture import _FixtureTransport
from test_sec_prospective_capture import capture_clock as capture_clock
from test_sec_prospective_capture import capture_db as capture_db
from test_sec_prospective_operator import _execution, _invoke
from test_sec_prospective_operator import approval_signer as approval_signer
from test_sec_prospective_operator import pilot_engine as pilot_engine

from app.models.ceri_tables import (
    CeriCompany,
    CeriEstimateSnapshot,
    CeriIngestionRun,
    CeriSourceRecord,
)
from app.services.ceri import source_record_service
from app.services.ceri.providers import eodhd_provider
from app.services.ceri.revision_feature_service import CeriRevisionFeatureService
from app.services.ceri.sec import prospective_capture as capture
from app.services.ceri.sec import reporting_currency
from scripts.ops import ceri_prospective_revenue as operator


def _cycle(base, symbol="ABBV.US", targets=None):
    grant = f"cycle-v1:{uuid4()}"
    proof = capture._REVIEWED[symbol]
    expiry = operator.datetime.now(UTC) + timedelta(minutes=5)
    return base.model_copy(
        update={
            "source_grant": grant,
            "request_key": f"ceri:prospective:{grant}",
            "symbols": (symbol,),
            "sec_attempts": 2,
            "eodhd_attempts": 1,
            "observations": 4,
            "starts_at": expiry - timedelta(minutes=5, seconds=10),
            "cutoff_at": expiry,
            "entitlement_valid_until": expiry + timedelta(minutes=1),
            "reviewed_filings": (
                (
                    symbol,
                    replace(
                        proof,
                        fiscal_targets=targets or proof.fiscal_targets,
                        fiscal_reference=f"Fixture calendar reviewed against {proof.accession}",
                    ),
                ),
            ),
        }
    )


def test_signed_cycles_reserve_once_and_preserve_exact_evidence(pilot_engine, approval_signer):
    engine, runtime, base, submissions = pilot_engine
    first = _cycle(base)
    transport = _FixtureTransport(submissions, rows=4)

    def signed(doc):
        return operator._approval(*approval_signer(doc.model_dump_json()))

    reservation = _invoke("reserve", engine, runtime, signed(first), transport)
    changed = first.model_copy(update={"approval_reference": "new signed reference"})
    with pytest.raises(operator.Unavailable, match="already_reserved"):
        _invoke("reserve", engine, runtime, signed(changed), transport)
    execution = _execution(reservation, first)
    symbol, filing = first.reviewed_filings[0]
    altered = execution.model_copy(
        update={
            "reviewed_filings": (
                (symbol, replace(filing, fiscal_reference="changed after reserve")),
            )
        }
    )
    crossed = _execution(reservation, _cycle(base))
    for rejected in (altered, crossed):
        with pytest.raises(ValueError, match="outside the approved scope"):
            _invoke("execute", engine, runtime, signed(rejected), transport)
    with pytest.raises(operator.Unavailable, match="operation_or_window"):
        _invoke("execute", engine, runtime, signed(first), transport)
    approval, signature = approval_signer(execution.model_dump_json())
    approval.write_text(execution.model_dump_json().replace("fixture operator", "forged"))
    with pytest.raises(operator.Unavailable, match="owner_signature_invalid"):
        operator._approval(approval, signature)
    assert transport.calls == []

    result = _invoke("execute", engine, runtime, signed(execution), transport)
    assert (result["status"], result["normalized"]) == ("COMPLETED", 4)
    assert result["requests"] == {"sec": 2, "eodhd": 1}
    assert result["scope"]["reviewed_filings"]["ABBV.US"]["sha256"] == filing.sha256
    with pytest.raises(ValueError, match="already claimed"):
        _invoke("execute", engine, runtime, signed(execution), transport)
    assert len(transport.calls) == 3

    second = _cycle(base)
    repeated = _FixtureTransport(submissions, rows=4)
    second_reservation = _invoke("reserve", engine, runtime, signed(second), repeated)
    assert second_reservation["ingestion_run_id"] != reservation["ingestion_run_id"]
    result = _invoke(
        "execute", engine, runtime, signed(_execution(second_reservation, second)), repeated
    )
    assert (result["status"], result["normalized"]) == ("PARTIAL", 0)
    assert result["issuers"] == [
        {
            "symbol": "ABBV.US",
            "status": "UNAVAILABLE",
            "reason": "no_new_provider_observations",
        }
    ]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CeriEstimateSnapshot)) == 4
        assert db.scalar(select(func.count()).select_from(CeriSourceRecord)) == 5

    third = _cycle(base)

    def mixed(request, *, timeout):
        response = repeated(request, timeout=timeout)
        if urlsplit(request.full_url).netloc == "data.sec.gov":
            return response
        rows = json.load(response)
        rows[0]["revenueEstimateAvg"] = "195"
        return type(response)(json.dumps(rows).encode())

    reservation = _invoke("reserve", engine, runtime, signed(third), mixed)
    result = _invoke("execute", engine, runtime, signed(_execution(reservation, third)), mixed)
    assert (result["status"], result["normalized"]) == ("COMPLETED", 1)
    assert set(result["issuers"][0]["already_seen_period_roles"]) == {
        "NEXT_QUARTER",
        "CURRENT_FISCAL_YEAR",
        "NEXT_FISCAL_YEAR",
    }
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CeriEstimateSnapshot)) == 5
        assert db.scalar(select(func.count()).select_from(CeriSourceRecord)) == 7


def test_concurrent_approved_cycle_uses_one_request_key(pilot_engine, monkeypatch):
    engine, runtime, base, submissions = pilot_engine
    approval = _cycle(base)
    transport = _FixtureTransport(submissions)
    original = operator._reserve
    ready = Barrier(2, timeout=15)

    def simultaneous_reserve(*args):
        ready.wait()
        return original(*args)

    def attempt(doc):
        try:
            return _invoke("reserve", engine, runtime, doc, transport)
        except operator.Unavailable as exc:
            return str(exc)

    monkeypatch.setattr(operator, "_reserve", simultaneous_reserve)
    changed = approval.model_copy(update={"observations": 3})
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (approval, changed)))
    (reservation,) = [row for row in outcomes if isinstance(row, dict)]
    assert reservation["request_key"] == approval.request_key
    assert outcomes.count("source_grant_already_reserved") == 1 and transport.calls == []
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CeriIngestionRun)) == 1


@pytest.mark.parametrize("mismatch", ["issuer", "cik", "hash", "calendar", "reference", "key"])
def test_missing_or_conflicting_cycle_evidence_has_no_provider_access(pilot_engine, mismatch):
    engine, runtime, base, submissions = pilot_engine
    approval = _cycle(base)
    symbol, proof = approval.reviewed_filings[0]
    updates = {
        "issuer": {"reviewed_filings": (("AMZN.US", proof),)},
        "cik": {"reviewed_filings": ((symbol, replace(proof, cik="0001018724")),)},
        "hash": {"reviewed_filings": ((symbol, replace(proof, sha256="a" * 64)),)},
        "calendar": {"reviewed_filings": ((symbol, replace(proof, fiscal_targets=())),)},
        "reference": {"reviewed_filings": ((symbol, replace(proof, fiscal_reference="")),)},
        "key": {"request_key": operator.REQUEST_KEY},
    }
    transport = _FixtureTransport(submissions)
    with pytest.raises(ValueError):
        _invoke(
            "reserve", engine, runtime, approval.model_copy(update=updates[mismatch]), transport
        )
    assert transport.calls == []
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(CeriIngestionRun)) == 0


def test_independent_cycles_supply_revenue_windows(pilot_engine, monkeypatch, record_property):
    engine, runtime, base, submissions = pilot_engine
    first_session = datetime(2026, 11, 25, 16, tzinfo=UTC)
    observed_at = first_session

    class DateTimeType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class CaptureDateTime(datetime, metaclass=DateTimeType):
        @classmethod
        def now(cls, tz=None):
            nonlocal observed_at
            observed_at += timedelta(milliseconds=1)
            return observed_at.astimezone(tz) if tz else observed_at.replace(tzinfo=None)

    for module in (capture, operator, eodhd_provider, reporting_currency):
        monkeypatch.setattr(module, "datetime", CaptureDateTime)
    monkeypatch.setattr(source_record_service, "_utcnow", lambda: CaptureDateTime.now(UTC))

    fiscal_end = date(2027, 9, 25)
    snapshots = []
    for elapsed, amount in ((0, 100), (58, 120), (83, 135), (90, 150)):
        # Match one fiscal year across four cycles, including a reviewed quarter rollover.
        observed_at = first_session + timedelta(days=elapsed)
        quarters = (
            (date(2026, 12, 26), date(2027, 3, 27))
            if elapsed == 0
            else (date(2027, 3, 27), date(2027, 6, 26))
        )
        targets = tuple(
            zip(capture._PERIODS, (*quarters, fiscal_end, date(2028, 9, 30)), strict=True)
        )
        doc = _cycle(base, symbol="AAPL.US", targets=targets)
        fixture = _FixtureTransport(submissions, rows=1, value=amount)

        def transport(request, *, timeout, fixture=fixture, amount=amount):
            response = fixture(request, timeout=timeout)
            if urlsplit(request.full_url).netloc == "data.sec.gov":
                return response
            (row,) = json.load(response)
            row.update(period="0y", date=fiscal_end.isoformat(), revenueEstimateAvg=str(amount))
            return type(response)(json.dumps([row]).encode())

        reservation = _invoke("reserve", engine, runtime, doc, transport)
        result = _invoke("execute", engine, runtime, _execution(reservation, doc), transport)
        assert (result["status"], result["normalized"]) == ("COMPLETED", 1)
        snapshots.append(result["issuers"][0]["observations"][0]["estimate_id"])

    with Session(engine) as db:
        latest = db.get(CeriEstimateSnapshot, snapshots[-1])
        records = []
        features = CeriRevisionFeatureService().calculate_windows(
            db,
            company_id=latest.company_id,
            metric="REVENUE",
            cutoff_at=observed_at + timedelta(minutes=1),
            period_slot="CURRENT_FISCAL_YEAR",
        )
        assert [feature.window_days for feature in features] == [7, 30, 90]
        for feature, expected, ratio in zip(
            features,
            reversed(snapshots[:-1]),
            (Decimal(1500) / 135, Decimal(25), Decimal(50)),
            strict=True,
        ):
            earlier = db.get(CeriEstimateSnapshot, expected)
            assert (
                feature.current_snapshot_id == latest.id
                and feature.baseline_snapshot_id == expected
            )
            assert latest.fiscal_period_end == earlier.fiscal_period_end == fiscal_end
            assert latest.currency_verified and earlier.currency_verified
            assert latest.canonical_currency == earlier.canonical_currency == "USD"
            assert latest.canonical_scale == earlier.canonical_scale == 1
            assert feature.comparison_mode == "HISTORICAL_OBSERVATION"
            assert feature.baseline_origin == "ACCUMULATED_IMMUTABLE_OBSERVATION"
            assert abs(feature.pct_change - ratio) < Decimal("0.0001")
            records.append(
                {
                    "window_days": feature.window_days,
                    "elapsed": feature.actual_elapsed_days,
                    "baseline_id": earlier.id,
                    "current_id": latest.id,
                    "fiscal_end": fiscal_end,
                    "baseline_amount": earlier.consensus,
                    "current_amount": latest.consensus,
                    "baseline_source_id": earlier.source_record_id,
                    "current_source_id": latest.source_record_id,
                    "baseline_authority_id": earlier.conversion_source_record_id,
                    "current_authority_id": latest.conversion_source_record_id,
                    "baseline_session": earlier.effective_session,
                    "current_session": latest.effective_session,
                    "baseline_known_at": earlier.known_at,
                    "current_known_at": latest.known_at,
                    "selection_reason": feature.provider_selection_reason,
                    "revision_pct": feature.pct_change,
                }
            )
        record_property("synthetic_postgres_revenue_revisions", json.dumps(records, default=str))


def test_revenue_windows_exclude_incomparable_and_unpossessed_baselines(pilot_engine):
    engine, _, _, _ = pilot_engine
    now = datetime(2026, 11, 24, 16, tzinfo=UTC)
    fiscal_end = date(2026, 12, 31)
    slot = "CURRENT_FISCAL_YEAR"
    with Session(engine) as db, db.begin():
        company = db.scalar(select(CeriCompany).where(CeriCompany.ticker == "ABBV"))

        def observation(label, observed, received, **overrides):
            provider = CeriSourceRecord(
                provider="eodhd",
                dataset="estimates",
                provider_record_id=label,
                content_hash=label,
                idempotency_key=label,
                retrieved_at=received,
                observed_at=observed,
            )
            db.add(provider)
            db.flush()
            fields = {
                "source_record_id": provider.id,
                "company_id": company.id,
                "metric": "REVENUE",
                "period_type": slot,
                "canonical_period_slot": slot,
                "fiscal_period_end": fiscal_end,
                "effective_session": observed.date(),
                "source_currency": "USD",
                "canonical_currency": "USD",
                "currency_verified": True,
                "source_scale": Decimal(1),
                "canonical_scale": Decimal(1),
                "provider_observed_at": observed,
                "effective_at": observed,
                "known_at": received,
                "retrieved_at": received,
                "canonical_observation_key": label,
                "source_provider": "eodhd",
                "consensus": Decimal(100),
            }
            estimate = CeriEstimateSnapshot(**(fields | overrides))
            db.add(estimate)
            db.flush()
            return estimate.id

        previous = now - timedelta(days=7)
        late = now + timedelta(days=1)
        current = observation("current", now, now, consensus=Decimal(150))
        observation("cross-currency", previous, previous, canonical_currency="EUR")
        observation("wrong-fiscal-end", previous, previous, fiscal_period_end=date(2026, 9, 30))
        observation("missing-units", previous, previous, canonical_scale=None)
        observation("late-possession", previous, late)
        observation("future-observation", late, now)
        observation(
            "historical-provider-trend",
            previous,
            previous,
            trend_baseline_window_days=7,
            baseline_origin="PROVIDER_RETROSPECTIVE_WINDOW",
        )
        after_tolerance = previous + timedelta(days=4)
        observation("outside-forward-tolerance", after_tolerance, after_tolerance)
        revision = CeriRevisionFeatureService().calculate_feature(
            db,
            company_id=company.id,
            metric="REVENUE",
            cutoff_at=now,
            window_days=7,
            period_slot=slot,
        )
        assert revision.current_snapshot_id == current
        assert revision.baseline_snapshot_id is None and revision.pct_change is None
        assert revision.unavailable_reason == "UNAVAILABLE_BASELINE_NOT_ACCUMULATED"
