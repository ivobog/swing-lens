from __future__ import annotations

import csv
import json
from io import StringIO
from typing import Any

RUN_EVIDENCE_CSV_HEADERS = [
    "ticker",
    "prediction_id",
    "prediction_as_of_date",
    "entry_model",
    "horizon_sessions",
    "outcome_definition",
    "estimate_kind",
    "source_version",
    "training_cutoff_at",
    "point_probability",
    "lower_bound",
    "upper_bound",
    "interval_width",
    "sample_n",
    "effective_n",
    "evidence_grade",
    "expected_return_pct",
    "median_return_pct",
    "median_mfe_pct",
    "median_mae_pct",
    "target_first_rate",
    "evidence_manifest_hash",
    "config_hash",
    "feature_schema_version",
]

EXPLORER_CSV_HEADERS = [
    "segment",
    "segment_value",
    "sample_n",
    "suppressed",
    "mean_probability",
    "mean_lower_bound",
    "evidence_grade_counts",
]


def export_run_evidence_csv(payload: dict[str, Any]) -> str:
    rows = [_run_export_row(row) for row in payload.get("items", [])]
    return _write_csv(RUN_EVIDENCE_CSV_HEADERS, rows)


def export_run_evidence_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def export_outcome_explorer_csv(payload: dict[str, Any]) -> str:
    rows = [
        {
            "segment": row["segment"],
            "segment_value": row["segment_value"],
            "sample_n": row["sample_n"],
            "suppressed": row["suppressed"],
            "mean_probability": row["mean_probability"],
            "mean_lower_bound": row["mean_lower_bound"],
            "evidence_grade_counts": json.dumps(
                row.get("evidence_grade_counts", {}),
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        for row in payload.get("segments", [])
    ]
    return _write_csv(EXPLORER_CSV_HEADERS, rows)


def export_reproduction_manifest_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True, default=str)


def _run_export_row(row: dict[str, Any]) -> dict[str, Any]:
    prediction = row.get("prediction") or {}
    estimate = row.get("estimate") or {}
    outcome = row.get("outcome_definition") or {}
    return {
        "ticker": prediction.get("ticker"),
        "prediction_id": prediction.get("id"),
        "prediction_as_of_date": prediction.get("prediction_as_of_date"),
        "entry_model": outcome.get("entry_model"),
        "horizon_sessions": outcome.get("horizon_sessions"),
        "outcome_definition": outcome.get("definition_id"),
        "estimate_kind": estimate.get("estimate_kind"),
        "source_version": estimate.get("source_version"),
        "training_cutoff_at": estimate.get("training_cutoff_at"),
        "point_probability": estimate.get("point_probability"),
        "lower_bound": estimate.get("lower_bound"),
        "upper_bound": estimate.get("upper_bound"),
        "interval_width": estimate.get("interval_width"),
        "sample_n": estimate.get("sample_n"),
        "effective_n": estimate.get("effective_n"),
        "evidence_grade": estimate.get("evidence_grade"),
        "expected_return_pct": estimate.get("expected_return_pct"),
        "median_return_pct": estimate.get("median_return_pct"),
        "median_mfe_pct": estimate.get("median_mfe_pct"),
        "median_mae_pct": estimate.get("median_mae_pct"),
        "target_first_rate": estimate.get("target_first_rate"),
        "evidence_manifest_hash": estimate.get("evidence_manifest_hash"),
        "config_hash": estimate.get("config_hash") or prediction.get("config_hash"),
        "feature_schema_version": estimate.get("feature_schema_version")
        or prediction.get("feature_schema_version"),
    }


def _write_csv(headers: list[str], rows: list[dict[str, Any]]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: "" if row.get(key) is None else row.get(key) for key in headers})
    return buffer.getvalue()
