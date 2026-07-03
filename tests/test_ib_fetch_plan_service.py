from datetime import date

from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlan,
    FetchPlanItem,
    _build_plan_item,
    _plan_action,
    fetch_plan_to_dict,
)
from app.services.ohlcv_coverage_service import OhlcvCoverageItem, OhlcvCoverageSummary
from app.settings import Settings


def test_plan_action_requires_contract_before_fetch() -> None:
    action, duration, reason = _plan_action(
        ticker="MSFT",
        contract_status="MISSING",
        what_to_show="TRADES",
        current_bar_count=0,
        required_bars=252,
        latest_current=False,
        force_refresh=True,
        force_full_backfill=True,
        settings=Settings(),
    )

    assert action == FetchAction.CONTRACT_RESOLUTION_REQUIRED
    assert duration is None
    assert "contract" in reason.lower()


def test_plan_action_marks_failed_contracts() -> None:
    action, duration, reason = _plan_action(
        ticker="MSFT",
        contract_status="FAILED",
        what_to_show="TRADES",
        current_bar_count=0,
        required_bars=252,
        latest_current=False,
        force_refresh=False,
        force_full_backfill=False,
        settings=Settings(),
    )

    assert action == FetchAction.FAILED
    assert duration is None
    assert "failed" in reason.lower()


def test_plan_action_selects_backfill_top_up_skip_and_forced_refresh() -> None:
    settings = Settings(
        ib_full_backfill_duration="3 Y",
        ib_top_up_duration="10 D",
        ib_refresh_duration="60 D",
    )

    assert _plan_action(
        "MSFT",
        "RESOLVED",
        "TRADES",
        current_bar_count=0,
        required_bars=252,
        latest_current=False,
        force_refresh=False,
        force_full_backfill=False,
        settings=settings,
    )[:2] == (FetchAction.FULL_BACKFILL, "3 Y")
    assert _plan_action(
        "MSFT",
        "RESOLVED",
        "TRADES",
        current_bar_count=300,
        required_bars=252,
        latest_current=False,
        force_refresh=False,
        force_full_backfill=False,
        settings=settings,
    )[:2] == (FetchAction.TOP_UP_RECENT, "10 D")
    assert _plan_action(
        "MSFT",
        "RESOLVED",
        "TRADES",
        current_bar_count=300,
        required_bars=252,
        latest_current=True,
        force_refresh=False,
        force_full_backfill=False,
        settings=settings,
    )[:2] == (FetchAction.SKIP, None)
    assert _plan_action(
        "MSFT",
        "RESOLVED",
        "TRADES",
        current_bar_count=300,
        required_bars=252,
        latest_current=True,
        force_refresh=True,
        force_full_backfill=False,
        settings=settings,
    )[:2] == (FetchAction.REFRESH_RECENT, "60 D")
    assert _plan_action(
        "MSFT",
        "RESOLVED",
        "TRADES",
        current_bar_count=300,
        required_bars=252,
        latest_current=True,
        force_refresh=False,
        force_full_backfill=True,
        settings=settings,
    )[:2] == (FetchAction.FORCE_REFRESH, "3 Y")


def test_build_plan_item_uses_coverage_for_specific_data_type() -> None:
    settings = Settings(ib_default_bar_size="1 day", ib_daily_bar_stale_after_days=3)
    coverage = OhlcvCoverageSummary(
        total_tickers=1,
        ready_count=1,
        insufficient_count=0,
        missing_count=0,
        benchmark_spy_ready=False,
        benchmark_qqq_ready=False,
        required_rows=252,
        items=[],
    )
    item = OhlcvCoverageItem(
        ticker="MSFT",
        adjusted_bars=300,
        trades_bars=0,
        has_price=True,
        has_volume=False,
        sufficient_history=True,
        status="missing_volume",
        first_adjusted_date=date(2025, 1, 1),
        latest_adjusted_date=date.today(),
    )

    plan_item = _build_plan_item(
        coverage_item=item,
        contract_status="RESOLVED",
        what_to_show="TRADES",
        coverage=coverage,
        settings=settings,
        force_refresh=False,
        force_full_backfill=False,
    )

    assert plan_item.action == FetchAction.FULL_BACKFILL
    assert plan_item.current_bar_count == 0
    assert plan_item.estimated_request_count == 1


def test_fetch_plan_to_dict_serializes_actions() -> None:
    plan = FetchPlan(
        run_id=7,
        requested_tickers=["MSFT"],
        symbols_including_benchmarks=["MSFT"],
        items=[
            FetchPlanItem(
                ticker="MSFT",
                contract_status="RESOLVED",
                what_to_show="TRADES",
                action=FetchAction.SKIP,
                duration=None,
                bar_size="1 day",
                current_bar_count=300,
                first_bar_date=date(2025, 1, 1),
                latest_bar_date=date(2026, 7, 1),
                required_bars=252,
                reason="Already current.",
                estimated_request_count=0,
            )
        ],
        estimated_request_count=0,
        estimated_full_backfills=0,
        estimated_top_ups=0,
        estimated_refreshes=0,
        estimated_skips=1,
        warnings=[],
    )

    payload = fetch_plan_to_dict(plan)

    assert payload["items"][0]["action"] == "SKIP"
