from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from app.services.ceri import pipeline_containment_service as containment
from app.services.pipeline_outcome import derive_pipeline_outcome

NO_CHANGES = {"excluded": 0, "projections_repaired": 0, "processing_terminalized": 0}


@pytest.mark.parametrize("scope", [None, "absent", {"excluded_tickers": ["ROKU"]}])
def test_safe_partial_keeps_ceri_without_requiring_partial_scope(scope):
    # Pipeline 67 / upload 63: ordinary full-scope PARTIAL, certified CERI,
    # coverage gaps, no integrity hazard. NULL and absent scopes are equivalent.
    result = {
        "uploaded_rows": 136,
        "combined_results": 136,
        "incomplete_rows": 28,
        "ib_failure_count": 2,
        "technical_error_count": 4,
        "ceri_failed": 0,
    }
    if scope != "absent":
        result["partial_scope"] = scope
    result["pipeline_outcome_summary"] = derive_pipeline_outcome("PARTIAL", result)
    pipeline = SimpleNamespace(id=67, upload_run_id=63, status="PARTIAL", result_json=result)
    db = Mock()
    db.scalars.side_effect = AssertionError("safe PARTIAL must not enter the exclusion branch")

    assert containment.reconcile_unsuccessful_pipeline_ceri(db, pipeline) == NO_CHANGES
    assert containment.reconcile_unsuccessful_pipeline_ceri(db, pipeline) == NO_CHANGES
    db.assert_not_called()
    assert db.mock_calls == []


@pytest.mark.parametrize(
    "status,result",
    [
        ("PARTIAL", {}),
        ("PARTIAL", {"pipeline_outcome_summary": "invalid"}),
        ("PARTIAL", {"pipeline_outcome_summary": {}}),
        ("PARTIAL", {"pipeline_outcome_summary": {
            "contract_version": "unknown",
            "integrity": {"status": "NO_INTEGRITY_HAZARD"},
        }}),
        ("PARTIAL", {"pipeline_outcome_summary": derive_pipeline_outcome(
            "PARTIAL", {"evidence_integrity_failure": True}
        )}),
        ("PARTIAL", {"partial_scope": {"excluded_tickers": ["ROKU"]},
                     "ceri_failed": 1,
                     "pipeline_outcome_summary": derive_pipeline_outcome("PARTIAL", {})}),
        ("FAILED", {"pipeline_outcome_summary": derive_pipeline_outcome("PARTIAL", {})}),
        ("CANCELLED", {"pipeline_outcome_summary": derive_pipeline_outcome("PARTIAL", {})}),
        ("BLOCKED", {"pipeline_outcome_summary": derive_pipeline_outcome("PARTIAL", {})}),
    ],
)
def test_unsafe_or_uncertified_terminal_owner_still_runs_containment(monkeypatch, status, result):
    snapshot = SimpleNamespace(id=123)
    db = Mock()
    db.scalars.side_effect = [[], [snapshot]]
    append = Mock(return_value=1)
    rebuild = Mock(return_value=2)
    terminalize = Mock(return_value=3)
    monkeypatch.setattr(containment, "append_evidence_dispositions", append)
    monkeypatch.setattr(containment, "_rebuild_ceri_projections", rebuild)
    monkeypatch.setattr(containment, "_terminalize_processing", terminalize)
    pipeline = SimpleNamespace(id=67, upload_run_id=63, status=status, result_json=result)

    assert containment.reconcile_unsuccessful_pipeline_ceri(db, pipeline) == {
        "excluded": 1, "projections_repaired": 2, "processing_terminalized": 3
    }
    request = append.call_args.args[1][0]
    assert request.ceri_snapshot_id == 123
    assert request.disposition == "EXCLUDED"
    assert request.reason_code == "PARENT_PIPELINE_UNSUCCESSFUL"
    assert request.incident_reference == "pipeline:67"
    rebuild.assert_called_once_with(db, [snapshot])
    terminalize.assert_called_once_with(db, pipeline)


def test_completed_owner_does_not_run_containment():
    db = Mock()
    pipeline = SimpleNamespace(status="COMPLETED", result_json={})
    assert containment.reconcile_unsuccessful_pipeline_ceri(db, pipeline) == NO_CHANGES
    assert db.mock_calls == []
