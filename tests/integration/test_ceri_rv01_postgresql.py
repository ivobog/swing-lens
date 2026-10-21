"""Production-session evidence drill-down on the migrated schema, retaining FKs."""

from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from test_ticker_certification_remediation_postgresql import seed_ancillary
from test_ticker_intelligence_remediation_postgresql import migrated_db as migrated_db
from test_ticker_intelligence_remediation_postgresql import settings
from test_ticker_search import insert

from app.db import get_db
from app.main import create_app
from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriCompany,
    CeriRevisionFeature,
    CeriSourceRecord,
)
from app.services.ceri import query_service
from app.services.ceri.constants import PURGE_INVALIDATION_FLAG
from app.services.ceri.query_service import CeriQueryError, CeriQueryService
from app.services.ticker_intelligence_service import ticker_intelligence


@pytest.fixture
def evidence_db(migrated_db):
    db = migrated_db
    cutoff = seed_ancillary(db)
    insert(
        db,
        CeriRevisionFeature,
        id=99200,
        company_id=8000,
        metric="EPS_DILUTED",
        period_key="2026Q2",
        period_slot="FQ0",
        as_of_session=cutoff.date(),
        calculation_cutoff_at=cutoff,
        window_days=90,
        absolute_change="0.25",
        pct_change="12.5",
        net_breadth="0.75",
        upward_count=7,
        downward_count=1,
        current_source_record_id=99100,
        baseline_source_record_id=99101,
        source_observation_ids_json=[99100, 99101],
        warnings_json=[],
        config_version="rv01",
        config_hash="rv01-hash",
        calculation_version="rv01",
        evidence_hash="rv01-feature",
    )
    insert(
        db,
        CeriCatalystEventRevision,
        id=99103,
        catalyst_event_id=99100,
        source_record_id=99102,
        revision_number=0,
        is_current=False,
        status="CANCELLED",
        direction="NEGATIVE",
        review_state="REJECTED",
        conflict_flags_json=[PURGE_INVALIDATION_FLAG],
    )
    insert(db, CeriCompany, id=99299, ticker="OTHER", company_name="Other issuer")
    insert(
        db,
        CeriCatalystEvent,
        id=99299,
        company_id=99299,
        category="PRODUCT",
        subject_key="unrelated",
        canonical_text="Unrelated event",
    )
    insert(
        db,
        CeriCatalystEventRevision,
        id=99299,
        catalyst_event_id=99299,
        source_record_id=99102,
        revision_number=1,
        is_current=True,
        status="ANNOUNCED",
        direction="POSITIVE",
    )
    db.commit()
    return db


@contextmanager
def reader(db, monkeypatch, *, trap_loader=True):
    db.expire_all()
    db.execute(text("SET TRANSACTION READ ONLY"))
    before = fingerprint(db)
    statements = []

    def capture(conn, cursor, statement, params, context, many):
        statements.append((statement, cursor.rowcount))

    if trap_loader:

        def forbidden(*_args, **_kwargs):
            raise AssertionError("Detail reader called predicate-free _load")

        monkeypatch.setattr(query_service, "_load", forbidden)
    app = create_app(settings())
    app.dependency_overrides[get_db] = lambda: db
    event.listen(db.get_bind(), "after_cursor_execute", capture)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            yield client, statements
    finally:
        event.remove(db.get_bind(), "after_cursor_execute", capture)
    assert all(sql.lstrip().upper().startswith(("SELECT", "WITH")) for sql, _ in statements)
    assert before == fingerprint(db)
    assert not db.new and not db.dirty and not db.deleted


def fingerprint(db):
    return {
        table: db.scalar(
            text(f"select md5(string_agg(md5(to_jsonb(t)::text), '' order by id)) from {table} t")
        )
        for table in (
            "ceri_companies",
            "ceri_source_records",
            "ceri_revision_features",
            "ceri_catalyst_events",
            "ceri_catalyst_event_revisions",
        )
    }


def test_revision_detail_scopes_company_and_preserves_lineage(evidence_db, monkeypatch):
    with reader(evidence_db, monkeypatch) as (client, reads):
        response = client.get("/api/ceri/revisions/99200")
        assert response.status_code == 200
        data = response.json()
        assert (data["id"], data["company_id"], data["ticker"]) == (99200, 8000, "EOG")
        assert data["metric"] == "EPS_DILUTED" and data["period_key"] == "2026Q2"
        assert data["pct_change"] == 12.5
        assert data["raw_breadth_counts"] == {"upward_count": 7, "downward_count": 1}
        assert (data["current_source_record_id"], data["baseline_source_record_id"]) == (
            99100,
            99101,
        )
        assert data["lineage"]["source_observation_ids"] == [99100, 99101]
        assert data["lineage"]["config_hash"] == "rv01-hash"
        assert data["lineage"]["stored_values"] == data["lineage"]["reproduced_values"]
        assert len(reads) == 2 and all("WHERE" in sql for sql, _ in reads)
        assert [rows for _, rows in reads] == [1, 1]


