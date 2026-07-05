import csv
import json
from collections.abc import Iterable
from io import StringIO
from typing import Any

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    IBFetchRun,
    RawCompanyRow,
    TechnicalScore,
    UploadRun,
)
from app.services.cockpit_sorting import cockpit_sort_key
from app.services.column_mapping_summary_service import ColumnMappingSummary
from app.services.ib_fetch_plan_service import FetchPlan
from app.services.ohlcv_coverage_service import OhlcvCoverageSummary
from app.services.technical_display_fields import (
    technical_v4_detail_fields,
    technical_v4_summary_fields,
)

EXPORT_TYPES = {
    "combined",
    "fundamentals",
    "technicals",
    "raw",
    "ib-fetch-plan",
    "ib-fetch-results",
    "coverage",
    "mapping",
}

COMBINED_HEADERS = [
    "run_id",
    "rank",
    "ticker",
    "company_name",
    "sector",
    "final_score",
    "fundamental_score",
    "fundamental_label",
    "fundamental_model_version",
    "fundamental_data_coverage_score",
    "fundamental_warning_flags",
    "earnings_quality_score",
    "capital_efficiency_score",
    "forward_quality_score",
    "shareholder_quality_score",
    "technical_classification",
    "technical_confidence",
    "technical_version",
    "technical_stage",
    "technical_regime",
    "technical_leadership_score",
    "technical_vcp_score",
    "technical_climax_risk_score",
    "technical_flags",
    "technical_warnings",
    "technical_sub_tags",
    "dual_score",
    "combined_decision",
    "position_size_hint",
    "is_complete",
    "has_warning",
    "warning_flags",
    "sort_bucket",
    "has_fundamental",
    "has_technical",
    "ohlcv_status",
    "adjusted_bars",
    "trades_bars",
    "first_adjusted_date",
    "first_trades_date",
    "latest_adjusted_date",
    "latest_trades_date",
    "latest_bar_current",
    "ohlcv_reason",
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
    "model_version",
    "growth_quality_score",
    "profitability_quality_score",
    "fcf_quality_score",
    "earnings_quality_score",
    "capital_efficiency_score",
    "balance_sheet_quality_score",
    "valuation_quality_score",
    "forward_quality_score",
    "shareholder_quality_score",
    "liquidity_risk_score",
    "data_coverage_score",
    "v2_warning_flags",
    "missing_critical_fields",
    "missing_high_fields",
    "parse_failure_count",
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
    "technical_version",
    "stage",
    "market_regime",
    "leadership_score",
    "vcp_score",
    "vcp_detected",
    "box_breakout",
    "box_tightness_score",
    "breakout_quality_score",
    "box_width_pct",
    "box_age",
    "donchian_20_breakout",
    "donchian_55_breakout",
    "atr_percentile_252",
    "volume_percentile_252",
    "range_percentile_252",
    "extension_percentile_252",
    "climax_risk_score",
    "feature_flags",
    "warning_flags",
    "sub_tags",
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

FETCH_PLAN_HEADERS = [
    "run_id",
    "ticker",
    "what_to_show",
    "action",
    "duration",
    "bar_size",
    "contract_status",
    "current_bar_count",
    "required_bars",
    "first_bar_date",
    "latest_bar_date",
    "estimated_request_count",
    "reason",
]

FETCH_RESULTS_HEADERS = [
    "fetch_run_id",
    "run_id",
    "fetch_status",
    "ticker",
    "what_to_show",
    "action",
    "duration",
    "bar_size",
    "item_status",
    "current_bar_count",
    "fetched",
    "inserted",
    "updated",
    "revised",
    "unchanged",
    "attempt_count",
    "started_at",
    "completed_at",
    "reason",
    "error_message",
]

COVERAGE_HEADERS = [
    "ticker",
    "status",
    "adjusted_bars",
    "trades_bars",
    "first_adjusted_date",
    "latest_adjusted_date",
    "first_trades_date",
    "latest_trades_date",
    "has_price",
    "has_volume",
    "sufficient_history",
    "latest_bar_current",
    "reason",
]

MAPPING_HEADERS = [
    "raw_header",
    "canonical_field",
    "priority",
    "component",
    "used_in_scoring",
    "sample_value",
]


def export_run_csv(
    run: UploadRun,
    export_type: str,
    coverage: OhlcvCoverageSummary | None = None,
) -> str:
    if export_type == "combined":
        coverage_by_ticker = _coverage_by_ticker(coverage)
        fundamentals_by_ticker = _fundamentals_by_ticker(run.fundamental_scores)
        technicals_by_ticker = _technicals_by_ticker(run.technical_scores)
        return _write_csv(
            COMBINED_HEADERS,
            [
                _combined_row(
                    run.id,
                    result,
                    coverage_by_ticker.get(result.ticker.upper()),
                    fundamentals_by_ticker.get(result.ticker.upper()),
                    technicals_by_ticker.get(result.ticker.upper()),
                )
                for result in _sorted_combined(run.combined_results)
            ],
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


def export_fetch_plan_csv(plan: FetchPlan) -> str:
    return _write_csv(
        FETCH_PLAN_HEADERS,
        [_fetch_plan_row(plan.run_id, item) for item in plan.items],
    )


def export_fetch_results_csv(fetch_run: IBFetchRun | None) -> str:
    if not fetch_run:
        return _write_csv(FETCH_RESULTS_HEADERS, [])
    return _write_csv(
        FETCH_RESULTS_HEADERS,
        [_fetch_result_row(fetch_run, item) for item in _sorted_fetch_items(fetch_run.items)],
    )


def export_coverage_csv(coverage: OhlcvCoverageSummary) -> str:
    return _write_csv(COVERAGE_HEADERS, [_coverage_row(item) for item in coverage.items])


def export_mapping_csv(summary: ColumnMappingSummary) -> str:
    return _write_csv(MAPPING_HEADERS, [_mapping_row(item) for item in summary.items])


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


def _combined_row(
    run_id: int,
    result: CombinedResult,
    coverage: Any | None,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
) -> dict[str, Any]:
    technical_v4 = technical_v4_summary_fields(technical)
    return {
        "run_id": run_id,
        "rank": result.final_rank,
        "ticker": result.ticker,
        "company_name": result.company_name,
        "sector": result.sector,
        "final_score": result.final_score,
        "fundamental_score": result.fundamental_score,
        "fundamental_label": result.fundamental_label,
        "fundamental_model_version": fundamental.scoring_model_version if fundamental else "",
        "fundamental_data_coverage_score": fundamental.data_coverage_score if fundamental else "",
        "fundamental_warning_flags": _flags_text(
            fundamental.v2_warning_flags_json if fundamental else None
        ),
        "earnings_quality_score": fundamental.earnings_quality_score if fundamental else "",
        "capital_efficiency_score": fundamental.capital_efficiency_score if fundamental else "",
        "forward_quality_score": fundamental.forward_quality_score if fundamental else "",
        "shareholder_quality_score": fundamental.shareholder_quality_score if fundamental else "",
        "technical_classification": result.technical_classification,
        "technical_confidence": technical.technical_confidence if technical else "",
        **technical_v4,
        "dual_score": result.dual_score,
        "combined_decision": result.combined_decision,
        "position_size_hint": result.position_size_hint,
        "is_complete": result.is_complete,
        "has_warning": result.has_warning,
        "warning_flags": _warning_flags_text(result.warning_flags_json),
        "sort_bucket": result.sort_bucket,
        "has_fundamental": result.has_fundamental,
        "has_technical": result.has_technical,
        "ohlcv_status": coverage.status if coverage else "",
        "adjusted_bars": coverage.adjusted_bars if coverage else "",
        "trades_bars": coverage.trades_bars if coverage else "",
        "first_adjusted_date": coverage.first_adjusted_date if coverage else "",
        "first_trades_date": coverage.first_trades_date if coverage else "",
        "latest_adjusted_date": coverage.latest_adjusted_date if coverage else "",
        "latest_trades_date": coverage.latest_trades_date if coverage else "",
        "latest_bar_current": coverage.latest_bar_current if coverage else "",
        "ohlcv_reason": coverage.reason if coverage else "",
        "notes": result.notes,
    }


def _fundamental_row(run_id: int, score: FundamentalScore) -> dict[str, Any]:
    debug_json = score.debug_json or {}
    coverage = debug_json.get("coverage") if isinstance(debug_json.get("coverage"), dict) else {}
    parse_diagnostics = (
        debug_json.get("parse_diagnostics")
        if isinstance(debug_json.get("parse_diagnostics"), dict)
        else {}
    )
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
        "trap_flags": _flags_text(score.trap_flags_json),
        "model_version": score.scoring_model_version or debug_json.get("model_version"),
        "growth_quality_score": score.growth_quality_score,
        "profitability_quality_score": score.profitability_quality_score,
        "fcf_quality_score": score.fcf_quality_score,
        "earnings_quality_score": score.earnings_quality_score,
        "capital_efficiency_score": score.capital_efficiency_score,
        "balance_sheet_quality_score": score.balance_sheet_quality_score,
        "valuation_quality_score": score.valuation_quality_score,
        "forward_quality_score": score.forward_quality_score,
        "shareholder_quality_score": score.shareholder_quality_score,
        "liquidity_risk_score": score.liquidity_risk_score,
        "data_coverage_score": score.data_coverage_score,
        "v2_warning_flags": _flags_text(score.v2_warning_flags_json),
        "missing_critical_fields": _list_text(coverage.get("missing_core_fields")),
        "missing_high_fields": _list_text(coverage.get("missing_high_fields")),
        "parse_failure_count": parse_diagnostics.get("failed_field_count", ""),
        "explanation": score.explanation,
    }


def _technical_row(run_id: int, score: TechnicalScore) -> dict[str, Any]:
    technical_v4 = technical_v4_detail_fields(score)
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
        **technical_v4,
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


def _fetch_plan_row(run_id: int | None, item: Any) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "ticker": item.ticker,
        "what_to_show": item.what_to_show,
        "action": item.action.value,
        "duration": item.duration,
        "bar_size": item.bar_size,
        "contract_status": item.contract_status,
        "current_bar_count": item.current_bar_count,
        "required_bars": item.required_bars,
        "first_bar_date": item.first_bar_date,
        "latest_bar_date": item.latest_bar_date,
        "estimated_request_count": item.estimated_request_count,
        "reason": item.reason,
    }


