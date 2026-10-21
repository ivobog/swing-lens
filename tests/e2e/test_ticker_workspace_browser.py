from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session
from test_ticker_search import CUTOFF, raw
from test_ticker_workspace import workspace_decision

from app.models.tables import PriceBar


@pytest.fixture(scope="module")
def workspace_server(live_server_url, live_server_database_url):
    engine = create_engine(live_server_database_url)
    with Session(engine) as db:
        workspace_decision(db, 6, cutoff=CUTOFF - timedelta(days=3))
        workspace_decision(db, 7)
        if db.scalar(select(PriceBar.id).where(PriceBar.ticker == "EOG").limit(1)) is None:
            db.execute(
                PriceBar.__table__.insert(),
                [
                    dict(
                        ticker="EOG",
                        bar_date=date(2026, 1, 1) + timedelta(days=i),
                        timeframe="1 day",
                        open=100 + i / 10,
                        high=102 + i / 10,
                        low=99 + i / 10,
                        close=101 + i / 10,
                        volume=1000 + i,
                        source="TEST",
                        what_to_show="TRADES",
                    )
                    for i in range(240)
                ],
            )
        raw(db, "UNSCORED", "Known without a decision", 1)
        for table in ("upload_runs", "pipeline_runs", "core_calculation_evidence"):
            db.execute(
                text(
                    f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                    f"(SELECT max(id) FROM {table}), true)"
                )
            )
        db.commit()
    engine.dispose()
    return live_server_url


