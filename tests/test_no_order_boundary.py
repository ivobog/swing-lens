from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import app.services.ib_fetch_executor as executor
from app.models.tables import IBFetchItem
from app.services.bar_cache_service import BarUpsertSummary
from app.services.ib_connection import check_ib_connection
from app.services.ib_fetch_executor import execute_fetch_plan
from app.services.ib_fetch_plan_service import FetchAction, FetchPlan, FetchPlanItem
from app.settings import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]
APP_ROOT = REPO_ROOT / "app"
EXCLUDED_APP_PATH_PARTS = {
    "static/vendor",
}
FORBIDDEN_BROKER_ORDER_PATTERNS = (
    re.compile(r"\.placeOrder\s*\("),
    re.compile(r"\.whatIfOrder\s*\("),
    re.compile(r"\.cancelOrder\s*\("),
    re.compile(r"\.reqGlobalCancel\s*\("),
    re.compile(r"\b(?:Order|MarketOrder|LimitOrder|StopOrder|StopLimitOrder|BracketOrder)\s*\("),
)


@dataclass(frozen=True)
class NoOrderViolation:
    path: Path
    line_number: int
    line: str


def test_app_code_does_not_reference_broker_order_apis() -> None:
    violations = find_broker_order_api_references()

    assert violations == []


def test_ib_connection_uses_read_only_session() -> None:
    ib = FakeNoOrderIB()

    status = check_ib_connection(
        settings=Settings(_env_file=None, job_worker_enabled=False),
        ib_factory=lambda: ib,
    )

    assert status.connected is True
    assert ib.connect_calls[-1]["readonly"] is True
    assert ib.order_api_calls == []


def test_ib_fetch_executor_uses_read_only_session_and_no_order_api(monkeypatch) -> None:
    db = FakeDb()
    ib = FakeNoOrderIB()
    monkeypatch.setattr(
        executor,
        "resolve_us_stock_contract",
        lambda db, ticker, ib: SimpleNamespace(
            contract=SimpleNamespace(symbol=ticker),
            error_message=None,
        ),
    )
    monkeypatch.setattr(
        executor,
        "cache_bars",
        lambda db, bars, **kwargs: BarUpsertSummary(
            inserted=1,
            updated=0,
            revised=0,
            unchanged=0,
        ),
    )

    fetch_run = execute_fetch_plan(
        db=db,
        plan=FetchPlan(
            run_id=7,
            requested_tickers=["MSFT"],
            symbols_including_benchmarks=["MSFT"],
            items=[
                FetchPlanItem(
                    ticker="MSFT",
                    contract_status="RESOLVED",
                    what_to_show="TRADES",
                    action=FetchAction.TOP_UP_RECENT,
                    duration="10 D",
                    bar_size="1 day",
                    current_bar_count=300,
                    first_bar_date=None,
                    latest_bar_date=None,
                    required_bars=252,
                    reason="top up",
                    estimated_request_count=1,
                )
            ],
            estimated_request_count=1,
            estimated_full_backfills=0,
            estimated_top_ups=1,
            estimated_refreshes=0,
            estimated_skips=0,
            warnings=[],
        ),
        ib_client_factory=lambda: ib,
        settings=Settings(_env_file=None, job_worker_enabled=False, ib_max_retries=1),
    )

    assert fetch_run.status == "COMPLETED"
    assert ib.connect_calls[-1]["readonly"] is True
    assert ib.historical_requests == 1
    assert ib.order_api_calls == []


def find_broker_order_api_references() -> list[NoOrderViolation]:
    violations: list[NoOrderViolation] = []
    for path in sorted(APP_ROOT.rglob("*")):
        if path.suffix not in {".py", ".html", ".js"}:
            continue
        relative = path.relative_to(REPO_ROOT).as_posix()
        if any(part in relative for part in EXCLUDED_APP_PATH_PARTS):
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if any(pattern.search(line) for pattern in FORBIDDEN_BROKER_ORDER_PATTERNS):
                violations.append(NoOrderViolation(path, line_number, line.strip()))
    return violations


class FakeNoOrderIB:
    def __init__(self) -> None:
        self.connected = False
        self.connect_calls = []
        self.historical_requests = 0
        self.order_api_calls = []

    def connect(self, *args, **kwargs) -> None:
        self.connect_calls.append(dict(kwargs))
        self.connected = True

    def disconnect(self) -> None:
        self.connected = False

    def isConnected(self) -> bool:
        return self.connected

    def reqHistoricalData(self, *args, **kwargs):
        self.historical_requests += 1
        return [SimpleNamespace(date="20260701", open=10, high=12, low=9, close=11, volume=1000)]

    def __getattr__(self, name: str):
        if "order" in name.lower():
            self.order_api_calls.append(name)
            raise AssertionError(f"broker order API must not be called: {name}")
        raise AttributeError(name)


class FakeDb:
    def __init__(self) -> None:
        self.added = []
        self.flushes = 0
        self.commits = 0

    def add(self, row) -> None:
        self.added.append(row)
        if isinstance(row, IBFetchItem) and row.fetch_run and row not in row.fetch_run.items:
            row.fetch_run.items.append(row)

    def flush(self) -> None:
        self.flushes += 1

    def commit(self) -> None:
        self.commits += 1
