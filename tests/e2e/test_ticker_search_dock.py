from pathlib import Path

import pytest
from playwright.sync_api import Page, expect
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from test_ticker_search import decision, insert, raw

from app.models.ceri_tables import CeriCompany
from app.models.tables import CombinedResult, PipelineRun, RawCompanyRow


@pytest.fixture(scope="module")
def ticker_server(live_server_url, live_server_database_url):
    engine = create_engine(live_server_database_url)
    with Session(engine) as db:
        # Seed each independent fixture even when workspace/browser tests have
        # already populated a decision in this shared disposable server.
        if db.get(PipelineRun, 7) is None:
            decision(db, 7)
            db.execute(
                CombinedResult.__table__.update()
                .where(CombinedResult.run_id == 7)
                .values(
                    final_score=7,
                    fundamental_score=9.11,
                    dual_score=6.83,
                    combined_decision="Strong candidate",
                    company_name="EOG Resources",
                )
            )
        if (
            db.scalar(select(RawCompanyRow.id).where(RawCompanyRow.ticker == "OLDONLY").limit(1))
            is None
        ):
            raw(db, "OLDONLY", "Historical upload only", 1)
        if db.get(CeriCompany, 901) is None:
            insert(
                db,
                CeriCompany,
                id=901,
                ticker="CERIONLY",
                exchange="US",
                company_name="Local CERI company",
            )
        # Explicit fixture IDs must not collide with ordinary upload/browser
        # regressions sharing this disposable server later in the test session.
        for table in (
            "upload_runs",
            "pipeline_runs",
            "core_calculation_evidence",
            "ceri_companies",
        ):
            db.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"(SELECT max(id) FROM {table}), true)"
                )
            )
        db.commit()
    engine.dispose()
    return live_server_url


def open_search(page, ticker_server, query="EO"):
    page.goto(ticker_server)
    page.get_by_role("button", name="Ticker Search", exact=True).click()
    field = page.get_by_role("combobox", name="Ticker or company")
    field.fill(query)
    expect(page.get_by_role("option")).not_to_have_count(0)
    return field


def select_eog(page, ticker_server):
    open_search(page, ticker_server)
    page.get_by_role("option").filter(has_text="EOG Resources").click()
    expect(page.get_by_role("link", name="Open Workspace")).to_be_visible()


def test_dock_nonmodal_original_controls_keyboard_and_preview(page: Page, ticker_server):
    page.goto(ticker_server)
    width_before = page.locator("main").bounding_box()["width"]
    page.keyboard.press("/")
    field = page.get_by_role("combobox", name="Ticker or company")
    expect(field).to_be_focused()
    assert page.locator("main").bounding_box()["width"] < width_before
    assert page.locator("#ticker-search").get_attribute("aria-modal") is None
    assert page.evaluate("getComputedStyle(document.body).overflow") != "hidden"
    # An existing dashboard form remains clickable while the dock is open.
    upload = page.locator("#csv-file")
    if upload.count():
        expect(upload).to_be_enabled()
        with page.expect_file_chooser() as chooser:
            page.locator("label[for=csv-file]").click()
        chooser.value.set_files([])
    page.get_by_role("link", name="Runs", exact=True).hover()
    field.fill("EO")
    expect(page.get_by_role("option")).to_have_count(1)
    field.press("ArrowDown")
    assert field.get_attribute("aria-activedescendant") == "ticker-option-0"
    field.press("ArrowUp")
    field.press("Enter")
    preview = page.get_by_role("region", name="Ticker preview")
    expect(preview).to_contain_text("Run 7")
    expect(preview).to_contain_text("market_risk_off")
    expect(preview).to_contain_text("9.11")
    assert page.url.rstrip("/") == ticker_server
    assert len(page.context.pages) == 1
    field.press("Escape")
    expect(page.locator("#ticker-search")).to_be_hidden()
    expect(page.get_by_role("button", name="Ticker Search", exact=True)).to_be_focused()


