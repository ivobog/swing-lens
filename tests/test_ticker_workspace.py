from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.exc import SQLAlchemyError
from test_calculation_identity import _identity
from test_ticker_search import CUTOFF, decision, insert, raw
from test_ticker_search import search_db as search_db

from app.db import get_db
from app.main import create_app
from app.models.ceri_tables import CeriCompany
from app.models.tables import CoreCalculationEvidence, PipelineRun
from app.routers import ticker_routes
from app.services import chart_data_service
from app.services.calculation_identity import (
    CalculationOwnership,
    CalculationSubject,
    IdentityDimension,
)
from app.services.score_card_view_service import build_score_cards
from app.services.ticker_search_service import ticker_summary
from app.services.ticker_workspace_service import ticker_workspace
from app.settings import Settings


def workspace_decision(db, run_id, *, cutoff=CUTOFF, status="COMPLETED", complete=True, missing=()):
    if db.get(CoreCalculationEvidence, run_id) is None:
        decision(db, run_id, cutoff=cutoff, status=status, complete=complete)
    base = _identity(cutoff=cutoff)
    identity = replace(
        base,
        ownership=CalculationOwnership(
            IdentityDimension.known(run_id), IdentityDimension.known(run_id)
        ),
        subject=CalculationSubject(
            IdentityDimension.known("EOG"), IdentityDimension.not_applicable()
        ),
        temporal=replace(base.temporal, as_of_session=IdentityDimension.known(cutoff.date())),
    ).canonical_payload()
    combined = db.execute(
        select(CoreCalculationEvidence.payload_json).where(
            CoreCalculationEvidence.id == run_id,
        )
    ).scalar_one()
    combined.update(
        company_name="EOG Resources",
        ticker="EOG",
        run_id=run_id,
        fundamental_label="Clean compounder",
        technical_classification="Filtered momentum",
        upcoming_earnings_date="2026-11-05",
        days_until_earnings=17,
        earnings_risk_level="clear",
        notes="Persisted historical decision",
    )
    source_ids = {"fundamental": run_id * 100 + 1, "technical": run_id * 100 + 2}
    payloads = {
        "fundamental": dict(
            fundamental_score="9.11",
            growth_quality_score="0",
            data_coverage_score="9.87",
            scoring_model_version="historical-model",
            debug_json={
                "coverage": {"missing_core_fields": [], "missing_high_fields": ["forward_pe"]}
            },
            v2_warning_flags_json={"flags": ["sparse_fundamental_data"]},
        ),
        "technical": dict(
            dual_score="6.83",
            trend_score=str(run_id),
            suggested_stop=str(run_id * 10 + 1),
            suggested_target=str(run_id * 10 + 12),
            reward_risk="2.29",
            entry_risk_pct="0",
            technical_confidence="high",
            action_bias="Momentum",
            stage="Stage 2",
            technical_engine_version="4.0.0",
            v4_debug_json={"engine_version": "4.0.0", "sub_tags": ["Tight closes"]},
            warning_flags_json=["market_risk_off"],
        ),
    }
    for role, payload in payloads.items():
        if role in missing:
            continue
        if db.get(CoreCalculationEvidence, source_ids[role]) is not None:
            continue
        insert(
            db,
            CoreCalculationEvidence,
            id=source_ids[role],
            artifact_kind=role.upper(),
            run_id=run_id,
            ticker="EOG",
            calculation_identity_json=identity,
            calculation_identity_fingerprint=f"source-{run_id}-{role}",
            payload_json={**payload, "run_id": run_id, "ticker": "EOG"},
            payload_fingerprint="payload",
            evidence_key=f"source-{run_id}-{role}",
            calculated_at=cutoff,
        )
    db.execute(
        CoreCalculationEvidence.__table__.update()
        .where(
            CoreCalculationEvidence.id == run_id,
        )
        .values(
            payload_json=combined,
            calculation_identity_json=identity,
            source_evidence_ids_json=source_ids,
        )
    )


@pytest.fixture
def workspace_client(search_db):
    app = create_app(
        Settings(
            _env_file=None,
            job_worker_enabled=False,
            db_monitor_enabled=False,
            observability_metrics_enabled=False,
        )
    )
    app.dependency_overrides[get_db] = lambda: search_db
    return TestClient(app)


