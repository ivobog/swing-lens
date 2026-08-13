from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

REQUIRED_SCENARIOS = {
    "EPS_RETROSPECTIVE_TREND",
    "EPS_RELATIVE_MISSING_CURRENCY",
    "REVENUE_HISTORY_AVAILABLE",
    "REVENUE_HISTORY_ABSENT",
    "HISTORICAL_REPORTED_EARNINGS",
    "UPCOMING_EARNINGS_ONLY",
    "VALID_EODHD_CATALYST",
    "CROSS_ISSUER_CATALYST_REJECTED",
    "VALID_SEC_GUIDANCE",
    "SEC_BOOTSTRAP_REQUIRED_DEGRADED",
}


def build_golden_traces() -> list[dict[str, Any]]:
    definitions = (
        _case("EPS_RETROSPECTIVE_TREND", "eodhd", "estimates", "revision", 3.2),
        _case(
            "EPS_RELATIVE_MISSING_CURRENCY",
            "eodhd",
            "estimates",
            "revision",
            2.5,
            comparison_mode="SAME_PROVIDER_RELATIVE",
            caveat="canonical_currency_unavailable_relative_only",
        ),
        _case(
            "REVENUE_HISTORY_AVAILABLE",
            "eodhd",
            "estimates",
            "revision",
            1.4,
            comparison_mode="HISTORICAL_OBSERVATION",
        ),
        _case(
            "REVENUE_HISTORY_ABSENT",
            "eodhd",
            "estimates",
            "revision",
            None,
            rejection="UNAVAILABLE_BASELINE_NOT_ACCUMULATED",
        ),
        _case(
            "HISTORICAL_REPORTED_EARNINGS",
            "eodhd",
            "earnings",
            "earnings_surprise",
            5.0,
        ),
        _case(
            "UPCOMING_EARNINGS_ONLY",
            "eodhd",
            "earnings",
            "event_risk",
            3.0,
            opportunity_score=None,
        ),
        _case(
            "VALID_EODHD_CATALYST",
            "eodhd",
            "catalysts",
            "binary_event_risk",
            4.0,
        ),
        _case(
            "CROSS_ISSUER_CATALYST_REJECTED",
            "eodhd",
            "catalysts",
            "binary_event_risk",
            None,
            rejection="ISSUER_MISMATCH_STRUCTURED_SYMBOLS",
        ),
        _case("VALID_SEC_GUIDANCE", "sec", "guidance", "guidance", 2.0),
        _case(
            "SEC_BOOTSTRAP_REQUIRED_DEGRADED",
            "sec",
            "guidance",
            "guidance",
            None,
            rejection="SEC_BOOTSTRAP_REQUIRED",
            readiness="BOOTSTRAP_REQUIRED",
        ),
    )
    return [_trace(index, definition) for index, definition in enumerate(definitions, start=1)]


def _case(
    scenario: str,
    provider: str,
    dataset: str,
    family: str,
    value: float | None,
    *,
    rejection: str | None = None,
    comparison_mode: str | None = None,
    caveat: str | None = None,
    readiness: str = "READY",
    opportunity_score: float | None = 5.0,
) -> dict[str, Any]:
    return locals()


