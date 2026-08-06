from __future__ import annotations

import re
from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

ACCESSIBILITY_SURFACES = (
    "/",
    "/runs",
    "/market-regime",
    "/setup-lifecycle",
    "/setup-lifecycle/alerts",
    "/ceri",
    "/ceri/operations",
    "/history",
    "/winner-probability/operations",
    "/ib",
    "/scoring",
    "/settings",
    "/help",
)


def _contrast_failures(page: Page) -> list[dict[str, object]]:
    return page.evaluate(
        """
        () => {
          const parseColor = (value) => {
            const match = value.match(/rgba?\\(([^)]+)\\)/);
            if (!match) return null;
            const parts = match[1].split(",").map((part) => Number(part.trim()));
            return {
              red: parts[0],
              green: parts[1],
              blue: parts[2],
              alpha: parts.length === 4 ? parts[3] : 1,
            };
          };
          const composite = (foreground, background) => {
            const alpha = foreground.alpha + background.alpha * (1 - foreground.alpha);
            if (alpha === 0) return {red: 255, green: 255, blue: 255, alpha: 1};
            return {
              red: (
                foreground.red * foreground.alpha
                + background.red * background.alpha * (1 - foreground.alpha)
              ) / alpha,
              green: (
                foreground.green * foreground.alpha
                + background.green * background.alpha * (1 - foreground.alpha)
              ) / alpha,
              blue: (
                foreground.blue * foreground.alpha
                + background.blue * background.alpha * (1 - foreground.alpha)
              ) / alpha,
              alpha,
            };
          };
          const effectiveBackground = (element) => {
            const layers = [];
            for (let current = element; current; current = current.parentElement) {
              const color = parseColor(getComputedStyle(current).backgroundColor);
              if (color && color.alpha > 0) layers.push(color);
            }
            let result = {red: 255, green: 255, blue: 255, alpha: 1};
            for (const layer of layers.reverse()) result = composite(layer, result);
            return result;
          };
          const luminance = (color) => {
            const channels = [color.red, color.green, color.blue].map((channel) => {
              const normalized = channel / 255;
              return normalized <= 0.04045
                ? normalized / 12.92
                : ((normalized + 0.055) / 1.055) ** 2.4;
            });
            return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
          };
          const contrast = (first, second) => {
            const light = Math.max(luminance(first), luminance(second));
            const dark = Math.min(luminance(first), luminance(second));
            return (light + 0.05) / (dark + 0.05);
          };
          const selector = "body *:not(script):not(style):not(svg):not(path)";
          return Array.from(document.querySelectorAll(selector)).flatMap((element) => {
            const style = getComputedStyle(element);
            const rect = element.getBoundingClientRect();
            const visible = (
              style.display !== "none"
              && style.visibility !== "hidden"
              && Number(style.opacity) > 0
              && rect.width > 0
              && rect.height > 0
            );
            const ownText = Array.from(element.childNodes)
              .filter((node) => node.nodeType === Node.TEXT_NODE)
              .map((node) => node.textContent.trim())
              .filter(Boolean)
              .join(" ");
            if (!visible || !ownText) return [];

            const foreground = parseColor(style.color);
            if (!foreground) return [];
            const background = effectiveBackground(element);
            const ratio = contrast(composite(foreground, background), background);
            const fontSize = Number.parseFloat(style.fontSize);
            const fontWeight = Number.parseInt(style.fontWeight, 10) || 400;
            const largeText = fontSize >= 24 || (fontSize >= 18.66 && fontWeight >= 700);
            const required = largeText ? 3 : 4.5;
            if (ratio + 0.01 >= required) return [];

            return [{
              element: element.tagName.toLowerCase(),
              className: element.className || "",
              text: ownText.slice(0, 120),
              ratio: Number(ratio.toFixed(2)),
              required,
            }];
          });
        }
        """
    )


@pytest.mark.e2e
@pytest.mark.slow
@pytest.mark.parametrize("route", ACCESSIBILITY_SURFACES)
def test_core_surfaces_have_accessible_structure_and_contrast(
    page: Page,
    live_server_url: str,
    route: str,
) -> None:
    response = page.goto(f"{live_server_url}{route}")

    assert response is not None
    assert response.status == 200
    expect(page.locator("main#main-content")).to_have_count(1)
    expect(page.locator("h1")).to_have_count(1)
    expect(page.get_by_role("navigation", name="Primary navigation")).to_have_count(1)

    unnamed_controls = page.locator(
        "input:not([type=hidden]), select, textarea, button"
    ).evaluate_all(
        """
        (controls) => controls.flatMap((control) => {
          if (control.disabled || control.hidden) return [];
          const labelledBy = control.getAttribute("aria-labelledby");
          const labelledText = labelledBy
            ? labelledBy
                .split(/\\s+/)
                .map((id) => document.getElementById(id)?.textContent || "")
                .join(" ")
            : "";
          const name = (
            control.getAttribute("aria-label")
            || labelledText
            || Array.from(control.labels || []).map((label) => label.textContent).join(" ")
            || control.textContent
            || control.getAttribute("title")
            || ""
          ).trim();
          return name ? [] : [{tag: control.tagName.toLowerCase(), type: control.type || ""}];
        })
        """
    )
    assert unnamed_controls == []

    tables_without_headers = page.locator("table").evaluate_all(
        "tables => tables.filter((table) => !table.querySelector('th')).length"
    )
    assert tables_without_headers == 0

    color_only_cues = page.locator(
        ".alert, .status-pill, .badge-warning, .badge-danger"
    ).evaluate_all(
        """
        (elements) => elements.flatMap((element) => {
          if (getComputedStyle(element).display === "none" || element.hidden) return [];
          return element.textContent.trim() ? [] : [element.className];
        })
        """
    )
    assert color_only_cues == []
    assert _contrast_failures(page) == []


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
