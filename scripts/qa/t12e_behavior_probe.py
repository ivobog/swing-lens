"""Complete READY business comparison against the earliest pre-enforcement baseline."""

from dataclasses import asdict

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from scripts.qa import t12a_behavior_probe as probe
from scripts.qa.t12c_behavior_probe import _business as contextual_business
from scripts.qa.t12d_behavior_probe import _business as winner_business

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
        ("sector_universe_service", "SectorUniverseService", "build", "SectorUniverse"),
        (
            "sector_rotation_service",
            "SectorRotationService",
            "build_sector_rotation_snapshot",
            "Sector",
        ),
    )
    probe.pytest_configure(config)


def _business(result):
    if (
        isinstance(result, (list, tuple))
        and result
        and hasattr(result[0], "universe_leadership_score")
    ):
        return Canonical.canonicalize(
            [{key: value for key, value in asdict(row).items() if key != "debug"} for row in result]
        )
    if hasattr(result, "universe_rows"):

        def without_debug(value):
            if isinstance(value, dict):
                return {key: without_debug(item) for key, item in value.items() if key != "debug"}
            if isinstance(value, list):
                return [without_debug(item) for item in value]
            return value

        return Canonical.canonicalize(without_debug(asdict(result)))
    if hasattr(result, "feature_json") or hasattr(result, "estimate"):
        return winner_business(result)
    return contextual_business(result)
