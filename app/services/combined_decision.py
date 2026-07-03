from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
)
from app.services.cockpit_sorting import cockpit_sort_key
from app.services.confidence_service import build_combined_warning_flags

DANGER_CLASSIFICATIONS = {
    "Distribution risk",
    "Blowoff top",
    "Failed breakout",
}
BUYABLE_CLASSIFICATIONS = {
    "Prime clean pullback",
    "Clean bull pullback",
    "Fresh breakout",
}


@dataclass(frozen=True)
class CombinedDecision:
    ticker: str
    company_name: str | None
    sector: str | None
    final_score: float
    fundamental_score: float | None
    fundamental_label: str | None
    technical_classification: str | None
    dual_score: float | None
    combined_decision: str
    position_size_hint: str
    notes: str
    warning_flags: list[str]
    is_complete: bool
    has_warning: bool
    has_fundamental: bool
    has_technical: bool
    sort_bucket: int


def refresh_combined_results(db: Session, run_id: int) -> list[CombinedResult]:
    rows = _rows_for_run(db, run_id)
    fundamentals = {
        score.ticker.upper(): score for score in _fundamentals_for_run(db, run_id)
    }
    technicals = {
        score.ticker.upper(): score for score in _technicals_for_run(db, run_id)
    }

    decisions = [
        combine_row_decision(
            row,
            fundamentals.get(row.ticker.upper()),
            technicals.get(row.ticker.upper()),
        )
        for row in _unique_rows(rows)
    ]
    decisions = sorted(decisions, key=cockpit_sort_key)

    db.execute(delete(CombinedResult).where(CombinedResult.run_id == run_id))
    results = [
        _to_model(run_id=run_id, final_rank=index, decision=decision)
        for index, decision in enumerate(decisions, start=1)
    ]
    db.add_all(results)
    db.flush()
    return results


def combine_row_decision(
    row: RawCompanyRow,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    config: dict[str, Any] | None = None,
) -> CombinedDecision:
    config = config or _load_scoring_config()
    weights = config["combined_score"]
    penalties = config["penalties"]
    labels = config["labels"]

    fundamental_score = _float_or_none(
        fundamental.fundamental_score if fundamental else None
    )
    dual_score = _float_or_none(technical.dual_score if technical else None)
    technical_classification = technical.classification if technical else None
    fundamental_label = fundamental.fundamental_label if fundamental else None

    final_score = _weighted_available_score(
        fundamental_score=fundamental_score,
        dual_score=dual_score,
        fundamental_weight=float(weights["fundamental_score"]),
        dual_weight=float(weights["dual_score"]),
    )
    notes: list[str] = []

    if fundamental_score is None:
        final_score -= float(penalties["missing_data"])
        notes.append("fundamental missing")
    if dual_score is None:
        final_score -= float(penalties["missing_data"])
        notes.append("technical missing")

    if technical_classification in DANGER_CLASSIFICATIONS:
        final_score -= float(penalties["danger_classification"])
        notes.append(technical_classification.lower())
    elif technical_classification == "Overheated momentum":
        final_score -= float(penalties["overheated_momentum"])
        notes.append("overheated")

    if fundamental_label == "Value trap risk":
        final_score -= float(penalties["value_trap_risk"])
        notes.append("value trap")
    elif fundamental_label == "Growth trap risk":
        final_score -= float(penalties["growth_trap_risk"])
        notes.append("growth trap")

    if technical and _liquidity_warning(technical.debug_json):
        final_score -= float(penalties["liquidity_warning"])
        notes.append("liquidity warning")

    final_score = _clamp(final_score)
    decision = _decision_label(
        final_score=final_score,
        labels=labels,
        has_fundamental=fundamental_score is not None,
        has_technical=dual_score is not None,
        technical_classification=technical_classification,
        fundamental_label=fundamental_label,
    )
    position_size = _position_size_hint(
        decision=decision,
        technical_classification=technical_classification,
        technical=technical,
    )
    warnings = build_combined_warning_flags(
        fundamental=fundamental,
        technical=technical,
        decision=decision,
    )

    return CombinedDecision(
        ticker=row.ticker.upper(),
        company_name=row.company_name,
        sector=row.sector,
        final_score=final_score,
        fundamental_score=fundamental_score,
        fundamental_label=fundamental_label,
        technical_classification=technical_classification,
        dual_score=dual_score,
        combined_decision=decision,
        position_size_hint=position_size,
        notes=", ".join(notes) if notes else "aligned",
        warning_flags=warnings.flags,
        is_complete=warnings.is_complete,
        has_warning=warnings.has_warning,
        has_fundamental=warnings.has_fundamental,
        has_technical=warnings.has_technical,
        sort_bucket=warnings.sort_bucket,
    )


