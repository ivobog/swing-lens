from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from app.models.tables import SectorRotationRow, SectorRotationSnapshot
from app.services.csv_export import write_csv
from app.services.sector_rotation_dtos import (
    SectorRotationDecision,
    SectorRotationSnapshotDto,
    SectorUniverseMetrics,
)

SECTOR_ROTATION_CSV_HEADERS = [
    "rank",
    "sector",
    "state",
    "permission",
    "final_score",
    "universe_score",
    "etf_score",
    "ticker_count",
    "top_25_count",
    "top_25_share",
    "buyable_share",
    "danger_share",
    "average_technical_score",
    "average_fundamental_score",
    "average_profile_score",
    "position_size_multiplier",
    "confidence",
    "warnings",
    "reasons",
]
SECTOR_ROTATION_SCHEMA_ID = "swinglens.sector-rotation.v1"


def snapshot_to_payload(
    snapshot: SectorRotationSnapshot | SectorRotationSnapshotDto,
    rows: list[SectorRotationRow] | None = None,
) -> dict[str, Any]:
    if isinstance(snapshot, SectorRotationSnapshotDto):
        return _dto_to_payload(snapshot)

    rows = rows if rows is not None else list(getattr(snapshot, "rows", []) or [])
    return {
        "snapshot": {
            "id": snapshot.id,
            "run_id": snapshot.run_id,
            "market_regime_snapshot_id": snapshot.market_regime_snapshot_id,
            "as_of_date": _json_value(snapshot.as_of_date),
            "calculation_version": snapshot.calculation_version,
            "config_version": snapshot.config_version,
            "config_hash": snapshot.config_hash,
            "mode": snapshot.mode,
            "default_ranking_profile": snapshot.default_ranking_profile,
            "benchmark_ticker": snapshot.benchmark_ticker,
            "summary": snapshot.summary_json,
            "warnings": snapshot.warning_flags_json,
            "debug": snapshot.debug_json,
            "created_at": _json_value(snapshot.created_at),
            "updated_at": _json_value(snapshot.updated_at),
        },
        "rows": [_row_payload(_orm_row_context(row)) for row in _sorted_orm_rows(rows)],
    }


def export_sector_rotation_json(
    snapshot: SectorRotationSnapshot | SectorRotationSnapshotDto,
    rows: list[SectorRotationRow] | None = None,
) -> str:
    return json.dumps(snapshot_to_payload(snapshot, rows), indent=2, sort_keys=True)


def export_sector_rotation_csv(
    snapshot: SectorRotationSnapshot | SectorRotationSnapshotDto,
    rows: list[SectorRotationRow] | None = None,
) -> str:
    contexts = _row_contexts(snapshot, rows)
    return write_csv(
        SECTOR_ROTATION_CSV_HEADERS,
        [_csv_row(context) for context in contexts],
        schema_id=SECTOR_ROTATION_SCHEMA_ID,
        metadata={"guidance_type": "research_context", "execution_instruction": False},
    )


def export_sector_rotation_markdown(
    snapshot: SectorRotationSnapshot | SectorRotationSnapshotDto,
    rows: list[SectorRotationRow] | None = None,
) -> str:
    payload = snapshot_to_payload(snapshot, rows)
    snapshot_payload = payload["snapshot"]
    row_payloads = payload["rows"]
    lines = [
        f"# Sector Rotation Brief - {snapshot_payload['as_of_date']}",
        "",
        f"- Mode: {snapshot_payload['mode']}",
        f"- Run: {snapshot_payload['run_id'] or 'N/A'}",
        f"- Leading sector: {snapshot_payload['summary'].get('leading_sector') or 'N/A'}",
        f"- Weakest sector: {snapshot_payload['summary'].get('weakest_sector') or 'N/A'}",
        f"- Riskiest sector: {snapshot_payload['summary'].get('riskiest_sector') or 'N/A'}",
        "",
        "| Rank | Sector | State | Permission | Score | Confidence |",
        "|---:|---|---|---|---:|---|",
    ]
    for row in row_payloads:
        lines.append(
            "| {rank} | {sector} | {state} | {permission} | {score} | {confidence} |".format(
                rank=row["rank"] or "",
                sector=row["sector"],
                state=row["rotation_state"],
                permission=row["permission"],
                score=_markdown_score(row["sector_final_score"]),
                confidence=row["confidence"],
            )
        )

    warnings = snapshot_payload.get("warnings") or []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        lines.extend(f"- `{warning}`" for warning in warnings)

    if snapshot_payload["mode"] == "universe_only":
        lines.extend(["", "_ETF confirmation was not used for this universe-only snapshot._"])
    return "\n".join(lines) + "\n"


