import faulthandler
import json
import socket
from threading import Thread
from time import monotonic, sleep
from urllib.parse import parse_qs, urlsplit

import pytest
import uvicorn
from evidence_output import artifact_path, output_directory
from market_sector_dashboard_fixture import create_fixture_app
from playwright.sync_api import expect, sync_playwright

EVIDENCE = output_directory("market-sector-intelligence-d4")
TECHNICAL_PATH = "/api/market-sector-intelligence/technicals/preview"


@pytest.mark.parametrize("scenario", ["technical", "chart", "missing", "equal_policy", "calendar"])
def test_shared_configuration_warning_uses_real_schemas_and_safe_text(
    diagnostic_browser, browser_fixture_server, scenario,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    base = browser_fixture_server
    malicious = '<img data-msi-xss src=x onerror="window.msiInjected=true">'
    endpoint = "charts/price-series" if scenario == "chart" else "technicals/preview"

    def alter_metadata(route):
        response = route.fetch()
        payload = response.json()
        if scenario == "technical":
            payload["d2_configuration_digest"] = malicious
        elif scenario == "chart":
            payload["configuration_digest"] = malicious
        elif scenario == "missing":
            payload.pop("d2_configuration_digest")
        elif scenario == "calendar":
            payload["calendar_version"] = "swinglens-us-equities-v1"
        else:
            # Different native policy/model identifiers are not the shared lineage.
            payload["effective_configuration"]["adapter_policy_digest"] = malicious
        route.fulfill(response=response, json=payload)

    page.route("**/api/market-sector-intelligence/" + endpoint + "*", alter_metadata)
    errors = loaded(page, base)
    if scenario == "equal_policy":
        expect(page.locator("#msi-mismatch")).to_be_hidden()
    else:
        expect(page.locator("#msi-mismatch")).to_be_visible()
        expect(page.locator("#msi-mismatch")).to_contain_text(
            "calendar versions differ" if scenario == "calendar" else
            "metadata is unavailable" if scenario == "missing" else "configuration differs"
        )
    page.get_by_text("Source identities and separate cutoffs", exact=True).click()
    assert page.locator("img[data-msi-xss]").count() == 0
    assert page.evaluate("window.msiInjected") is None
    assert errors == []
    assert metrics(page, base)["forbidden_calls"] == 0
    page.screenshot(
        path=artifact_path(str(EVIDENCE / ("lineage-" + scenario + ".png"))), full_page=True
    )
    context.close()


@pytest.fixture(scope="module")
def browser_fixture_server():
    app, close = create_fixture_app()
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="error")
    )
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    start = monotonic()
    while not server.started and monotonic() - start < 15:
        sleep(0.05)
    assert server.started
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    try:
        yield "http://127.0.0.1:" + str(port)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        listener.close()
        close()


@pytest.fixture(scope="module")
def diagnostic_browser():
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture(autouse=True)
def close_case_browser_contexts(diagnostic_browser):
    """Failed route callbacks must stay with their owning test case."""
    try:
        yield
    finally:
        for context in diagnostic_browser.contexts:
            context.close()


def loaded(page, base, mode="ready"):
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    try:
        page.goto(base + "/__fixture__/mode/" + mode)
        expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
    except Exception:
        # Record the blocked backend threads after the original deadline fails.
        with (output_directory("msi-readiness") / "threads.txt").open("w") as stream:
            faulthandler.dump_traceback(file=stream, all_threads=True)
        raise
    assert page.locator("#msi-ranks tbody tr").count() == 11
    assert page.locator("#msi-technicals tbody tr").count() == 11
    return errors


def metrics(page, base):
    return page.request.get(base + "/__fixture__/metrics").json()


