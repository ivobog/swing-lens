from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse

from app.services.market_sector_intelligence_chart_service import WINDOWS, selected_symbols
from app.services.market_sector_intelligence_config import (
    MarketSectorIntelligenceConfigError,
    load_market_sector_intelligence_config,
)
from app.settings import get_settings
from app.templates import templates

router = APIRouter(tags=["gui"])


@router.get("/sector-rotation", response_class=HTMLResponse)
def market_sector_intelligence_page(
    request: Request,
    etf: str | None = None,
    horizon: int = 126,
    snapshot_id: Annotated[int | None, Query(ge=1, le=9007199254740991)] = None,
    symbols: Annotated[str | None, Query(max_length=128)] = None,
    mode: Literal["relative", "absolute"] = "relative",
) -> HTMLResponse:
    from dataclasses import asdict

    if horizon not in WINDOWS:
        raise HTTPException(status_code=400, detail={"code": "INVALID_CHART_SELECTION"})
    sectors, error = [], None
    chosen = []
    focus = etf.strip().upper() if etf is not None else "XLK"
    try:
        config = load_market_sector_intelligence_config()
        sectors = [asdict(sector) for sector in config.sectors]
        try:
            chosen = selected_symbols(symbols if symbols is not None else "XLK,XLF,XLE", config)
            if focus not in {sector.etf_symbol for sector in config.sectors}:
                raise ValueError
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail={"code": "INVALID_CHART_SELECTION"}
            ) from exc
    except MarketSectorIntelligenceConfigError:
        error = "Independent sector configuration is unavailable."
    return templates.TemplateResponse(request, "market_sector_intelligence.html", {
        "active_nav": "market-sector-intelligence", "sectors": sectors,
        "configuration_error": error,
        "initial_state": {"etf": focus, "horizon": horizon, "snapshot_id": snapshot_id,
                          "symbols": chosen, "mode": mode},
    })


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    market_sector = None
    market_sector_error = None
    try:
        market_sector = load_market_sector_intelligence_config()
    except MarketSectorIntelligenceConfigError as exc:
        market_sector_error = str(exc)
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active_nav": "settings",
            "settings_groups": _settings_groups(settings),
            "market_sector": market_sector,
            "market_sector_error": market_sector_error,
        },
    )


@router.get("/scoring", response_class=HTMLResponse)
def scoring_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "scoring.html",
        {
            "active_nav": "scoring",
            "model": _scoring_model(),
        },
    )


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "help.html", {"active_nav": "help"})


def _settings_groups(settings) -> list[dict[str, object]]:
    return [
        {
            "title": "App",
            "rows": [
                ("Name", settings.app_name),
                ("Host", f"{settings.app_host}:{settings.app_port}"),
                ("Debug", "Yes" if settings.debug else "No"),
            ],
        },
        {
            "title": "Storage",
            "rows": [
                ("Upload dir", settings.upload_dir),
                ("Export dir", settings.export_dir),
                ("Cache dir", settings.cache_dir),
                ("Max upload", f"{settings.max_upload_size_mb} MB"),
            ],
        },
        {
            "title": "IB Gateway",
            "rows": [
                ("Host", settings.ib_host),
                ("Port", settings.ib_port),
                ("Client ID", settings.ib_client_id),
                ("Read-only mode", "Market data only"),
                ("Use RTH", "Yes" if settings.ib_use_rth else "No"),
            ],
        },
        {
            "title": "IB Fetch Planning",
            "rows": [
                ("Default duration", settings.ib_default_duration),
                ("Full backfill", settings.ib_full_backfill_duration),
                ("Top-up", settings.ib_top_up_duration),
                ("Refresh", settings.ib_refresh_duration),
                ("Bar size", settings.ib_default_bar_size),
                ("Requests/min", settings.ib_requests_per_minute),
                ("Minimum gap", f"{settings.ib_min_seconds_between_requests}s"),
                ("Backoff", f"{settings.ib_backoff_seconds}s"),
                ("Max retries", settings.ib_max_retries),
                ("Benchmarks", ", ".join(settings.ib_benchmark_symbols)),
                ("Required daily bars", settings.ib_required_daily_bars),
                ("Stale after", f"{settings.ib_daily_bar_stale_after_days} days"),
            ],
        },
        {
            "title": "Scoring",
            "rows": [
                ("Fundamentals model", _scoring_model()["model_version"]),
                ("Combined fundamental weight", _combined_weights()["fundamental_score"]),
                ("Combined technical weight", _combined_weights()["dual_score"]),
            ],
        },
    ]


def _scoring_model() -> dict[str, Any]:
    fundamentals = _load_yaml(Path("config/fundamentals_v2.yaml"))
    combined = _load_yaml(Path("config/scoring_weights.yaml"))
    return {
        "model_version": fundamentals.get("model_version", "fundamentals_v2.1"),
        "weights": fundamentals.get("weights", {}),
        "components": fundamentals.get("components", {}),
        "field_priorities": fundamentals.get("field_priorities", {}),
        "missing_data": fundamentals.get("missing_data", {}),
        "thresholds": fundamentals.get("thresholds", {}),
        "combined_weights": combined.get("combined_score", {}),
        "penalties": combined.get("penalties", {}),
        "labels": combined.get("labels", {}),
    }


def _combined_weights() -> dict[str, Any]:
    return _load_yaml(Path("config/scoring_weights.yaml")).get("combined_score", {})


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}
