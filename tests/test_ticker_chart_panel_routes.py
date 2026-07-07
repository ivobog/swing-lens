from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.models.tables import CombinedResult, FundamentalScore, RawCompanyRow, UploadRun
from app.routers import run_routes


def test_ticker_chart_panel_renders_context(monkeypatch) -> None:
    captured = {}
    run = _run()

    def fake_template_response(request, template_name, context):
        captured["request"] = request
        captured["template_name"] = template_name
        captured["context"] = context
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(run_routes, "_load_run", lambda db, run_id: run)
    monkeypatch.setattr(run_routes.templates, "TemplateResponse", fake_template_response)

    response = run_routes.ticker_chart_panel(
        run_id=7,
        ticker="msft",
        request=SimpleNamespace(),
        db=SimpleNamespace(),
    )

    context = captured["context"]
    assert response.status_code == 200
    assert captured["template_name"] == "ticker_chart_panel.html"
    assert context["active_nav"] == "runs"
    assert context["run"] is run
    assert context["ticker"] == "MSFT"
    assert context["company_name"] == "Microsoft Corporation"
    assert context["sector"] == "Technology"
    assert context["chart_data_url"] == "/api/runs/7/tickers/MSFT/chart-data"
    assert context["back_url"] == "/runs/7"
    assert [card["title"] for card in context["score_cards"]] == [
        "Combined Decision",
        "Fundamentals",
        "Technicals",
        "Risk Context",
        "Warnings and Missing Data",
    ]


def test_ticker_chart_panel_returns_404_for_unknown_ticker(monkeypatch) -> None:
    monkeypatch.setattr(run_routes, "_load_run", lambda db, run_id: _run())

    with pytest.raises(HTTPException) as exc_info:
        run_routes.ticker_chart_panel(
            run_id=7,
            ticker="NVDA",
            request=SimpleNamespace(),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Ticker not found in this run."


def test_ticker_chart_panel_returns_404_for_unknown_run(monkeypatch) -> None:
    monkeypatch.setattr(run_routes, "_load_run", lambda db, run_id: None)

    with pytest.raises(HTTPException) as exc_info:
        run_routes.ticker_chart_panel(
            run_id=999,
            ticker="MSFT",
            request=SimpleNamespace(),
            db=SimpleNamespace(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Run not found"


def test_ticker_chart_data_validates_ticker_and_returns_payload(monkeypatch) -> None:
    calls = {}
    payload = {"ticker": "MSFT", "bars": [{"time": "2026-01-01", "close": 10.0}]}

    def fake_build_ticker_chart_payload(db, run_id, ticker):
        calls["db"] = db
        calls["run_id"] = run_id
        calls["ticker"] = ticker
        return payload

    db = SimpleNamespace()
    monkeypatch.setattr(run_routes, "_load_run", lambda db, run_id: _run())
    monkeypatch.setattr(
        run_routes,
        "build_ticker_chart_payload",
        fake_build_ticker_chart_payload,
    )

    result = run_routes.ticker_chart_data(run_id=7, ticker="msft", db=db)

    assert result is payload
    assert calls == {"db": db, "run_id": 7, "ticker": "MSFT"}


def _run() -> UploadRun:
    run = UploadRun(id=7, filename="sample.csv", row_count=1, status="COMPLETED")
    run.raw_company_rows = [
        RawCompanyRow(
            run_id=7,
            row_number=1,
            ticker="MSFT",
            company_name="Microsoft Corporation",
            sector="Technology",
            raw_json={"Symbol": "MSFT"},
        )
    ]
    run.fundamental_scores = [
        FundamentalScore(
            run_id=7,
            ticker="MSFT",
            fundamental_score=Decimal("7.4"),
        )
    ]
    run.technical_scores = []
    run.combined_results = [
        CombinedResult(
            run_id=7,
            ticker="MSFT",
            company_name="Microsoft Corporation",
            sector="Technology",
            final_rank=1,
            final_score=Decimal("8.2"),
            combined_decision="Strong candidate",
            earnings_warning_flags_json=[],
            warning_flags_json=[],
            is_complete=True,
        )
    ]
    return run