def test_desktop_keyboard_native_charts_history_deeplinks_and_no_technical_recalculation(
    diagnostic_browser,
    browser_fixture_server,
    record_property,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    base = browser_fixture_server
    started = monotonic()
    errors = loaded(page, base)
    before = metrics(page, base)
    assert "11/11 V4" in page.locator("#msi-status-technical").inner_text()
    assert page.get_by_role("link", name="Market Sector Intelligence", exact=True).count() == 1
    assert page.locator("#msi-scatter [role=button]").count() >= 11
    assert "3 actual saved captures" in page.locator("#msi-history-note").inner_text()
    assert "0 incompatible" in page.locator("#msi-history-note").inner_text()
    page.screenshot(path=artifact_path(str(EVIDENCE / "desktop.png")), full_page=True)
    page.locator("#msi-mode").select_option("absolute")
    page.locator("#msi-horizon").select_option("21")
    page.locator("#msi-horizon").select_option("5")
    expect(page.locator("#msi-line-values tbody tr")).to_have_count(6, timeout=30000)
    page.locator('#msi-lines circle[tabindex="0"]').first.focus()
    expect(page.locator("#msi-line-tooltip")).to_contain_text("CHART_PRICE_HISTORY")
    expect(page.locator("#msi-line-tooltip")).to_contain_text("index")
    assert "horizon=5" in page.url
    page.get_by_role("button", name="Sector ↕", exact=True).focus()
    page.keyboard.press("Enter")
    assert page.locator("#msi-ranks th[aria-sort]").get_attribute("aria-sort") == "ascending"
    page.locator("#msi-selection input[value=XLB]").check()
    page.locator("#msi-focus").select_option("XLF")
    expect(page.locator("#msi-detail-title")).to_contain_text("XLF")
    page.locator("#msi-snapshot").select_option("1")
    expect(page.locator("#msi-mismatch")).to_be_visible()
    assert "Market sessions differ" in page.locator("#msi-mismatch").inner_text()
    assert "PERSISTED" in page.locator("#msi-rotation-source").inner_text()
    assert (
        page.get_by_role("link", name="Saved snapshot JSON ↗").get_attribute("href").endswith("/1")
    )
    page.get_by_text("Source identities and separate cutoffs", exact=True).click()
    assert "configuration_digest" in page.locator("#msi-detail").inner_text()
    marker = page.locator("#msi-scatter [role=button]").first
    marker.focus()
    page.keyboard.press("Enter")
    assert "PP" in page.locator("#msi-chart-tooltip").inner_text()
    assert "PERSISTED" in page.locator("#msi-chart-tooltip").inner_text()
    after = metrics(page, base)
    assert after["requests"][TECHNICAL_PATH] == before["requests"][TECHNICAL_PATH]
    assert after["forbidden_calls"] == 0
    assert set(after["sql"]) <= {"SELECT"}
    assert errors == []
    record_property("desktop_load_seconds", monotonic() - started)
    record_property("fixture_process_rss_bytes", after["rss_bytes"])
    record_property("technical_requests_during_controls", 0)
    record_property(
        "browser_resource_entries",
        json.dumps(
            page.evaluate(
                "performance.getEntriesByType('resource').map(r => "
                "({name: new URL(r.name).pathname, duration: r.duration, bytes: r.transferSize}))"
            )
        ),
    )
    page.goto(base + "/sector-rotation?etf=XLK&horizon=21&snapshot_id=999")
    expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
    assert "not been substituted" in page.locator("#msi-rotation-source").inner_text()
    assert "Unavailable" in page.locator("#msi-status-archive").inner_text()
    assert page.locator("#msi-lines polyline").count() > 0
    page.get_by_role("link", name="Sector mapping ↗", exact=True).click()
    expect(page.locator("#market-sector-heading")).to_be_visible()
    context.close()


def test_mobile_layout_keyboard_focus_and_accessible_numeric_alternatives(
    diagnostic_browser,
    browser_fixture_server,
):
    context = diagnostic_browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, device_scale_factor=1
    )
    page = context.new_page()
    errors = loaded(page, browser_fixture_server)
    assert page.evaluate("document.documentElement.scrollWidth === window.innerWidth")
    page.locator("#msi-focus").focus()
    assert page.evaluate("document.activeElement.id") == "msi-focus"
    page.keyboard.press("ArrowDown")
    page.keyboard.press("Enter")
    page.get_by_text("Accessible line values (common start = 100)", exact=True).click()
    assert page.locator("#msi-line-values tbody tr").count() == 127
    page.locator("#msi-heatmap").scroll_into_view_if_needed()
    assert page.locator("#msi-heatmap tbody tr").count() == 11
    page.screenshot(path=artifact_path(str(EVIDENCE / "mobile.png")), full_page=True)
    assert errors == []
    context.close()


