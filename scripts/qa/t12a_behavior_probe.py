"""Opt-in pytest probe for baseline/T12A business-output comparison.

Load with -p scripts.qa.t12a_behavior_probe --t12a-business-output=PATH.
Records selected business results only; returns original objects without modification.
Use the same probe and fixtures in both checkouts, then compare canonical JSON files.
"""

from __future__ import annotations

from functools import wraps
from importlib import import_module
from pathlib import Path

from app.services.canonical_evidence import CanonicalEvidenceSerializer

_records = []
_test_id = "collection"
_FIELDS = (
    "ticker",
    "final_score",
    "fundamental_score",
    "dual_score",
    "combined_decision",
    "position_size_hint",
    "is_complete",
    "profile_rank",
    "profile_score",
    "decision_label",
    "technical_profile_score",
    "data_quality_label",
    "confidence_score",
    "confidence_label",
    "required_feature_coverage",
    "freshness_status",
    "proposed_state",
    "previous_state",
    "phase_code",
    "actionability_candidate",
    "actionability",
    "terminal_reason",
    "opportunity_score",
    "event_risk_score",
    "data_confidence",
    "coverage_pct",
    "posture",
    "eligibility_status",
    "exclusion_reason",
    "technical_score",
    "combined_score",
    "status",
    "raw_probability",
    "calibrated_probability",
    "evidence_grade",
    "sample_size",
    "point_probability",
    "posterior_probability",
)
_TARGETS = (
    ("combined_decision", None, "combine_row_decision", "Combined"),
    ("ranking_profile_engine", None, "rank_profile", "Ranking"),
    ("setup_lifecycle.snapshot_builder", "SetupLifecycleSnapshotBuilder", "build", "Setup"),
    ("setup_lifecycle.lifecycle_engine", "SetupLifecycleEngine", "evaluate", "Lifecycle"),
    (
        "setup_lifecycle.actionability_policy",
        "SetupLifecycleActionabilityPolicy",
        "evaluate",
        "Actionability",
    ),
    ("ceri.snapshot_service", "CeriSnapshotService", "build_snapshot", "CERI"),
    ("winner_probability.feature_extractor", "WinnerFeatureExtractor", "extract", "WinnerFeatures"),
    (
        "winner_probability.capture_service",
        "WinnerPredictionCaptureService",
        "_build_prediction_snapshot",
        "Winner",
    ),
    (
        "winner_probability.probability_estimator",
        "ProbabilityEstimator",
        "create_decision_time_estimate",
        "WinnerEstimate",
    ),
)


def pytest_addoption(parser):
    parser.addoption("--t12a-business-output", default=None)


def pytest_configure(config):
    if not config.getoption("--t12a-business-output"):
        return
    _records.clear()
    for module, class_name, method, producer in _TARGETS:
        owner = import_module(f"app.services.{module}")
        if class_name:
            owner = getattr(owner, class_name)
        setattr(owner, method, _probe(getattr(owner, method), producer))


def pytest_runtest_setup(item):
    global _test_id
    _test_id = item.nodeid


def _probe(original, producer):
    @wraps(original)
    def wrapped(*args, **kwargs):
        result = original(*args, **kwargs)
        _records.append({"test": _test_id, "producer": producer, "output": _business(result)})
        return result

    return wrapped


def _business(result):
    if isinstance(result, (list, tuple)):
        return [_business(row) for row in result]
    # Setup and estimate result wrappers contain the actual domain output.
    result = getattr(result, "dto", getattr(result, "estimate", result))
    return CanonicalEvidenceSerializer.canonicalize(
        {name: getattr(result, name) for name in _FIELDS if hasattr(result, name)}
    )


def pytest_sessionfinish(session):
    output = session.config.getoption("--t12a-business-output")
    if output:
        Path(output).write_text(CanonicalEvidenceSerializer.dumps(_records), encoding="utf-8")
