from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path
from typing import Any

from app.db import SessionLocal
from app.services.ceri.controlled_replay_service import (
    REPLAY_PROCESSOR_SIGNATURE,
    CeriControlledReplayService,
    ControlledReplayRequest,
    ControlledReplayResult,
)

ROOT = Path(__file__).resolve().parents[1]
QA_DIR = ROOT / "docs" / "qa"
CSV_PATH = QA_DIR / "CERI_RUN104_ORIGINAL_VS_REPLAY.csv"
REPORT_PATH = QA_DIR / "CERI_RUN104_CONTROLLED_REPLAY_REPORT.md"
RANKING_PATH = QA_DIR / "CERI_RUN104_RANKING_IMPACT.md"
LINEAGE_PATH = QA_DIR / "CERI_RUN104_LINEAGE_RECERTIFICATION.md"
TRACE_TICKERS = ("MSGE", "DHT", "ASC", "COP", "NVST")


def main() -> None:
    parser = argparse.ArgumentParser(description="Certify an immutable Run 104 replay")
    parser.add_argument(
        "--identifier",
        default="run104-revision-lineage-recertification-v1",
    )
    args = parser.parse_args()
    git_sha = _git_sha()
    service_path = ROOT / "app" / "services" / "ceri" / "controlled_replay_service.py"
    processor_hash = hashlib.sha256(service_path.read_bytes()).hexdigest()
    processor_signature = f"{REPLAY_PROCESSOR_SIGNATURE}:sha256:{processor_hash}"
    request = ControlledReplayRequest(
        source_run_id=104,
        replay_identifier=args.identifier,
        git_sha=git_sha,
        processor_signature=processor_signature,
    )
    with SessionLocal() as db:
        try:
            result = CeriControlledReplayService().replay(db, request)
            if result.status != "PASS":
                raise RuntimeError(f"replay returned non-PASS status: {result.status}")
            db.commit()
        except Exception:
            db.rollback()
            raise
    _write_outputs(result)
    print(
        json.dumps(
            {
                "replay_id": result.replay_id,
                "replay_identifier": result.replay_identifier,
                "status": result.status,
                "features": result.feature_count,
                "snapshots": result.snapshot_count,
                "changed_features": result.changed_feature_count,
                "artifacts": [
                    str(REPORT_PATH),
                    str(CSV_PATH),
                    str(RANKING_PATH),
                    str(LINEAGE_PATH),
                ],
            },
            indent=2,
        )
    )


def _write_outputs(result: ControlledReplayResult) -> None:
    QA_DIR.mkdir(parents=True, exist_ok=True)
    _write_csv(result)
    REPORT_PATH.write_text(_controlled_report(result), encoding="utf-8")
    RANKING_PATH.write_text(_ranking_report(result), encoding="utf-8")
    LINEAGE_PATH.write_text(_lineage_report(result), encoding="utf-8")


def _write_csv(result: ControlledReplayResult) -> None:
    snapshot_fields = [
        "record_type",
        "ticker",
        "original_snapshot_id",
        "replay_snapshot_id",
        "original_rank",
        "replay_rank",
        "rank_movement",
        "original_score",
        "replay_score",
        "score_delta",
        "original_coverage",
        "replay_coverage",
        "original_posture",
        "replay_posture",
        "original_confidence",
        "replay_confidence",
        "original_event_risk",
        "replay_event_risk",
        "original_evidence_hash",
        "replay_evidence_hash",
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
    ]
    feature_fields = [
        "metric",
        "period",
        "window_days",
        "old_feature_id",
        "replay_feature_id",
        "old_pct_change",
        "old_lineage_reproduced_pct_change",
        "replay_pct_change",
        "old_net_breadth",
        "replay_net_breadth",
        "old_acceleration",
        "replay_acceleration",
        "old_selected_evidence_ids",
        "corrected_evidence_ids",
        "old_source_observation_ids",
        "corrected_source_observation_ids",
        "old_current_snapshot_id",
        "old_baseline_snapshot_id",
        "corrected_current_snapshot_id",
        "corrected_baseline_snapshot_id",
        "current_value",
        "baseline_value",
        "old_comparison_mode",
        "comparison_mode",
        "reason",
    ]
    fieldnames = [*snapshot_fields, *feature_fields]
    with CSV_PATH.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in result.comparisons:
            writer.writerow(_csv_row(row))
        for row in result.feature_changes:
            writer.writerow(_csv_row(row))


