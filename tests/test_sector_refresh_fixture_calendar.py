"""Explicit receipt times at the native V3 forming-session boundary."""

from datetime import date, datetime

import pytest
from sector_refresh_helpers import forming_response_session


@pytest.mark.parametrize(
    ("reviewed", "observed", "forming"),
    [
        ("2026-10-29", "2026-10-30T12:00:00+00:00", None),
        ("2026-10-29", "2026-10-30T13:29:59+00:00", None),
        ("2026-10-29", "2026-10-30T13:30:00+00:00", "2026-10-30"),
        ("2026-10-29", "2026-10-30T19:59:59+00:00", "2026-10-30"),
        ("2026-10-29", "2026-10-30T20:00:00+00:00", None),
        ("2026-10-30", "2026-10-31T15:00:00+00:00", None),
        ("2026-10-30", "2026-11-02T14:30:00+00:00", "2026-11-02"),
        ("2026-10-28", "2026-10-30T15:00:00+00:00", None),
    ],
)
def test_forming_provider_fixture_obeys_reviewed_session_window(reviewed, observed, forming):
    actual = forming_response_session(
        date.fromisoformat(reviewed), datetime.fromisoformat(observed)
    )
    assert actual == (date.fromisoformat(forming) if forming else None)