def test_browser_history_restores_default_explicit_and_optional_url_state(
    diagnostic_browser,
    browser_fixture_server,
    record_property,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    base = browser_fixture_server
    failed_requests = []
    page.on("requestfailed", lambda request: failed_requests.append(request.url))
    errors = loaded(page, base)
    before = metrics(page, base)

    def observe(stage):
        value = page.evaluate(
            "({url: location.pathname + location.search, entries: history.length, "
            "etf: document.getElementById('msi-focus').value, "
            "detail: document.getElementById('msi-detail-title').textContent, "
            "horizon: document.getElementById('msi-horizon').value, "
            "mode: document.getElementById('msi-mode').value, "
            "snapshot: document.getElementById('msi-snapshot').value})"
        )
        record_property("history_" + stage, json.dumps(value))
        return value

    def expect_etf(symbol):
        expect(page.locator("#msi-focus")).to_have_value(symbol)
        expect(page.locator("#msi-detail-title")).to_contain_text(symbol + " · ")

    try:
        # A: a parameter-free entry must restore its defaults, never stale state.
        initial = observe("initial")
        assert initial["url"] == "/sector-rotation"
        expect_etf("XLK")
        page.locator("#msi-focus").select_option("XLF")
        expect_etf("XLF")
        assert parse_qs(urlsplit(page.url).query)["etf"] == ["XLF"]
        entries = observe("explicit")["entries"]
        assert entries == initial["entries"] + 1
        page.go_back()
        observe("default_back")
        assert urlsplit(page.url).query == ""
        expect_etf("XLK")
        assert page.evaluate("history.length") == entries
        page.locator("#msi-focus").scroll_into_view_if_needed()
        page.screenshot(path=artifact_path(str(EVIDENCE / "history-default-back.png")))
        page.go_forward()
        expect_etf("XLF")
        assert parse_qs(urlsplit(page.url).query)["etf"] == ["XLF"]
        assert observe("explicit_forward")["entries"] == entries
        page.go_back()
        expect_etf("XLK")

        # C: restore defaults when optional parameters disappear from the URL.
        page.locator("#msi-horizon").select_option("5")
        expect(page.locator("#msi-line-values tbody tr")).to_have_count(6, timeout=30000)
        page.locator("#msi-mode").select_option("absolute")
        page.locator("#msi-snapshot").select_option("1")
        page.locator("#msi-selection input[value=XLB]").check()
        expect(page.locator("#msi-line-values thead")).to_contain_text("XLB index")
        optional_entries = observe("optional_explicit")["entries"]
        page.go_back()
        expect(page.locator("#msi-selection input[value=XLB]")).not_to_be_checked()
        expect(page.locator("#msi-line-values thead")).not_to_contain_text("XLB index")
        page.go_back()
        expect(page.locator("#msi-snapshot")).to_have_value("")
        assert "snapshot_id" not in parse_qs(urlsplit(page.url).query)
        expect(page.locator("#msi-rotation-source")).to_contain_text("PREVIEW")
        page.go_back()
        expect(page.locator("#msi-mode")).to_have_value("relative")
        page.go_back()
        expect_etf("XLK")
        expect(page.locator("#msi-horizon")).to_have_value("126")
        expect(page.locator("#msi-mode")).to_have_value("relative")
        expect(page.locator("#msi-line-values tbody tr")).to_have_count(127)
        assert urlsplit(page.url).query == ""
        assert sorted(page.locator("#msi-selection input:checked").evaluate_all(
            "inputs => inputs.map(input => input.value)"
        )) == ["XLE", "XLF", "XLK"]
        assert observe("all_defaults_back")["entries"] == optional_entries
        after_controls = metrics(page, base)
        assert after_controls["requests"][TECHNICAL_PATH] == before["requests"][TECHNICAL_PATH]
        chart_path = "/api/market-sector-intelligence/charts/price-series"
        assert after_controls["requests"][chart_path] == before["requests"][chart_path] + 1
        record_property("history_technical_requests_during_controls", 0)

        # B: an explicit deep link survives a document navigation and Back.
        page.goto(base + "/sector-rotation?etf=XLF")
        expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
        expect_etf("XLF")
        deep_link = page.url
        deep_entries = page.evaluate("history.length")
        deep_metrics = metrics(page, base)
        page.get_by_role("link", name="Sector mapping ↗", exact=True).click()
        expect(page.locator("#market-sector-heading")).to_be_visible()
        assert page.evaluate("history.length") == deep_entries + 1
        page.go_back()
        expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
        assert page.url == deep_link
        expect_etf("XLF")
        assert observe("deep_link_back")["entries"] == deep_entries + 1
        # A fresh document may load D3 once; same-document history never does.
        after = metrics(page, base)
        document_technical_reads = (
            after["requests"][TECHNICAL_PATH] - deep_metrics["requests"][TECHNICAL_PATH]
        )
        assert 0 <= document_technical_reads <= 1
        assert page.evaluate(
            "performance.getEntriesByType('resource').filter(r => "
            "new URL(r.name).pathname === "
            "'/api/market-sector-intelligence/technicals/preview').length"
        ) == 1
        page.locator("#msi-focus").scroll_into_view_if_needed()
        page.screenshot(path=artifact_path(str(EVIDENCE / "history-deep-link-back.png")))
        assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
        assert errors == [] and failed_requests == []
        record_property("history_console_errors", 0)
        record_property("history_failed_requests", 0)
    finally:
        context.close()


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("empty", "No saved snapshots"),
        ("partial", "10/11 V4 · 0/11 V5"),
        ("qqq", "0/11 V4 · 0/11 V5"),
        ("disabled", "DISABLED"),
        ("unactivated", "Unavailable"),
        ("unavailable", "Unavailable"),
    ],
)
def test_truthful_empty_partial_disabled_and_panel_failures(
    diagnostic_browser,
    browser_fixture_server,
    mode,
    expected,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server, mode)
    assert expected in page.locator(".msi-status-grid").inner_text()
    if mode in {"partial", "qqq"}:
        assert "V5_COHORT_NOT_COMPARABLE" in page.locator("#msi-cohort").inner_text()
        assert all(
            "N/A / N/A" in row for row in page.locator("#msi-technicals tbody tr").all_inner_texts()
        )
    if mode == "qqq":
        assert "12/12 current" in page.locator("#msi-coverage").inner_text()
        assert "QQQ_CONTEXT_UNAVAILABLE" in page.locator("#msi-cohort").inner_text()
    if mode in {"unactivated", "unavailable"}:
        assert page.locator("#msi-lines polyline").count() > 0
        assert "COMPLETE" in page.locator("#msi-status-rotation").inner_text()
    if mode == "empty":
        assert "No saved rotation snapshots yet" in page.locator("#msi-history-note").inner_text()
        assert "N/A" in page.locator("#msi-heatmap").inner_text()
    assert "Traceback" not in page.locator("#msi-dashboard").inner_text()
    assert errors == []
    page.screenshot(path=artifact_path(str(EVIDENCE / (mode + ".png"))), full_page=True)
    assert metrics(page, browser_fixture_server)["forbidden_calls"] == 0
    context.close()