def test_history_known_without_scores_empty_error_retry_and_safe_strings(page, ticker_server):
    field = open_search(page, ticker_server, "OLDONLY")
    page.get_by_role("option").click()
    expect(page.get_by_role("region", name="Ticker preview")).to_contain_text(
        "No eligible decision"
    )
    with page.expect_popup() as event:
        page.get_by_role("link", name="Open Workspace").click()
    popup = event.value
    popup.wait_for_url("**/tickers/OLDONLY?identity=raw%3A*")
    assert "run_id=" not in popup.url
    expect(popup.get_by_role("heading", name="No eligible decision available")).to_be_visible()
    assert popup.evaluate("window.opener === null")
    assert page.url.rstrip("/") == ticker_server
    popup.close()
    field.fill("CERIONLY")
    expect(page.get_by_role("option")).to_contain_text("Local CERI company")
    page.get_by_role("option").click()
    expect(page.get_by_role("region", name="Ticker preview")).to_contain_text(
        "No eligible decision"
    )
    field.fill("unknown-ticker")
    expect(page.locator("[data-ticker-status]")).to_have_text("No matching local tickers.")
    page.route("**/api/tickers/search?*", lambda route: route.fulfill(status=503))
    field.fill("EO")
    expect(page.get_by_role("button", name="Retry search")).to_be_visible()
    page.unroute("**/api/tickers/search?*")
    page.get_by_role("button", name="Retry search").click()
    expect(page.get_by_role("option")).to_have_count(1)
    page.route(
        "**/api/tickers/search?*",
        lambda route: route.fulfill(
            json={
                "results": [
                    {
                        "identity": "raw:1",
                        "ticker": "SAFE",
                        "company_name": "<img src=x onerror=alert(1)>",
                        "qualifier": "Upload identity",
                    }
                ]
            }
        ),
    )
    field.fill("SAFE")
    expect(page.get_by_role("option")).to_contain_text("<img src=x onerror=alert(1)>")
    assert page.locator("#ticker-search-results img").count() == 0


