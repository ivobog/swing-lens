"""Real Chrome, actual HTTP admission and native worker, disposable PostgreSQL."""

import socket
from datetime import UTC, datetime
from threading import Thread
from time import monotonic, sleep

import pytest
import uvicorn
from evidence_output import output_directory
from playwright.sync_api import expect, sync_playwright
from sector_refresh_helpers import configure_stubs, execute_one, prepared_database
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.main import create_app
from app.models.tables import BackgroundJob, PriceBar
from app.routers import market_sector_intelligence_routes as routes
from app.services import market_sector_refresh_service as refresh
from app.services.market_clock_service import MarketClockService
from app.services.market_sector_intelligence_coverage import MarketSectorIntelligenceCoverageService


@pytest.fixture
def refresh_browser_server(disposable_postgres_database, monkeypatch):
    engine, expected = prepared_database(disposable_postgres_database)
    settings, health, calls = configure_stubs(monkeypatch, disposable_postgres_database)
    probes = []

    def probe(**_):
        probes.append(1)
        return health

    monkeypatch.setattr(refresh, "check_status", probe)
    app = create_app(settings)

    def database():
        with Session(engine) as db:
            yield db

    app.dependency_overrides[get_db] = database
    requests = []

    @app.middleware("http")
    async def record(request, call_next):
        requests.append((request.method, request.url.path))
        return await call_next(request)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, lifespan="off", log_level="critical")
    )
    thread = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    start = monotonic()
    while not server.started and monotonic() - start < 15:
        sleep(0.05)
    assert server.started
    try:
        yield engine, expected, health, calls, probes, requests, "http://127.0.0.1:" + str(port)
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        assert not thread.is_alive()
        listener.close()
        engine.dispose()