def test_workspace_defaults_and_explicit_runs_reuse_preview_authority(search_db, workspace_client):
    db = search_db
    workspace_decision(db, 100, cutoff=CUTOFF - timedelta(days=3))
    workspace_decision(db, 7)
    workspace_decision(db, 8, cutoff=CUTOFF + timedelta(days=1), status="FAILED")
    workspace_decision(db, 9, cutoff=CUTOFF + timedelta(days=2), complete=False)
    decision(db, 10, cutoff=CUTOFF + timedelta(days=3), projected=False)
    decision(db, 11, cutoff=CUTOFF + timedelta(days=4))
    db.execute(
        PipelineRun.__table__.update()
        .where(PipelineRun.id == 11)
        .values(
            execution_authority_state="QUARANTINED",
        )
    )
    default = ticker_workspace(db, "eog")
    assert default["selected_run_id"] == ticker_summary(db, "EOG")["decision"]["run_id"] == 7
    assert [r["run_id"] for r in default["runs"]] == [7, 100]
    assert default["combined"].final_score == Decimal("7")  # mutable compatibility row is 999
    assert default["technical"].suggested_stop == Decimal("71")
    assert default["fundamental"].scoring_model_version == "historical-model"
    old = ticker_workspace(db, "EOG", run_id=100)
    assert old["combined"].final_score == Decimal("100")
    assert old["technical"].suggested_stop == Decimal("1001")
    html = workspace_client.get("/tickers/EOG?run_id=100")
    assert html.status_code == 200
    assert "Current cached prices; research levels from Run 100." in html.text
    assert 'value="100" selected' in html.text
    assert "Run 8 ·" not in html.text and "Run 9 ·" not in html.text
    assert "Run 10 ·" not in html.text and "Run 11 ·" not in html.text
    assert workspace_client.get("/tickers/EOG?run_id=8").status_code == 404


def test_every_score_card_field_and_valid_zero_is_preserved(search_db, workspace_client):
    workspace_decision(search_db, 7)
    context = ticker_workspace(search_db, "EOG")
    expected = build_score_cards(
        context["raw_row"], context["fundamental"], context["technical"], context["combined"]
    )
    assert context["score_cards"] == expected
    html = workspace_client.get("/tickers/EOG").text
    for card in expected:
        assert card["title"] in html
        for item in card["items"]:
            assert f">{item['label']}</dt>" in html
            assert item["value"] in html
    assert "0.00" in html
    assert "forward_pe" in html
    assert html.count("data-workspace-section=") == 10
    assert 'data-workspace-section="chart"' in html
    assert 'data-workspace-section="1" open' in html
    assert 'data-workspace-section="2" open' not in html
    assert "Research only." in html
    assert "Certified evidence #7" in html


@pytest.mark.parametrize("run", ["-1", "bad", "999"])
def test_invalid_or_unavailable_requested_run_never_falls_back(search_db, workspace_client, run):
    workspace_decision(search_db, 7)
    response = workspace_client.get(f"/tickers/EOG?run_id={run}")
    assert response.status_code == (404 if run == "999" else 422)
    if run == "999":
        assert "Run 999 is unavailable" in response.text
        assert "data-chart-url=" not in response.text


def test_unknown_unscored_ambiguous_and_wrong_qualifier_states(search_db, workspace_client):
    assert workspace_client.get("/tickers/UNKNOWN").status_code == 404
    raw(search_db, "OLD", "Old upload")
    known = workspace_client.get("/tickers/OLD")
    assert known.status_code == 200 and "No eligible decision available" in known.text
    assert workspace_client.get("/tickers/OLD?identity=ceri:999").status_code == 404
    assert workspace_client.get("/tickers/OLD?identity=bad").status_code == 422
    insert(search_db, CeriCompany, id=1, ticker="DUAL", exchange="US", company_name="Dual US")
    insert(search_db, CeriCompany, id=2, ticker="DUAL", exchange="LSE", company_name="Dual UK")
    ambiguous = workspace_client.get("/tickers/DUAL")
    assert ambiguous.status_code == 409 and "Choose a ticker identity" in ambiguous.text
    qualified = workspace_client.get("/tickers/DUAL?identity=ceri:2")
    assert qualified.status_code == 200 and "Decision identity unavailable" in qualified.text
    assert "Dual UK" in qualified.text


