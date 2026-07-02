import csv
import json
from collections.abc import Iterable
from io import StringIO
from typing import Any

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)

EXPORT_TYPES = {"combined", "fundamentals", "technicals", "raw"}

COMBINED_HEADERS = [
    "run_id",
    "rank",
    "ticker",
    "company_name",
    "sector",
    "final_score",
    "fundamental_score",
    "fundamental_label",
    "technical_classification",
    "dual_score",
    "combined_decision",
    "position_size_hint",
    "notes",
]

FUNDAMENTAL_HEADERS = [
    "run_id",
    "ticker",
    "growth_score",
    "profitability_score",
    "fcf_score",
    "balance_sheet_score",
    "valuation_score",
    "momentum_score",
    "dilution_score",
    "risk_score",
    "missing_data_penalty",
    "fundamental_score",
    "fundamental_label",
    "trap_flags",
    "explanation",
]

TECHNICAL_HEADERS = [
    "run_id",
    "ticker",
    "trend_score",
    "local_trend_score",
    "momentum_score",
    "setup_score",
    "risk_score",
    "market_score",
    "relative_strength_score",
    "sector_relative_strength_score",
    "combined_relative_strength_score",
    "htf_score",
    "dual_score",
    "classification",
    "pullback_health",
    "action_bias",
    "suggested_stop",
    "suggested_target",
    "reward_risk",
    "entry_risk_pct",
    "technical_confidence",
    "insufficient_data",
]

RAW_HEADERS = [
    "run_id",
    "row_number",
    "ticker",
    "company_name",
    "sector",
    "raw_column_count",
    "raw_json",
]


def export_run_csv(run: UploadRun, export_type: str) -> str:
    if export_type == "combined":
        return _write_csv(
            COMBINED_HEADERS,
            [_combined_row(run.id, result) for result in _sorted_combined(run.combined_results)],
        )
    if export_type == "fundamentals":
        rows = [
            _fundamental_row(run.id, score)
            for score in _sorted_by_ticker(run.fundamental_scores)
        ]
        return _write_csv(
            FUNDAMENTAL_HEADERS,
            rows,
        )
    if export_type == "technicals":
        return _write_csv(
            TECHNICAL_HEADERS,
            [_technical_row(run.id, score) for score in _sorted_by_ticker(run.technical_scores)],
        )
    if export_type == "raw":
        return _write_csv(
            RAW_HEADERS,
            [_raw_row(run.id, row) for row in _sorted_raw(run.raw_company_rows)],
        )
    raise ValueError(f"Unknown export type: {export_type}")


def export_filename(run: UploadRun, export_type: str) -> str:
    safe_name = "".join(
        char.lower() if char.isalnum() else "-"
        for char in run.filename.rsplit(".", 1)[0]
    ).strip("-")
    prefix = safe_name or "run"
    return f"swinglens_run_{run.id}_{prefix}_{export_type}.csv"


def _write_csv(headers: list[str], rows: Iterable[dict[str, Any]]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in headers})
    return buffer.getvalue()


def _combined_row(run_id: int, result: CombinedResult) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "rank": result.final_rank,
        "ticker": result.ticker,
        "company_name": result.company_name,
        "sector": result.sector,
        "final_score": result.final_score,
        "fundamental_score": result.fundamental_score,
        "fundamental_label": result.fundamental_label,
        "technical_classification": result.technical_classification,
        "dual_score": result.dual_score,
        "combined_decision": result.combined_decision,
        "position_size_hint": result.position_size_hint,
        "notes": result.notes,
    }


def _fundamental_row(run_id: int, score: FundamentalScore) -> dict[str, Any]:
    flags = ""
    if score.trap_flags_json and score.trap_flags_json.get("flags"):
        flags = "; ".join(score.trap_flags_json["flags"])
    return {
        "run_id": run_id,
        "ticker": score.ticker,
        "growth_score": score.growth_score,
        "profitability_score": score.profitability_score,
        "fcf_score": score.fcf_score,
        "balance_sheet_score": score.balance_sheet_score,
        "valuation_score": score.valuation_score,
        "momentum_score": score.momentum_score,
        "dilution_score": score.dilution_score,
        "risk_score": score.risk_score,
        "missing_data_penalty": score.missing_data_penalty,
        "fundamental_score": score.fundamental_score,
        "fundamental_label": score.fundamental_label,
        "trap_flags": flags,
        "explanation": score.explanation,
    }


def _technical_row(run_id: int, score: TechnicalScore) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "ticker": score.ticker,
        "trend_score": score.trend_score,
        "local_trend_score": score.local_trend_score,
        "momentum_score": score.momentum_score,
        "setup_score": score.setup_score,
        "risk_score": score.risk_score,
        "market_score": score.market_score,
        "relative_strength_score": score.relative_strength_score,
        "sector_relative_strength_score": score.sector_relative_strength_score,
        "combined_relative_strength_score": score.combined_relative_strength_score,
        "htf_score": score.htf_score,
        "dual_score": score.dual_score,
        "classification": score.classification,
        "pullback_health": score.pullback_health,
        "action_bias": score.action_bias,
        "suggested_stop": score.suggested_stop,
        "suggested_target": score.suggested_target,
        "reward_risk": score.reward_risk,
        "entry_risk_pct": score.entry_risk_pct,
        "technical_confidence": score.technical_confidence,
        "insufficient_data": score.insufficient_data,
    }


def _raw_row(run_id: int, row: RawCompanyRow) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "row_number": row.row_number,
        "ticker": row.ticker,
        "company_name": row.company_name,
        "sector": row.sector,
        "raw_column_count": len(row.raw_json),
        "raw_json": json.dumps(row.raw_json, sort_keys=True),
    }


def _sorted_combined(results: list[CombinedResult]) -> list[CombinedResult]:
    return sorted(results, key=lambda result: result.final_rank or 0)


def _sorted_raw(rows: list[RawCompanyRow]) -> list[RawCompanyRow]:
    return sorted(rows, key=lambda row: row.row_number)


def _sorted_by_ticker(rows: list[Any]) -> list[Any]:
    return sorted(rows, key=lambda row: row.ticker)


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    return value