def _weighted_available_score(
    fundamental_score: float | None,
    dual_score: float | None,
    fundamental_weight: float,
    dual_weight: float,
) -> float:
    total = 0.0
    weight = 0.0
    if fundamental_score is not None:
        total += fundamental_score * fundamental_weight
        weight += fundamental_weight
    if dual_score is not None:
        total += dual_score * dual_weight
        weight += dual_weight
    return total / weight if weight else 0.0


def _decision_label(
    final_score: float,
    labels: dict[str, Any],
    has_fundamental: bool,
    has_technical: bool,
    technical_classification: str | None,
    fundamental_label: str | None,
) -> str:
    if not has_fundamental or not has_technical:
        return "Incomplete data"
    if technical_classification in DANGER_CLASSIFICATIONS:
        return "Avoid"
    if fundamental_label == "Value trap risk":
        return "Avoid"
    if final_score >= float(labels["strong_candidate_min_score"]):
        return "Strong candidate"
    if final_score >= float(labels["candidate_min_score"]):
        return "Candidate"
    if final_score >= float(labels["watch_min_score"]):
        return "Watchlist"
    return "Avoid"


def _position_size_hint(
    decision: str,
    technical_classification: str | None,
    technical: TechnicalScore | None,
) -> str:
    risk_score = _float_or_none(technical.risk_score if technical else None)
    if decision == "Incomplete data":
        return "Wait"
    if decision == "Avoid":
        return "Avoid"
    if (
        decision == "Strong candidate"
        and technical_classification in BUYABLE_CLASSIFICATIONS
        and (risk_score is None or risk_score <= 3.5)
    ):
        return "Full starter"
    if decision in {"Strong candidate", "Candidate"}:
        return "Half starter"
    return "Small probe"


def _to_model(
    run_id: int,
    final_rank: int,
    decision: CombinedDecision,
) -> CombinedResult:
    return CombinedResult(
        run_id=run_id,
        ticker=decision.ticker,
        company_name=decision.company_name,
        sector=decision.sector,
        final_rank=final_rank,
        final_score=_to_decimal(decision.final_score),
        fundamental_score=_to_decimal(decision.fundamental_score),
        fundamental_label=decision.fundamental_label,
        technical_classification=decision.technical_classification,
        dual_score=_to_decimal(decision.dual_score),
        combined_decision=decision.combined_decision,
        position_size_hint=decision.position_size_hint,
        notes=decision.notes,
    )


def _unique_rows(rows: list[RawCompanyRow]) -> list[RawCompanyRow]:
    seen: set[str] = set()
    unique: list[RawCompanyRow] = []
    for row in rows:
        ticker = row.ticker.upper()
        if ticker not in seen:
            seen.add(ticker)
            unique.append(row)
    return unique


def _rows_for_run(db: Session, run_id: int) -> list[RawCompanyRow]:
    return list(
        db.scalars(
            select(RawCompanyRow)
            .where(RawCompanyRow.run_id == run_id)
            .order_by(RawCompanyRow.row_number)
        )
    )


def _fundamentals_for_run(db: Session, run_id: int) -> list[FundamentalScore]:
    return list(
        db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id))
    )


def _technicals_for_run(db: Session, run_id: int) -> list[TechnicalScore]:
    return list(db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id)))


def _load_scoring_config(path: Path = Path("config/scoring_weights.yaml")) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _liquidity_warning(debug_json: dict[str, Any] | None) -> bool:
    if not debug_json:
        return False
    derived = debug_json.get("derived")
    return bool(derived and derived.get("liquidity_warning"))


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _to_decimal(value: float | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(round(float(value), 4)))


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(value, 4)))
