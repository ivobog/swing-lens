from datetime import date

import pytest

from app.prewarm import parse_args


def test_prewarm_cli_parses_plan_sources_and_tickers() -> None:
    recent = parse_args(["--source", "recent-runs", "--recent-run-count", "3"])
    explicit = parse_args(
        [
            "--source",
            "explicit",
            "--tickers",
            "aapl, MSFT",
            "--session",
            "2026-08-11",
            "--no-benchmarks",
        ]
    )

    assert recent.source == "recent-runs"
    assert recent.recent_run_count == 3
    assert explicit.tickers == ("AAPL", "MSFT")
    assert explicit.session == date(2026, 8, 11)
    assert explicit.no_benchmarks is True


def test_prewarm_cli_requires_explicit_tickers() -> None:
    with pytest.raises(SystemExit):
        parse_args(["--source", "explicit"])
