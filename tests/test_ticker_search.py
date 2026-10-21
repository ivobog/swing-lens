"""Real SQL tests over disposable PostgreSQL; production data is never written."""

import re
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import ForeignKeyConstraint, MetaData, create_engine, event
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.ceri_tables import CeriCompany, CeriCompanyAlias
from app.models.tables import (
    CombinedResult,
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    IBContract,
    PipelineRun,
    RawCompanyRow,
    UploadRun,
)
from app.routers.ticker_routes import router
from app.services.pipeline_outcome import derive_pipeline_outcome
from app.services.producer_readiness import (
    ReadinessReason,
    ReadinessStatus,
    normalize_producer_readiness,
)
from app.services.ticker_search_service import search_tickers, ticker_summary

MODELS = (
    UploadRun,
    RawCompanyRow,
    CeriCompany,
    CeriCompanyAlias,
    IBContract,
    CombinedResult,
    CoreCalculationEvidence,
    CoreCalculationCurrentProjection,
    PipelineRun,
)
CUTOFF = datetime(2026, 10, 19, 20, tzinfo=UTC)


def frozen_readiness(producer, payload, fingerprint):
    readiness = normalize_producer_readiness(
        producer, payload, identity_fingerprint=fingerprint
    )
    # These reader fixtures represent already-published READY evidence with an
    # advisory market warning. Preserve that stored state independently of the
    # checkout's current producer policy, retaining its warning and native signals.
    if (
        payload.get("warning_flags_json") == ["market_risk_off"]
        and readiness.status is ReadinessStatus.DEGRADED
        and readiness.warning_reasons == (ReadinessReason.PRODUCER_WARNING,)
        and not readiness.blocking_reasons
    ):
        readiness = replace(readiness, status=ReadinessStatus.READY)
    return readiness.canonical_payload()


@pytest.fixture
def search_db(disposable_postgres_database):
    engine = create_engine(disposable_postgres_database)
    metadata = MetaData()
    for model in MODELS:
        table = model.__table__.to_metadata(metadata)
        # These read APIs need only the selected tables, not every domain FK.
        for constraint in list(table.constraints):
            if isinstance(constraint, ForeignKeyConstraint):
                table.constraints.remove(constraint)
        table.foreign_keys.clear()
    metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def insert(db, model, **values):
    db.execute(model.__table__.insert().values(**values))


def raw(db, ticker, name, run_id=1, row_id=None):
    if not db.get(UploadRun, run_id):
        insert(db, UploadRun, id=run_id, filename="test.csv", status="COMPLETED")
    values = dict(run_id=run_id, ticker=ticker, company_name=name, row_number=1, raw_json={})
    if row_id:
        values["id"] = row_id
    insert(db, RawCompanyRow, **values)


def decision(
    db,
    run_id,
    *,
    ticker="EOG",
    cutoff=CUTOFF,
    status="COMPLETED",
    complete=True,
    projected=True,
    certified=True,
    hazard=False,
    fingerprint=None,
):
    raw(db, ticker, "EOG Resources", run_id)
    outcome = derive_pipeline_outcome(status, {"contamination_detected": hazard})
    insert(
        db,
        PipelineRun,
        id=run_id,
        upload_run_id=run_id,
        status=status,
        result_json={"pipeline_outcome_summary": outcome},
    )
    fingerprint = fingerprint or f"test-identity-{run_id}"
    payload = dict(
        is_complete=complete,
        final_score=str(run_id),
        fundamental_score="9.11",
        dual_score="6.83",
        final_rank=1,
        sector="Energy",
        combined_decision="Strong candidate",
        position_size_hint="Half starter",
        warning_flags_json=["market_risk_off"],
        earnings_warning_flags_json=[],
    )
    payload["producer_readiness"] = frozen_readiness("COMBINED", payload, fingerprint)
    insert(
        db,
        CoreCalculationEvidence,
        id=run_id,
        artifact_kind="COMBINED",
        run_id=run_id,
        ticker=ticker,
        calculation_identity_fingerprint=fingerprint,
        calculation_identity_json={
            "ownership": {"pipeline_id": {"state": "KNOWN", "value": run_id}}
        },
        payload_json=payload,
        payload_fingerprint="payload",
        evidence_key=f"evidence-{run_id}",
        calculated_at=cutoff,
    )
    insert(
        db,
        CombinedResult,
        run_id=run_id,
        ticker=ticker,
        evidence_id=run_id if certified else None,
        # Mutated compatibility scores must never be used by the preview.
        final_score=999,
        fundamental_score=888,
        dual_score=777,
        is_complete=True,
        debug_json={},
    )
    if projected:
        insert(
            db,
            CoreCalculationCurrentProjection,
            artifact_kind="COMBINED",
            run_id=run_id,
            ticker=ticker,
            evidence_id=run_id,
            ranking_profile_key="",
        )