def test_manual_only_gateway_failure_native_completion_stale_persistence_and_url_state(
    refresh_browser_server, monkeypatch
):
    engine, expected, health, calls, probes, requests, base = refresh_browser_server
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1280, "height": 900})
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        evidence = output_directory("sector-refresh")
        url = base + "/sector-rotation?etf=XLF&mode=absolute&horizon=21&symbols=XLF,XLK"
        page.goto(url)
        expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
        expect(page.locator("#msi-refresh")).to_be_enabled()
        expect(page.locator("#msi-freshness")).to_contain_text("STALE · 1 trading session behind")
        assert page.locator("#msi-freshness").get_attribute("data-status") == "STALE"
        disclosure = page.locator(".msi-freshness-evidence summary")
        disclosure.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#msi-freshness-detail")).to_be_visible()
        page.keyboard.press("Enter")
        expect(page.locator("#msi-freshness-detail")).to_be_hidden()
        page.screenshot(path=str(evidence / "stale.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        box = page.locator("#msi-refresh").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 390
        page.screenshot(path=str(evidence / "mobile-heading.png"))
        page.set_viewport_size({"width": 1280, "height": 900})
        assert probes == [] and calls == [] and not any(method == "POST" for method, _ in requests)
        page.locator("#msi-mode").select_option("relative")
        page.locator("#msi-focus").select_option("XLK")
        page.locator("#msi-horizon").select_option("5")
        selected_url = page.url
        expect(page.locator("#msi-reload")).to_be_enabled()
        assert probes == [] and calls == []
        health.status = "IB_PROCESS_RUNNING_API_NOT_READY"
        button = page.get_by_role("button", name="Refresh Sector", exact=True)
        button.focus()
        page.keyboard.press("Enter")
        expect(page.locator("#msi-refresh-status")).to_contain_text(
            "Start and sign in to IB Gateway"
        )
        expect(button).to_be_enabled()
        expect(button).to_be_focused()
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 1
        health.status = "IB_API_READY"
        health.zero_close_symbol = "XLK"
        monkeypatch.setattr(refresh, "_last_probe", float("-inf"))
        button.click()
        expect(button).to_be_disabled()
        expect(button).to_be_focused()
        expect(page.locator("#msi-refresh-status")).to_contain_text(
            "acquisition items completed", timeout=15000
        )
        # Reopening the page follows the same active durable operation, GET only.
        page.reload()
        expect(page.locator("#msi-refresh-status")).to_contain_text(
            "Refreshing sector data", timeout=15000
        )
        assert len(probes) == 2
        technical_reads = requests.count(
            ("GET", "/api/market-sector-intelligence/technicals/preview")
        )
        with Session(engine) as db:
            assert db.scalar(select(func.count()).select_from(BackgroundJob)) == 2
        final = execute_one(engine)
        assert final["status"] == "COMPLETED"
        expect(page.locator("#msi-refresh-status")).to_contain_text(
            "No sector refresh is active", timeout=30000
        )
        expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
        assert page.url == selected_url
        expect(page.locator("#msi-freshness")).to_contain_text("STALE")
        expect(page.locator("#msi-freshness-detail")).to_contain_text(
            "XLK ADJUSTED_LAST: 1 session(s) behind"
        )
        page.screenshot(path=str(evidence / "completed-still-stale.png"), full_page=True)
        assert (
            requests.count(("GET", "/api/market-sector-intelligence/technicals/preview"))
            == technical_reads + 1
        )
        health.zero_close_symbol = None
        monkeypatch.setattr(refresh, "_last_probe", float("-inf"))
        # Respect the server's terminal-operation cooldown, rather than bypassing
        # its rate limit to force the second deliberate browser acquisition.
        with Session(engine) as db:
            newest = db.scalars(select(BackgroundJob).order_by(BackgroundJob.id.desc())).first()
            remaining = (
                refresh.RETRY_SECONDS - (datetime.now(UTC) - newest.created_at).total_seconds()
            )
        if remaining > 0:
            sleep(remaining)
        button.click()
        expect(page.locator("#msi-refresh-status")).to_contain_text(
            "acquisition items completed", timeout=15000
        )
        final = execute_one(engine)
        assert final["status"] == "COMPLETED"
        expect(page.locator("#msi-refresh-status")).to_contain_text(
            "No sector refresh is active", timeout=30000
        )
        expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
        expect(page.locator("#msi-freshness")).to_have_text("CURRENT")
        page.screenshot(path=str(evidence / "current.png"), full_page=True)
        assert page.url == selected_url and len(calls) == 52 and len(probes) == 3
        # Stored absence is rendered honestly without provider work.
        with Session(engine) as db:
            db.query(PriceBar).delete(synchronize_session=False)
            db.commit()
        page.locator("#msi-reload").click()
        expect(page.locator("#msi-freshness")).to_have_text("MISSING", timeout=30000)
        assert len(probes) == 3 and len(calls) == 52
        assert sum(method == "POST" for method, _ in requests) == 3
        assert errors == []
        context.close()
        browser.close()


def test_browser_calendar_freshness_remains_get_only(refresh_browser_server, monkeypatch):
    engine, _, _, calls, probes, requests, base = refresh_browser_server
    clock = MarketClockService()

    class FixtureClock:
        def __init__(self, cutoff):
            self.cutoff = cutoff

        def cutoff_for(self, *_a, **_kw):
            return self.cutoff

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        for stamp in (
            datetime(2026, 10, 24, 12, tzinfo=UTC),
            datetime(2026, 10, 25, 12, tzinfo=UTC),
            datetime(2026, 10, 26, 20, 14, tzinfo=UTC),
            datetime(2026, 10, 26, 20, 15, tzinfo=UTC),
            datetime(2026, 7, 3, 19, tzinfo=UTC),
            datetime(2026, 12, 25, 20, tzinfo=UTC),
        ):
            cutoff = clock.cutoff_for(stamp, reason="BROWSER_CALENDAR_FIXTURE")
            fixture_clock = FixtureClock(cutoff)

            monkeypatch.setattr(
                routes,
                "MarketSectorIntelligenceCoverageService",
                lambda clock=fixture_clock: MarketSectorIntelligenceCoverageService(clock=clock),
            )
            # Explicit synthetic fixture changes, before read-only page requests.
            with Session(engine) as db:
                db.query(PriceBar).update(
                    {
                        PriceBar.bar_date: cutoff.latest_completed_session,
                        PriceBar.created_at: stamp,
                        PriceBar.first_seen_at: stamp,
                    },
                    synchronize_session=False,
                )
                db.commit()
            page.goto(base + "/sector-rotation")
            expect(page.locator("#msi-reload")).to_be_enabled(timeout=60000)
            expect(page.locator("#msi-freshness")).to_have_text("CURRENT")
            expect(page.locator("#msi-freshness-detail")).to_contain_text(
                cutoff.latest_completed_session.isoformat()
            )
        assert calls == probes == []
        assert not any(method == "POST" for method, _ in requests)
        assert errors == []
        browser.close()