def _fetch_result_row(fetch_run: IBFetchRun, item: Any) -> dict[str, Any]:
    return {
        "fetch_run_id": fetch_run.id,
        "run_id": fetch_run.run_id,
        "fetch_status": fetch_run.status,
        "ticker": item.ticker,
        "what_to_show": item.what_to_show,
        "action": item.action,
        "duration": item.duration,
        "bar_size": item.bar_size,
        "item_status": item.status,
        "current_bar_count": item.current_bar_count,
        "fetched": item.fetched,
        "inserted": item.inserted,
        "updated": item.updated,
        "revised": item.revised,
        "unchanged": item.unchanged,
        "attempt_count": item.attempt_count,
        "started_at": item.started_at,
        "completed_at": item.completed_at,
        "reason": item.reason,
        "error_message": item.error_message,
    }


def _coverage_row(item: Any) -> dict[str, Any]:
    return {
        "ticker": item.ticker,
        "status": item.status,
        "adjusted_bars": item.adjusted_bars,
        "trades_bars": item.trades_bars,
        "first_adjusted_date": item.first_adjusted_date,
        "latest_adjusted_date": item.latest_adjusted_date,
        "first_trades_date": item.first_trades_date,
        "latest_trades_date": item.latest_trades_date,
        "has_price": item.has_price,
        "has_volume": item.has_volume,
        "sufficient_history": item.sufficient_history,
        "latest_bar_current": item.latest_bar_current,
        "reason": item.reason,
    }