def test_obsolete_search_and_preview_responses_cannot_overwrite(page, ticker_server):
    open_search(page, ticker_server)
    # Ignore AbortSignal deliberately: version guards must also protect races.
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (url, options) => {
        if (url.includes('q=SLOW')) {
          await new Promise(resolve => setTimeout(resolve, 800));
          return new Response(JSON.stringify({results:[{identity:'raw:999', ticker:'SLOW',
            company_name:'Obsolete', qualifier:'Old'}]}), {status:200});
        }
        return original(url, options);
      };
    }""")
    field = page.get_by_role("combobox", name="Ticker or company")
    field.fill("SLOW")
    page.wait_for_timeout(250)  # allow the deliberately delayed request to start
    field.fill("EO")
    expect(page.get_by_role("option")).to_contain_text("EOG Resources")
    page.wait_for_timeout(850)
    expect(page.get_by_role("option")).to_contain_text("EOG Resources")
    # Delay the EOG preview, then choose a CERI-only identity.
    page.evaluate("""() => {
      const original = window.fetch;
      window.fetch = async (url, options) => {
        if (url.includes('/EOG/summary')) {
          const response = await original(url);
          await new Promise(resolve => setTimeout(resolve, 700));
          return response;
        }
        return original(url, options);
      };
    }""")
    page.get_by_role("option").click()
    field.fill("CERIONLY")
    expect(page.get_by_role("option")).to_contain_text("CERIONLY")
    page.get_by_role("option").click()
    expect(page.locator(".ticker-preview h3")).to_have_text("CERIONLY")
    page.wait_for_timeout(850)
    expect(page.locator(".ticker-preview h3")).to_have_text("CERIONLY")


def test_preview_error_retry_and_reopening_cancelled_search(page, ticker_server):
    field = open_search(page, ticker_server)
    page.route("**/api/tickers/EOG/summary?*", lambda route: route.fulfill(status=503))
    page.get_by_role("option").click()
    expect(page.get_by_role("button", name="Retry preview")).to_be_visible()
    page.unroute("**/api/tickers/EOG/summary?*")
    page.get_by_role("button", name="Retry preview").click()
    expect(page.get_by_role("link", name="Open Workspace")).to_be_visible()
    field.fill("OLDONLY")
    page.get_by_role("button", name="Close ticker search").click()
    page.get_by_role("button", name="Ticker Search", exact=True).click()
    expect(page.get_by_role("option")).to_contain_text("OLDONLY")


@pytest.mark.parametrize("fallback", [False, True])
def test_workspace_opens_once_selected_ticker_run_and_safe_opener(page, ticker_server, fallback):
    select_eog(page, ticker_server)
    destination = page.get_by_role("link", name="Open Workspace").get_attribute("href")
    if fallback:
        page.evaluate("window.open = () => null")
    with page.expect_popup() as event:
        page.get_by_role("link", name="Open Workspace").click()
    popup = event.value
    popup.wait_for_url(ticker_server + destination)
    assert "/tickers/EOG?identity=" in popup.url and "run_id=7" in popup.url
    expect(popup.get_by_role("heading", name="EOG", exact=False).first).to_be_visible()
    assert popup.evaluate("window.opener === null")
    assert len(page.context.pages) == 2
    if not fallback:
        with page.expect_popup() as second_event:
            page.get_by_role("link", name="Open Workspace").click()
        second = second_event.value
        second.wait_for_url(ticker_server + destination)
        assert len(page.context.pages) == 3
        second.close()
    assert page.url.rstrip("/") == ticker_server
    expect(page.locator("#ticker-search")).to_be_visible()
    page.get_by_role("combobox", name="Ticker or company").fill("OLDONLY")
    expect(page.get_by_role("option")).to_contain_text("OLDONLY")
    popup.close()


def test_escape_preserves_existing_confirmation_and_shortcut_ignores_editors(page, ticker_server):
    open_search(page, ticker_server)
    page.goto(f"{ticker_server}/runs/7")
    page.get_by_role("button", name="Ticker Search", exact=True).click()
    # Use an existing run action, cancel it, and verify no mutation is submitted.
    form = page.locator("form[data-confirm]").first
    expect(form).to_be_attached()
    form.locator("button[type=submit], button:not([type])").first.click()
    expect(page.get_by_role("dialog")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.get_by_role("dialog")).to_be_hidden()
    expect(page.locator("#ticker-search")).to_be_visible()
    field = page.get_by_role("combobox", name="Ticker or company")
    field.focus()
    field.press("/")
    expect(field).to_have_value("/")
    field.press("Escape")
    expect(page.locator("#ticker-search")).to_be_hidden()
    page.get_by_role("button", name="Ticker Search", exact=True).click()
    page.get_by_role("button", name="Close ticker search").click()
    expect(page.locator("#ticker-search")).to_be_hidden()
    page.locator("[data-filter-decision]").focus()
    page.keyboard.press("/")
    expect(page.locator("#ticker-search")).to_be_hidden()


def test_narrow_layout_and_global_shared_shell(page, ticker_server):
    page.set_viewport_size({"width": 390, "height": 844})
    select_eog(page, ticker_server)
    dock = page.locator("#ticker-search").bounding_box()
    main = page.locator("main").bounding_box()
    assert dock["width"] <= 390
    assert main["y"] >= dock["y"] + dock["height"]
    page.get_by_role("button", name="Close ticker search").click()
    for route in ("/runs", "/runs/7", "/runs/7/tickers/EOG/chart", "/scoring", "/help"):
        response = page.goto(ticker_server + route)
        assert response.status == 200
        page.get_by_role("button", name="Ticker Search", exact=True).click()
        expect(page.get_by_role("combobox", name="Ticker or company")).to_be_focused()
        page.get_by_role("button", name="Close ticker search").click()
    page.set_viewport_size({"width": 1440, "height": 1000})
    select_eog(page, ticker_server)
    Path("output/playwright").mkdir(parents=True, exist_ok=True)
    page.screenshot(path="output/playwright/ticker-search-dock.png", full_page=True)
