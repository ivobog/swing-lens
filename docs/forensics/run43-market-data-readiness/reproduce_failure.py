"""Offline causal replay using retained plan and synthetic response dates.

No provider calls, database connections, or production mutations are possible.
Synthetic bars reproduce the retained response endpoints, not the lost OHLCV.
"""

# ruff: noqa: E402
from __future__ import annotations

import json
import runpy
import sys
from dataclasses import fields, replace
from datetime import date
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import app.services.ib_fetch_executor as executor
from app.models.tables import IBFetchItem, IBFetchRun
from app.services.bar_cache_service import BarUpsertSummary
from app.services.ib_data_fetcher import HistoricalBar
from app.services.ib_fetch_plan_service import (
    FetchAction,
    FetchPlanItem,
    _incremental_request_window,
)
from app.services.ib_historical_request_scope import reviewed_start_for_duration
from app.services.market_data_session_readiness import classify_session_readiness
from app.services.us_market_calendar import next_us_trading_day, us_trading_sessions_between
from app.settings import Settings


def main():
    helpers = runpy.run_path(str(ROOT / "tests/test_ib_fetch_executor.py"))
    pipeline = json.loads((OUT / "pipeline.json").read_text())[0]
    retained = [
        i for i in pipeline["result_json"]["ib_fetch_plan"]["items"] if i["ticker"] == "DDS"
    ]
    expected = date(2026, 10, 16)
    prior_latest = date(2026, 10, 5)
    start = date(2026, 9, 28)
    dates = [start]
    while dates[-1] < expected:
        dates.append(next_us_trading_day(dates[-1]))
    results = []
    resume_results = []
    for item_json in retained:
        attrs = {f.name: item_json[f.name] for f in fields(FetchPlanItem) if f.name in item_json}
        for name, value in list(attrs.items()):
            if value and (name.endswith("_date") or name == "reviewed_session_expiry"):
                attrs[name] = date.fromisoformat(value)
        attrs["action"] = FetchAction(attrs["action"])
        plan = FetchPlanItem(**attrs)
        for scenario, response_dates in (
            ("retained_endpoints_with_synthetic_intermediate_sessions", dates),
            ("counterfactual_trim_earlier_session_only", dates[1:]),
            ("negative_control_future_session", [*dates[1:], next_us_trading_day(expected)]),
            ("negative_control_expected_session_absent", dates[1:-1]),
        ):
            bars = [
                HistoricalBar(
                    ticker="DDS",
                    bar_date=day,
                    timeframe="1 day",
                    open=10,
                    high=11,
                    low=9,
                    close=10,
                    volume=100,
                    source="IB",
                    what_to_show=plan.what_to_show,
                    adjustment_type="adjusted" if plan.what_to_show == "ADJUSTED_LAST" else None,
                )
                for day in response_dates
            ]
            calls = []

            def fake_cache(_db, values, _calls=calls, **_kwargs):
                _calls.append([v.bar_date.isoformat() for v in values])
                return BarUpsertSummary(inserted=len(values))

            item = IBFetchItem(
                id=5599 if plan.what_to_show == "ADJUSTED_LAST" else 5600,
                fetch_run_id=23,
                ticker="DDS",
                what_to_show=plan.what_to_show,
                bar_size="1 day",
                status="PLANNED",
                decision_metadata_json={},
                fetched=0,
                inserted=0,
                updated=0,
                revised=0,
                unchanged=0,
                attempt_count=0,
                current_bar_count=755,
            )
            with (
                patch.object(executor, "fetch_daily_bars", return_value=bars) as provider,
                patch.object(executor, "cache_bars", side_effect=fake_cache),
                patch.object(executor, "latest_completed_us_trading_day", return_value=expected),
            ):
                executor._execute_plan_item(
                    helpers["FakeDb"](),
                    helpers["FakeIB"](),
                    item,
                    plan,
                    helpers["FakeLimiter"](),
                    Settings(ib_max_retries=3),
                    False,
                    False,
                )
            latest = max(response_dates) if calls else prior_latest
            readiness = classify_session_readiness(
                ticker="DDS",
                expected_session=expected,
                latest_bar_session=latest,
                lifecycle=None,
                fetch_items=(item,),
                contract_resolution_status="RESOLVED",
            )
            if scenario.startswith("retained_"):
                assert item.status == "FAILED" and calls == [] and provider.call_count == 1
                assert readiness["reason_code"] == "EXPECTED_SESSION_UNAVAILABLE"
            elif scenario.startswith("counterfactual_"):
                assert item.status == "SUCCESS" and len(calls) == 1
                assert readiness["disposition"] == "READY"
            elif scenario == "negative_control_future_session":
                assert item.status == "FAILED" and calls == []
            else:
                assert (
                    item.status == "SUCCESS"
                    and readiness["disposition"] == "REQUIRED_DATA_UNAVAILABLE"
                )
            results.append(
                {
                    "scenario": scenario,
                    "feed": plan.what_to_show,
                    "synthetic_response_start": min(response_dates),
                    "synthetic_response_end": max(response_dates),
                    "synthetic_response_count": len(bars),
                    "provider_calls": provider.call_count,
                    "cache_calls": len(calls),
                    "status": item.status,
                    "attempt_count": item.attempt_count,
                    "fetched_counter": item.fetched,
                    "error_message": item.error_message,
                    "metadata": item.decision_metadata_json,
                    "readiness_disposition": readiness["disposition"],
                    "readiness_reason": readiness["reason_code"],
                }
            )
            if scenario.startswith("retained_"):
                fake_db = helpers["FakeDb"]()
                retained_run = IBFetchRun(id=23, run_id=43, status="RUNNING", items=[item])
                fake_db.add(retained_run)
                with (
                    patch.object(executor, "_start_fetch_run", return_value=retained_run),
                    patch.object(executor, "_existing_fetch_item", return_value=item),
                    patch.object(executor, "fetch_daily_bars") as resumed_provider,
                ):
                    resumed = executor.execute_fetch_plan(
                        fake_db,
                        replace(helpers["_fetch_plan"](plan), run_id=43),
                        ib_client_factory=helpers["FakeIB"],
                        rate_limiter=helpers["FakeLimiter"](),
                        settings=Settings(ib_max_retries=3),
                        fetch_run_id=23,
                    )
                assert resumed_provider.call_count == 0 and item.attempt_count == 1
                assert resumed.status == "FAILED" and item.status == "FAILED"
                resume_results.append(
                    {
                        "feed": plan.what_to_show,
                        "condition": "same acquisition selects the existing failed fetch item",
                        "provider_calls": resumed_provider.call_count,
                        "attempt_count_unchanged": item.attempt_count,
                        "item_status": item.status,
                        "single_item_replay_run_status": resumed.status,
                    }
                )
    result = {
        "evidence_type": (
            "offline replay of live functions with retained plan and synthetic date-only fixture"
        ),
        "production_writes": False,
        "provider_network_calls": 0,
        "synthetic_fixture_is_not_original_provider_payload": True,
        "planner_reproduction": {
            "latest_cached_session": prior_latest,
            "expected_session": expected,
            "revision_sessions": 5,
            "incremental_request_window": _incremental_request_window(
                prior_latest, expected, revision_sessions=5, fallback_duration="10 D"
            ),
            "reviewed_start_for_14_D": reviewed_start_for_duration(
                "14 D", end=expected, bar_size="1 day"
            ),
            "sessions_between_old_and_expected": us_trading_sessions_between(
                prior_latest, expected
            ),
        },
        "scenarios": results,
        "resume_replays": resume_results,
        "assertions_passed": True,
    }
    (OUT / "reproduction-results.json").write_text(
        json.dumps(result, indent=2, default=str), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "assertions_passed": True,
                "scenarios": len(results),
                "resume_replays": len(resume_results),
                "production_writes": False,
            }
        )
    )


if __name__ == "__main__":
    main()
