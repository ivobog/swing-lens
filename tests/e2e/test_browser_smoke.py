from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect


@pytest.mark.e2e
@pytest.mark.slow
def test_dashboard_keyboard_navigation_and_responsive_layout(
    page: Page,
    live_server_url: str,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(live_server_url)

    expect(page).to_have_title("SwingLens Dashboard")
    expect(page.get_by_role("heading", name="Dashboard", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Daily CSV Upload")).to_be_visible()
    expect(page.get_by_role("button", name="Process")).to_be_visible()
    page.keyboard.press("Tab")
    expect(page.locator(".skip-link")).to_be_focused()
    expect(page.locator("main#main-content")).to_have_count(1)


@pytest.mark.e2e
@pytest.mark.slow
def test_ceri_operations_contains_wide_tables_on_mobile(
    page: Page,
    live_server_url: str,
) -> None:
    page.set_viewport_size({"width": 390, "height": 844})
    page.goto(f"{live_server_url}/ceri/operations")

    expect(page.get_by_role("heading", name="CERI Operations", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Provider Health", exact=True)).to_be_visible()
    assert page.evaluate(
        "Math.max(document.body.scrollWidth, document.documentElement.scrollWidth) "
        "<= window.innerWidth"
    )
    assert page.locator(".table-wrap").first.evaluate(
        "element => element.scrollWidth > element.clientWidth"
    )


@pytest.mark.e2e
@pytest.mark.slow
def test_dashboard_loads_without_browser_console_errors(
    page: Page,
    live_server_url: str,
) -> None:
    errors: list[str] = []
    page.on(
        "console",
        lambda message: errors.append(message.text) if message.type == "error" else None,
    )

    page.goto(live_server_url)
    expect(page).to_have_title("SwingLens Dashboard")

    assert errors == []


@pytest.mark.e2e
@pytest.mark.slow
def test_csv_upload_reaches_run_detail_in_real_browser(
    page: Page,
    live_server_url: str,
    tmp_path: Path,
) -> None:
    csv_path = tmp_path / "browser-unicode.csv"
    csv_path.write_text(
        "Symbol,Description,Sector,Price,Market capitalization\n"
        "MSFT,Microsoft,Technology,410,3050000000000\n"
        "SAP,SAP München,Technology,190,250000000000\n",
        encoding="utf-8",
    )

    page.goto(live_server_url)
    page.locator("#csv-file").set_input_files(csv_path)
    page.get_by_role("button", name="Process").click()

    expect(page).to_have_url(re.compile(r"/runs/\d+$"))
    first_run_url = page.url
    expect(page.get_by_role("heading", name=re.compile(r"Run \d+"))).to_be_visible()
    expect(page.get_by_text("browser-unicode.csv", exact=True)).to_be_visible()
    expect(page.get_by_role("heading", name="Raw CSV Preview")).to_be_visible()

    page.goto(live_server_url)
    page.locator("#csv-file").set_input_files(csv_path)
    page.get_by_role("button", name="Process").click()
    expect(page).to_have_url(re.compile(r"/runs/\d+$"))
    assert page.url != first_run_url


@pytest.mark.e2e
@pytest.mark.slow
def test_settings_page_exposes_research_only_ib_boundary(
    page: Page,
    live_server_url: str,
) -> None:
    page.goto(f"{live_server_url}/settings")

    expect(page.get_by_role("heading", name="Settings", exact=True)).to_be_visible()
    expect(page.get_by_text("Market data only", exact=True)).to_be_visible()
    expect(page.get_by_text("Read-only", exact=True)).to_be_visible()
    expect(page.locator("body")).not_to_contain_text("DATABASE_URL")
    expect(page.locator("body")).not_to_contain_text("postgres:postgres")
