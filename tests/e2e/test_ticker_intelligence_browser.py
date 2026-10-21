from pathlib import Path

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from test_ticker_intelligence import seed_intelligence
from test_ticker_search import raw
from test_ticker_workspace_browser import workspace_server as workspace_server

from app.models.ceri_tables import CeriScoreSnapshot
from app.models.tables import SignalChangeEvent


@pytest.fixture(scope="module")
def intelligence_server(workspace_server, live_server_database_url):
    engine = create_engine(live_server_database_url)
    with Session(engine) as db:
        seed_intelligence(db)
        raw(db, "EMPTY", "Known without intelligence", 1)
        db.execute(
            CeriScoreSnapshot.__table__.update()
            .where(CeriScoreSnapshot.id == 9000)
            .values(
                top_positive_contributors_json=[
                    {"name": "<img src=x onerror=alert(1)>", "value": 8.2}
                ]
            )
        )
        db.execute(
            SignalChangeEvent.__table__.update()
            .where(SignalChangeEvent.id == 9100)
            .values(
                episode_id=None,
                old_value_json={"value": "8.2971"},
                new_value_json={"value": "6.8252"},
                delta_numeric="-1.4719",
                threshold_name="crossing_7",
                threshold_direction="EXIT",
            )
        )
        db.commit()
    engine.dispose()
    return workspace_server


def panel(page, kind):
    return page.locator(f'[data-workspace-section="{kind}"]')


def open_panel(page, kind):
    section = panel(page, kind)
    section.locator(":scope > summary").click()
    return section


def test_signal_transition_and_alert_render_their_own_fields(page, intelligence_server):
    page.goto(intelligence_server + "/tickers/EOG?identity=ceri:8000&run_id=7")
    section = open_panel(page, "lifecycle")
    signal = section.locator('[data-event-kind="signal"][data-event-id="9100"]')
    for value in ("technical_score", "8.2971", "6.8252", "-1.47", "crossing_7", "EXIT"):
        expect(signal).to_contain_text(value)
    link = signal.get_by_role("link", name="Setup evidence #8500")
    expect(link).to_have_attribute("href", "/api/setup-lifecycle/evidence/setups/8500")
    assert page.request.get(intelligence_server + link.get_attribute("href")).status == 200
    transition = section.locator('[data-event-kind="lifecycle"]').first
    expect(transition).to_contain_text("From state")
    expect(transition).to_contain_text("READY")
    alert = section.locator('[data-event-kind="alert"]').first
    expect(alert).to_contain_text("UNREAD")
    expect(alert).to_contain_text("MARKET_RISK_OFF")


