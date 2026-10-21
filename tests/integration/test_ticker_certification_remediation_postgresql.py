"""C01/C02/C03/C05 counterexamples on the migrated production schema."""

from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text, update
from test_ticker_intelligence import frozen
from test_ticker_intelligence_remediation_postgresql import clone, data, html, settings
from test_ticker_intelligence_remediation_postgresql import migrated_db as migrated_db
from test_ticker_search import insert

from app.db import get_db
from app.main import create_app
from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriDerivedFeature,
    CeriEarningsActual,
    CeriScoreSnapshot,
    CeriSourceRecord,
)
from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    RankingResult,
    WinnerModelVersion,
    WinnerProbabilityEstimate,
)
from app.services.ceri.pit_eligibility import source_record_is_eligible
from app.services.ceri.query_service import _earnings_surprise_features
from app.services.ticker_intelligence_service import ticker_intelligence
from app.services.winner_probability.api_service import _estimate_payload


def seed_ancillary(db):
    cutoff = db.get(CeriScoreSnapshot, 9000).cutoff_at
    for index, offset in enumerate((-1, 1, 2)):
        sid = 99100 + index
        known = cutoff + timedelta(days=offset)
        insert(
            db,
            CeriSourceRecord,
            id=sid,
            provider="certification",
            dataset="earnings",
            provider_record_id=str(sid),
            content_hash=str(sid),
            idempotency_key=str(sid),
            observed_at=known,
            published_at=known,
            ingested_at=known,
            quarantine_reason="test quarantine" if index == 2 else None,
        )
        insert(
            db,
            CeriEarningsActual,
            id=sid,
            source_record_id=sid,
            company_id=8000,
            metric="EPS",
            period_type="FQ",
            fiscal_period_end=cutoff.date() - timedelta(days=90 + index),
            report_at=cutoff - timedelta(days=1),
            report_session=cutoff.date() - timedelta(days=1),
            actual_value=1.23 + index,
            surprise_pct=4.5 + index,
        )
        insert(
            db,
            CeriCatalystEvent,
            id=sid,
            company_id=8000,
            category="PRODUCT",
            subject_key=str(sid),
            canonical_text=f"cert-catalyst-{sid}",
            first_seen_at=known,
        )
        insert(
            db,
            CeriCatalystEventRevision,
            id=sid,
            catalyst_event_id=sid,
            source_record_id=sid,
            revision_number=1,
            is_current=True,
            announced_at=known,
            expected_date=cutoff.date() + timedelta(days=30),
            status="ANNOUNCED",
            direction="POSITIVE",
            issuer_relevance=True,
            binary_eligible=True,
            created_at=known,
        )
    return cutoff


def seed_overflow(db):
    template = deepcopy(db.get(CoreCalculationEvidence, 7000).payload_json)
    for index in range(101):
        key = f"overflow_{index:03}"
        payload = template | {"ranking_profile": key, "ranking_label": key}
        frozen(db, 100000 + index, "RANKING", 7, payload=payload, profile=key)
        insert(
            db,
            RankingResult,
            run_id=7,
            ticker="EOG",
            ranking_profile=key,
            ranking_label=key,
            profile_rank=1,
            profile_score=7,
            technical_profile_score=8,
            decision_label="Research candidate",
            evidence_id=100000 + index,
        )
        insert(
            db,
            CoreCalculationCurrentProjection,
            artifact_kind="RANKING",
            run_id=7,
            ticker="EOG",
            ranking_profile_key=key,
            evidence_id=100000 + index,
        )


