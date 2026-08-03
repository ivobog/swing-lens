from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models.tables import (
    CombinedResult,
    RankingResult,
    RawCompanyRow,
    SectorRotationSnapshot,
    TechnicalScore,
    UploadRun,
)
from app.routers.export_responses import attachment_response
from app.security import ROUTE_CLASS_PUBLIC_LOCAL, unsafe_route
from app.services.sector_rotation_config import load_sector_rotation_config
from app.services.sector_rotation_export_service import (
    export_sector_rotation_csv,
    export_sector_rotation_json,
    export_sector_rotation_markdown,
    snapshot_to_payload,
)
from app.services.sector_rotation_repository import SectorRotationRepository
from app.services.sector_rotation_service import SectorRotationService
from app.services.sector_taxonomy import (
    SectorNormalizationResult,
    normalize_sector_result,
    sector_slug,
)
from app.templates import templates

router = APIRouter(tags=["sector-rotation"])
DbSession = Annotated[Session, Depends(get_db)]


@router.get("/runs/{run_id}/sector-rotation", response_class=HTMLResponse)
def sector_rotation_dashboard(request: Request, run_id: int, db: DbSession) -> HTMLResponse:
    _require_run(db, run_id)
    payload = _run_payload_or_calculate(db, run_id)
    return templates.TemplateResponse(
        request,
        "sector_rotation_dashboard.html",
        _dashboard_template_context(payload=payload, run_id=run_id),
    )


