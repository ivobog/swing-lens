import csv
from collections.abc import Iterable
from io import StringIO
from typing import Any

from sqlalchemy.orm import Session

from app.models.tables import RankingResult
from app.services.ranking_profile_service import (
    get_all_ranking_results,
    get_ranking_results,
)

RANKING_RESULT_HEADERS = [
    "rank",
    "ticker",
    "company_name",
    "sector",
    "profile_name",
    "profile_label",
    "profile_score",
    "technical_profile_score",
    "fundamental_score",
    "base_technical_score",
    "technical_classification",
    "fundamental_label",
    "decision",
    "position_size_hint",
    "notes",
    "warning_flags",
    "earnings_date",
    "days_until_earnings",
    "earnings_risk",
]


def export_ranking_profile_csv(
    db: Session,
    run_id: int,
    profile_name: str,
) -> str:
    return _rows_to_csv(get_ranking_results(db, run_id, profile_name))


def export_all_ranking_profiles_csv(db: Session, run_id: int) -> str:
    return _rows_to_csv(get_all_ranking_results(db, run_id))


def _rows_to_csv(rows: Iterable[RankingResult]) -> str:
    return _write_csv(RANKING_RESULT_HEADERS, [_ranking_result_row(row) for row in rows])


def _ranking_result_row(result: RankingResult) -> dict[str, Any]:
    return {
        "rank": result.profile_rank,
        "ticker": result.ticker,
        "company_name": result.company_name,
        "sector": result.sector,
        "profile_name": result.ranking_profile,
        "profile_label": result.ranking_label,
        "profile_score": result.profile_score,
        "technical_profile_score": result.technical_profile_score,
        "fundamental_score": result.fundamental_score,
        "base_technical_score": result.base_technical_score,
        "technical_classification": result.technical_classification,
        "fundamental_label": result.fundamental_label,
        "decision": result.decision_label,
        "position_size_hint": result.position_size_hint,
        "notes": result.notes,
        "warning_flags": _list_text(result.warning_flags_json),
        "earnings_date": result.upcoming_earnings_date,
        "days_until_earnings": result.days_until_earnings,
        "earnings_risk": result.earnings_risk_level,
    }


def _write_csv(headers: list[str], rows: Iterable[dict[str, Any]]) -> str:
    buffer = StringIO()
    writer = csv.DictWriter(buffer, fieldnames=headers, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in headers})
    return buffer.getvalue()


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    return value


def _list_text(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    return "; ".join(str(value) for value in values)
