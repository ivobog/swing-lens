import unicodedata
from typing import Annotated, Literal
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.db import get_db
from app.services.chart_data_service import build_ticker_chart_payload
from app.services.ticker_intelligence_service import ticker_intelligence
from app.services.ticker_search_service import search_tickers, ticker_summary
from app.services.ticker_workspace_service import ticker_workspace
from app.templates import templates

router = APIRouter(tags=["tickers"])
DbSession = Annotated[Session, Depends(get_db)]
TickerIdentity = Annotated[
    str | None, Query(pattern=r"^(ceri|raw|contract):[0-9]+$", max_length=40)
]


def _validate_text(value: str):
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise HTTPException(422, "Control characters are not allowed.")
    return value.strip()


@router.get("/api/tickers/search")
def search(
    db: DbSession,
    q: Annotated[str, Query(max_length=100)] = "",
    limit: Annotated[int, Query(ge=1, le=50)] = 15,
):
    return {"results": search_tickers(db, _validate_text(q), limit)}


@router.get("/api/tickers/{ticker}/summary")
def summary(
    ticker: str,
    db: DbSession,
    identity: TickerIdentity = None,
):
    ticker = _validate_ticker(ticker)
    result = ticker_summary(db, ticker, identity)
    if result["state"] == "unknown_ticker":
        raise HTTPException(404, result["state"])
    return result


def _validate_ticker(ticker):
    ticker = _validate_text(ticker)
    if not ticker or len(ticker) > 32:
        raise HTTPException(422, "Ticker must contain 1–32 characters.")
    return ticker


@router.get("/tickers/{ticker}", response_class=HTMLResponse)
def workspace(
    ticker: str,
    request: Request,
    db: DbSession,
    identity: TickerIdentity = None,
    run_id: Annotated[int | None, Query(ge=1)] = None,
):
    ticker = _validate_ticker(ticker)
    try:
        context = ticker_workspace(db, ticker, identity, run_id)
    except SQLAlchemyError:
        context = {
            "state": "request_error",
            "ticker": ticker,
            "runs": [],
            "selected_run_id": run_id,
        }
    context["active_nav"] = ""
    if context["state"] == "available":
        params = urlencode(
            {
                "identity": context["identity"]["identity"],
                "run_id": context["selected_run_id"],
                "evidence_id": context["evidence"].id,
            }
        )
        context["chart_data_url"] = (
            f"/api/tickers/{quote(context['ticker'], safe='')}/chart-data?{params}"
        )
    status = {
        "unknown_ticker": 404,
        "identity_ambiguous": 409,
        "selected_run_unavailable": 404,
        "request_error": 503,
    }.get(context["state"], 200)
    return templates.TemplateResponse(request, "ticker_workspace.html", context, status_code=status)


@router.get("/api/tickers/{ticker}/chart-data")
def workspace_chart_data(
    ticker: str,
    db: DbSession,
    run_id: Annotated[int, Query(ge=1)],
    identity: TickerIdentity = None,
    evidence_id: Annotated[int | None, Query(ge=1)] = None,
):
    context = ticker_workspace(db, _validate_ticker(ticker), identity, run_id, include_runs=False)
    if context["state"] != "available":
        raise HTTPException(404, context["state"])
    if evidence_id is not None and evidence_id != context["evidence"].id:
        raise HTTPException(409, "Decision evidence changed. Refresh the workspace.")
    payload = build_ticker_chart_payload(
        db,
        context["selected_run_id"],
        context["ticker"],
        technical_source=context["technical"],
    )
    payload["provenance"] = {
        "prices": "current_cached",
        "run_id": context["selected_run_id"],
        "evidence_id": context["evidence"].id,
        "cutoff": context["evidence"].calculated_at.isoformat(),
    }
    # Keep this read-only view useful without suggesting acquisition actions.
    if not payload["bars"]:
        payload["message"] = "No cached chart data available. Decision evidence remains below."
    return payload


@router.get("/api/tickers/{ticker}/intelligence/{section}")
def workspace_intelligence(
    ticker: str,
    section: Literal["ceri", "lifecycle", "rankings", "winner"],
    request: Request,
    db: DbSession,
    identity: TickerIdentity = None,
    run_id: Annotated[int | None, Query(ge=1)] = None,
    evidence_id: Annotated[int | None, Query(ge=1)] = None,
    detail: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    ticker = _validate_ticker(ticker)
    try:
        data = ticker_intelligence(
            db,
            ticker,
            identity,
            run_id,
            evidence_id,
            section,
            detail=detail,
            settings=request.app.state.settings,
            offset=offset,
        )
    except SQLAlchemyError:
        # A section request owns its transaction; failures cannot break siblings.
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"state": "error", "summary": "Intelligence data could not load. Retry this section."},
            status_code=503,
        )
    result = {key: data[key] for key in ("state", "summary", "as_of") if key in data}
    pagination_urls = {}
    if "pagination" in data:
        result["pagination"] = data["pagination"]
        for direction in ("next", "previous"):
            page_offset = data["pagination"][f"{direction}_offset"]
            if page_offset is not None:
                params = urlencode({
                    "identity": data["identity"],
                    "run_id": data["run_id"],
                    "evidence_id": data["decision_evidence_id"],
                    "detail": "true",
                    "offset": page_offset,
                })
                pagination_urls[direction] = (
                    f"/api/tickers/{quote(ticker, safe='')}/intelligence/rankings?{params}"
                )
    if detail:
        result["html"] = templates.env.get_template("partials/_ticker_intelligence.html").render(
            data=data,
            section=section,
            ticker=ticker.upper(),
            pagination_urls=pagination_urls,
        )
    return result