def test_summaries_lazy_details_cache_and_original_chart(page, intelligence_server):
    errors = []
    requests = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("request", lambda request: requests.append(request.url))
    page.goto(intelligence_server + "/tickers/EOG?identity=ceri:8000&run_id=7")
    expect(page.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    expect(page.locator("#ticker-chart canvas").first).to_be_visible()
    expect(panel(page, "ceri").locator("[data-intelligence-summary]")).to_contain_text(
        "Opportunity 8.20"
    )
    expect(panel(page, "lifecycle").locator("[data-intelligence-summary]")).to_contain_text("READY")
    expect(panel(page, "rankings").locator("[data-intelligence-summary]")).to_contain_text(
        "3 profiles"
    )
    expect(panel(page, "winner").locator("[data-intelligence-summary]")).to_contain_text("64%")
    assert not [url for url in requests if "detail=true" in url]
    assert page.locator("[data-workspace-section]").count() == 10
    for kind, expected in (
        ("ceri", "Evidence age"),
        ("lifecycle", "Secondary episode"),
        ("rankings", "7.25"),
        ("winner", "64.0%"),
    ):
        section = open_panel(page, kind)
        expect(section.locator("[data-intelligence-content]")).to_contain_text(expected)
        count = len(
            [url for url in requests if f"intelligence/{kind}?" in url and "detail=true" in url]
        )
        assert count == 1
        section.locator(":scope > summary").click()
        section.locator(":scope > summary").click()
        expect(section.locator("[data-intelligence-content]")).to_contain_text(expected)
        assert (
            len(
                [url for url in requests if f"intelligence/{kind}?" in url and "detail=true" in url]
            )
            == 1
        )
    ceri = panel(page, "ceri")
    ceri.get_by_text("Opportunity components and contributors", exact=True).click()
    expect(ceri.get_by_text("<img src=x onerror=alert(1)>", exact=True)).to_be_visible()
    assert ceri.locator("[data-intelligence-content] img").count() == 0
    assert errors == []


def test_independent_error_loading_and_retry(page, intelligence_server):
    def fail_details(route):
        if "detail=true" in route.request.url:
            route.fulfill(status=503, json={"state": "error"})
        else:
            route.continue_()

    page.route("**/api/tickers/EOG/intelligence/ceri?*", fail_details)
    page.goto(intelligence_server + "/tickers/EOG?run_id=7")
    ceri = open_panel(page, "ceri")
    expect(ceri.get_by_role("button", name="Retry CERI Intelligence")).to_be_visible()
    lifecycle = open_panel(page, "lifecycle")
    expect(lifecycle.locator("[data-intelligence-content]")).to_contain_text("Primary episode")
    expect(page.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    # Returning through the browser's page cache must reactivate cancelled reads.
    page.evaluate("""() => {
      dispatchEvent(new PageTransitionEvent('pagehide'));
      dispatchEvent(new PageTransitionEvent('pageshow', {persisted: true}));
    }""")
    expect(ceri.get_by_role("button", name="Retry CERI Intelligence")).to_be_visible()
    page.unroute("**/api/tickers/EOG/intelligence/ceri?*")
    ceri.get_by_role("button", name="Retry CERI Intelligence").click()
    expect(ceri.locator("[data-intelligence-content]")).to_contain_text("Opportunity score")
    expect(ceri.get_by_role("button", name="Retry CERI Intelligence")).to_be_hidden()


def test_run_change_refreshes_rankings_winner_and_retains_current_labels(page, intelligence_server):
    page.goto(intelligence_server + "/tickers/EOG?run_id=7")
    for kind in ("ceri", "lifecycle", "rankings", "winner"):
        open_panel(page, kind)
    expect(panel(page, "winner").locator("[data-intelligence-content]")).to_contain_text("64.0%")
    expect(panel(page, "rankings").locator("[data-intelligence-content]")).to_contain_text("7.25")
    page.get_by_label("Decision Run", exact=True).select_option("6")
    page.get_by_role("button", name="View decision").click()
    expect(panel(page, "rankings").locator("[data-intelligence-content]")).to_contain_text("6.25")
    expect(panel(page, "winner").locator("[data-intelligence-content]")).to_contain_text("41.0%")
    expect(panel(page, "ceri")).to_contain_text("Current independent CERI projection")
    expect(panel(page, "ceri").locator("[data-intelligence-summary]")).to_contain_text("2026-10-21")
    expect(panel(page, "lifecycle")).to_contain_text("may postdate the selected decision")
    expect(page.locator("[data-chart-summary]")).to_contain_text("Research stop 61")
    page.reload()
    expect(panel(page, "winner").locator("[data-intelligence-content]")).to_contain_text("41.0%")


def test_obsolete_response_after_navigation_cannot_overwrite(page, intelligence_server):
    page.goto(intelligence_server + "/tickers/EOG?run_id=7")
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (url, options) => {
        if (url.includes('intelligence/winner') && url.includes('detail=true')) {
          await new Promise(resolve => setTimeout(resolve, 1200));
          return new Response(JSON.stringify({
            state:'available', summary:'OBSOLETE', html:'OBSOLETE'
          }));
        }
        return original(url, options);
      };
    }""")
    winner = open_panel(page, "winner")
    expect(winner.locator("[data-intelligence-status]")).to_have_text("Loading intelligence…")
    page.get_by_label("Decision Run", exact=True).select_option("6")
    page.get_by_role("button", name="View decision").click()
    expect(panel(page, "winner").locator("[data-intelligence-content]")).to_contain_text("41.0%")
    page.wait_for_timeout(1400)
    expect(page.locator("body")).not_to_contain_text("OBSOLETE")


def test_missing_sources_keyboard_narrow_layout_and_dock(page, intelligence_server):
    page.goto(intelligence_server + "/tickers/EMPTY")
    for kind in ("ceri", "lifecycle", "rankings", "winner"):
        section = panel(page, kind)
        section.locator(":scope > summary").focus()
        page.keyboard.press("Enter")
        expect(section).to_have_attribute("open", "")
        expect(section.locator("[data-intelligence-content]")).not_to_be_empty()
    expect(page.get_by_role("heading", name="No eligible decision available")).to_be_visible()
    page.goto(intelligence_server + "/tickers/EOG?run_id=7")
    expect(panel(page, "winner").locator("[data-intelligence-content]")).to_contain_text("64.0%")
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.get_by_role("button", name="Ticker Search", exact=True).click()
    field = page.get_by_role("combobox", name="Ticker or company")
    expect(field).to_be_focused()
    field.fill("EO")
    expect(page.get_by_role("option")).not_to_have_count(0)
    field.press("Escape")
    expect(page.locator("#ticker-search")).to_be_hidden()
    Path("output/playwright").mkdir(parents=True, exist_ok=True)
    page.screenshot(path="output/playwright/ticker-intelligence-mobile.png", full_page=True)
