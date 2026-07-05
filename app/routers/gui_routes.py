from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.settings import get_settings
from app.templates import templates

router = APIRouter(tags=["gui"])


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "active_nav": "settings",
            "settings_groups": _settings_groups(settings),
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
        "model_version": fundamentals.get("model_version", "fundamentals_v2.0"),
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
