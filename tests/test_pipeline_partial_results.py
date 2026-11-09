from datetime import date
from types import SimpleNamespace

import pytest

from app.services import pipeline_partial_results as partial
from app.services.market_data_session_readiness import SessionReadinessResult
from app.services.pipeline_outcome import derive_pipeline_outcome


def _source(monkeypatch, disposition="REQUIRED_DATA_UNAVAILABLE"):
    source = SimpleNamespace(
        id=53, upload_run_id=51, status="BLOCKED", current_step="FETCHING_MARKET_DATA"
    )
    rows = (
        SimpleNamespace(
            ticker="MSFT", disposition="READY", technical_eligible=True, downstream_eligible=True
        ),
        SimpleNamespace(
            ticker="ROKU",
            disposition=disposition,
            technical_eligible=False,
            downstream_eligible=False,
            reason_code="MARKET_DATA_PROVIDER_REJECTED",
            expected_session=date(2026, 11, 6),
            latest_bar_session=date(2026, 10, 19),
            id=2745,
        ),
    )
    monkeypatch.setattr(
        partial,
        "load_current_session_readiness",
        lambda *_a, **_kw: SessionReadinessResult(rows=rows),
    )
    monkeypatch.setattr(partial, "pipeline_scope_tickers", lambda *_a: ("MSFT", "ROKU"))
    monkeypatch.setattr(
        partial,
        "require_semantic_authority",
        lambda *_a: SimpleNamespace(scope_id="original-scope"),
    )
    return SimpleNamespace(scalar=lambda _q: source), source


def test_partial_admission_records_exact_subset_and_preserves_source(monkeypatch):
    db, source = _source(monkeypatch)
    scope = partial.prepare_partial_scope(
        db, source_pipeline_id=53, upload_run_id=51, excluded_tickers=("ROKU",)
    )
    assert scope["included_tickers"] == ["MSFT"]
    assert scope["excluded_tickers"] == ["ROKU"]
    assert scope["requested_tickers"] == ["MSFT", "ROKU"]
    assert scope["source_scope_id"] == "original-scope"
    assert scope["exclusions"]["ROKU"]["source_disposition_id"] == 2745
    assert source.status == "BLOCKED"


@pytest.mark.parametrize("excluded", [("MSFT",), (), ("ROKU", "MSFT")])
def test_partial_admission_rejects_unreviewed_scope_changes(monkeypatch, excluded):
    db, _ = _source(monkeypatch)
    with pytest.raises(ValueError, match="PARTIAL_EXCLUSIONS_CHANGED"):
        partial.prepare_partial_scope(
            db, source_pipeline_id=53, upload_run_id=51, excluded_tickers=excluded
        )


def test_partial_admission_cannot_bypass_unresolved_instrument_identity(monkeypatch):
    db, _ = _source(monkeypatch, disposition="UNKNOWN")
    with pytest.raises(ValueError, match="RESOLVED_DATA_FAILURE"):
        partial.prepare_partial_scope(
            db, source_pipeline_id=53, upload_run_id=51, excluded_tickers=("ROKU",)
        )


def test_partial_outcome_exposes_original_coverage_gap():
    outcome = derive_pipeline_outcome(
        "PARTIAL",
        {
            "uploaded_rows": 131,
            "combined_results": 130,
            "market_data_excluded_count": 1,
            "partial_scope": {"excluded_tickers": ["ROKU"]},
        },
    )
    assert outcome["execution_outcome"] == "SUCCEEDED_WITH_GAPS"
    assert outcome["coverage"]["expected_rows"] == 131
    assert outcome["coverage"]["combined_rows"] == 130
    assert outcome["coverage"]["excluded_tickers"] == ["ROKU"]
    assert outcome["coverage"]["gaps"]["market_data_excluded_count"] == 1


def test_successful_partial_cohort_keeps_its_ceri_evidence():
    from app.services.ceri.pipeline_containment_service import reconcile_unsuccessful_pipeline_ceri

    result = {"partial_scope": {"excluded_tickers": ["ROKU"]}, "ceri_failed": 0}
    result["pipeline_outcome_summary"] = derive_pipeline_outcome("PARTIAL", result)
    pipeline = SimpleNamespace(status="PARTIAL", result_json=result)
    assert reconcile_unsuccessful_pipeline_ceri(object(), pipeline) == {
        "excluded": 0, "projections_repaired": 0, "processing_terminalized": 0
    }
