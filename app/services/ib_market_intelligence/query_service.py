from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.ib_market_intelligence_tables import (
    IBExecutionFill,
    IBFlexImportRun,
    IBHistogramBin,
    IBHistogramSnapshot,
    IBIntelligenceFeature,
    IBIntelligenceRequestItem,
    IBIntelligenceRun,
    IBScannerCandidate,
    IBScannerRun,
    IBTradeEpisode,
    IBTradeResearchLink,
)
from app.services.ib_market_intelligence.journal import journal_analytics


def overview(db: Session) -> dict[str, Any]:
    features = latest_features(db)
    by_module: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for feature in features:
        by_module[feature["module"]].append(feature)
    latest_scan = db.scalar(select(IBScannerRun).order_by(IBScannerRun.started_at.desc()))
    latest_flex = db.scalar(select(IBFlexImportRun).order_by(IBFlexImportRun.started_at.desc()))
    return {
        "cards": {
            "liquidity": _coverage_card(
                by_module["LIQUIDITY"], warning_classes={"POOR", "VERY_POOR"}
            ),
            "short_pressure": _coverage_card(
                by_module["SHORT_PRESSURE"],
                warning_classes={"HIGH_BORROW_COST", "EXTREME_BORROW_COST", "NOT_SHORTABLE"},
            ),
            "volatility": _coverage_card(
                by_module["VOLATILITY"],
                warning_classes={"ELEVATED_IV_PREMIUM", "EXTREME_IV_PREMIUM"},
            ),
            "options_activity": _coverage_card(
                by_module["OPTIONS_ACTIVITY"], warning_classes={"ABNORMAL_OPTION_ACTIVITY"}
            ),
            "scanner_candidates": db.scalar(select(func.count()).select_from(IBScannerCandidate))
            or 0,
            "histogram_coverage": len(by_module["HISTOGRAM"]),
            "closed_trades": db.scalar(
                select(func.count())
                .select_from(IBTradeEpisode)
                .where(IBTradeEpisode.status == "CLOSED")
            )
            or 0,
        },
        "latest_scanner_run": _scanner_run_dict(latest_scan) if latest_scan else None,
        "latest_flex_import": _flex_run_dict(latest_flex) if latest_flex else None,
        "latest_features": features,
    }


def latest_features(db: Session, *, ticker: str | None = None) -> list[dict[str, Any]]:
    statement = select(IBIntelligenceFeature).order_by(
        IBIntelligenceFeature.as_of_session.desc(), IBIntelligenceFeature.calculated_at.desc()
    )
    if ticker:
        statement = statement.where(func.upper(IBIntelligenceFeature.ticker) == ticker.upper())
    rows = db.scalars(statement).all()
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for row in rows:
        key = (row.ticker, row.module)
        if key in seen:
            continue
        seen.add(key)
        result.append(_feature_dict(row))
    return sorted(result, key=lambda item: (item["ticker"], item["module"]))


def scanner_runs(db: Session, *, limit: int = 50) -> dict[str, Any]:
    runs = db.scalars(
        select(IBScannerRun).order_by(IBScannerRun.started_at.desc()).limit(limit)
    ).all()
    candidates = db.execute(
        select(IBScannerCandidate, IBScannerRun)
        .join(IBScannerRun, IBScannerRun.id == IBScannerCandidate.scanner_run_id)
        .order_by(
            IBScannerCandidate.ticker, IBScannerCandidate.rank, IBScannerRun.started_at.desc()
        )
    ).all()
    merged: dict[tuple[int | None, str], dict[str, Any]] = {}
    for candidate, run in candidates:
        key = (candidate.ib_conid, candidate.ticker)
        item = merged.setdefault(
            key,
            {
                "ticker": candidate.ticker,
                "ib_conid": candidate.ib_conid,
                "best_rank": candidate.rank,
                "discovery_reasons": [],
                "universe_source": "IBKR_SCANNER",
            },
        )
        item["best_rank"] = min(item["best_rank"], candidate.rank)
        item["discovery_reasons"].append(
            {"scanner_run_id": run.id, "scanner": run.scanner_name, "rank": candidate.rank}
        )
    return {
        "runs": [_scanner_run_dict(run) for run in runs],
        "candidate_pool": list(merged.values()),
    }


def histogram_detail(db: Session, ticker: str) -> dict[str, Any] | None:
    snapshot = db.scalar(
        select(IBHistogramSnapshot)
        .where(func.upper(IBHistogramSnapshot.ticker) == ticker.upper())
        .order_by(IBHistogramSnapshot.observed_at.desc())
    )
    if snapshot is None:
        return None
    bins = db.scalars(
        select(IBHistogramBin)
        .where(IBHistogramBin.histogram_snapshot_id == snapshot.id)
        .order_by(IBHistogramBin.price)
    ).all()
    feature = next(
        (row for row in latest_features(db, ticker=ticker) if row["module"] == "HISTOGRAM"), None
    )
    return {
        "snapshot_id": snapshot.id,
        "ticker": snapshot.ticker,
        "requested_period": snapshot.requested_period,
        "use_rth": snapshot.use_rth,
        "observed_at": snapshot.observed_at,
        "reference_price": _number(snapshot.reference_price),
        "availability_status": snapshot.availability_status,
        "source_semantics": snapshot.source_semantics,
        "warnings": snapshot.warnings_json,
        "bins": [
            {
                "price": _number(row.price),
                "activity_count": _number(row.activity_count),
                "activity_rank": row.activity_rank,
                "density_percentile": _number(row.density_percentile),
            }
            for row in bins
        ],
        "feature": feature,
    }


