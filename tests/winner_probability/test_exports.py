from __future__ import annotations

import csv
from io import StringIO

from app.services.winner_probability.exports import (
    EXPLORER_CSV_HEADERS,
    RUN_EVIDENCE_CSV_HEADERS,
    export_outcome_explorer_csv,
    export_run_evidence_csv,
    export_run_evidence_json,
)


def test_run_evidence_export_includes_phase_7_audit_headers() -> None:
    csv_text = export_run_evidence_csv(
        {
            "items": [
                {
                    "prediction": {
                        "id": 101,
                        "ticker": "MSFT",
                        "prediction_as_of_date": "2026-07-31",
                        "config_hash": "prediction-config",
                        "feature_schema_version": "owpe-features-1.0.0",
                    },
                    "outcome_definition": {
                        "definition_id": "T2_5_S2_0_H5_NEXT_OPEN",
                        "entry_model": "NEXT_OPEN",
                        "horizon_sessions": 5,
                    },
                    "estimate": {
                        "estimate_kind": "DECISION_TIME",
                        "source_version": "cohort_baseline_v1",
                        "model_key": "cohort-baseline",
                        "model_status": "BASELINE",
                        "model_version_label": "cohort-baseline",
                        "calibration_status": "cohort_baseline",
                        "calibration_calculated_at": None,
                        "training_cutoff_at": "2026-07-31T21:00:00+00:00",
                        "point_probability": 0.61,
                        "lower_bound": 0.52,
                        "upper_bound": 0.7,
                        "interval_width": 0.18,
                        "sample_n": 120,
                        "effective_n": 118.5,
                        "evidence_grade": "High",
                        "expected_return_pct": 1.4,
                        "median_return_pct": 1.1,
                        "median_mfe_pct": 3.2,
                        "median_mae_pct": -1.0,
                        "target_first_rate": 0.64,
                        "evidence_manifest_hash": "manifest-hash",
                        "config_hash": "estimate-config",
                        "feature_schema_version": "owpe-features-1.0.0",
                    },
                }
            ]
        }
    )

    assert csv_text.startswith(",".join(RUN_EVIDENCE_CSV_HEADERS))
    rows = list(csv.DictReader(StringIO(csv_text)))
    assert rows[0]["ticker"] == "MSFT"
    assert rows[0]["estimate_kind"] == "DECISION_TIME"
    assert rows[0]["entry_model"] == "NEXT_OPEN"
    assert rows[0]["horizon_sessions"] == "5"
    assert rows[0]["model_key"] == "cohort-baseline"
    assert rows[0]["model_status"] == "BASELINE"
    assert rows[0]["calibration_status"] == "cohort_baseline"
    assert rows[0]["evidence_manifest_hash"] == "manifest-hash"
    assert rows[0]["guidance_type"] == "research_probability"
    assert rows[0]["execution_instruction"] == "False"
    assert rows[0]["evidence_mode"] == "DECISION_TIME"


def test_run_evidence_json_export_is_stable_and_sorted() -> None:
    json_text = export_run_evidence_json({"run_id": 7, "items": []})

    assert '"items": []' in json_text
    assert '"run_id": 7' in json_text
    assert '"execution_instruction": false' in json_text
    assert '"guidance_type": "research_probability"' in json_text


def test_outcome_explorer_export_flattens_segment_rows() -> None:
    csv_text = export_outcome_explorer_csv(
        {
            "segments": [
                {
                    "segment": "setup_family",
                    "segment_value": "Breakout",
                    "sample_n": 8,
                    "suppressed": True,
                    "mean_probability": None,
                    "mean_lower_bound": None,
                    "evidence_grade_counts": {"Low": 8},
                }
            ]
        }
    )

    assert csv_text.startswith(",".join(EXPLORER_CSV_HEADERS))
    rows = list(csv.DictReader(StringIO(csv_text)))
    assert rows[0]["segment_value"] == "Breakout"
    assert rows[0]["suppressed"] == "True"
    assert rows[0]["evidence_grade_counts"] == '{"Low":8}'