def test_workspace_header_chart_cards_and_disclosure_state(page, workspace_server):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    response = page.goto(workspace_server + "/tickers/EOG")
    assert response.status == 200
    expect(page.get_by_role("heading", name="EOG", exact=True)).to_be_visible()
    expect(page.locator(".workspace-metrics")).to_contain_text("7.00")
    expect(page.get_by_label("Decision Run", exact=True)).to_have_value("7")
    expect(page.locator("[aria-label='Primary warnings']")).to_contain_text("Market Risk Off")
    expect(page.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    expect(page.locator("#ticker-chart canvas").first).to_be_visible()
    expect(
        page.get_by_text("Current cached prices; research levels from Run 7.", exact=True)
    ).to_be_visible()
    sections = page.locator("[data-workspace-section]")
    assert sections.count() == 10
    for title in (
        "Price Chart",
        "Combined Decision",
        "Fundamentals",
        "Technicals",
        "Risk Context",
        "Warnings and Missing Data",
    ):
        expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
    fundamental = page.locator('[data-workspace-section="2"]')
    assert fundamental.get_attribute("open") is None
    fundamental.locator("summary").click()
    expect(fundamental).to_have_attribute("open", "")
    expect(fundamental).to_contain_text("historical-model")
    expect(fundamental).to_contain_text("0.00")
    page.get_by_label("Decision Run", exact=True).select_option("6")
    page.get_by_role("button", name="View decision").click()
    expect(page.get_by_label("Decision Run", exact=True)).to_have_value("6")
    expect(fundamental).to_have_attribute("open", "")
    expect(page.locator(".workspace-metrics")).to_contain_text("6.00")
    expect(page.locator("[data-chart-summary]")).to_contain_text("Research stop 61")
    expect(page.locator("[aria-label='Selected decision provenance']")).to_contain_text("Run 6")
    assert "run_id=6" in page.url
    page.reload()
    expect(page.get_by_label("Decision Run", exact=True)).to_have_value("6")
    expect(fundamental).to_have_attribute("open", "")
    page.get_by_text("Chart data summary", exact=True).click()
    expect(page.locator("[data-chart-fallback-body]")).to_contain_text("Latest")
    assert errors == []


def test_workspace_resizes_with_dock_and_narrow_viewport(page, workspace_server):
    page.goto(workspace_server + "/tickers/EOG?run_id=7")
    expect(page.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    chart = page.locator("#ticker-chart")
    original = chart.bounding_box()["width"]
    page.get_by_role("button", name="Ticker Search", exact=True).click()
    expect(page.get_by_role("combobox", name="Ticker or company")).to_be_focused()
    assert chart.bounding_box()["width"] < original
    # Lightweight Charts must adapt its actual canvas, not only its wrapper.
    page.wait_for_function("""() => {
      const chart = document.querySelector('#ticker-chart');
      const canvas = chart.querySelector('canvas');
      return canvas && canvas.getBoundingClientRect().width <= chart.clientWidth;
    }""")
    page.get_by_role("combobox", name="Ticker or company").press("Escape")
    expect(page.locator("#ticker-search")).to_be_hidden()
    assert chart.bounding_box()["width"] == original
    page.set_viewport_size({"width": 390, "height": 844})
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    expect(page.get_by_label("Decision Run", exact=True)).to_be_visible()
    page.get_by_role("heading", name="Risk Context", exact=True).click()
    expect(page.locator('[data-workspace-section="4"]')).to_contain_text("71.00")
    page.get_by_role("button", name="Ticker Search", exact=True).click()
    dock = page.locator("#ticker-search").bounding_box()
    main = page.locator("main").bounding_box()
    assert main["y"] >= dock["y"] + dock["height"]
    page.get_by_role("button", name="Close ticker search").click()


def test_dock_new_destination_and_independent_workspace(page, workspace_server):
    page.goto(workspace_server)
    page.keyboard.press("/")
    field = page.get_by_role("combobox", name="Ticker or company")
    field.fill("EO")
    expect(page.get_by_role("option")).not_to_have_count(0)
    field.press("Enter")
    link = page.get_by_role("link", name="Open Workspace")
    expect(link).to_be_visible()
    destination = link.get_attribute("href")
    assert urlsplit(destination).path == "/tickers/EOG"
    params = parse_qs(urlsplit(destination).query)
    assert params["run_id"] == ["7"] and params["identity"][0].startswith(("raw:", "ceri:"))
    with page.expect_popup() as opened:
        link.click()
    workspace = opened.value
    workspace.wait_for_url(workspace_server + destination)
    expect(workspace.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    assert workspace.evaluate("opener === null")
    assert page.url.rstrip("/") == workspace_server
    expect(field).to_be_visible()
    workspace.close()


def test_missing_chart_and_unavailable_decision_stay_usable(page, workspace_server):
    page.route(
        "**/api/tickers/EOG/chart-data?*",
        lambda route: route.fulfill(
            json={
                "ticker": "EOG",
                "bars": [],
                "message": "No cached chart data available.",
            }
        ),
    )
    page.goto(workspace_server + "/tickers/EOG?run_id=7")
    expect(page.locator("#ticker-chart-empty")).to_have_text("No cached chart data available.")
    expect(page.locator('[data-workspace-section="1"]')).to_contain_text("7.00")
    response = page.goto(workspace_server + "/tickers/EOG?run_id=999999")
    assert response.status == 404
    expect(
        page.get_by_role("heading", name="Run 999999 is unavailable for this identity")
    ).to_be_visible()
    expect(page.get_by_label("Decision Run", exact=True)).to_have_value("999999")
    response = page.goto(workspace_server + "/tickers/UNSCORED")
    assert response.status == 200
    expect(page.get_by_role("heading", name="No eligible decision available")).to_be_visible()


def test_workspace_visual_review(page, workspace_server):
    page.goto(workspace_server + "/tickers/EOG?run_id=7")
    expect(page.locator("[data-chart-summary]")).to_contain_text("240 daily bars")
    Path("output/playwright").mkdir(parents=True, exist_ok=True)
    page.screenshot(path="output/playwright/ticker-workspace-desktop.png", full_page=True)
    page.set_viewport_size({"width": 390, "height": 844})
    page.screenshot(path="output/playwright/ticker-workspace-mobile.png", full_page=True)
