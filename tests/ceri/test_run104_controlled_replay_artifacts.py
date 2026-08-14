from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
QA = ROOT / "docs" / "qa"


def _rows() -> list[dict[str, str]]:
    path = QA / "CERI_RUN104_ORIGINAL_VS_REPLAY.csv"
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_run104_replay_csv_has_complete_unique_population_and_required_parity() -> None:
    rows = _rows()
    snapshots = [row for row in rows if row["record_type"] == "SNAPSHOT"]
    assert len(snapshots) == 177
    assert len({row["ticker"] for row in snapshots}) == 177
    assert len([row for row in rows if row["record_type"] == "REVISION_FEATURE"]) == 1253
    required = {
        "original_revision_magnitude",
        "replay_revision_magnitude",
        "original_revision_breadth",
        "replay_revision_breadth",
        "original_revision_acceleration",
        "replay_revision_acceleration",
        "original_surprise_trend",
        "replay_surprise_trend",
        "original_guidance",
        "replay_guidance",
        "original_catalysts",
        "replay_catalysts",
        "original_price_response",
        "replay_price_response",
        "original_coverage",
        "replay_coverage",
        "original_score",
        "replay_score",
        "original_posture",
        "replay_posture",
        "original_confidence",
        "replay_confidence",
        "original_event_risk",
        "replay_event_risk",
        "original_evidence_hash",
        "replay_evidence_hash",
    }
    assert required <= snapshots[0].keys()
    assert all(row["original_event_risk"] == row["replay_event_risk"] for row in snapshots)
    assert all(row["original_coverage"] == row["replay_coverage"] for row in snapshots)


def test_msge_trace_explains_historical_pairing_and_corrected_atomic_lineage() -> None:
    rows = [
        row
        for row in _rows()
        if row["record_type"] == "REVISION_FEATURE"
        and row["ticker"] == "MSGE"
        and row["period"] == "CURRENT_QUARTER"
    ]
    assert {row["old_pct_change"] for row in rows} == {"50.909091"}
    assert {row["old_lineage_reproduced_pct_change"] for row in rows} == {
        "0",
        "3.061224",
    }
    assert {row["replay_pct_change"] for row in rows} == {"50.909091"}
    assert all(row["current_value"] == "-0.135" for row in rows)
    assert all(row["baseline_value"] == "-0.275" for row in rows)


def test_reports_distinguish_original_history_from_authoritative_replay() -> None:
    report = (QA / "CERI_RUN104_CONTROLLED_REPLAY_REPORT.md").read_text(encoding="utf-8")
    lineage = (QA / "CERI_RUN104_LINEAGE_RECERTIFICATION.md").read_text(
        encoding="utf-8"
    )
    assert "**ORIGINAL RUN 104:** immutable, historically affected" in report
    assert "**CONTROLLED REPLAY:** corrected/reproducible certification result" in report
    assert "run104-revision-lineage-recertification-v2" in report
    assert "suitable to become the reference basis for the next live CERI run" in report
    assert "every selected revision feature" in lineage
    assert "**PASS**" in lineage
    assert "estimate_coverage_low remains INFO for 175/177" in lineage