@router.get("/runs/{run_id}/sector-rotation/{sector_slug}", response_class=HTMLResponse)
def sector_rotation_drilldown(
    request: Request,
    run_id: int,
    sector_slug: str,
    db: DbSession,
) -> HTMLResponse:
    _require_run(db, run_id)
    payload = _run_payload_or_calculate(db, run_id)
    row = _payload_sector_row(payload, sector_slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Sector was not found in this snapshot.")
    return templates.TemplateResponse(
        request,
        "sector_rotation_drilldown.html",
        _drilldown_template_context(
            payload=payload,
            row=row,
            run_id=run_id,
            ticker_rows=_sector_ticker_drilldown_rows(db, run_id, sector_slug),
        ),
    )


@router.get("/api/runs/{run_id}/sector-rotation")
def api_sector_rotation(run_id: int, db: DbSession) -> dict:
    _require_run(db, run_id)
    return _run_payload_or_calculate(db, run_id)


@router.get("/api/runs/{run_id}/sector-rotation/{sector_slug}")
def api_sector_rotation_drilldown(run_id: int, sector_slug: str, db: DbSession) -> dict:
    _require_run(db, run_id)
    payload = _run_payload_or_calculate(db, run_id)
    row = _payload_sector_row(payload, sector_slug)
    if row is None:
        raise HTTPException(status_code=404, detail="Sector was not found in this snapshot.")
    return {"snapshot": payload["snapshot"], "row": row}


@router.get("/api/sector-rotation/snapshots")
def api_sector_rotation_snapshots(
    db: DbSession,
    limit: int = 30,
    run_id: int | None = None,
) -> list[dict]:
    repo = SectorRotationRepository()
    return [
        snapshot_to_payload(snapshot, repo.get_snapshot_rows(db, snapshot.id))["snapshot"]
        for snapshot in repo.history(db, limit=limit, run_id=run_id)
    ]


@router.get("/api/sector-rotation/snapshots/{snapshot_id}")
def api_sector_rotation_snapshot(snapshot_id: int, db: DbSession) -> dict:
    snapshot = _snapshot_or_404(db, snapshot_id)
    repo = SectorRotationRepository()
    return snapshot_to_payload(snapshot, repo.get_snapshot_rows(db, snapshot.id))


@router.post("/api/runs/{run_id}/sector-rotation/recalculate")
@unsafe_route(ROUTE_CLASS_PUBLIC_LOCAL, reason="recalculates persisted sector rotation snapshot")
def recalculate_run_sector_rotation_api(run_id: int, db: DbSession) -> dict:
    _require_run(db, run_id)
    try:
        dto = SectorRotationService().build_sector_rotation_snapshot(
            db,
            run_id=run_id,
            persist=True,
        )
        db.commit()
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        db.rollback()
        raise
    return snapshot_to_payload(dto)


@router.get("/runs/{run_id}/sector-rotation/export.csv")
def export_sector_rotation_dashboard_csv(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot, rows = _run_snapshot_and_rows_or_404(db, run_id)
    return _attachment_response(
        export_sector_rotation_csv(snapshot, rows),
        media_type="text/csv",
        filename=f"swinglens_run_{run_id}_sector_rotation.csv",
    )


@router.get("/runs/{run_id}/sector-rotation/export.json")
def export_sector_rotation_dashboard_json(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot, rows = _run_snapshot_and_rows_or_404(db, run_id)
    return _attachment_response(
        export_sector_rotation_json(snapshot, rows),
        media_type="application/json",
        filename=f"swinglens_run_{run_id}_sector_rotation.json",
    )


@router.get("/runs/{run_id}/sector-rotation/brief.md")
def export_sector_rotation_dashboard_markdown(run_id: int, db: DbSession) -> Response:
    _require_run(db, run_id)
    snapshot, rows = _run_snapshot_and_rows_or_404(db, run_id)
    return _attachment_response(
        export_sector_rotation_markdown(snapshot, rows),
        media_type="text/markdown",
        filename=f"swinglens_run_{run_id}_sector_rotation_brief.md",
    )


def _run_payload_or_calculate(db: Session, run_id: int) -> dict:
    repo = SectorRotationRepository()
    snapshot = repo.latest_for_run(db, run_id)
    if snapshot is not None:
        return snapshot_to_payload(snapshot, repo.get_snapshot_rows(db, snapshot.id))

    try:
        dto = SectorRotationService().build_sector_rotation_snapshot(
            db,
            run_id=run_id,
            persist=True,
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    return snapshot_to_payload(dto)


def _run_snapshot_and_rows_or_404(
    db: Session,
    run_id: int,
) -> tuple[SectorRotationSnapshot, list]:
    repo = SectorRotationRepository()
    snapshot = repo.latest_for_run(db, run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="No sector rotation snapshot exists for run.")
    return snapshot, repo.get_snapshot_rows(db, snapshot.id)


def _snapshot_or_404(db: Session, snapshot_id: int) -> SectorRotationSnapshot:
    snapshot = db.get(SectorRotationSnapshot, snapshot_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Sector rotation snapshot was not found.")
    return snapshot


def _payload_sector_row(payload: dict, sector_slug: str) -> dict | None:
    return next(
        (row for row in payload["rows"] if row["sector_slug"] == sector_slug),
        None,
    )


def _dashboard_template_context(payload: dict, run_id: int) -> dict:
    rows = payload["rows"]
    return {
        "active_nav": "runs",
        "title": f"Run {run_id} Sector Rotation",
        "run_id": run_id,
        "snapshot": payload["snapshot"],
        "rows": rows,
        "summary_metrics": _summary_metrics(payload),
        "profile_distribution_rows": _distribution_rows(rows, "profile_distribution"),
        "setup_distribution_rows": _distribution_rows(rows, "setup_distribution"),
        "warning_distribution_rows": _distribution_rows(rows, "warning_distribution"),
        "state_tones": _state_tones(rows),
        "permission_tones": _permission_tones(rows),
    }


def _drilldown_template_context(
    payload: dict,
    row: dict,
    run_id: int,
    ticker_rows: list[dict],
) -> dict:
    return {
        "active_nav": "runs",
        "title": f"{row['sector']} Sector Rotation",
        "run_id": run_id,
        "snapshot": payload["snapshot"],
        "row": row,
        "ticker_rows": ticker_rows,
        "top_by_profile": _top_tickers(ticker_rows, "profile_rank"),
        "top_by_technical": _top_tickers(ticker_rows, "technical_score"),
        "top_by_fundamental": _top_tickers(ticker_rows, "fundamental_score"),
        "setup_groups": _setup_groups(ticker_rows),
        "warning_rows": _warning_rows(ticker_rows),
        "state_tone": _state_tone(row.get("rotation_state")),
        "permission_tone": _permission_tone(row.get("permission")),
    }


def _summary_metrics(payload: dict) -> list[dict[str, object]]:
    snapshot = payload["snapshot"]
    rows = payload["rows"]
    summary = snapshot.get("summary") or {}
    ticker_count = sum(int(row.get("ticker_count") or 0) for row in rows)
    high_confidence = sum(1 for row in rows if row.get("confidence") == "high")
    warnings = list(snapshot.get("warnings") or [])
    return [
        {"label": "Leading", "value": summary.get("leading_sector") or "N/A"},
        {"label": "Weakest", "value": summary.get("weakest_sector") or "N/A"},
        {"label": "Riskiest", "value": summary.get("riskiest_sector") or "N/A"},
        {"label": "Sectors", "value": len(rows)},
        {"label": "Tickers", "value": ticker_count},
        {"label": "High Confidence", "value": high_confidence},
        {"label": "Warnings", "value": len(warnings)},
    ]


def _distribution_rows(rows: list[dict], key: str) -> list[dict[str, object]]:
    names = sorted(
        {
            str(name)
            for row in rows
            for name in (row.get(key) or {})
            if str(name).strip()
        }
    )
    return [
        {
            "name": name,
            "values": [
                {
                    "sector": row["sector"],
                    "sector_slug": row["sector_slug"],
                    "count": _distribution_count((row.get(key) or {}).get(name, 0)),
                }
                for row in rows
            ],
        }
        for name in names
    ]


def _distribution_count(value: object) -> int:
    if isinstance(value, dict):
        return int(value.get("top_25_count") or value.get("count") or 0)
    return int(value or 0)


def _state_tones(rows: list[dict]) -> dict[str, str]:
    return {str(row.get("rotation_state")): _state_tone(row.get("rotation_state")) for row in rows}


def _permission_tones(rows: list[dict]) -> dict[str, str]:
    return {str(row.get("permission")): _permission_tone(row.get("permission")) for row in rows}


def _state_tone(state: object) -> str:
    return {
        "Leading": "success",
        "Improving": "success",
        "Neutral": "muted",
        "Fading": "warning",
        "Lagging": "danger",
        "Crowded risk": "warning",
        "Risk-off": "danger",
        "Insufficient data": "muted",
    }.get(str(state), "muted")


def _permission_tone(permission: object) -> str:
    return {
        "full_allowed": "success",
        "reduced_size": "warning",
        "watch_only": "muted",
        "avoid_new_longs": "danger",
    }.get(str(permission), "muted")


def _sector_ticker_drilldown_rows(db: Session, run_id: int, selected_slug: str) -> list[dict]:
    config = load_sector_rotation_config()
    raw_rows = list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )
    combined_by_ticker = {
        row.ticker.upper(): row
        for row in db.scalars(select(CombinedResult).where(CombinedResult.run_id == run_id))
    }
    technical_by_ticker = {
        row.ticker.upper(): row
        for row in db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id))
    }
    default_profile = str(config["defaults"]["default_ranking_profile"])
    ranking_by_ticker = {
        row.ticker.upper(): row
        for row in db.scalars(
            select(RankingResult)
            .where(RankingResult.run_id == run_id)
            .where(RankingResult.ranking_profile == default_profile)
        )
    }

    seen: set[str] = set()
    rows: list[dict] = []
    for raw_row in raw_rows:
        ticker = raw_row.ticker.upper()
        if ticker in seen:
            continue
        seen.add(ticker)
        combined = combined_by_ticker.get(ticker)
        ranking = ranking_by_ticker.get(ticker)
        technical = technical_by_ticker.get(ticker)
        normalization = _normalization_for_drilldown_row(
            raw_row=raw_row,
            fallback_sector=(
                raw_row.sector
                or getattr(combined, "sector", None)
                or getattr(ranking, "sector", None)
            ),
            config=config,
        )
        if sector_slug(normalization.canonical_sector) != selected_slug:
            continue
        rows.append(
            {
                "ticker": ticker,
                "company_name": raw_row.company_name
                or getattr(combined, "company_name", None)
                or getattr(ranking, "company_name", None),
                "sector": normalization.canonical_sector,
                "raw_sector": normalization.raw_sector,
                "sector_taxonomy": normalization.taxonomy,
                "sector_mapping_status": normalization.status,
                "final_rank": getattr(combined, "final_rank", None),
                "final_score": _float_or_none(getattr(combined, "final_score", None)),
                "profile_rank": getattr(ranking, "profile_rank", None),
                "profile_score": _float_or_none(getattr(ranking, "profile_score", None)),
                "technical_score": _float_or_none(getattr(technical, "dual_score", None)),
                "fundamental_score": _float_or_none(
                    getattr(combined, "fundamental_score", None)
                    or getattr(ranking, "fundamental_score", None)
                ),
                "technical_classification": getattr(
                    technical,
                    "classification",
                    None,
                )
                or getattr(combined, "technical_classification", None)
                or getattr(ranking, "technical_classification", None),
                "decision_label": getattr(ranking, "decision_label", None)
                or getattr(combined, "combined_decision", None),
                "position_size_hint": getattr(ranking, "position_size_hint", None)
                or getattr(combined, "position_size_hint", None),
                "warning_flags": list(
                    getattr(ranking, "warning_flags_json", None)
                    or getattr(combined, "warning_flags_json", None)
                    or getattr(technical, "warning_flags_json", None)
                    or []
                ),
            }
        )
    return sorted(rows, key=lambda row: (row["profile_rank"] or 999999, row["ticker"]))


def _normalization_for_drilldown_row(
    raw_row: object,
    fallback_sector: object,
    config: dict,
) -> SectorNormalizationResult:
    raw_sector = getattr(raw_row, "sector", None) or fallback_sector
    canonical = str(getattr(raw_row, "sector_canonical", None) or "").strip()
    status = str(getattr(raw_row, "sector_mapping_status", None) or "").strip()
    taxonomy = str(getattr(raw_row, "sector_taxonomy", None) or "").strip()
    if canonical and status:
        return SectorNormalizationResult(
            raw_sector=str(raw_sector).strip() if raw_sector is not None else None,
            canonical_sector=canonical,
            taxonomy=taxonomy
            or str(config.get("sector_taxonomy", {}).get("source") or "unknown"),
            status=status,
        )
    return normalize_sector_result(raw_sector, config)


def _top_tickers(rows: list[dict], key: str, limit: int = 10) -> list[dict]:
    def sort_key(row: dict) -> tuple:
        value = row.get(key)
        if key.endswith("rank"):
            return (value is None, value or 999999, row["ticker"])
        return (value is None, -(float(value) if value is not None else -1.0), row["ticker"])

    return sorted(rows, key=sort_key)[:limit]


def _setup_groups(rows: list[dict]) -> dict[str, list[dict]]:
    groups = {"buyable": [], "watch": [], "danger": []}
    for row in rows:
        label = str(row.get("decision_label") or "").lower()
        classification = str(row.get("technical_classification") or "").lower()
        warnings = [str(flag).lower() for flag in row.get("warning_flags") or []]
        if "strong candidate" in label or "candidate" == label or "breakout" in classification:
            groups["buyable"].append(row)
        elif "no trade" in label or "avoid" in label or warnings:
            groups["danger"].append(row)
        else:
            groups["watch"].append(row)
    return groups


def _warning_rows(rows: list[dict]) -> list[dict[str, object]]:
    counts: dict[str, int] = {}
    for row in rows:
        for warning in row.get("warning_flags") or []:
            counts[str(warning)] = counts.get(str(warning), 0) + 1
    return [{"warning": warning, "count": count} for warning, count in sorted(counts.items())]


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    return float(value)


def _require_run(db: Session, run_id: int) -> None:
    exists = db.scalar(select(UploadRun.id).where(UploadRun.id == run_id))
    if exists is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} was not found.")


def _attachment_response(content: str, media_type: str, filename: str) -> Response:
    return attachment_response(
        content,
        media_type=media_type,
        filename=filename,
    )
