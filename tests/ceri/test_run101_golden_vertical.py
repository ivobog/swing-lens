from __future__ import annotations

import json

from scripts.qa.certify_ceri_run101_golden import (
    REQUIRED_SCENARIOS,
    build_golden_traces,
    certify_golden_cohort,
    write_artifacts,
)


def test_golden_cohort_covers_all_required_vertical_scenarios() -> None:
    traces = build_golden_traces()

    assert len(traces) == 10
    assert {trace["scenario"] for trace in traces} == REQUIRED_SCENARIOS
    for trace in traces:
        assert list(trace["stages"]) == [
            "provider_source",
            "normalized_evidence",
            "eligibility",
            "feature",
            "component",
            "coverage",
            "score_risk_confidence",
            "snapshot",
            "lifecycle",
            "alert",
            "api_ui",
        ]
        assert trace["stages"]["snapshot"]["calculation_version"] == "ceri-1.2.0"
        assert trace["stages"]["snapshot"]["reproducible"] is True
        assert trace["stages"]["lifecycle"]["is_first_observation"] is True
        assert trace["stages"]["lifecycle"]["change_events"] == []
        assert trace["stages"]["alert"]["emitted"] is False


def test_golden_trace_lists_every_selected_and_rejected_id_with_reason() -> None:
    traces = build_golden_traces()

    for trace in traces:
        eligibility = trace["stages"]["eligibility"]
        classified = {
            row["evidence_id"] for row in eligibility["accepted"] + eligibility["rejected"]
        }
        selected = set(trace["selected_evidence_ids"])
        rejected = {row["evidence_id"] for row in trace["rejected_evidence"]}
        assert selected | rejected <= classified
        assert all(row["reason"] for row in trace["rejected_evidence"])
        assert trace["stages"]["api_ui"]["snapshot_hash"] == trace["stages"][
            "snapshot"
        ]["evidence_hash"]


def test_golden_certification_writes_machine_readable_artifacts(tmp_path) -> None:
    traces = build_golden_traces()
    summary = certify_golden_cohort(traces)

    write_artifacts(tmp_path, traces=traces, summary=summary)

    assert summary["status"] == "PASS"
    assert summary["passed"] == 10
    assert json.loads((tmp_path / "certification_summary.json").read_text())["status"] == "PASS"
    assert len(json.loads((tmp_path / "vertical_traces.json").read_text())) == 10
    assert (tmp_path / "REPORT.md").exists()