def test_vision_overview_and_paired_chart_match_actual_native_responses(
    diagnostic_browser, browser_fixture_server,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    with page.expect_response("**/rotation/preview") as rotation_response, page.expect_response(
        "**/technicals/preview"
    ) as technical_response:
        errors = loaded(page, browser_fixture_server)
    rotation, technical = rotation_response.value.json(), technical_response.value.json()
    expected_counts = {
        state: sum(row["rotation_state"] == state for row in rotation["sectors"])
        for state in ("Leading", "Improving", "Weakening", "Lagging")
    }
    counts = page.locator("#msi-quadrants > div").evaluate_all(
        "items => Object.fromEntries(items.map(item => "
        "[item.querySelector('dt').textContent, Number(item.querySelector('dd').textContent)]))"
    )
    assert counts == expected_counts and sum(counts.values()) == 11
    assert "COMPLETE · 11/11 eligible" in page.locator("#msi-summary-coverage").inner_text()
    assert rotation["market_session"] in page.locator("#msi-summary-source").inner_text()
    assert "benchmark SPY" in page.locator("#msi-summary-source").inner_text()
    expected_strongest = sorted(rotation["sectors"], key=lambda r: r["rank"])[:3]
    expected_improvers = sorted(
        (r for r in rotation["sectors"] if r["relative_momentum_pp"] > 0),
        key=lambda r: (-r["relative_momentum_pp"], r["rank"], r["etf_symbol"]),
    )[:3]
    assert page.locator("#msi-strongest button").all_text_contents() == [
        r["etf_symbol"] for r in expected_strongest
    ]
    assert page.locator("#msi-improvers button").all_text_contents() == [
        r["etf_symbol"] for r in expected_improvers
    ]
    for model, name, key in (
        ("V4", "v4", "final_v4_score"), ("V5", "v5", "technical_composite_score"),
    ):
        bars = page.locator(f'#msi-technical-chart rect[data-model="{model}"]').evaluate_all(
            "items => Object.fromEntries(items.map(item => "
            "[item.dataset.etf, Number(item.dataset.value)]))"
        )
        assert bars == {row["etf_symbol"]: row[name][key] for row in technical["rows"]}
    assert "not a calibrated probability of future returns" in page.locator(
        "section[aria-labelledby=msi-technicals-title]"
    ).inner_text()
    before = metrics(page, browser_fixture_server)
    page.locator("#msi-strongest button").first.click()
    expect(page.locator("#msi-focus")).to_have_value(expected_strongest[0]["etf_symbol"])
    after = metrics(page, browser_fixture_server)
    assert after["requests"] == before["requests"]
    assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
    assert errors == []
    page.screenshot(path=artifact_path(str(EVIDENCE / "vision-desktop.png")), full_page=True)
    page.locator("#msi-technical-chart").screenshot(
        path=artifact_path(str(EVIDENCE / "vision-native-comparison.png"))
    )
    context.close()


@pytest.mark.parametrize("mode", ["empty", "partial", "disabled"])
def test_vision_summary_withholds_highlights_for_incomplete_cohorts(
    diagnostic_browser, browser_fixture_server, mode,
):
    context = diagnostic_browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server, mode)
    assert page.locator("#msi-strongest button, #msi-improvers button").count() == 0
    expect(page.locator("#msi-summary-note")).to_be_visible()
    expect(page.locator("#msi-summary-note")).to_contain_text("highlights withheld")
    counts = sum(int(value) for value in page.locator("#msi-quadrants dd").all_text_contents())
    assert counts == (10 if mode == "partial" else 0)
    assert page.locator('#msi-technical-chart rect[data-model="V5"]').count() == 0
    assert page.locator('#msi-technical-chart rect[data-model="V4"]').count() == (
        10 if mode == "partial" else 0
    )
    if mode == "partial":
        expect(page.locator("#msi-summary-note")).to_contain_text("XLK")
        expect(page.locator("#msi-summary-note")).to_contain_text("STALE")
    assert page.evaluate("document.documentElement.scrollWidth === window.innerWidth")
    assert errors == [] and metrics(page, browser_fixture_server)["forbidden_calls"] == 0
    page.screenshot(
        path=artifact_path(str(EVIDENCE / ("vision-" + mode + "-mobile.png"))), full_page=True
    )
    context.close()


