"""Opt-in exact core business DTO probe; excludes assigned provenance and clocks.

Uses the T12A probe hooks and --t12a-business-output option. Run identical fixtures
against the T12A checkout and T12B, then classify READY parity and expected exclusions.
"""

from dataclasses import asdict, is_dataclass

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from scripts.qa import t12a_behavior_probe as probe

pytest_addoption = probe.pytest_addoption
pytest_runtest_setup = probe.pytest_runtest_setup
pytest_sessionfinish = probe.pytest_sessionfinish


def pytest_configure(config):
    probe._business = _business
    probe.pytest_configure(config)


def _business(result):
    if isinstance(result, (tuple, list)):
        return [_business(row) for row in result]
    result = getattr(result, "dto", result)
    if hasattr(result, "promoted_fields"):
        values = {
            key: getattr(result, key)
            for key in (
                "ticker",
                "timeframe",
                "data_as_of_date",
                "data_quality_label",
                "promoted_fields",
                "signals",
                "feature_flags",
                "warning_flags",
                "missing_data",
            )
        }
    elif is_dataclass(result):
        values = asdict(result)
        for key in ("debug", "debug_evidence", "evidence"):
            values.pop(key, None)
    else:
        values = {name: getattr(result, name) for name in probe._FIELDS if hasattr(result, name)}
    return Canonical.canonicalize(values)
