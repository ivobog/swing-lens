from datetime import date, datetime

import pytest

from app.services.earnings_date_parser import parse_earnings_date


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        ("2026-07-14", date(2026, 7, 14)),
        ("Jul 14, 2026", date(2026, 7, 14)),
        ("July 14, 2026", date(2026, 7, 14)),
        ("14 Jul 2026", date(2026, 7, 14)),
        ("14 July 2026", date(2026, 7, 14)),
        ("07/14/2026", date(2026, 7, 14)),
        ("14.07.2026", date(2026, 7, 14)),
        (date(2026, 7, 14), date(2026, 7, 14)),
        (datetime(2026, 7, 14, 16, 30), date(2026, 7, 14)),
    ],
)
def test_parse_earnings_date_supports_tradingview_formats(raw_value, expected) -> None:
    assert parse_earnings_date(raw_value) == expected


@pytest.mark.parametrize("raw_value", ["", " ", "-", "--", "\u2014", "N/A", "None", None])
def test_parse_earnings_date_treats_missing_values_as_none(raw_value) -> None:
    assert parse_earnings_date(raw_value) is None


def test_parse_earnings_date_returns_none_for_invalid_values() -> None:
    assert parse_earnings_date("next Tuesday maybe") is None