def test_focused_saved_trails_restore_on_back_forward_and_refresh_without_synthetic_points(
    diagnostic_browser, browser_fixture_server,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server)
    before = metrics(page, browser_fixture_server)
    page.locator("#msi-focus").select_option("XLB")

    def focused(symbol):
        expect(page.locator("#msi-focus")).to_have_value(symbol)
        expect(page.locator("#msi-trail-focus")).to_contain_text(symbol + " focused")
        assert page.locator(
            f'#msi-scatter polyline[data-etf="{symbol}"][data-focused="true"]'
        ).count() == 1  # October 16 → 19 only. October 20 was never captured.
        assert page.locator(
            '#msi-scatter polyline[data-focused="true"]'
        ).get_attribute("stroke-width") == "3.5"
        assert page.locator(
            f'#msi-scatter g[data-etf="{symbol}"][data-source="PERSISTED"][data-focused="true"]'
        ).evaluate_all("items => items.map(item => item.dataset.session).sort()") == [
            "2026-10-16", "2026-10-19", "2026-10-21"
        ]
        assert page.locator(
            f'#msi-scatter g[data-etf="{symbol}"][data-source="PREVIEW"][data-focused="true"]'
        ).count() == 1

    focused("XLB")  # A focused ETF is visible even outside the price-line multiselection.
    assert not page.locator('#msi-selection input[value="XLB"]').is_checked()
    expect(page.locator(".msi-rank-movement")).to_contain_text("unavailable")
    page.go_back()
    focused("XLK")
    page.go_forward()
    focused("XLB")
    assert metrics(page, browser_fixture_server)["requests"] == before["requests"]
    page.screenshot(path=artifact_path(str(EVIDENCE / "vision-focused-trail.png")), full_page=True)
    page.reload()
    expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
    focused("XLB")
    after_reload = metrics(page, browser_fixture_server)
    assert after_reload["requests"][TECHNICAL_PATH] == before["requests"][TECHNICAL_PATH] + 1
    page.locator('#msi-ranks button[aria-label="Inspect XLF"]').click()
    focused("XLF")
    page.locator("#msi-technical-chart text[role=button]").filter(has_text="XLE").focus()
    page.keyboard.press("Enter")
    focused("XLE")
    page.locator("#msi-trails").uncheck()
    assert page.locator("#msi-scatter polyline").count() == 0
    page.locator("#msi-trails").check()
    focused("XLE")
    after = metrics(page, browser_fixture_server)
    assert after["requests"] == after_reload["requests"]
    assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
    assert errors == []
    context.close()