def test_event_detail_and_revision_page_are_scoped_audit_reads(evidence_db, monkeypatch):
    with reader(evidence_db, monkeypatch) as (client, reads):
        response = client.get("/api/ceri/events/99100")
        assert response.status_code == 200
        data = response.json()
        assert (data["id"], data["company_id"], data["ticker"]) == (99100, 8000, "EOG")
        assert data["current_revision"]["id"] == 99100
        assert data["current_revision"]["source_record_id"] == 99100
        assert {r["id"] for r in data["revisions"]} == {99100, 99103}
        historical = next(r for r in data["revisions"] if r["id"] == 99103)
        assert historical["review_state"] == "REJECTED" and historical["invalidated_by_purge"]
        assert historical["lineage"]["source_record_id"] == 99102
        assert len(reads) == 3 and all("WHERE" in sql for sql, _ in reads)
        assert [rows for _, rows in reads] == [1, 1, 2]
        reads.clear()
        page = client.get("/api/ceri/events/99100/revisions?limit=1&sort=revision_number").json()
        assert page["total"] == 2 and [r["id"] for r in page["items"]] == [99103]
        assert len(reads) == 3  # Reuse the detail's scoped revision payload; no N+1/reload.
        assert all("WHERE" in sql for sql, _ in reads)


@pytest.mark.parametrize("kind", ["revisions", "events"])
def test_unknown_evidence_is_structured_404(evidence_db, monkeypatch, kind):
    with reader(evidence_db, monkeypatch) as (client, reads):
        response = client.get(f"/api/ceri/{kind}/99999999")
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "TICKER_NOT_FOUND"
        assert len(reads) == 1 and reads[0][1] == 0


def test_missing_optional_source_remains_null_without_substitution(evidence_db, monkeypatch):
    db = evidence_db
    db.execute(
        update(CeriRevisionFeature)
        .where(CeriRevisionFeature.id == 99200)
        .values(current_source_record_id=None)
    )
    db.execute(
        update(CeriCatalystEventRevision)
        .where(CeriCatalystEventRevision.id == 99100)
        .values(source_record_id=None)
    )
    db.commit()
    with reader(db, monkeypatch) as (client, _):
        revision = client.get("/api/ceri/revisions/99200")
        catalyst = client.get("/api/ceri/events/99100")
        assert revision.status_code == catalyst.status_code == 200
        assert revision.json()["current_source_record_id"] is None
        assert revision.json()["baseline_source_record_id"] == 99101
        assert catalyst.json()["current_revision"]["source_record_id"] is None
        assert catalyst.json()["current_revision"]["lineage"]["source_record_id"] is None


def test_company_fk_remains_enforced_and_missing_lookup_does_not_load_catalog(
    evidence_db, monkeypatch
):
    db = evidence_db
    with pytest.raises(IntegrityError), db.begin_nested():
        db.execute(
            update(CeriRevisionFeature)
            .where(CeriRevisionFeature.id == 99200)
            .values(company_id=99999999)
        )
    db.commit()
    with reader(db, monkeypatch) as (client, _):
        assert query_service._get(db, CeriCompany, 99999999) is None
        assert client.get("/api/ceri/revisions/99200").json()["ticker"] == "EOG"


def test_audit_detail_does_not_promote_quarantined_or_purged_evidence(evidence_db, monkeypatch):
    db = evidence_db
    db.execute(
        update(CeriSourceRecord)
        .where(CeriSourceRecord.id == 99100)
        .values(quarantine_reason="rv01 audit")
    )
    db.execute(
        update(CeriRevisionFeature)
        .where(CeriRevisionFeature.id == 99200)
        .values(warnings_json=[PURGE_INVALIDATION_FLAG])
    )
    db.commit()
    with reader(db, monkeypatch, trap_loader=False) as (client, _):
        # Audit endpoints retain explicitly requested historical/raw evidence.
        revision = client.get("/api/ceri/revisions/99200")
        catalyst = client.get("/api/ceri/events/99100")
        assert revision.status_code == catalyst.status_code == 200
        assert revision.json()["invalidated_by_purge"] is True
        assert catalyst.json()["current_revision"]["source_record_id"] == 99100
        current = ticker_intelligence(
            db, "EOG", "ceri:8000", 7, 7, "ceri", detail=True, settings=settings()
        )
        assert current["latest"]["id"] == 9000
        assert 99100 not in {r["id"] for r in current["catalysts"]}


def test_event_without_revisions_has_explicit_empty_detail(evidence_db, monkeypatch):
    db = evidence_db
    insert(
        db,
        CeriCatalystEvent,
        id=99298,
        company_id=8000,
        category="PRODUCT",
        subject_key="no revisions",
    )
    db.commit()
    with reader(db, monkeypatch) as (client, reads):
        response = client.get("/api/ceri/events/99298")
        assert response.status_code == 200
        assert response.json()["revisions"] == [] and response.json()["current_revision"] is None
        assert len(reads) == 3 and reads[-1][1] == 0


def test_sql_errors_are_not_converted_to_empty_details(evidence_db):
    db = evidence_db
    with pytest.raises(DBAPIError):
        db.execute(text("select 1/0"))
    with pytest.raises(DBAPIError):
        CeriQueryService().revision_detail(db, 99200)
    db.rollback()


def test_shared_key_lookup_preserves_missing_run_and_production_guard(evidence_db):
    with pytest.raises(CeriQueryError) as error:
        CeriQueryService()._require_run(evidence_db, 99999999)
    assert error.value.status_code == 404 and error.value.code == "RUN_NOT_FOUND"
    with pytest.raises(TypeError, match="predicate-free CERI model loading is fixture-only"):
        query_service._load(evidence_db, CeriCompany)