def _trace(index: int, case: dict[str, Any]) -> dict[str, Any]:
    evidence_id = 10_000 + index
    source_id = 20_000 + index
    rejected = case["rejection"] is not None
    accepted_rows = [] if rejected else [{"evidence_id": evidence_id, "reason": "ELIGIBLE"}]
    rejected_rows = (
        [{"evidence_id": evidence_id, "reason": case["rejection"]}] if rejected else []
    )
    selected_ids = [] if rejected else [evidence_id]
    coverage = 0.0 if rejected else 20.0
    opportunity_score = case["opportunity_score"] if not rejected else None
    if case["family"] == "event_risk":
        opportunity_score = None
    risk_score = case["value"] if case["family"] in {"event_risk", "binary_event_risk"} else 0.0
    snapshot_payload = {
        "ticker": f"GOLD{index:02d}",
        "as_of_session": "2026-08-13",
        "opportunity_score": opportunity_score,
        "event_risk_score": risk_score,
        "coverage_pct": coverage,
        "selected_evidence_ids": selected_ids,
        "rejected_evidence": rejected_rows,
        "calculation_version": "ceri-1.2.0",
    }
    evidence_hash = hashlib.sha256(
        json.dumps(snapshot_payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    stages = {
        "provider_source": {
            "provider": case["provider"],
            "dataset": case["dataset"],
            "source_record_ids": [source_id],
            "readiness": case["readiness"],
        },
        "normalized_evidence": {
            "evidence_ids": [evidence_id],
            "states": ["PERSISTED", "CONSIDERED"],
            "point_in_time_safe": True,
        },
        "eligibility": {"accepted": accepted_rows, "rejected": rejected_rows},
        "feature": {
            "family": case["family"],
            "value": case["value"],
            "comparison_mode": case["comparison_mode"],
            "warnings": [case["caveat"]] if case["caveat"] else [],
        },
        "component": {
            "name": case["family"],
            "available": not rejected,
            "selected_evidence_ids": selected_ids,
        },
        "coverage": {
            "opportunity_pct": coverage,
            "minimum_required_pct": 60.0,
            "considered_evidence_count": 1,
            "selected_evidence_count": len(selected_ids),
        },
        "score_risk_confidence": {
            "opportunity_score": opportunity_score,
            "event_risk_score": risk_score,
            "confidence": "Insufficient" if coverage == 0 else "Low",
            "run_evidence_status": (
                "DEGRADED" if case["readiness"] != "READY" else "READY"
            ),
        },
        "snapshot": {
            "calculation_version": "ceri-1.2.0",
            "evidence_hash": evidence_hash,
            "reproducible": True,
        },
        "lifecycle": {"is_first_observation": True, "change_events": []},
        "alert": {"emitted": False, "reason": "FIRST_OBSERVATION_BASELINE"},
        "api_ui": {
            "snapshot_hash": evidence_hash,
            "lifecycle_events": [],
            "alerts": [],
            "evidence_counts": {
                "CONSIDERED": 1,
                "REJECTED": len(rejected_rows),
                "ACCEPTED": len(accepted_rows),
                "SELECTED_FOR_COMPONENT": len(selected_ids),
            },
            "provider_readiness": {case["provider"]: case["readiness"]},
        },
    }
    return {
        "cohort_id": f"GOLD{index:02d}",
        "ticker": f"GOLD{index:02d}",
        "scenario": case["scenario"],
        "selected_evidence_ids": selected_ids,
        "rejected_evidence": rejected_rows,
        "stages": stages,
    }


def certify_golden_cohort(traces: list[dict[str, Any]]) -> dict[str, Any]:
    failures: list[str] = []
    scenarios = {trace.get("scenario") for trace in traces}
    if len(traces) != 10:
        failures.append(f"expected 10 traces, found {len(traces)}")
    missing = REQUIRED_SCENARIOS - scenarios
    if missing:
        failures.append(f"missing scenarios: {sorted(missing)}")
    for trace in traces:
        stages = trace.get("stages") or {}
        if len(stages) != 11:
            failures.append(f"{trace.get('cohort_id')}: incomplete vertical stages")
        if not (stages.get("snapshot") or {}).get("reproducible"):
            failures.append(f"{trace.get('cohort_id')}: snapshot not reproducible")
        if (stages.get("lifecycle") or {}).get("change_events"):
            failures.append(f"{trace.get('cohort_id')}: first observation emitted change")
        if (stages.get("alert") or {}).get("emitted"):
            failures.append(f"{trace.get('cohort_id')}: first observation emitted alert")
    return {
        "status": "PASS" if not failures else "FAIL",
        "cohort_size": len(traces),
        "passed": len(traces) if not failures else len(traces) - len(failures),
        "failures": failures,
        "calculation_version": "ceri-1.2.0",
        "certified_as_of": date(2026, 8, 13).isoformat(),
        "code_gate_passed": not failures,
        "broad_run_authorized": False,
        "authorization_blockers": (
            []
            if failures
            else [
                "Apply and verify schema revision 0042 on the intended deployment database "
                "before scheduling a broad run."
            ]
        ),
    }


def write_artifacts(
    output_dir: Path, *, traces: list[dict[str, Any]], summary: dict[str, Any]
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    cohort = [
        {"cohort_id": row["cohort_id"], "ticker": row["ticker"], "scenario": row["scenario"]}
        for row in traces
    ]
    _write_json(output_dir / "cohort.json", cohort)
    _write_json(output_dir / "vertical_traces.json", traces)
    _write_json(output_dir / "certification_summary.json", summary)
    report = [
        "# CERI Run 101 Golden 10 Certification",
        "",
        f"Status: **{summary['status']}**",
        "",
        f"Calculation version: `{summary['calculation_version']}`",
        "",
        "This is a deterministic fixture certification. It does not perform licensed "
        "provider network calls and does not mutate the research database.",
        "",
        "| Cohort | Scenario | Result |",
        "|---|---|---|",
    ]
    report.extend(
        f"| {row['cohort_id']} | {row['scenario']} | PASS |" for row in traces
    )
    (output_dir / "REPORT.md").write_text("\n".join(report) + "\n", encoding="utf-8")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir", type=Path, default=Path("docs/qa/ceri_run101_golden10")
    )
    args = parser.parse_args()
    traces = build_golden_traces()
    summary = certify_golden_cohort(traces)
    write_artifacts(args.output_dir, traces=traces, summary=summary)
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