def test_native_chart_classifications_render_as_safe_text_on_mobile(
    diagnostic_browser, browser_fixture_server,
):
    context = diagnostic_browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    malicious = '<img data-msi-xss src=x onerror="window.msiInjected=true">' + "x" * 200

    def alter_classification(route):
        response = route.fetch()
        payload = response.json()
        payload["rows"][0]["v4"]["classification"] = malicious
        route.fulfill(response=response, json=payload)

    page.route("**/technicals/preview", alter_classification)
    errors = loaded(page, browser_fixture_server)
    expect(page.locator("#msi-technicals tbody")).to_contain_text(malicious)
    assert page.locator("img[data-msi-xss]").count() == 0
    assert page.evaluate("window.msiInjected") is None
    assert page.evaluate("document.documentElement.scrollWidth === window.innerWidth")
    assert errors == [] and metrics(page, browser_fixture_server)["forbidden_calls"] == 0
    context.close()


def test_stored_rank_movement_and_transient_hover_use_actual_saved_evidence(
    diagnostic_browser, browser_fixture_server,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server)
    page.screenshot(path=artifact_path(str(EVIDENCE / "vision-overview.png")))
    first = page.request.get(
        browser_fixture_server + "/api/market-sector-intelligence/rotation/snapshots/1"
    ).json()
    second = page.request.get(
        browser_fixture_server + "/api/market-sector-intelligence/rotation/snapshots/2"
    ).json()
    before = metrics(page, browser_fixture_server)
    page.locator("#msi-snapshot").select_option("2")
    prior = next(row for row in first["sectors"] if row["etf_symbol"] == "XLK")
    current = next(row for row in second["sectors"] if row["etf_symbol"] == "XLK")
    movement = page.locator(".msi-rank-movement")
    expect(movement).to_contain_text(first["market_session"] + " → " + second["market_session"])
    expect(movement).to_contain_text(f'#{prior["rank"]} → #{current["rank"]}')
    expect(movement).to_contain_text("no preview rank included")
    expect(page.locator("#msi-summary-source")).to_contain_text("PERSISTED")
    expect(page.locator("#msi-mismatch")).to_be_visible()
    saved_url = page.url
    marker = page.locator('#msi-scatter g[data-etf="XLF"][data-session="2026-10-19"]')
    marker.hover()
    path = page.locator('#msi-scatter polyline[data-etf="XLF"]')
    expect(path).to_have_attribute("data-emphasized", "true")
    expect(path).to_have_attribute("stroke-width", "3.5")
    page.locator("#msi-summary-title").hover()
    expect(page.locator('#msi-scatter polyline[data-etf="XLK"]')).to_have_attribute(
        "data-emphasized", "true"
    )
    marker.focus()
    expect(path).to_have_attribute("data-emphasized", "true")
    page.locator("#msi-focus").focus()
    expect(path).to_have_attribute("data-emphasized", "false")
    assert page.url == saved_url
    expect(page.locator("#msi-focus")).to_have_value("XLK")
    after = metrics(page, browser_fixture_server)
    assert after["requests"] == before["requests"]
    assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
    assert errors == []
    page.screenshot(
        path=artifact_path(str(EVIDENCE / "vision-consecutive-saved.png")), full_page=True
    )
    context.close()