def _dto_to_payload(snapshot: SectorRotationSnapshotDto) -> dict[str, Any]:
    universe_by_slug = {row.sector_slug: row for row in snapshot.universe_rows}
    etf_by_slug = {row.sector_slug: row for row in snapshot.etf_rows}
    return {
        "snapshot": {
            "id": None,
            "run_id": snapshot.run_id,
            "market_regime_snapshot_id": snapshot.market_regime_snapshot_id,
            "as_of_date": snapshot.as_of_date,
            "calculation_version": snapshot.calculation_version,
            "config_version": snapshot.config_version,
            "config_hash": snapshot.config_hash,
            "mode": snapshot.mode,
            "default_ranking_profile": snapshot.default_ranking_profile,
            "benchmark_ticker": snapshot.benchmark_ticker,
            "summary": snapshot.summary,
            "warnings": snapshot.warnings,
            "debug": snapshot.debug,
            "created_at": None,
            "updated_at": None,
        },
        "rows": [
            _row_payload(
                _dto_row_context(
                    decision,
                    universe_by_slug.get(decision.sector_slug),
                    etf_by_slug.get(decision.sector_slug),
                )
            )
            for decision in _sorted_decisions(snapshot.rows)
        ],
    }


def _row_contexts(
    snapshot: SectorRotationSnapshot | SectorRotationSnapshotDto,
    rows: list[SectorRotationRow] | None,
) -> list[dict[str, Any]]:
    if isinstance(snapshot, SectorRotationSnapshotDto):
        universe_by_slug = {row.sector_slug: row for row in snapshot.universe_rows}
        etf_by_slug = {row.sector_slug: row for row in snapshot.etf_rows}
        return [
            _dto_row_context(
                decision,
                universe_by_slug.get(decision.sector_slug),
                etf_by_slug.get(decision.sector_slug),
            )
            for decision in _sorted_decisions(snapshot.rows)
        ]
    rows = rows if rows is not None else list(getattr(snapshot, "rows", []) or [])
    return [_orm_row_context(row) for row in _sorted_orm_rows(rows)]


def _dto_row_context(
    decision: SectorRotationDecision,
    universe: SectorUniverseMetrics | None,
    etf: Any | None = None,
) -> dict[str, Any]:
    top_counts = universe.top_counts if universe is not None else {}
    return {
        "rank": decision.rank,
        "sector": decision.sector,
        "sector_slug": decision.sector_slug,
        "rotation_state": decision.rotation_state,
        "permission": decision.permission,
        "sector_final_score": decision.final_score,
        "universe_leadership_score": decision.universe_score,
        "etf_rotation_score": decision.etf_score,
        "ticker_count": universe.ticker_count if universe is not None else None,
        "top_25_count": top_counts.get("top_25", 0),
        "top_25_share": _share(top_counts.get("top_25", 0), universe.ticker_count)
        if universe is not None
        else None,
        "buyable_share": universe.buyable_share if universe is not None else None,
        "danger_share": universe.danger_share if universe is not None else None,
        "average_technical_score": universe.average_technical_score if universe else None,
        "average_fundamental_score": universe.average_fundamental_score if universe else None,
        "average_profile_score": universe.average_profile_score if universe else None,
        "position_size_multiplier": decision.position_size_multiplier,
        "confidence": decision.confidence,
        "previous_rank": decision.previous_rank,
        "rank_change": decision.rank_change,
        "score_change": decision.score_change,
        "warnings": decision.warnings,
        "reasons": decision.reasons,
        "component_scores": universe.component_scores if universe is not None else {},
        "raw_sector_distribution": (
            universe.raw_sector_distribution if universe is not None else {}
        ),
        "sector_mapping_status_counts": (
            universe.sector_mapping_status_counts if universe is not None else {}
        ),
        "profile_distribution": universe.profile_distribution if universe is not None else {},
        "setup_distribution": universe.setup_distribution if universe is not None else {},
        "warning_distribution": universe.warning_distribution if universe is not None else {},
        "etf_metrics": _etf_metrics_payload(etf),
        "debug": {**(universe.debug if universe is not None else {}), **decision.debug},
    }