def test_search_ranking_case_whitespace_company_and_historical_dedup(search_db):
    db = search_db
    for run_id in range(1, 5):
        raw(db, "EO", "Exact Corp", run_id)
    raw(db, "EOG", "Prefix Corp", 1)
    raw(db, "GEO", "Substring Corp", 1)
    raw(db, "AAA", "The Eo Company", 1)
    items = search_tickers(db, " eo ")
    assert [item["ticker"] for item in items] == ["EO", "EOG", "GEO", "AAA"]
    assert search_tickers(db, "prefix")[0]["ticker"] == "EOG"
    assert items == search_tickers(db, "EO")
    assert search_tickers(db, "not-known") == []


def test_ceri_only_contracts_alias_validity_and_punctuation(search_db):
    db = search_db
    insert(db, CeriCompany, id=1, ticker="MOG.A", exchange="NYSE", company_name="Moog A")
    insert(
        db,
        CeriCompanyAlias,
        company_id=1,
        provider="SEC",
        alias_type="ticker",
        alias_value="MOG-A",
        confidence="Normal",
    )
    insert(db, CeriCompany, id=2, ticker="ONLY", exchange="NASDAQ", company_name="CERI only")
    insert(
        db,
        CeriCompanyAlias,
        company_id=2,
        provider="SEC",
        alias_type="ticker",
        alias_value="EXPIRED",
        valid_to=date(2000, 1, 1),
        confidence="Normal",
    )
    insert(
        db,
        CeriCompanyAlias,
        company_id=2,
        provider="SEC",
        alias_type="ticker",
        alias_value="FUTURE",
        valid_from=date(2099, 1, 1),
        confidence="Normal",
    )
    insert(db, IBContract, ticker="IBONLY", resolution_status="RESOLVED", primary_exchange="NYSE")
    assert search_tickers(db, "mog-a")[0]["ticker"] == "MOG.A"
    assert search_tickers(db, "EXPIRED") == []
    assert search_tickers(db, "FUTURE") == []
    assert search_tickers(db, "only")[0]["ticker"] == "ONLY"
    assert ticker_summary(db, "ONLY")["state"] == "no_eligible_decision"
    assert ticker_summary(db, "MOG-A")["identity"]["ticker"] == "MOG.A"
    raw(db, "BRK.B", "Berkshire")
    assert search_tickers(db, "BRK-B") == []


def test_duplicate_sources_and_ambiguity_are_explicit(search_db):
    db = search_db
    raw(db, "EOG", "EOG Resources", 1)
    raw(db, "EOG", "EOG Resources", 2)
    insert(db, CeriCompany, id=1, ticker="EOG", exchange="US")
    items = search_tickers(db, "EOG")
    assert len(items) == 1
    assert items[0]["company_name"] == "EOG Resources"
    insert(db, CeriCompany, id=2, ticker="EOG", exchange="LSE", company_name="Another security")
    assert ticker_summary(db, "EOG")["state"] == "identity_ambiguous"
    qualified = ticker_summary(db, "EOG", "ceri:2")
    assert qualified["state"] == "decision_identity_unverified"
    assert qualified["workspace_url"] is None
    assert {i["qualifier"] for i in search_tickers(db, "EOG")} == {"US", "LSE", "Upload identity"}
    raw(db, "REUSED", "Old Company", 1)
    raw(db, "REUSED", "Different Company", 2)
    assert ticker_summary(db, "REUSED")["state"] == "identity_ambiguous"


