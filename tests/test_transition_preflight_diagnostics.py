from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.transition_preflight_plan_service import (
    TransitionPreflightError,
    _canonical_structural_differences,
    _verification_checkpoint_due,
    verify_transition_decision_manifests_before_mutation,
)


def test_anchor_structural_diff_reports_exact_paths_without_values() -> None:
    expected = {
        "contract": "transition-run-start-anchor-v2",
        "run": {"id": 4, "raw_rows_fingerprint": "secret-source-a"},
        "immutable_source_lineage": {"MSFT": {"expected_pointer": {"latest_revision": 1}}},
    }
    actual = {
        "contract": "transition-run-start-anchor-v2",
        "run": {"id": 4, "raw_rows_fingerprint": "secret-source-b"},
        "immutable_source_lineage": {"MSFT": {"expected_pointer": {"latest_revision": 2}}},
    }

    differences = _canonical_structural_differences(expected, actual)

    assert [row["path"] for row in differences] == [
        "$.immutable_source_lineage.MSFT.expected_pointer.latest_revision",
        "$.run.raw_rows_fingerprint",
    ]
    assert all("value" not in row["expected"] for row in differences)
    assert all("value" not in row["actual"] for row in differences)
    assert differences[0]["origin"]["source"].startswith("setup_signal_snapshot_current_selections")
    assert differences[1]["origin"]["source"] == "raw_company_rows.* semantic projection"


def test_reconstruction_failure_is_typed_before_anchor_comparison() -> None:
    plan = SimpleNamespace(
        id=31,
        upload_run_id=7,
        pipeline_run_id=11,
        market_calculation_context_id=19,
        candidate_classification="HIGH",
        selection_keys_json=[],
    )

    class Db:
        def scalar(self, _statement):
            return plan

    cutoff = SimpleNamespace(context_id=19)
    with pytest.raises(TransitionPreflightError) as caught:
        verify_transition_decision_manifests_before_mutation(
            Db(),
            upload_run_id=7,
            market_cutoff=cutoff,
            built_rows=(),
            repository=object(),
            reconstruction_errors={
                "MSFT": (
                    "Parent instance is not bound to a Session; lazy load operation cannot "
                    "proceed; password=secret"
                )
            },
        )

    assert caught.value.code == "DECISION_MANIFEST_RECONSTRUCTION_FAILED"
    assert caught.value.diagnostics["comparison_point"] == ("SETUP_PRE_MUTATION_RECONSTRUCTION")
    assert caught.value.diagnostics["failed_tickers"] == ["MSFT"]
    assert caught.value.diagnostics["errors"]["MSFT"]["category"] == ("DETACHED_ORM_RELATIONSHIP")
    assert set(caught.value.diagnostics["errors"]["MSFT"]) == {
        "category",
        "message_digest",
    }


def test_transition_verification_checkpoints_every_five_members() -> None:
    checkpoints = [
        index for index in range(1, 101) if _verification_checkpoint_due(index, 100)
    ]

    assert checkpoints == list(range(5, 101, 5))
    assert _verification_checkpoint_due(3, 3)