def test_c01_earnings_and_catalysts_share_source_time_and_authority(migrated_db):
    db = migrated_db
    cutoff = seed_ancillary(db)
    assert source_record_is_eligible(db.get(CeriSourceRecord, 99100), cutoff)
    assert not source_record_is_eligible(db.get(CeriSourceRecord, 99101), cutoff)
    result = data(db)
    assert [row["id"] for row in result["earnings"]] == [99100]
    assert [row["id"] for row in result["catalysts"]] == [99100]
    body = html(db)
    assert "cert-catalyst-99100" in body and "1.23" in body
    assert "cert-catalyst-99101" not in body and "cert-catalyst-99102" not in body
    db.execute(
        update(CeriSourceRecord)
        .where(CeriSourceRecord.id == 99102)
        .values(
            observed_at=cutoff - timedelta(days=1),
            published_at=cutoff - timedelta(days=1),
            ingested_at=cutoff - timedelta(days=1),
        )
    )
    assert [row["id"] for row in data(db)["earnings"]] == [99100]
    assert [row["id"] for row in data(db)["catalysts"]] == [99100]
    # Rejected current revisions and superseded versions cannot re-enter the panel.
    clone(db, CeriCatalystEvent, 99100, id=99103, subject_key="rejected")
    clone(
        db,
        CeriCatalystEventRevision,
        99100,
        id=99103,
        catalyst_event_id=99103,
        review_state="REJECTED",
    )
    clone(db, CeriCatalystEventRevision, 99100, id=99104, revision_number=0, is_current=False)
    assert [row["id"] for row in data(db)["catalysts"]] == [99100]
    # Local possession must not be replaced by an earlier provider date.
    db.execute(
        update(CeriSourceRecord)
        .where(CeriSourceRecord.id == 99100)
        .values(retrieved_at=cutoff + timedelta(seconds=1))
    )
    assert data(db)["earnings"] == [] and data(db)["catalysts"] == []
    db.execute(
        update(CeriSourceRecord).where(CeriSourceRecord.id == 99100).values(
            retrieved_at=cutoff - timedelta(days=1), export_policy="purged"
        )
    )
    assert data(db)["earnings"] == [] and data(db)["catalysts"] == []
    # Records remain available for explicit audit; no state is rewritten.
    assert db.get(CeriSourceRecord, 99102).quarantine_reason == "test quarantine"
    assert db.get(CeriEarningsActual, 99101) is not None
    assert data(db)["latest"]["id"] == 9000


def test_c02_all_104_profiles_accessible_through_bounded_continuation(migrated_db):
    db = migrated_db
    seed_overflow(db)
    app = create_app(settings())
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as client:
        url = (
            "/api/tickers/EOG/intelligence/rankings"
            "?identity=ceri:8000&run_id=7&evidence_id=7&detail=true"
        )
        first = client.get(url).json()
        assert "104" in first["summary"]
        assert first["pagination"]["total"] == 104
        assert first["pagination"]["returned"] == 100
        assert "Next profiles" in first["html"]
        assert first["pagination"]["next_offset"] == 100
        second = client.get(url + "&offset=100").json()
        assert second["pagination"]["returned"] == 4
        assert second["pagination"]["next_offset"] is None
        assert "Previous profiles" in second["html"]
        # The service pages use the same verified run and frozen evidence address.
        pages = [
            ticker_intelligence(
                db, "EOG", "ceri:8000", 7, 7, "rankings", detail=True, settings=settings(), offset=o
            )
            for o in (0, 100)
        ]
        keys = [p["key"] for page in pages for p in page["profiles"]]
        assert len(keys) == len(set(keys)) == 104
        assert all(p["state"] == "available" for page in pages for p in page["profiles"])
        assert "Run 7" in second["html"] and "Frozen configuration" in second["html"]
        assert client.get(url + "&offset=-1").status_code == 422
        assert (
            client.get(url.replace("evidence_id=7", "evidence_id=6") + "&offset=100").json()[
                "state"
            ]
            == "changed"
        )
        old = client.get(url.replace("run_id=7&evidence_id=7", "run_id=6&evidence_id=6")).json()
        assert old["pagination"]["total"] == 3
        assert "overflow_" not in old["html"]


