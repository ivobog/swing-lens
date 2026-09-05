from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models.tables import UploadRun
from app.routers.winner_probability_routes import _run_evidence_summary
from app.templates import templates


def test_run_detail_template_links_to_winner_evidence(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    run = UploadRun(id=7, filename="sample.csv", row_count=1, status="COMPLETED")

    html = templates.get_template("run_detail.html").render(
        run=run,
        winner_probability_context={
            "prediction_count": 1,
            "estimate_count": 1,
            "insufficient_count": 0,
            "run_url": "/runs/7/winner-probability",
            "operations_url": "/winner-probability/operations",
            "prediction_by_ticker": {"MSFT": 101},
        },
        combined_results=[],
    )

    assert "Winner Evidence" in html
    assert "Existing ranks stay unchanged." in html
    assert 'href="/runs/7/winner-probability"' in html
    assert 'href="/api/winner-probability/run/7/export.csv"' in html


def test_run_page_renders_required_evidence_fields(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("winner_probability_run.html").render(
        run=UploadRun(id=7, filename="sample.csv", status="COMPLETED"),
        payload=_run_payload(),
        summary={
            "row_count": 1,
            "estimate_count": 1,
            "calibrated_count": 1,
            "insufficient_count": 0,
        },
        filters=_filters(),
        ui_error=None,
    )

    assert "Winner Evidence - Run 7" in html
    assert "target-before-stop label" in html
    assert "67%" in html
    assert "55-78%" in html
    assert "High" in html
    assert "cohort-baseline" in html
    assert "cohort_baseline" in html
    assert "120" in html
    assert "native 15 / pre-1.1 replay 105" in html
    assert "2.1" in html
    assert "3.7 / -1.2" in html
    assert "64%" in html
    assert "DECISION_TIME" in html
    assert 'href="/winner-probability/predictions/101"' in html


def test_run_page_renders_insufficient_raw_evidence_reason(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    payload = _run_payload()
    payload["items"][0]["estimate"] = {
        **payload["items"][0]["estimate"],
        "point_probability": None,
        "lower_bound": None,
        "upper_bound": None,
        "evidence_grade": "Insufficient",
        "sample_n": 3,
        "effective_n": 3.0,
        "insufficient_reasons": ["no_eligible_cohort"],
    }

    html = templates.get_template("winner_probability_run.html").render(
        run=UploadRun(id=7, filename="sample.csv", status="COMPLETED"),
        payload=payload,
        summary={
            "row_count": 1,
            "estimate_count": 1,
            "calibrated_count": 0,
            "insufficient_count": 1,
        },
        filters=_filters(),
        ui_error=None,
    )

    assert "Insufficient" in html
    assert "Withheld" in html
    assert "no_eligible_cohort" in html
    assert "3" in html


def test_run_summary_uses_full_counts_instead_of_page_length() -> None:
    payload = _run_payload()
    payload["counts"] = {
        "run_total": 186,
        "filtered_total": 186,
        "estimate_total": 184,
        "calibrated_total": 0,
        "insufficient_total": 184,
        "missing_estimate_total": 2,
    }

    summary = _run_evidence_summary(payload)

    assert summary == {
        "row_count": 1,
        "filtered_count": 186,
        "run_count": 186,
        "estimate_count": 184,
        "calibrated_count": 0,
        "insufficient_count": 184,
        "missing_estimate_count": 2,
    }


def test_ticker_evidence_page_separates_label_entry_model_and_estimate_views(
    monkeypatch,
) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    payload = _prediction_payload()

    html = templates.get_template("winner_probability_ticker.html").render(
        payload=payload,
        reproduction={
            "matches": True,
            "mismatches": [],
            "evidence_manifest_hash": "manifest-hash",
            "sample_n": 120,
        },
        estimate_view="DECISION_TIME",
        as_of_date="",
    )

    assert "target-before-stop probability, not generic positive-return probability" in html
    assert "NEXT_OPEN" in html
    assert "SIGNAL_CLOSE_DIAGNOSTIC" not in html
    assert "Decision-Time Estimate" in html
    assert "Latest Re-score" in html
    assert "cohort-baseline" in html
    assert "cohort_baseline" in html
    assert "Audit JSON" in html
    assert "Matches" in html


def test_ticker_evidence_page_can_show_diagnostic_entry_model(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)
    payload = _prediction_payload()
    payload["outcome_definition"] = {
        **payload["outcome_definition"],
        "definition_id": "T2_5_S2_0_H5_SIGNAL_CLOSE_DIAGNOSTIC",
        "entry_model": "SIGNAL_CLOSE_DIAGNOSTIC",
    }

    html = templates.get_template("winner_probability_ticker.html").render(
        payload=payload,
        reproduction=None,
        estimate_view="DECISION_TIME",
        as_of_date="",
    )

    assert "SIGNAL_CLOSE_DIAGNOSTIC" in html
    assert "target-before-stop probability" in html


def test_outcome_explorer_marks_low_sample_segments(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("winner_probability_outcomes.html").render(
        payload={
            "segments": [
                {
                    "segment": "setup_family",
                    "segment_value": "Breakout",
                    "sample_n": 4,
                    "suppressed": True,
                    "mean_probability": None,
                    "mean_lower_bound": None,
                    "evidence_grade_counts": {"Low": 4},
                }
            ]
        },
        segment_by="setup_family",
        min_sample=10,
        estimate_view="DECISION_TIME",
        ui_error=None,
    )

    assert "Outcome Explorer" in html
    assert "Low sample" in html
    assert "Suppressed" in html
    assert "Breakout" in html
    assert "/api/winner-probability/outcomes/explorer/export.csv" in html


def test_operations_page_exposes_overdue_pending_and_failed_jobs(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("winner_probability_operations.html").render(
        status={
            "pending_outcomes": 12,
            "overdue_pending_outcomes": 2,
            "failed_processing_runs": 1,
            "recent_processing_runs": [
                {
                    "id": 9,
                    "process_type": "WINNER_OUTCOME_MATURATION",
                    "status": "FAILED",
                    "run_id": None,
                    "started_at": "2026-07-31T21:00:00+00:00",
                    "completed_at": "2026-07-31T21:01:00+00:00",
                    "counts": {"failed": 1},
                    "error_message": "market data incomplete",
                }
            ],
        },
        admin_enabled=True,
    )

    assert "Winner Probability Operations" in html
    assert "Overdue" in html
    assert "2" in html
    assert "FAILED" in html
    assert "market data incomplete" in html
    assert "Process due outcomes" in html
    assert "Refresh cohorts" in html
    assert "data-winner-json-form" in html
    assert "data-winner-form-output" in html
    assert "<caption>Recent winner probability processing runs and errors.</caption>" in html


def test_operations_page_disables_maturation_button_for_active_workflow(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("winner_probability_operations.html").render(
        status={
            "pending_outcomes": 4227,
            "overdue_pending_outcomes": 4227,
            "failed_processing_runs": 0,
            "maturation_queue": {
                "due_total": 4227,
                "retry_eligible_now": 0,
                "retry_deferred": 4227,
                "earliest_retry_not_before": "2026-09-05T13:15:00+02:00",
            },
            "active_maturation_workflow": {
                "job_id": 10001,
                "status": "QUEUED",
                "workflow_key": "winner:h5-next-open:maturation",
                "root_job_id": 9978,
                "trigger_source": "MANUAL",
            },
            "recent_processing_runs": [],
        },
        admin_enabled=True,
    )

    assert "H5 Calendar Due" in html
    assert "Retry Eligible Now" in html
    assert "Retry Deferred" in html
    assert "winner:h5-next-open:maturation" in html
    assert 'disabled aria-disabled="true"' in html
    assert "2026-09-05T13:15:00+02:00" in html


def test_model_health_page_renders_calibration_and_drift(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("winner_probability_models.html").render(
        models=[
            {
                "id": 1,
                "model_key": "cohort-baseline",
                "artifact_hash": "hash",
                "status": "ACTIVE",
                "algorithm": "cohort",
                "entry_model": "NEXT_OPEN",
                "training_cutoff_at": "2026-07-31T21:00:00+00:00",
                "metrics": {
                    "brier_score": 0.18,
                    "log_loss": 0.54,
                    "ece": 0.04,
                },
            }
        ],
        selected_model_id=1,
        calibration={
            "bins": [
                {
                    "bin_floor": 0.6,
                    "bin_ceiling": 0.7,
                    "sample_n": 80,
                    "mean_prediction": 0.65,
                    "observed_rate": 0.66,
                    "error": 0.01,
                }
            ]
        },
        drift={
            "metrics": [
                {
                    "as_of_date": "2026-07-31",
                    "metric_name": "brier_score_delta",
                    "metric_value": 0.02,
                    "threshold_value": 0.05,
                    "breached": False,
                    "sufficient_sample": True,
                }
            ]
        },
        admin_enabled=True,
    )

    assert "Model Health" in html
    assert "cohort-baseline" in html
    assert "0.1800" in html
    assert "65%" in html
    assert "66%" in html
    assert "brier_score_delta" in html
    assert "Retire" in html


def test_nav_includes_winner_evidence(monkeypatch) -> None:
    monkeypatch.setitem(templates.env.globals, "url_for", lambda _name, path: path)

    html = templates.get_template("partials/_nav.html").render(active_nav="winner-probability")

    assert 'href="/winner-probability/operations"' in html
    assert "Winner Evidence" in html
    assert "active" in html


def _filters() -> dict[str, object]:
    return {
        "outcome_definition_id": "",
        "estimate_view": "DECISION_TIME",
        "sort": "lower_bound",
        "direction": "desc",
        "probability_min": None,
        "lower_bound_min": None,
        "interval_width_max": None,
        "evidence_grade": "",
        "effective_sample_size_min": None,
        "median_return_min": None,
        "mfe_min": None,
        "mae_max": None,
        "target_first_rate_min": None,
        "earnings_risk": "",
        "market_risk_state": "",
        "sector_state": "",
        "data_quality": "",
    }


def _run_payload() -> dict[str, object]:
    return {
        "run_id": 7,
        "estimate_view": "DECISION_TIME",
        "outcome_definition": {
            "definition_id": "T2_5_S2_0_H5_NEXT_OPEN",
            "label": "+2.5% target before -2.0% stop within 5 sessions",
            "entry_model": "NEXT_OPEN",
            "horizon_sessions": 5,
        },
        "items": [
            {
                "prediction": {
                    "id": 101,
                    "ticker": "MSFT",
                    "setup_classification": "Clean bull pullback",
                    "setup_family": "Pullback",
                },
                "estimate": {
                    "id": 201,
                    "estimate_kind": "DECISION_TIME",
                    "point_probability": 0.67,
                    "lower_bound": 0.55,
                    "upper_bound": 0.78,
                    "interval_width": 0.23,
                    "evidence_grade": "High",
                    "model_key": "cohort-baseline",
                    "model_status": "BASELINE",
                    "model_version_label": "cohort-baseline",
                    "calibration_status": "cohort_baseline",
                    "calibration_calculated_at": None,
                    "sample_n": 120,
                    "effective_n": 118.0,
                    "evidence_composition": {
                        "native_1_1_n": 15,
                        "pre11_compatible_n": 105,
                    },
                    "median_return_pct": 2.1,
                    "median_mfe_pct": 3.7,
                    "median_mae_pct": -1.2,
                    "target_first_rate": 0.64,
                    "training_cutoff_at": "2026-07-31T21:00:00+00:00",
                    "insufficient_reasons": [],
                },
                "outcome_definition": {
                    "definition_id": "T2_5_S2_0_H5_NEXT_OPEN",
                    "entry_model": "NEXT_OPEN",
                    "horizon_sessions": 5,
                },
            }
        ],
    }


def _prediction_payload() -> dict[str, object]:
    estimate = {
        "id": 201,
        "estimate_kind": "DECISION_TIME",
        "source": "COHORT",
        "source_version": "cohort_baseline_v1",
        "point_probability": 0.67,
        "lower_bound": 0.55,
        "upper_bound": 0.78,
        "evidence_grade": "High",
        "model_key": "cohort-baseline",
        "model_status": "BASELINE",
        "model_version_label": "cohort-baseline",
        "calibration_status": "cohort_baseline",
        "calibration_calculated_at": None,
        "sample_n": 120,
        "effective_n": 118.0,
        "median_return_pct": 2.1,
        "median_mfe_pct": 3.7,
        "median_mae_pct": -1.2,
        "target_first_rate": 0.64,
        "training_cutoff_at": "2026-07-31T21:00:00+00:00",
        "evidence_manifest_hash": "manifest-hash",
        "insufficient_reasons": [],
        "metadata": {"cohort_level": "L3", "cohort_key": "setup=Pullback"},
    }
    return {
        "prediction": {
            "id": 101,
            "run_id": 7,
            "ticker": "MSFT",
            "captured_at": "2026-07-31T21:00:00+00:00",
            "source_data_cutoff_at": "2026-07-31T21:00:00+00:00",
            "setup_classification": "Clean bull pullback",
            "setup_family": "Pullback",
            "ranking_profile": "momentum_swing",
            "market_regime": "Confirmed Uptrend",
            "market_risk_state": "Green",
            "sector_state": "Leading",
            "sector_rank": 1,
            "earnings_risk_level": "low",
            "technical_data_quality": "ok",
            "feature_vector_hash": "feature-hash",
            "config_hash": "config-hash",
            "feature_schema_version": "owpe-features-1.0.0",
            "eligibility_status": "ELIGIBLE",
            "source_ids": {"ranking_result_id": 51},
            "feature_json": {"ticker": "MSFT"},
        },
        "outcome_definition": {
            "definition_id": "T2_5_S2_0_H5_NEXT_OPEN",
            "label": "+2.5% target before -2.0% stop within 5 sessions",
            "entry_model": "NEXT_OPEN",
            "horizon_sessions": 5,
        },
        "decision_time_estimate": estimate,
        "latest_rescore": {**estimate, "id": 202, "estimate_kind": "LATEST_RESCORE"},
        "selected_estimate": estimate,
        "forward_outcomes": [
            {
                "status": "MATURED",
                "entry_session": date(2026, 8, 3),
                "due_session": date(2026, 8, 7),
                "close_return_pct": Decimal("2.5"),
                "mfe_pct": Decimal("4.0"),
                "mae_pct": Decimal("-1.0"),
                "positive_return": True,
                "revision": 1,
            }
        ],
        "target_stop_outcomes": [
            {
                "status": "MATURED",
                "entry_model": "NEXT_OPEN",
                "first_event": "TARGET_FIRST",
                "evaluated_at": "2026-08-07T21:00:00+00:00",
                "primary_winner": True,
                "revision": 1,
            }
        ],
        "warnings": [],
        "exclusions": [],
    }