def trade_journal(
    db: Session, *, group_by: str = "setup_family", include_account: bool = False
) -> dict[str, Any]:
    episodes = db.scalars(select(IBTradeEpisode).order_by(IBTradeEpisode.opened_at.desc())).all()
    links = {row.trade_episode_id: row for row in db.scalars(select(IBTradeResearchLink)).all()}
    fills = db.scalars(
        select(IBExecutionFill).order_by(IBExecutionFill.execution_time.desc()).limit(500)
    ).all()
    return {
        "episodes": [
            {
                "id": row.id,
                "ticker": row.ticker,
                "direction": row.direction,
                "opened_at": row.opened_at,
                "closed_at": row.closed_at,
                "average_entry_price": _number(row.average_entry_price),
                "average_exit_price": _number(row.average_exit_price),
                "gross_pnl": _number(row.gross_pnl),
                "broker_reported_realized_pnl": _number(row.broker_realized_pnl),
                "net_pnl": _number(row.net_pnl),
                "return_pct": _number(row.return_pct),
                "commissions": _number(row.commissions),
                "fees": _number(row.fees),
                "holding_seconds": row.holding_seconds,
                "status": row.status,
                "research_link": _link_dict(links.get(row.id)),
            }
            for row in episodes
        ],
        "fills": [
            {
                "id": row.id,
                "symbol": row.symbol,
                "execution_time": row.execution_time,
                "side": row.side,
                "quantity": _number(row.quantity),
                "price": _number(row.price),
                "commission": _number(row.commission),
                "fees": _number(row.fees),
                "exchange": row.exchange,
                "order_reference": row.order_reference,
                **({"account": row.account_masked_label} if include_account else {}),
            }
            for row in fills
        ],
        "analytics": journal_analytics(db, group_by=group_by),
    }


def operations(db: Session) -> dict[str, Any]:
    rows = db.scalars(
        select(IBIntelligenceRun).order_by(IBIntelligenceRun.started_at.desc()).limit(100)
    ).all()
    latest_by_module: dict[str, dict[str, Any]] = {}
    for row in rows:
        latest_by_module.setdefault(row.module, _run_dict(row))
    request_items = db.scalars(
        select(IBIntelligenceRequestItem)
        .order_by(IBIntelligenceRequestItem.started_at.desc())
        .limit(200)
    ).all()
    return {
        "latest_by_module": latest_by_module,
        "runs": [_run_dict(row) for row in rows],
        "request_items": [
            {
                "id": item.id,
                "run_id": item.intelligence_run_id,
                "ticker": item.ticker,
                "request_family": item.request_family,
                "request_type": item.request_type,
                "status": item.status,
                "availability_status": item.availability_status,
                "result_counts": item.result_counts_json,
                "error_message": item.error_message,
                "started_at": item.started_at,
                "completed_at": item.completed_at,
            }
            for item in request_items
        ],
    }


def _feature_dict(row: IBIntelligenceFeature) -> dict[str, Any]:
    return {
        "id": row.id,
        "ticker": row.ticker,
        "module": row.module,
        "as_of_session": row.as_of_session,
        "calculated_at": row.calculated_at,
        "classification": row.classification,
        "score": _number(row.score),
        "confidence": row.confidence,
        "freshness_status": row.freshness_status,
        "coverage_status": row.coverage_status,
        "components": row.components_json,
        "reasons": row.reasons_json,
        "warnings": row.warnings_json,
        "calculation_version": row.calculation_version,
        "config_hash": row.config_hash,
    }


def _coverage_card(rows: list[dict[str, Any]], *, warning_classes: set[str]) -> dict[str, Any]:
    return {
        "coverage": len(rows),
        "warning_count": sum(row["classification"] in warning_classes for row in rows),
    }


def _scanner_run_dict(row: IBScannerRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "scanner_name": row.scanner_name,
        "scan_code": row.scan_code,
        "status": row.status,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "error_message": row.error_message,
        "filters": row.filters_json,
    }


def _flex_run_dict(row: IBFlexImportRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "query_type": row.query_type,
        "status": row.status,
        "dry_run": row.dry_run,
        "row_count": row.row_count,
        "inserted_count": row.inserted_count,
        "duplicate_count": row.duplicate_count,
        "corrected_count": row.corrected_count,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
        "error_message": row.error_message,
    }


def _run_dict(row: IBIntelligenceRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "job_type": row.job_type,
        "module": row.module,
        "status": row.status,
        "scope": row.scope_json,
        "counts": row.counts_json,
        "checkpoint": row.checkpoint_json,
        "warnings": row.warning_flags_json,
        "error_message": row.error_message,
        "started_at": row.started_at,
        "completed_at": row.completed_at,
    }


def _link_dict(row: IBTradeResearchLink | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "matching_status": row.matching_status,
        "decision_timestamp": row.decision_timestamp,
        "context": row.context_json,
        "leakage_check": row.leakage_check,
        "ambiguity": row.ambiguity_json,
    }


def _number(value: Any) -> float | None:
    return float(value) if value is not None else None