def _mapping_row(item: Any) -> dict[str, Any]:
    return {
        "raw_header": item.raw_header,
        "canonical_field": item.canonical_field,
        "priority": item.priority,
        "component": item.component,
        "used_in_scoring": item.used_in_scoring,
        "sample_value": item.sample_value,
    }


def _coverage_by_ticker(coverage: OhlcvCoverageSummary | None) -> dict[str, Any]:
    if not coverage:
        return {}
    return {item.ticker.upper(): item for item in coverage.items}


def _fundamentals_by_ticker(scores: list[FundamentalScore]) -> dict[str, FundamentalScore]:
    return {score.ticker.upper(): score for score in scores}


def _technicals_by_ticker(scores: list[TechnicalScore]) -> dict[str, TechnicalScore]:
    return {score.ticker.upper(): score for score in scores}


def _sorted_combined(results: list[CombinedResult]) -> list[CombinedResult]:
    return sorted(results, key=cockpit_sort_key)


def _sorted_raw(rows: list[RawCompanyRow]) -> list[RawCompanyRow]:
    return sorted(rows, key=lambda row: row.row_number)


def _sorted_by_ticker(rows: list[Any]) -> list[Any]:
    return sorted(rows, key=lambda row: row.ticker)


def _sorted_fetch_items(rows: list[Any]) -> list[Any]:
    return sorted(rows, key=lambda row: (row.ticker, row.what_to_show))


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _flags_text(flags_json: dict[str, Any] | None) -> str:
    if not flags_json:
        return ""
    return _list_text(flags_json.get("flags"))


def _warning_flags_text(flags: list[str] | None) -> str:
    return _list_text(flags)


def _list_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "; ".join(str(value) for value in values)