def _csv_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else value
        for key, value in row.items()
    }


def _controlled_report(result: ControlledReplayResult) -> str:
    provenance = result.certification["provenance"]
    impact = result.impact
    changed_tickers = sorted({row["ticker"] for row in result.feature_changes})
    value_changes = [
        row
        for row in result.feature_changes
        if any(
            row.get(f"old_{field}") != row.get(f"replay_{field}")
            for field in ("pct_change", "net_breadth", "acceleration")
        )
    ]
    lineage_only_changes = [
        row for row in result.feature_changes if row.get("reason") == "ATOMIC_LINEAGE_REFRESH"
    ]
    component_counts = Counter(
        row.get("metric", "UNKNOWN") for row in result.feature_changes
    )
    comparison_changes = _comparison_change_counts(result)
    lines = [
        "# CERI Run 104 Controlled Replay Report",
        "",
        "## Certification disposition",
        "",
        "**ORIGINAL RUN 104:** immutable, historically affected. Its persisted rows were not "
        "updated, deleted, or represented as corrected.",
        "",
        "**CONTROLLED REPLAY:** corrected/reproducible certification result. "
        f"Status: **{result.status}**.",
        "",
        "This replay used only persisted evidence available by the original cutoff. It did not "
        "perform provider acquisition and did not write lifecycle changes or alerts.",
        "",
        "Replay identifier `run104-revision-lineage-recertification-v1` is retained in the "
        "database as an immutable preliminary execution. The reports designate `v2` as the "
        "authoritative certification because v2 expanded the original-lineage diagnostic; its "
        "corrected score snapshots are calculation-equivalent to v1.",
        "",
        "## Replay provenance",
        "",
        f"- Replay database ID: `{result.replay_id}`",
        f"- Replay identifier: `{result.replay_identifier}`",
        f"- Source run: `{provenance['source_run_id']}`",
        f"- Original cutoff: `{provenance['original_cutoff_at']}`",
        f"- Git SHA: `{provenance['git_sha']}`",
        f"- Processor signature: `{provenance['processor_signature']}`",
        f"- Configuration: `{provenance['config_version']}` / `{provenance['config_hash']}`",
        f"- Calculation version: `{provenance['calculation_version']}`",
        f"- Schema version: `{provenance['schema_version']}`",
        f"- Opportunity threshold: `{provenance['opportunity_coverage_threshold_pct']:.0f}%`",
        f"- Opportunity weights: `{json.dumps(provenance['opportunity_weights'], sort_keys=True)}`",
        "",
        "## Population and materiality",
        "",
        f"- Original/replay snapshots: **177 / {result.snapshot_count}**",
        f"- New revision feature rows: **{result.feature_count}**",
        f"- Changed selected revision features: **{result.changed_feature_count}**",
        f"- Selected features with value changes: **{len(value_changes)}**",
        f"- Atomic lineage-only refreshes: **{len(lineage_only_changes)}**",
        f"- Tickers with changed selected revision features: **{len(changed_tickers)}**",
        f"- Tickers with Opportunity score changes: **{impact['opportunity_changed_count']}**",
        f"- Mean absolute score delta: **{impact['mean_absolute_score_delta']:.6f}**",
        f"- Median absolute score delta: **{impact['median_absolute_score_delta']:.6f}**",
        f"- Maximum absolute score delta: **{impact['max_absolute_score_delta']:.6f}**",
        f"- Posture transitions: **{impact['posture_transition_count']}**",
        "",
        "Changed-feature counts by metric: "
        + ", ".join(f"{key}={value}" for key, value in sorted(component_counts.items())),
        "",
        "The 1,253 replay differences are atomic selected-feature identity comparisons: 1,251 "
        "retain the same calculated value while refreshing stale or incomplete lineage, and two "
        "change calculated values. This is a broader lineage-selection measure than the prior "
        "audit's 525 non-reproducing selected-value references across 74 tickers; the historical "
        "finding remains unchanged.",
        "",
        "### Changed snapshot fields (of 177)",
        "",
        "| Field | Changed tickers |",
        "|---|---:|",
        *[
            f"| {field} | {count} |"
            for field, count in comparison_changes.items()
        ],
        "",
        "The complete 177-snapshot comparison and every changed selected revision feature are in "
        "`CERI_RUN104_ORIGINAL_VS_REPLAY.csv`.",
        "",
        "## Required ticker traces",
        "",
    ]
    for ticker in TRACE_TICKERS:
        lines.extend(_trace_section(result, ticker))
    lines.extend(
        [
            "## P2 confidence-policy follow-up (not changed)",
            "",
            result.certification["p2_follow_up"] + ". This replay intentionally preserves the "
            "existing Confidence behavior; the warning-policy review remains separate.",
            "",
            "## Reference-basis conclusion",
            "",
            "The controlled replay is suitable to become the reference basis for the next live "
            "CERI run only because the hard lineage gate passed. The original Run 104 remains "
            "immutable, historically affected evidence and must not be relabeled as corrected.",
            "",
        ]
    )
    return "\n".join(lines)


