from __future__ import annotations

import csv
from datetime import UTC, datetime

import psycopg
import pytest


def test_qa_paths_and_settings_are_isolated(qa_paths, settings_factory) -> None:
    settings = settings_factory(setup_lifecycle_enabled=True)

    assert settings.upload_dir == qa_paths.uploads
    assert settings.export_dir == qa_paths.exports
    assert settings.cache_dir == qa_paths.cache
    assert settings.job_worker_enabled is False
    assert settings.setup_lifecycle_enabled is True


def test_csv_factory_preserves_unicode_blanks_and_formula_payload(csv_factory) -> None:
    path = csv_factory(
        [
            {
                "Symbol": "ŽABA",
                "Description": "München growth",
                "Score": "",
                "Note": "=HYPERLINK(\"https://invalid.example\")",
            }
        ]
    )

    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assert rows == [
        {
            "Symbol": "ŽABA",
            "Description": "München growth",
            "Score": "",
            "Note": "=HYPERLINK(\"https://invalid.example\")",
        }
    ]


def test_ohlcv_factory_is_repeatable_and_uses_weekdays(ohlcv_factory) -> None:
    first = ohlcv_factory(count=8)
    second = ohlcv_factory(count=8)

    assert first == second
    assert len(first) == 8
    assert all(bar["date"].weekday() < 5 for bar in first)
    assert all(bar["low"] <= bar["open"] <= bar["high"] for bar in first)
    assert all(bar["low"] <= bar["close"] <= bar["high"] for bar in first)


def test_fixed_clock_is_timezone_aware_and_stable(fixed_clock) -> None:
    assert fixed_clock.now() == datetime(2026, 8, 5, 20, 0, tzinfo=UTC)
    assert fixed_clock.now() == fixed_clock.now()
    assert fixed_clock.today().isoformat() == "2026-08-05"


def test_fake_ib_gateway_enforces_read_only_and_scripts_results(
    fake_ib_gateway_factory,
) -> None:
    gateway = fake_ib_gateway_factory({"MSFT": [{"close": 100.0}]})

    gateway.connect("127.0.0.1", 4002, clientId=21, readonly=True)
    bars = gateway.reqHistoricalData(type("Contract", (), {"symbol": "MSFT"})())

    assert gateway.isConnected() is True
    assert bars == [{"close": 100.0}]
    assert gateway.historical_requests == ["MSFT"]
    assert gateway.order_api_calls == []


@pytest.mark.integration
@pytest.mark.destructive
def test_disposable_postgres_fixture_is_scoped_to_a_qa_database(
    disposable_postgres_database: str,
) -> None:
    database_name = disposable_postgres_database.rsplit("/", 1)[-1]

    assert database_name.startswith("swinglens_pytest_")
    with psycopg.connect(disposable_postgres_database.replace("+psycopg", "")) as connection:
        actual_name = connection.execute("select current_database()").fetchone()[0]

    assert actual_name == database_name


def test_app_client_factory_uses_shallow_health_without_worker(app_client_factory) -> None:
    with app_client_factory() as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["app"] == "SwingLens"
    assert payload["database_configured"] is True
    assert "database_url" not in payload
    assert "postgres:postgres" not in response.text
