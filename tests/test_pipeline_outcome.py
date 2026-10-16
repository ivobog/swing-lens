from app.services.pipeline_outcome import (
    INTEGRITY_HAZARD_DETECTED,
    NO_INTEGRITY_HAZARD,
    PIPELINE_OUTCOME_CONTRACT_VERSION,
    derive_pipeline_outcome,
    pipeline_requires_ceri_containment,
)


def test_partial_execution_with_coverage_gaps_is_not_an_integrity_failure() -> None:
    summary = derive_pipeline_outcome(
        "PARTIAL",
        {
            "uploaded_rows": 118,
            "combined_results": 118,
            "incomplete_rows": 53,
            "warning_rows": 104,
            "winner_prediction_failed": 0,
        },
    )

    assert summary["contract_version"] == PIPELINE_OUTCOME_CONTRACT_VERSION
    assert summary["execution_outcome"] == "SUCCEEDED_WITH_GAPS"
    assert summary["coverage"]["gaps"] == {"incomplete_rows": 53}
    assert summary["integrity"] == {"status": NO_INTEGRITY_HAZARD, "hazards": []}
    assert not pipeline_requires_ceri_containment(
        "PARTIAL", {"pipeline_outcome_summary": summary}
    )


def test_partial_execution_with_explicit_integrity_hazard_is_contained() -> None:
    summary = derive_pipeline_outcome(
        "PARTIAL",
        {
            "combined_results": 10,
            "incomplete_rows": 1,
            "identity_mismatch_detected": True,
        },
    )

    assert summary["integrity"]["status"] == INTEGRITY_HAZARD_DETECTED
    assert summary["integrity"]["hazards"] == ["identity_mismatch_detected"]
    assert pipeline_requires_ceri_containment(
        "PARTIAL", {"pipeline_outcome_summary": summary}
    )


def test_legacy_partial_without_versioned_summary_remains_fail_closed() -> None:
    assert pipeline_requires_ceri_containment("PARTIAL", {})
    assert pipeline_requires_ceri_containment(
        "PARTIAL", {"pipeline_outcome_summary": {"integrity": {"status": NO_INTEGRITY_HAZARD}}}
    )


def test_failed_cancelled_and_blocked_always_require_containment() -> None:
    for status in ("FAILED", "CANCELLED", "BLOCKED"):
        assert pipeline_requires_ceri_containment(status, {})


def test_outcome_summary_is_deterministic() -> None:
    result = {"combined_results": 3, "warning_rows": 2}
    assert derive_pipeline_outcome("COMPLETED", result) == derive_pipeline_outcome(
        "COMPLETED", dict(reversed(list(result.items())))
    )