@pytest.mark.parametrize("status", ["ACTIVE", "RETIRED", "CANDIDATE"])
def test_c03_model_metadata_equals_canonical_persisted_status(migrated_db, status):
    db = migrated_db
    estimate = db.get(WinnerProbabilityEstimate, 81007)
    insert(
        db,
        WinnerModelVersion,
        id=99000,
        model_key="certification-model-v7",
        algorithm="logistic",
        status=status,
        outcome_definition_id=estimate.outcome_definition_id,
        entry_model="NEXT_OPEN",
        feature_schema_version=estimate.feature_schema_version,
        calculation_version="cert-v7",
        config_hash=estimate.config_hash,
        training_cutoff_at=estimate.training_cutoff_at,
        artifact_schema_version="v1",
        artifact_format="json",
        artifact_hash="cert-artifact",
    )
    db.execute(
        update(WinnerProbabilityEstimate)
        .where(WinnerProbabilityEstimate.id == 81007)
        .values(model_version_id=99000, source="MODEL", source_version="cert-v7")
    )
    db.expire_all()
    actual = data(db, "winner")["items"][0]["estimates"][0]["estimate"]
    canonical = _estimate_payload(db, db.get(WinnerProbabilityEstimate, 81007))
    for key in ("model_version_id", "model_key", "model_version_label", "model_status"):
        assert actual[key] == canonical[key]
    assert actual["model_status"] == status
    assert actual["id"] == 81007 and actual["point_probability"] == 0.64
    body = html(db, "winner")
    assert f"<dd>{status}</dd>" in body
    assert "certification-model-v7#99000" in body


def test_c03_missing_model_does_not_invent_status(migrated_db):
    db = migrated_db
    db.execute(
        update(WinnerProbabilityEstimate)
        .where(WinnerProbabilityEstimate.id == 81007)
        .values(model_version_id=None, source="MODEL", source_version="legacy-model")
    )
    result = data(db, "winner")["items"][0]["estimates"][0]["estimate"]
    assert result["model_version_id"] is None
    assert result["model_status"] is None


@pytest.mark.parametrize("irrelevant_rows", [0, 1000, 5000])
def test_c05_relevant_feature_query_does_not_materialize_unrelated_history(
    migrated_db, irrelevant_rows
):
    db = migrated_db
    seed_ancillary(db)
    snapshot = db.get(CeriScoreSnapshot, 9000)
    insert(
        db,
        CeriDerivedFeature,
        id=120000,
        company_id=8000,
        feature_family="earnings_surprise",
        feature_key="latest",
        as_of_session=snapshot.as_of_session,
        calculation_context_id=snapshot.calculation_context_id,
        calculation_cutoff_at=snapshot.cutoff_at,
        ownership_mode="STANDALONE",
        config_version=snapshot.config_version,
        config_hash=snapshot.config_hash,
        calculation_version=snapshot.calculation_version,
        evidence_hash="legitimate",
        value_json={"features": [{"earnings_actual_id": 99100, "surprise_pct": "17.5"}]},
    )
    clone(
        db,
        CeriDerivedFeature,
        120000,
        id=120001,
        config_hash="future",
        calculation_cutoff_at=snapshot.cutoff_at + timedelta(days=1),
        value_json={"features": [{"earnings_actual_id": 99100, "surprise_pct": "999"}]},
    )
    if irrelevant_rows:
        db.execute(
            text(
                """insert into ceri_derived_features(
                    company_id,feature_family,feature_key,as_of_session,ownership_mode,
                    value_json,evidence_hash,config_version,config_hash,calculation_version)
                select 8000,'irrelevant_history','cert-'||n,current_date-100,'STANDALONE',
                    jsonb_build_object('padding',repeat('x',4096)),'cert-'||n,'cert','cert','cert'
                from generate_series(1,cast(:n as integer)) n"""
            ),
            {"n": irrelevant_rows},
        )
    reads = []

    def capture(conn, cursor, statement, params, context, many):
        if "FROM ceri_derived_features" in statement:
            reads.append((statement, cursor.rowcount))

    event.listen(db.get_bind(), "after_cursor_execute", capture)
    try:
        result = _earnings_surprise_features(db, snapshot)
    finally:
        event.remove(db.get_bind(), "after_cursor_execute", capture)
    assert result == [{"earnings_actual_id": 99100, "surprise_pct": "17.5"}]
    assert len(reads) == 1 and reads[0][1] == 1
    assert "feature_family =" in reads[0][0] and "LIMIT" in reads[0][0]
    assert data(db)["earnings"][0]["surprise_pct"] == "17.5"
