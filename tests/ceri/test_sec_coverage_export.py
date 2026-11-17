from __future__ import annotations

import csv
from copy import deepcopy
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace

import pytest

from app.models.tables import CombinedResult
from app.services.ceri.sec.coverage import build_sec_coverage
from app.services.ceri.sec.readiness_diagnostics import (
    SecTickerReadiness,
    SecTickerReadinessCategory,
    SecUniverseReadiness,
)
from app.services.export_service import (
    COMBINED_HEADERS,
    SEC_COVERAGE_HEADERS,
    export_run_csv,
)

SIGNATURE = "sec-guidance:export-test"
NO_MATCH_REASON = "SEC company-ticker metadata has no exact ticker match."


def _pipeline():
    pipeline = SimpleNamespace(
        id=56,
        upload_run_id=55,
        scope_id="scope-export",
        refresh_cycle_id="cycle-export",
        acquisition_plan_id="plan-export",
        result_json={},
    )
    readiness = SecUniverseReadiness(
        SIGNATURE,
        (
            SecTickerReadiness("GOOD", SecTickerReadinessCategory.READY, ciks=("0000000001",)),
            SecTickerReadiness("NOSEC", SecTickerReadinessCategory.UNRESOLVED_MAPPING),
            SecTickerReadiness(
                "TOWN", SecTickerReadinessCategory.SEC_NOT_APPLICABLE,
                reason="Reviewed FDIC reporting issuer",
            ),
        ),
    )
    coverage = build_sec_coverage(
        pipeline=pipeline,
        readiness=readiness,
        identity_results={"NOSEC": {"status": "UNRESOLVED", "reason": NO_MATCH_REASON}},
        attempted_at=datetime(2026, 11, 9, 22, 5, tzinfo=UTC),
    )
    pipeline.result_json = {
        "sec_coverage": coverage,
        "sec_preflight": {"readiness": readiness.as_dict()},
        "sec_unresolved_count": 1,
        "sec_unresolved_tickers": coverage["gaps"],
    }
    return pipeline


def _run(*pipelines):
    return SimpleNamespace(
        id=55,
        pipeline_runs=list(pipelines),
        fundamental_scores=[],
        technical_scores=[],
        combined_results=[
            CombinedResult(
                run_id=55,
                ticker=ticker,
                company_name=f"{ticker} company",
                final_rank=index + 1,
                final_score=Decimal("8.5"),
                sort_bucket=10,
                combined_decision="Watch",
                notes="retained note",
            )
            for index, ticker in enumerate(("GOOD", "NOSEC", "TOWN"))
        ],
    )


def _csv(run):
    reader = csv.DictReader(StringIO(export_run_csv(run, "combined")))
    return reader.fieldnames, {row["ticker"]: row for row in reader}


def test_combined_csv_preserves_existing_values_and_adds_scoped_sec_dispositions():
    headers, rows = _csv(_run(_pipeline()))

    assert headers[:len(COMBINED_HEADERS)] == COMBINED_HEADERS
    assert set(SEC_COVERAGE_HEADERS) <= set(headers)
    assert rows["GOOD"]["sec_source_status"] == "READY"
    assert rows["GOOD"]["sec_source_reason"] == ""
    assert rows["NOSEC"]["sec_source_status"] == "SEC_IDENTITY_UNRESOLVED"
    assert rows["NOSEC"]["sec_source_reason"] == NO_MATCH_REASON
    assert rows["TOWN"]["sec_source_status"] == "SEC_NOT_APPLICABLE"
    assert rows["TOWN"]["sec_source_reason"] == "Reviewed FDIC reporting issuer"
    for ticker, row in rows.items():
        assert row["run_id"] == "55"
        assert row["company_name"] == f"{ticker} company"
        assert row["final_score"] == "8.5"
        assert row["combined_decision"] == "Watch"
        assert row["notes"] == "retained note"
        assert row["sec_processor_signature"] == SIGNATURE
        assert row["export_schema_id"] == "swinglens.combined.v1"
        assert row["guidance_type"] == "research_hint"
        assert row["execution_instruction"] == "False"