def _orm_row_context(row: SectorRotationRow) -> dict[str, Any]:
    return {
        "rank": row.current_rank,
        "sector": row.sector,
        "sector_slug": row.sector_slug,
        "rotation_state": row.rotation_state,
        "permission": row.sector_permission,
        "sector_final_score": row.sector_final_score,
        "universe_leadership_score": row.universe_leadership_score,
        "etf_rotation_score": row.etf_rotation_score,
        "ticker_count": row.ticker_count,
        "top_25_count": row.top_25_count,
        "top_25_share": row.top_25_share,
        "buyable_share": row.buyable_share,
        "danger_share": row.danger_share,
        "average_technical_score": row.average_technical_score,
        "average_fundamental_score": row.average_fundamental_score,
        "average_profile_score": row.average_profile_score,
        "position_size_multiplier": row.position_size_multiplier,
        "confidence": row.confidence,
        "previous_rank": row.previous_rank,
        "rank_change": row.rank_change,
        "score_change": row.score_change,
        "warnings": row.warning_flags_json,
        "reasons": row.reason_codes_json,
        "component_scores": row.component_scores_json,
        "raw_sector_distribution": (row.debug_json or {}).get(
            "raw_sector_distribution",
            {},
        ),
        "sector_mapping_status_counts": (row.debug_json or {}).get(
            "sector_mapping_status_counts",
            {},
        ),
        "profile_distribution": row.profile_distribution_json,
        "setup_distribution": row.setup_distribution_json,
        "warning_distribution": row.warning_distribution_json,
        "etf_metrics": row.etf_metrics_json,
        "debug": row.debug_json,
    }


def _row_payload(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": context["rank"],
        "sector": context["sector"],
        "sector_slug": context["sector_slug"],
        "rotation_state": context["rotation_state"],
        "permission": context["permission"],
        "position_size_multiplier": context["position_size_multiplier"],
        "sector_final_score": context["sector_final_score"],
        "universe_leadership_score": context["universe_leadership_score"],
        "etf_rotation_score": context["etf_rotation_score"],
        "ticker_count": context["ticker_count"],
        "top_25_count": context["top_25_count"],
        "top_25_share": context["top_25_share"],
        "buyable_share": context["buyable_share"],
        "danger_share": context["danger_share"],
        "average_technical_score": context["average_technical_score"],
        "average_fundamental_score": context["average_fundamental_score"],
        "average_profile_score": context["average_profile_score"],
        "confidence": context["confidence"],
        "previous_rank": context["previous_rank"],
        "rank_change": context["rank_change"],
        "score_change": context["score_change"],
        "component_scores": context["component_scores"],
        "raw_sector_distribution": context["raw_sector_distribution"],
        "sector_mapping_status_counts": context["sector_mapping_status_counts"],
        "profile_distribution": context["profile_distribution"],
        "setup_distribution": context["setup_distribution"],
        "warning_distribution": context["warning_distribution"],
        "etf_metrics": context["etf_metrics"],
        "reasons": context["reasons"],
        "warnings": context["warnings"],
        "debug": context["debug"],
    }


def _csv_row(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "rank": context["rank"],
        "sector": context["sector"],
        "state": context["rotation_state"],
        "permission": context["permission"],
        "final_score": context["sector_final_score"],
        "universe_score": context["universe_leadership_score"],
        "etf_score": context["etf_rotation_score"],
        "ticker_count": context["ticker_count"],
        "top_25_count": context["top_25_count"],
        "top_25_share": context["top_25_share"],
        "buyable_share": context["buyable_share"],
        "danger_share": context["danger_share"],
        "average_technical_score": context["average_technical_score"],
        "average_fundamental_score": context["average_fundamental_score"],
        "average_profile_score": context["average_profile_score"],
        "position_size_multiplier": context["position_size_multiplier"],
        "confidence": context["confidence"],
        "warnings": _list_text(context["warnings"]),
        "reasons": _list_text(context["reasons"]),
    }


def _etf_metrics_payload(etf: Any | None) -> dict[str, Any]:
    if etf is None:
        return {}
    return {
        "proxy_ticker": etf.proxy_ticker,
        "benchmark_ticker": etf.benchmark_ticker,
        "as_of_date": etf.as_of_date,
        "score": etf.etf_rotation_score,
        "component_scores": dict(etf.component_scores),
        "metrics": dict(etf.metrics),
        "warnings": list(etf.warnings),
        "debug": dict(etf.debug),
    }


def _sorted_orm_rows(rows: list[SectorRotationRow]) -> list[SectorRotationRow]:
    return sorted(
        rows,
        key=lambda row: (
            row.current_rank if row.current_rank is not None else 999999,
            row.sector,
        ),
    )


def _sorted_decisions(rows: list[SectorRotationDecision]) -> list[SectorRotationDecision]:
    return sorted(
        rows,
        key=lambda row: (
            row.rank if row.rank is not None else 999999,
            row.sector,
        ),
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, date | datetime):
        return value.isoformat()
    return value


def _list_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "; ".join(str(value) for value in values)


def _markdown_score(value: Any) -> str:
    if value is None or value == "":
        return ""
    return f"{float(value):.2f}"


def _share(count: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(count / denominator, 4)