@pytest.mark.parametrize("key", ["Enter", "Space"])
@pytest.mark.parametrize("chart", ["scatter_preview", "scatter_saved", "technical"])
def test_chart_keyboard_activation_retains_observation_and_tab_order(
    diagnostic_browser, browser_fixture_server, chart, key,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server)
    before = metrics(page, browser_fixture_server)
    if chart == "technical":
        host, symbol = "#msi-technical-chart", "XLE"
        marker = page.locator(host + " text[role=button]").filter(has_text=symbol)
    else:
        host, symbol = "#msi-scatter", "XLF"
        source = "PREVIEW" if chart == "scatter_preview" else "PERSISTED"
        marker = page.locator(host + f' g[data-etf="{symbol}"][data-source="{source}"]')
        if chart == "scatter_saved":
            marker = marker.filter(has=page.locator("title")).and_(
                page.locator('[data-session="2026-10-19"]')
            )
        assert page.locator('#msi-scatter g[data-etf="XLF"]').count() == 4
    label = marker.get_attribute("aria-label")
    controls = page.locator(host + ' [role=button][tabindex="0"]').evaluate_all(
        "items => items.map(item => item.getAttribute('aria-label'))"
    )
    index = controls.index(label)
    assert 0 < index < len(controls) - 1
    # Establish keyboard modality, then activate the exact saved/preview control.
    page.keyboard.press("Tab")
    marker.focus()
    scroll = page.evaluate("scrollY")
    entries = page.evaluate("history.length")
    page.keyboard.down(key)
    expect(marker).to_be_focused()  # UI-F01 previously leaves BODY focused here.
    page.keyboard.down(key)  # Held-key repeat must not add another selection entry.
    page.keyboard.up(key)
    expect(marker).to_be_focused()
    assert page.evaluate("history.length") == entries + 1
    assert page.evaluate("scrollY") == scroll
    assert marker.evaluate(
        "el => el.matches(':focus-visible') && getComputedStyle(el).outlineStyle === 'solid'"
        " && parseFloat(getComputedStyle(el).outlineWidth) >= 3"
    )
    identity = marker.get_attribute("data-focus-key")
    assert identity and json.loads(identity)[:2] == [
        "technical" if chart == "technical" else "scatter", symbol
    ]
    expect(page.locator("#msi-focus")).to_have_value(symbol)
    expect(page.locator("#msi-detail-title")).to_contain_text(symbol + " · ")
    assert parse_qs(urlsplit(page.url).query)["etf"] == [symbol]
    expect(page.locator(f'#msi-scatter g[data-etf="{symbol}"]').first).to_have_attribute(
        "data-focused", "true"
    )
    expect(page.locator("#msi-technical-chart text[role=button]").filter(
        has_text=symbol
    )).to_have_attribute("font-weight", "800")
    assert page.locator(host + ' [role=button][tabindex="0"]').evaluate_all(
        "items => items.map(item => item.getAttribute('aria-label'))"
    ) == controls
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == controls[index + 1]
    page.keyboard.press("Shift+Tab")
    expect(marker).to_be_focused()
    assert marker.get_attribute("data-focus-key") == identity
    page.keyboard.press("Shift+Tab")
    assert page.evaluate("document.activeElement.getAttribute('aria-label')") == controls[index - 1]
    page.keyboard.press("Tab")
    expect(marker).to_be_focused()
    page.screenshot(path=artifact_path(str(EVIDENCE / ("keyboard-" + chart + "-" + key + ".png"))))
    after = metrics(page, browser_fixture_server)
    assert after["requests"] == before["requests"]
    assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
    assert errors == []
    context.close()


