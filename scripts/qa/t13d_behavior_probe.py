"""Identical baseline/current capture of decision mathematics and native decisions."""

from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from functools import wraps

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from scripts.qa import t12a_behavior_probe as probe
from scripts.qa import t13c_behavior_probe as previous

pytest_addoption = probe.pytest_addoption
pytest_runtest_setup = probe.pytest_runtest_setup
pytest_sessionfinish = probe.pytest_sessionfinish


def pytest_configure(config):
    previous.pytest_configure(config)
    extra = (
        (
            "setup_lifecycle.alert_service",
            "SetupLifecycleAlertService",
            "evaluate_lifecycle_event",
            "SetupAlerts",
        ),
        (
            "setup_lifecycle.alert_service",
            "SetupLifecycleAlertService",
            "evaluate_signal_change_events",
            "SignalAlerts",
        ),
        (
            "winner_probability.cohort_statistics",
            "CohortStatisticsService",
            "calculate",
            "WinnerCohortMath",
        ),
        (
            "winner_probability.outcome_service",
            "OutcomeMaturationService",
            "_calculate_forward",
            "WinnerOutcomeMath",
        ),
    )
    if config.getoption("--t12a-business-output"):
        from importlib import import_module

        capture = import_module("app.services.winner_probability.capture_service")
        capture.WinnerPredictionCaptureService.capture_run = _fixed_capture_clock(
            capture.WinnerPredictionCaptureService.capture_run
        )
        for module, owner, method, label in extra:
            cls = getattr(import_module("app.services." + module), owner)
            setattr(cls, method, probe._probe(getattr(cls, method), label))
    probe._business = _business


def _fixed_capture_clock(method):
    @wraps(method)
    def capture(*args, **kwargs):
        # Identical explicit inputs in both checkouts, including session/hash.
        # Preserve every fixture that already supplies its historical cutoff.
        if all(kwargs.get(key) is None for key in ("captured_at", "decision_at", "market_cutoff")):
            kwargs["decision_at"] = datetime(2026, 9, 16, 18, tzinfo=UTC)
            kwargs["captured_at"] = kwargs["decision_at"]
        return method(*args, **kwargs)

    return capture


def _business(result):
    if hasattr(result, "wins") and hasattr(result, "posterior_probability"):
        return Canonical.canonicalize(asdict(result))
    if hasattr(result, "created") and hasattr(result, "suppressed"):
        return Canonical.canonicalize(asdict(result))
    if hasattr(result, "values") and hasattr(result, "ticker_bars"):
        # Configuration semantics is adoption metadata; financial outputs and
        # exact input lineage remain in this comparison.
        values = dict(result.values)
        values["metadata_json"] = {
            k: v
            for k, v in values.get("metadata_json", {}).items()
            if k != "configuration_semantics"
        }
        return Canonical.canonicalize(values)
    if is_dataclass(result) and hasattr(result, "feature_json"):
        return previous._business(result)
    return previous._business(result)
