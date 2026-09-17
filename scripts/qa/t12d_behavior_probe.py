"""Opt-in exact READY Winner vector/source-pin/probability capture."""

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from scripts.qa import t12a_behavior_probe as probe

pytest_addoption = probe.pytest_addoption
pytest_runtest_setup = probe.pytest_runtest_setup
pytest_sessionfinish = probe.pytest_sessionfinish


def pytest_configure(config):
    probe._business = _business
    probe._TARGETS = (
        *probe._TARGETS,
        (
            "winner_probability.probability_estimator",
            "ProbabilityEstimator",
            "create_decision_time_estimate",
            "WinnerProbability",
        ),
    )
    probe.pytest_configure(config)


def _business(result):
    if hasattr(result, "feature_json"):
        return Canonical.canonicalize(
            {
                key: getattr(result, key)
                for key in (
                    "ticker",
                    "feature_json",
                    "feature_vector_hash",
                    "source_ids_json",
                    "eligibility_status",
                    "exclusion_reason",
                )
            }
        )
    if hasattr(result, "estimate"):
        return Canonical.canonicalize(
            {
                "status": result.status,
                "estimate": {
                    key: getattr(result.estimate, key)
                    for key in (
                        "point_probability",
                        "lower_bound",
                        "upper_bound",
                        "interval_width",
                        "sample_n",
                        "effective_n",
                        "evidence_grade",
                        "insufficient_reasons_json",
                    )
                },
            }
        )
    return {}