def test_selection_origins_keep_focus_and_hover_does_not_select(
    diagnostic_browser, browser_fixture_server,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server)
    before = metrics(page, browser_fixture_server)
    selector = page.locator("#msi-focus")
    selector.focus()
    selector.select_option("XLB")  # Also exercises adding a trail outside the price selection.
    expect(selector).to_be_focused()
    table = page.locator('#msi-ranks button[aria-label="Inspect XLF"]')
    table.click()
    expect(table).to_be_focused()
    expect(selector).to_have_value("XLF")
    mouse_control = page.locator("#msi-technical-chart text[role=button]").filter(has_text="XLE")
    mouse_control.click()
    expect(mouse_control).to_be_focused()
    expect(selector).to_have_value("XLE")
    url, entries = page.url, page.evaluate("history.length")
    marker = page.locator('#msi-scatter g[data-etf="XLF"][data-source="PREVIEW"]')
    marker.hover()
    expect(page.locator('#msi-scatter polyline[data-etf="XLF"]')).to_have_attribute(
        "data-emphasized", "true"
    )
    expect(mouse_control).to_be_focused()
    page.locator("#msi-summary-title").hover()
    expect(page.locator('#msi-scatter polyline[data-etf="XLE"]')).to_have_attribute(
        "data-emphasized", "true"
    )
    assert page.url == url and page.evaluate("history.length") == entries
    expect(selector).to_have_value("XLE")
    after = metrics(page, browser_fixture_server)
    assert after["requests"] == before["requests"]
    assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
    assert errors == []
    context.close()


@pytest.mark.parametrize("mode", ["partial", "empty", "disabled"])
def test_keyboard_focus_survives_missing_chart_evidence(
    diagnostic_browser, browser_fixture_server, mode,
):
    context = diagnostic_browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server, mode)
    before = metrics(page, browser_fixture_server)
    # D1 orders by canonical sector name: Technology is followed by Utilities.
    universe = json.loads(page.locator("#msi-bootstrap").text_content())["sectors"]
    assert [row["etf_symbol"] for row in universe][-2:] == ["XLK", "XLU"]
    marker = page.locator("#msi-technical-chart text[role=button]").filter(has_text="XLK")
    assert "native V4 N/A" in marker.get_attribute("aria-label")
    for key in ["Enter", "Space"]:
        marker.focus()
        page.keyboard.press(key)
        expect(marker).to_be_focused()
        expect(page.locator("#msi-focus")).to_have_value("XLK")
        page.keyboard.press("Tab")
        expect(page.locator("#msi-technical-chart text[role=button]").filter(
            has_text="XLU"
        )).to_be_focused()
        page.keyboard.press("Shift+Tab")
        expect(marker).to_be_focused()
    assert page.locator('#msi-technical-chart rect[data-model="V5"]').count() == 0
    if mode == "partial":
        scatter = page.locator('#msi-scatter g[data-etf="XLF"][data-source="PREVIEW"]')
        scatter.focus()
        page.keyboard.press("Space")
        expect(scatter).to_be_focused()
    else:
        assert page.locator('#msi-scatter g[data-source="PREVIEW"]').count() == 0
    after = metrics(page, browser_fixture_server)
    assert after["requests"] == before["requests"]
    assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
    assert errors == []
    context.close()


def test_keyboard_activation_after_history_and_refresh_keeps_exact_saved_observation(
    diagnostic_browser, browser_fixture_server,
):
    context = diagnostic_browser.new_context(viewport={"width": 1440, "height": 1100})
    page = context.new_page()
    errors = loaded(page, browser_fixture_server)
    before = metrics(page, browser_fixture_server)
    marker = page.locator(
        '#msi-scatter g[data-etf="XLF"][data-source="PERSISTED"][data-session="2026-10-19"]'
    )
    marker.focus()
    page.keyboard.press("Enter")
    expect(marker).to_be_focused()
    identity, url = marker.get_attribute("data-focus-key"), page.url
    page.go_back()
    expect(page.locator("#msi-focus")).to_have_value("XLK")
    page.go_forward()
    expect(page.locator("#msi-focus")).to_have_value("XLF")
    assert page.url == url
    assert metrics(page, browser_fixture_server)["requests"] == before["requests"]
    page.reload()
    expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
    assert marker.get_attribute("data-focus-key") == identity
    reloaded = metrics(page, browser_fixture_server)
    assert reloaded["requests"][TECHNICAL_PATH] == before["requests"][TECHNICAL_PATH] + 1
    marker.focus()
    page.keyboard.press("Space")
    expect(marker).to_be_focused()
    assert marker.get_attribute("data-focus-key") == identity
    page.keyboard.press("Tab")
    expect(page.locator(
        '#msi-scatter g[data-etf="XLF"][data-source="PERSISTED"][data-session="2026-10-21"]'
    )).to_be_focused()
    after = metrics(page, browser_fixture_server)
    assert after["requests"] == reloaded["requests"]
    assert after["forbidden_calls"] == 0 and set(after["sql"]) <= {"SELECT"}
    assert errors == []
    context.close()
