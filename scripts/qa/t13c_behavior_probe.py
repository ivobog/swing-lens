"""Opt-in contextual business capture, identical in baseline and current checkouts."""

from dataclasses import asdict, is_dataclass
from types import SimpleNamespace

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from scripts.qa import t12a_behavior_probe as probe
from scripts.qa import t12e_behavior_probe as previous

pytest_addoption = probe.pytest_addoption
pytest_runtest_setup = probe.pytest_runtest_setup
pytest_sessionfinish = probe.pytest_sessionfinish


def pytest_configure(config):
    probe._TARGETS = (
        *probe._TARGETS,
        (
            "market_regime_command_center",
            "MarketRegimeCommandCenterService",
            "build_snapshot",
            "Regime",
        ),
        *[
            ("ib_market_intelligence.calculations", None, f"calculate_{module}", f"IBMI.{module}")
            for module in (
                "liquidity",
                "short_pressure",
                "volatility",
                "options_activity",
                "histogram",
            )
        ],
    )
    previous.pytest_configure(config)
    probe._business = _business


def _plain(value):
    if is_dataclass(value):
        value = asdict(value)
    if isinstance(value, SimpleNamespace):
        value = vars(value)
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items() if key != "debug"}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    return value


def _business(result):
    if (
        hasattr(result, "universe_rows")
        or hasattr(result, "index_health")
        or (hasattr(result, "module") and hasattr(result, "components"))
    ):
        return Canonical.canonicalize(_plain(result))
    return previous._business(result)
