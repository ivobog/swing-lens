"""A wedged panel cannot consume another panel's native execution capacity."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import HTTPException

from app.services import sector_read_budget as budget


def test_hanging_technical_read_has_one_slot_and_coverage_remains_usable():
    entered, release = Event(), Event()

    @budget.panel_budget
    def market_sector_technicals_preview():
        entered.set()
        assert release.wait(10)
        return {"v4_ready_count": 0}

    @budget.panel_budget
    def market_sector_coverage():
        return {"status": "STALE", "session_lag": 6}

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(market_sector_technicals_preview)
        try:
            assert entered.wait(5)
            for _ in range(10):
                with pytest.raises(HTTPException) as error:
                    market_sector_technicals_preview()
                assert error.value.status_code == 503
                assert error.value.detail["code"] == "SECTOR_PANEL_ALREADY_RUNNING"
                assert market_sector_coverage() == {"status": "STALE", "session_lag": 6}
        finally:
            release.set()
        assert pending.result(5) == {"v4_ready_count": 0}
    assert market_sector_technicals_preview() == {"v4_ready_count": 0}


def test_native_deadline_releases_panel_capacity_without_publishing_late_result(monkeypatch):
    clock = [0]
    monkeypatch.setattr(budget, "monotonic", lambda: clock[0])

    @budget.panel_budget
    def market_sector_coverage():
        clock[0] += 200
        return {"status": "CURRENT"}

    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            market_sector_coverage()
        assert error.value.status_code == 504


def test_measured_pipeline_load_coverage_can_complete_within_budget(monkeypatch):
    clock = [0]
    monkeypatch.setattr(budget, "monotonic", lambda: clock[0])

    @budget.panel_budget
    def market_sector_coverage():
        # The observed concurrent native-pipeline measurement was 50.356s.
        clock[0] += 50.4
        return {"status": "STALE", "session_lag": 2}

    assert market_sector_coverage() == {"status": "STALE", "session_lag": 2}


def test_original_scope_and_future_cutoff_remain_explicit():
    from datetime import UTC, datetime, timedelta

    from app.routers.market_sector_intelligence_routes import _read_cutoff

    with pytest.raises(HTTPException):
        _read_cutoff(datetime.now())
    with pytest.raises(HTTPException):
        _read_cutoff(datetime.now(UTC) + timedelta(days=1))
    assert _read_cutoff(None) == {}