def test_preview_frozen_scores_latest_cutoff_and_invalid_newer_sources(search_db):
    db = search_db
    decision(db, 100, cutoff=CUTOFF - timedelta(days=1))
    decision(db, 2, cutoff=CUTOFF, status="PARTIAL")
    decision(db, 3, cutoff=CUTOFF + timedelta(days=1), status="FAILED")
    decision(db, 4, cutoff=CUTOFF + timedelta(days=2), complete=False)
    decision(db, 5, cutoff=CUTOFF + timedelta(days=3), projected=False)
    decision(db, 6, cutoff=CUTOFF + timedelta(days=4), certified=False)
    decision(db, 7, cutoff=CUTOFF + timedelta(days=5), status="PARTIAL", hazard=True)
    decision(db, 8, cutoff=CUTOFF + timedelta(days=6), status="RUNNING")
    decision(db, 9, cutoff=CUTOFF + timedelta(days=7))
    db.execute(
        CombinedResult.__table__.update()
        .where(CombinedResult.run_id == 9)
        .values(
            debug_json={"mutation_semantics": {"artifact_role": "LEGACY_SERVING_ONLY"}},
        )
    )
    result = ticker_summary(db, "EOG")
    assert result["state"] == "available"
    assert result["decision"]["run_id"] == 2
    assert result["decision"]["final_score"] == "2"
    assert result["decision"]["fundamental_score"] == "9.11"
    assert result["decision"]["dual_score"] == "6.83"
    assert result["workspace_url"] == "/tickers/EOG?identity=raw%3A1&run_id=2"
    assert set(result["intelligence"].values()) == {"not_checked"}


def test_legacy_partial_and_invalid_readiness_cannot_be_promoted(search_db):
    db = search_db
    decision(db, 1)
    decision(db, 2, cutoff=CUTOFF + timedelta(days=1), status="PARTIAL")
    db.execute(PipelineRun.__table__.update().where(PipelineRun.id == 2).values(result_json={}))
    decision(db, 3, cutoff=CUTOFF + timedelta(days=2))
    db.execute(
        CoreCalculationEvidence.__table__.update()
        .where(CoreCalculationEvidence.id == 3)
        .values(calculation_identity_fingerprint="wrong")
    )
    assert ticker_summary(db, "EOG")["decision"]["run_id"] == 1


def test_summary_known_unscored_unknown_and_qualified_wrong_symbol(search_db):
    raw(search_db, "OLD", "Older upload", 1)
    raw(search_db, "NEW", "New upload", 2)
    summary = ticker_summary(search_db, "OLD")
    assert summary["state"] == "no_eligible_decision"
    assert summary["workspace_url"] == "/tickers/OLD?identity=raw%3A1"
    assert ticker_summary(search_db, "UNKNOWN")["state"] == "unknown_ticker"
    assert ticker_summary(search_db, "OLD", "raw:999")["state"] == "unknown_ticker"


def test_limits_wildcards_empty_queries_and_api_validation(search_db):
    db = search_db
    for i in range(65):
        raw(db, f"T{i:02}", "Test company")
    calls = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda *args: calls.append(args[2]))
    assert search_tickers(db, "  ") == []
    assert calls == []
    assert len(search_tickers(db, "T", 15)) == 15
    assert len(search_tickers(db, "T", 500)) == 50
    assert len(calls) == 2
    assert all("LIMIT" in sql for sql in calls)
    assert search_tickers(db, "%") == []
    assert search_tickers(db, "' OR 1=1 --") == []
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        assert len(client.get("/api/tickers/search?q=T").json()["results"]) == 15
        for suffix in ("?q=T&limit=51", "?q=T&limit=0", "?q=" + "a" * 101, "?q=%00", "?limit=bad"):
            assert client.get("/api/tickers/search" + suffix).status_code == 422
        assert client.get("/api/tickers/UNKNOWN/summary").status_code == 404
        assert client.get("/api/tickers/T00/summary?identity=bad").status_code == 422
        assert client.get("/api/tickers/T00/summary").json()["state"] == "no_eligible_decision"


def test_requests_execute_only_selects_and_do_not_lazy_load_history(search_db):
    db = search_db
    decision(db, 1)
    statements = []
    event.listen(db.get_bind(), "before_cursor_execute", lambda *args: statements.append(args[2]))
    items = search_tickers(db, "EO")
    result = ticker_summary(db, "EOG", items[0]["identity"])
    assert result["state"] == "available"
    assert len(statements) == 4  # search, identity, ambiguity count, compact decision
    assert all(sql.lstrip().startswith(("SELECT", "WITH")) for sql in statements)
    assert not any(re.search(r"\b(INSERT|UPDATE|DELETE|CALL)\b", sql) for sql in statements)
    assert not db.new and not db.dirty and not db.deleted
    assert not any("price_bars" in sql or "ceri_score" in sql for sql in statements)