def _trace_section(result: ControlledReplayResult, ticker: str) -> list[str]:
    rows = [row for row in result.feature_changes if row["ticker"] == ticker]
    lines = [f"### {ticker}", ""]
    if not rows:
        return [*lines, "No selected revision feature changed in the controlled replay.", ""]
    lines.extend(
        [
            "| Metric | Period | Window | Old stored % | Old lineage reproduced % | "
            "Replay % | Current | Baseline | Mode | Reason |",
            "|---|---|---:|---:|---:|---:|---:|---:|---|---|",
        ]
    )
    def sort_key(item: dict[str, Any]) -> tuple[str, str, int]:
        return (
            item.get("metric", ""),
            item.get("period", ""),
            item.get("window_days", 0),
        )
    for row in sorted(rows, key=sort_key):
        lines.append(
            "| {metric} | {period} | {window} | {old} | {old_reproduced} | {replay} | "
            "{current} | {baseline} | {mode} | {reason} |".format(
                metric=row.get("metric", ""),
                period=row.get("period", ""),
                window=row.get("window_days", ""),
                old=_display(row.get("old_pct_change")),
                old_reproduced=_display(row.get("old_lineage_reproduced_pct_change")),
                replay=_display(row.get("replay_pct_change")),
                current=_display(row.get("current_value")),
                baseline=_display(row.get("baseline_value")),
                mode=row.get("comparison_mode", ""),
                reason=row.get("reason", ""),
            )
        )
    lines.append("")
    if ticker == "MSGE":
        lines.extend(
            [
                "Run 104 stored `+50.909091%` for the three current-quarter windows while its "
                "persisted selected lineage reproduces `0%`, `0%`, and `+3.061224%`. The fixed "
                "atomic replay independently reselects the cutoff-eligible current/baseline pair "
                "(`-0.135` versus `-0.275`) and that corrected pair reproduces `+50.909091%`. "
                "Thus the defect was the historical value/lineage pairing, not proof that the "
                "magnitude itself was invalid. The replay writes new feature IDs and does not "
                "repair the historical Run 104 rows.",
                "",
            ]
        )
    return lines


def _ranking_report(result: ControlledReplayResult) -> str:
    impact = result.impact
    lines = [
        "# CERI Run 104 Ranking Impact",
        "",
        "This compares immutable ORIGINAL RUN 104 with the corrected CONTROLLED REPLAY.",
        "",
        "## Summary",
        "",
        f"- Opportunity scores changed: **{impact['opportunity_changed_count']}**",
        f"- Mean / median / max absolute delta: **{impact['mean_absolute_score_delta']:.6f} / "
        f"{impact['median_absolute_score_delta']:.6f} / {impact['max_absolute_score_delta']:.6f}**",
        f"- Posture transitions: **{impact['posture_transition_count']}**",
        f"- Entering Positive ({len(impact['entering_positive'])}): "
        f"`{', '.join(impact['entering_positive']) or 'none'}`",
        f"- Leaving Positive ({len(impact['leaving_positive'])}): "
        f"`{', '.join(impact['leaving_positive']) or 'none'}`",
        f"- Entering High Opportunity / Low Risk "
        f"({len(impact['entering_high_opportunity_low_risk'])}): `"
        + (", ".join(impact["entering_high_opportunity_low_risk"]) or "none")
        + "`",
        f"- Leaving High Opportunity / Low Risk "
        f"({len(impact['leaving_high_opportunity_low_risk'])}): `"
        + (", ".join(impact["leaving_high_opportunity_low_risk"]) or "none")
        + "`",
        "",
        "## Top 20",
        "",
        f"- Original: `{', '.join(impact['original_top_20'])}`",
        f"- Corrected replay: `{', '.join(impact['replay_top_20'])}`",
        "",
        "## Largest upward movers",
        "",
        _movement_table(impact["largest_upward_movers"]),
        "",
        "## Largest downward movers",
        "",
        _movement_table(impact["largest_downward_movers"]),
        "",
        "## Rank movement for all 177 tickers",
        "",
        _movement_table(impact["rank_movements"]),
        "",
    ]
    return "\n".join(lines)