def test_legacy_export_schema_is_identical_when_no_coverage_is_supplied():
    run = _run()
    expected = export_run_csv(run, "combined")
    del run.pipeline_runs

    assert export_run_csv(run, "combined") == expected
    headers, _rows = _csv(run)
    assert not set(SEC_COVERAGE_HEADERS) & set(headers)
    assert headers == COMBINED_HEADERS + [
        "export_schema_id", "export_schema_version", "guidance_type", "execution_instruction"
    ]


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("id", 57),
        ("scope_id", "other-scope"),
        ("refresh_cycle_id", "other-cycle"),
        ("acquisition_plan_id", "other-plan"),
        ("upload_run_id", 99),
    ],
)
def test_wrong_coverage_owner_never_leaks_into_csv(field, changed):
    pipeline = _pipeline()
    setattr(pipeline, field, changed)

    headers, _rows = _csv(_run(pipeline))

    assert not set(SEC_COVERAGE_HEADERS) & set(headers)


def test_tampered_retained_coverage_does_not_add_columns_or_warning():
    pipeline = _pipeline()
    pipeline.result_json["sec_coverage"]["gaps"][0]["reason"] = "untrusted replacement"

    headers, _rows = _csv(_run(pipeline))

    assert not set(SEC_COVERAGE_HEADERS) & set(headers)


@pytest.mark.parametrize("changed", ["signature", "universe"])
def test_mismatched_current_readiness_cannot_project_retained_coverage(changed):
    pipeline = _pipeline()
    readiness = pipeline.result_json["sec_preflight"]["readiness"]
    if changed == "signature":
        readiness["processor_signature"] = "sec-guidance:new"
    else:
        readiness["tickers"].pop()

    headers, _rows = _csv(_run(pipeline))

    assert not set(SEC_COVERAGE_HEADERS) & set(headers)


@pytest.mark.parametrize("newer_has_coverage", [False, True])
def test_latest_pipeline_wins_without_falling_back_to_old_source_state(newer_has_coverage):
    old = _pipeline()
    latest = deepcopy(old)
    latest.id = 57
    if not newer_has_coverage:
        latest.result_json = {}
    # When coverage is present it belongs to pipeline 56, not this newer owner.
    headers, _rows = _csv(_run(latest, old))

    assert not set(SEC_COVERAGE_HEADERS) & set(headers)


def test_current_ready_state_supersedes_historical_unresolved_gap_in_export():
    pipeline = _pipeline()
    raw = pipeline.result_json["sec_preflight"]["readiness"]["tickers"][1]
    raw.update(category="READY", ciks=["0000000002"], reason=None)

    _headers, rows = _csv(_run(pipeline))

    assert rows["NOSEC"]["sec_source_status"] == "READY"
    assert rows["NOSEC"]["sec_source_reason"] == ""
    assert pipeline.result_json["sec_coverage"]["gaps"]  # Historical evidence remains intact.


def test_current_gap_projection_can_export_without_inventing_ready_for_other_tickers():
    pipeline = _pipeline()
    pipeline.result_json.pop("sec_preflight")

    _headers, rows = _csv(_run(pipeline))

    assert rows["NOSEC"]["sec_source_status"] == "SEC_IDENTITY_UNRESOLVED"
    assert rows["GOOD"]["sec_source_status"] == ""
    assert rows["TOWN"]["sec_source_status"] == ""


def test_explicitly_cleared_gap_projection_does_not_resurrect_historical_warning():
    pipeline = _pipeline()
    pipeline.result_json.pop("sec_preflight")
    pipeline.result_json.update(sec_unresolved_count=0, sec_unresolved_tickers=[])

    _headers, rows = _csv(_run(pipeline))

    assert all(row["sec_source_status"] == "" for row in rows.values())
    assert all(row["sec_source_reason"] == "" for row in rows.values())


def test_ticker_outside_retained_scope_gets_no_coverage_attribution():
    run = _run(_pipeline())
    run.combined_results.append(
        CombinedResult(ticker="OUTSIDE", final_score=Decimal("7"), sort_bucket=10)
    )

    _headers, rows = _csv(run)

    assert all(rows["OUTSIDE"][key] == "" for key in SEC_COVERAGE_HEADERS)
