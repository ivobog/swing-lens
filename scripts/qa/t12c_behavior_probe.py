"""Opt-in complete READY business capture, extending the certified T12B probe."""

from app.services.canonical_evidence import CanonicalEvidenceSerializer as Canonical
from scripts.qa import t12a_behavior_probe as probe
from scripts.qa.t12b_behavior_probe import _business as core_business

pytest_addoption = probe.pytest_addoption
pytest_runtest_setup = probe.pytest_runtest_setup
pytest_sessionfinish = probe.pytest_sessionfinish


def pytest_configure(config):
    probe._business = _business
    probe.pytest_configure(config)


def _business(result):
    if hasattr(result, "opportunity_ledger_json"):
        return Canonical.canonicalize(
            {
                key: getattr(result, key)
                for key in (
                    "ticker",
                    "as_of_session",
                    "opportunity_score",
                    "event_risk_score",
                    "data_confidence",
                    "coverage_pct",
                    "posture",
                    "opportunity_ledger_json",
                    "confidence_ledger_json",
                    "event_risk_ledger_json",
                    "reasons_json",
                    "warnings_json",
                    "top_positive_contributors_json",
                    "top_negative_contributors_json",
                    "alignment_flags_json",
                )
            }
        )
    return core_business(result)