def test_chart_uses_pinned_levels_and_guards_page_evidence(
    search_db, workspace_client, monkeypatch
):
    workspace_decision(search_db, 6, cutoff=CUTOFF - timedelta(days=3))
    workspace_decision(search_db, 7)
    frame = pd.DataFrame(
        [dict(date="2026-10-19", open=100, high=110, low=90, close=105, volume=1000)]
    )
    monkeypatch.setattr(
        chart_data_service, "load_preferred_ohlcv_frames", lambda *_: (frame, frame)
    )
    for run_id, stop in [(6, 61), (7, 71)]:
        response = workspace_client.get(
            f"/api/tickers/EOG/chart-data?run_id={run_id}&evidence_id={run_id}"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["levels"]["stop"] == stop
        assert payload["provenance"]["run_id"] == run_id
        assert payload["provenance"]["prices"] == "current_cached"
    assert (
        workspace_client.get("/api/tickers/EOG/chart-data?run_id=7&evidence_id=999").status_code
        == 409
    )
    assert workspace_client.get("/api/tickers/EOG/chart-data?run_id=999").status_code == 404
    empty = pd.DataFrame()
    monkeypatch.setattr(chart_data_service, "load_preferred_ohlcv_frames", lambda *_: (empty, None))
    payload = workspace_client.get("/api/tickers/EOG/chart-data?run_id=7").json()
    assert payload["bars"] == [] and "No cached chart data" in payload["message"]
    assert workspace_client.get("/tickers/EOG?run_id=7").status_code == 200


def test_missing_or_incompatible_sources_never_fall_back_to_other_runs(
    search_db,
    workspace_client,
    monkeypatch,
):
    workspace_decision(search_db, 6, cutoff=CUTOFF - timedelta(days=3))
    workspace_decision(search_db, 7, missing=("fundamental",))
    source = search_db.execute(
        select(CoreCalculationEvidence.calculation_identity_json).where(
            CoreCalculationEvidence.id == 702,
        )
    ).scalar_one()
    source["ownership"]["pipeline_id"]["value"] = 6
    search_db.execute(
        CoreCalculationEvidence.__table__.update()
        .where(
            CoreCalculationEvidence.id == 702,
        )
        .values(calculation_identity_json=source)
    )
    context = ticker_workspace(search_db, "EOG", run_id=7)
    assert context["fundamental"] is None and context["technical"] is None
    assert context["combined"].final_score == 7
    html = workspace_client.get("/tickers/EOG?run_id=7").text
    assert "Pinned evidence unavailable for this decision." in html
    assert "Research levels are unavailable" in html
    frame = pd.DataFrame(
        [dict(date="2026-10-19", open=100, high=110, low=90, close=105, volume=1000)]
    )
    monkeypatch.setattr(
        chart_data_service, "load_preferred_ohlcv_frames", lambda *_: (frame, frame)
    )
    chart = workspace_client.get("/api/tickers/EOG/chart-data?run_id=7")
    assert chart.status_code == 200
    assert chart.json()["levels"] == {"entry_reference": 105.0}


def test_workspace_and_chart_are_read_only(search_db, workspace_client, monkeypatch):
    workspace_decision(search_db, 7)
    search_db.commit()
    search_db.execute(text("SET TRANSACTION READ ONLY"))
    statements = []
    event.listen(
        search_db.get_bind(), "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    monkeypatch.setattr(
        chart_data_service, "load_preferred_ohlcv_frames", lambda *_: (pd.DataFrame(), None)
    )
    assert workspace_client.get("/tickers/EOG").status_code == 200
    assert workspace_client.get("/api/tickers/EOG/chart-data?run_id=7").status_code == 200
    assert all(sql.lstrip().startswith(("SELECT", "WITH")) for sql in statements)
    assert not search_db.new and not search_db.dirty and not search_db.deleted
    assert not any("background_jobs" in sql for sql in statements)


def test_query_failure_renders_honest_error(workspace_client, monkeypatch):
    def fail(*_args):
        raise SQLAlchemyError("private database diagnostic")

    monkeypatch.setattr(ticker_routes, "ticker_workspace", fail)
    response = workspace_client.get("/tickers/EOG")
    assert response.status_code == 503
    assert "Decision data could not load" in response.text
    assert "private database diagnostic" not in response.text


def test_summary_destination_preserves_selected_identity_and_run(search_db):
    workspace_decision(search_db, 7)
    summary = ticker_summary(search_db, "EOG")
    url = urlsplit(summary["workspace_url"])
    assert url.path == "/tickers/EOG"
    assert parse_qs(url.query) == {"identity": [summary["identity"]["identity"]], "run_id": ["7"]}