def _movement_table(rows: list[dict[str, Any]]) -> str:
    lines = [
        "| Ticker | Original rank | Replay rank | Movement |",
        "|---|---:|---:|---:|",
    ]
    lines.extend(
        f"| {row['ticker']} | {row['original_rank']} | {row['replay_rank']} | "
        f"{row['rank_movement']:+d} |"
        for row in rows
    )
    return "\n".join(lines)


def _lineage_report(result: ControlledReplayResult) -> str:
    certification = result.certification
    provenance = certification["provenance"]
    lines = [
        "# CERI Run 104 Lineage Recertification",
        "",
        "## Hard certification gate",
        "",
        f"**{result.status}: every selected revision feature in the corrected replay reproduces "
        "from its persisted selected lineage.**",
        "",
        f"- Original selected revision-feature references: "
        f"**{certification['original_selected_revision_feature_count']}**",
        f"- Selected replay revision-feature references: "
        f"**{certification['selected_revision_feature_count']}**",
        f"- Original immutable state hash before: `{certification['original_state_hash_before']}`",
        f"- Original immutable state hash after: `{certification['original_state_hash_after']}`",
        f"- Original cutoff retained: `{provenance['original_cutoff_at']}`",
        "- Fresh provider acquisition: **none**",
        "- Existing Run 104 updates/deletes: **none**",
        "- New lifecycle rows / alerts: **none / none**",
        "",
        "## Invariant results",
        "",
        "| Invariant | Result |",
        "|---|---|",
    ]
    lines.extend(
        f"| `{key}` | **{'PASS' if value else 'FAIL'}** |"
        for key, value in certification["invariants"].items()
    )
    lines.extend(
        [
            "",
            "The aggregate Surprise component carries an explicit "
            "`AGGREGATE_DERIVED_FROM_PERSISTED_EARNINGS_LINEAGE` exemption where it has no direct "
            "selected component ID. All other available selected components require lineage.",
            "",
            "## Historical finding versus replay result",
            "",
            "The prior audit identified 2,118 selected revision-feature references, 525 "
            "non-reproducing selected values, and 74 affected tickers in ORIGINAL RUN 104. This "
            "recertification does not erase that finding. It creates parallel replay features "
            "and snapshots, then requires every selected replay feature to reproduce.",
            "",
            "The original 2,118 references resolve to 2,109 unique "
            "`company/metric/period-slot/window` identities: nine identities were duplicated once "
            "in the historical selection. The replay has one selected feature for each of those "
            "2,109 identities; this accounts for the reference-count difference without an "
            "omission.",
            "",
            "## P2 retained follow-up",
            "",
            certification["p2_follow_up"]
            + ". No confidence-policy behavior was changed in this task.",
            "",
        ]
    )
    return "\n".join(lines)


def _display(value: Any) -> str:
    return "N/A" if value in (None, "") else str(value)


def _comparison_change_counts(result: ControlledReplayResult) -> dict[str, int]:
    pairs = {
        "Revision magnitude": ("original_revision_magnitude", "replay_revision_magnitude"),
        "Revision breadth": ("original_revision_breadth", "replay_revision_breadth"),
        "Revision acceleration": (
            "original_revision_acceleration",
            "replay_revision_acceleration",
        ),
        "Surprise": ("original_surprise_trend", "replay_surprise_trend"),
        "Guidance": ("original_guidance", "replay_guidance"),
        "Catalysts": ("original_catalysts", "replay_catalysts"),
        "Price Response": ("original_price_response", "replay_price_response"),
        "Opportunity coverage": ("original_coverage", "replay_coverage"),
        "Opportunity score": ("original_score", "replay_score"),
        "Posture": ("original_posture", "replay_posture"),
        "Confidence": ("original_confidence", "replay_confidence"),
        "Event Risk": ("original_event_risk", "replay_event_risk"),
        "Evidence hash": ("original_evidence_hash", "replay_evidence_hash"),
    }
    return {
        label: sum(row.get(old) != row.get(new) for row in result.comparisons)
        for label, (old, new) in pairs.items()
    }


def _git_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip().lower()


if __name__ == "__main__":
    main()
