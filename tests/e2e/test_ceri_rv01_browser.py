"""Follow both CERI evidence links through a fresh migrated application process."""

from datetime import timedelta

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine, update
from sqlalchemy.orm import Session
from test_ticker_intelligence_browser import intelligence_server as intelligence_server
from test_ticker_search import insert
from test_ticker_workspace_browser import workspace_server as workspace_server

from app.models.ceri_tables import (
    CeriCatalystEvent,
    CeriCatalystEventRevision,
    CeriRevisionFeature,
    CeriScoreSnapshot,
    CeriSourceRecord,
)


@pytest.fixture(scope="module")
def drilldown_server(intelligence_server, live_server_database_url):
    engine = create_engine(live_server_database_url)
    with Session(engine) as db:
        snapshot = db.get(CeriScoreSnapshot, 9000)
        known = snapshot.cutoff_at - timedelta(days=1)
        insert(
            db,
            CeriSourceRecord,
            id=99500,
            provider="rv01",
            dataset="catalysts",
            provider_record_id="browser-source",
            content_hash="browser-source",
            idempotency_key="browser-source",
            observed_at=known,
            ingested_at=known,
        )
        insert(
            db,
            CeriRevisionFeature,
            id=99501,
            company_id=8000,
            metric="EPS_DILUTED",
            period_key="2026Q2",
            window_days=90,
            as_of_session=snapshot.as_of_session,
            calculation_cutoff_at=snapshot.cutoff_at,
            calculation_context_id=snapshot.calculation_context_id,
            known_at=known,
            current_source_record_id=99500,
            source_observation_ids_json=[99500],
            pct_change=12.5,
            config_version=snapshot.config_version,
            config_hash=snapshot.config_hash,
            calculation_version=snapshot.calculation_version,
        )
        db.execute(
            update(CeriScoreSnapshot)
            .where(CeriScoreSnapshot.id == 9000)
            .values(
                opportunity_ledger_json={
                    "components": [{"name": "revision_magnitude", "evidence_ids": [99501]}]
                },
            )
        )
        insert(
            db,
            CeriCatalystEvent,
            id=99502,
            company_id=8000,
            category="PRODUCT",
            subject_key="rv01-browser",
            canonical_text="RV01 browser catalyst",
        )
        insert(
            db,
            CeriCatalystEventRevision,
            id=99503,
            catalyst_event_id=99502,
            source_record_id=99500,
            revision_number=1,
            is_current=True,
            announced_at=known,
            expected_date=snapshot.as_of_session + timedelta(days=2),
            status="ANNOUNCED",
            direction="POSITIVE",
            issuer_relevance=True,
        )
        db.commit()
    engine.dispose()
    return intelligence_server


def test_displayed_revision_and_event_links_open_and_return(page, drilldown_server):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(drilldown_server + "/tickers/EOG?identity=ceri:8000&run_id=7")
    ceri = page.locator('[data-workspace-section="ceri"]')

    def evidence_links():
        if ceri.get_attribute("open") is None:
            ceri.locator(":scope > summary").click()
        summary = ceri.get_by_text("Catalysts, estimates, earnings and guidance", exact=True)
        expect(summary).to_be_visible()
        if summary.locator("..").get_attribute("open") is None:
            summary.click()

    evidence_links()
    with page.expect_navigation() as navigation:
        ceri.get_by_role("link", name="Revision evidence #99501", exact=True).click()
    response = navigation.value
    assert response.status == 200 and page.url.endswith("/api/ceri/revisions/99501")
    data = response.json()
    assert (data["id"], data["company_id"], data["ticker"]) == (99501, 8000, "EOG")
    assert data["current_source_record_id"] == 99500
    assert data["lineage"]["source_observation_ids"] == [99500]
    page.go_back()
    evidence_links()
    with page.expect_navigation() as navigation:
        ceri.get_by_role("link", name="Event evidence #99502", exact=True).click()
    response = navigation.value
    assert response.status == 200 and page.url.endswith("/api/ceri/events/99502")
    data = response.json()
    assert (data["id"], data["company_id"], data["ticker"]) == (99502, 8000, "EOG")
    assert data["current_revision"]["source_record_id"] == 99500
    assert [r["id"] for r in data["revisions"]] == [99503]
    page.go_back()
    expect(page.get_by_label("Decision Run", exact=True)).to_have_value("7")
    expect(page.locator(".workspace-metrics")).to_contain_text("7.00")
    expect(page.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    expect(page.locator("#ticker-chart canvas").first).to_be_visible()
    evidence_links()
    assert not errors
