"""Identical pure core calculator capture in pre-Phase-4/current checkouts."""

from dataclasses import asdict
from functools import wraps
from importlib import import_module

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from scripts.qa import t12a_behavior_probe as probe

pytest_addoption = probe.pytest_addoption
pytest_runtest_setup = probe.pytest_runtest_setup
pytest_sessionfinish = probe.pytest_sessionfinish


def pytest_configure(config):
    probe._records.clear()
    if not config.getoption("--t12a-business-output"):
        return
    for module, method, label in (
        ("fundamental_ranker_v2", "score_rows_v2", "Fundamental"),
        ("pine_replica_engine", "score_from_feature_result", "TechnicalBase"),
        ("technical_score_v4", "technical_score_v4_from_base_score", "TechnicalV4"),
    ):
        owner = import_module("app.services." + module)
        setattr(owner, method, _capture(getattr(owner, method), label))


def _capture(original, label):
    @wraps(original)
    def calculate(*args, **kwargs):
        result = original(*args, **kwargs)
        rows = result if isinstance(result, list) else [result]
        probe._records.append(
            {
                "test": probe._test_id,
                "producer": label,
                "output": Canonical.canonicalize([asdict(row) for row in rows]),
            }
        )
        return result

    return calculate
