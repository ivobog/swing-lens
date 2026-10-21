"""Real history restoration and bounded ranking continuation in both engines."""

from copy import deepcopy
from datetime import timedelta

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_ticker_intelligence import frozen
from test_ticker_intelligence_browser import intelligence_server as intelligence_server
from test_ticker_search import CUTOFF, insert
from test_ticker_workspace import workspace_decision
from test_ticker_workspace_browser import workspace_server as workspace_server

from app.models.tables import (
    CoreCalculationCurrentProjection,
    CoreCalculationEvidence,
    RankingResult,
)


@pytest.fixture(scope="module")
def remediation_server(intelligence_server, live_server_database_url):
    engine = create_engine(live_server_database_url)
    with Session(engine) as db:
        workspace_decision(db, 45, cutoff=CUTOFF + timedelta(days=3))
        workspace_decision(db, 44, cutoff=CUTOFF + timedelta(days=2))
        template = deepcopy(db.get(CoreCalculationEvidence, 7000).payload_json)
        for index in range(101):
            key = f"overflow_{index:03}"
            frozen(
                db,
                100000 + index,
                "RANKING",
                7,
                payload=template | {"ranking_profile": key, "ranking_label": key},
                profile=key,
            )
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
        db.commit()
    engine.dispose()
    return intelligence_server


def test_c04_history_restores_authoritative_run_and_keeps_lazy_sections(page, remediation_server):
    page.goto(remediation_server + "/tickers/EOG")
    selector = page.get_by_label("Decision Run", exact=True)
    provenance = page.locator('[aria-label="Selected decision provenance"]')
    chart = page.locator("[data-chart-summary]")

    def check(run):
        expect(selector).to_have_value(str(run))
        expect(provenance).to_contain_text(f"Run {run}")
        expect(provenance).to_contain_text(f"Certified evidence #{run}")
        expect(page.locator(".workspace-metrics")).to_contain_text(f"{run}.00")
        expect(chart).to_contain_text(f"Research stop {run * 10 + 1}")
        expect(chart).to_contain_text(f"target {run * 10 + 12}")
        assert f"run_id={run}" in page.url or (run == 45 and "run_id=" not in page.url)

    check(45)
    fundamentals = page.locator('[data-workspace-section="2"]')
    fundamentals.locator(":scope > summary").click()
    ceri = page.locator('[data-workspace-section="ceri"]')
    ceri.locator(":scope > summary").click()
    expect(ceri.locator("[data-intelligence-content]")).to_contain_text("Opportunity score")
    selector.select_option("44")
    page.get_by_role("button", name="View decision", exact=True).click()
    check(44)
    expect(fundamentals).to_have_attribute("open", "")
    page.go_back()
    check(45)
    expect(fundamentals).to_have_attribute("open", "")
    page.go_forward()
    check(44)
    page.reload()
    check(44)
    expect(fundamentals).to_have_attribute("open", "")
    expect(ceri.locator("[data-intelligence-content]")).to_contain_text("Opportunity score")
    # Simulate form restoration before a persisted pageshow event as well.
    selector.select_option("45")
    page.evaluate("dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}))")
    check(44)


def test_c02_ranking_continuation_is_visible_bounded_and_retryable(page, remediation_server):
    page.goto(remediation_server + "/tickers/EOG?identity=ceri:8000&run_id=7")
    panel = page.locator('[data-workspace-section="rankings"]')
    panel.locator(":scope > summary").click()
    expect(panel).to_contain_text("104 persisted profiles")
    rows = panel.locator("tbody > tr")
    expect(rows).to_have_count(100)
    first = rows.locator("td:first-child").all_text_contents()
    next_link = panel.get_by_role("link", name="Next profiles", exact=True)
    expect(next_link).to_be_visible()
    original_url = page.url
    # A failed continuation retries that page, not page one or the whole workspace.
    pattern = "**/api/tickers/EOG/intelligence/rankings?*offset=100*"
    page.route(pattern, lambda route: route.fulfill(status=503, json={"state": "error"}))
    next_link.click()
    retry = panel.get_by_role("button", name="Retry Ranking Profiles", exact=True)
    expect(retry).to_be_visible()
    page.unroute(pattern)
    retry.click()
    expect(rows).to_have_count(4)
    second = rows.locator("td:first-child").all_text_contents()
    assert len(set(first + second)) == 104 and page.url == original_url
    expect(panel).to_contain_text("Showing 101–104 of 104 persisted profiles")
    panel.get_by_role("link", name="Previous profiles", exact=True).click()
    expect(rows).to_have_count(100)
    assert rows.locator("td:first-child").all_text_contents() == first
    expect(page.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    expect(page.get_by_label("Decision Run", exact=True)).to_have_value("7")
