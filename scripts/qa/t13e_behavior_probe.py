"""Identical clock inputs for actual pre-Phase-4/current native parity."""

from datetime import date

from scripts.qa import t13d_behavior_probe as previous

pytest_addoption = previous.pytest_addoption
pytest_runtest_setup = previous.pytest_runtest_setup
pytest_sessionfinish = previous.pytest_sessionfinish


def pytest_configure(config):
    previous.pytest_configure(config)
    if config.getoption("--t12a-business-output"):
        from app.services import combined_decision

        # A clock is an input. Keep it identical across midnight and checkouts;
        # retain earnings offsets and every other financial output in capture.
        combined_decision.current_local_date = lambda: date(2026, 9, 16)
