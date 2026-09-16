import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, selectinload

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    RawCompanyRow,
    TechnicalScore,
    WinnerPredictionSnapshot,
)
from app.services.calculation_identity import (
    PIPELINE_CONTEXT_COMPATIBILITY,
    CalculationIdentity,
    CalculationIdentityCompatibilityValidator,
)
from app.services.cockpit_sorting import cockpit_sort_key
from app.services.combined_ranking_identity import (
    COMBINED_INPUT_COMPATIBILITY,
    build_combined_result_identity,
    cohort_identity_fingerprint,
    embed_calculation_identity,
    fundamental_score_identity,
    require_fundamental_raw_source,
    require_source_inputs,
    technical_score_identity,
)
from app.services.confidence_service import build_combined_warning_flags
from app.services.core_calculation_evidence import CoreEvidenceKind, persist_core_evidence
from app.services.earnings_date_parser import MISSING_EARNINGS_DATE_VALUES
from app.services.earnings_risk_service import (
    EarningsRiskResult,
    calculate_earnings_risk,
    current_local_date,
)
from app.services.market_calculation_context_service import (
    calculation_identity_from_market_context,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.technical_consumer_eligibility import (
    TECHNICAL_ELIGIBILITY_KEY,
    TECHNICAL_TO_COMBINED,
    technical_decision_input,
)
from app.services.warning_flag_service import warning_flags_for_row

logger = logging.getLogger(__name__)

DANGER_CLASSIFICATIONS = {
    "Distribution risk",
    "Blowoff top",
    "Failed breakout",
    "Climax reversal risk",
    "Late-stage extension",
}
BUYABLE_CLASSIFICATIONS = {
    "Prime clean pullback",
    "Clean bull pullback",
    "Fresh breakout",
    "Volatility contraction setup",
    "Tight base breakout",
    "RS leader pullback",
}
DECISION_ORDER = {
    "Avoid": 1,
    "Watchlist": 2,
    "Candidate": 3,
    "Strong candidate": 4,
}
FUNDAMENTAL_RISK_LABEL_CAPS = {
    "Growth trap risk": "Candidate",
    "Quality risk": "Candidate",
}
EARNINGS_DATE_RAW_KEYS = {
    "upcoming earnings date",
    "earnings date",
    "next earnings date",
    "earnings",
    "upcoming_earnings_date",
    "earnings_date",
    "next_earnings_date",
}
COMBINED_DECISION_CALCULATION_VERSION = "combined-decision-1.0.0"


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
    upcoming_earnings_date: date | None
    days_until_earnings: int | None
    earnings_risk_level: str | None
    earnings_warning_flags: list[str]
    notes: str
    warning_flags: list[str]
    is_complete: bool
    has_warning: bool
    has_fundamental: bool
    has_technical: bool
    sort_bucket: int
    debug_evidence: dict[str, Any]


def refresh_combined_results(
    db: Session,
    run_id: int,
    *,
    market_cutoff: MarketCalculationCutoff | None = None,
    pipeline_run_id: int | None = None,
) -> list[CombinedResult]:
    rows = _rows_for_run(db, run_id)
    fundamentals = {score.ticker.upper(): score for score in _fundamentals_for_run(db, run_id)}
    technicals = {score.ticker.upper(): score for score in _technicals_for_run(db, run_id)}

    config = _load_scoring_config()
    validated: list[
        tuple[
            RawCompanyRow,
            FundamentalScore,
            TechnicalScore,
            CalculationIdentity,
            CalculationIdentity,
        ]
    ] = []
    for row in _unique_rows(rows):
        ticker = row.ticker.upper()
        fundamental = fundamentals.get(ticker)
        technical = technicals.get(ticker)
        if fundamental is None or technical is None:
            raise ValueError(
                "CALCULATION_IDENTITY_REJECTED: consumer=CombinedResult "
                f"producer=FundamentalScore+TechnicalScore ticker={ticker} "
                "result=INSUFFICIENT_IDENTITY details=required source artifact is absent"
            )
        fundamental_identity = fundamental_score_identity(fundamental)
        technical_identity = technical_score_identity(technical)
        require_fundamental_raw_source(
            fundamental_identity,
            row,
            consumer="CombinedResult",
        )
        compatibility = require_source_inputs(
            fundamental_identity,
            technical_identity,
            policy=COMBINED_INPUT_COMPATIBILITY,
            consumer="CombinedResult",
            left_producer="FundamentalScore",
            right_producer="TechnicalScore",
        )
        _validate_explicit_pipeline_context(
            source_identity=technical_identity,
            run_id=run_id,
            ticker=ticker,
            market_cutoff=market_cutoff,
            pipeline_run_id=pipeline_run_id,
        )
        logger.debug(
            "combined input calculation identity compatible",
            extra={
                "calculation_identity_policy": compatibility.policy,
                "calculation_identity_result": compatibility.status.value,
                "calculation_identity_left_fingerprint": compatibility.left_fingerprint,
                "calculation_identity_right_fingerprint": compatibility.right_fingerprint,
            },
        )
        validated.append((row, fundamental, technical, fundamental_identity, technical_identity))

    _require_single_combined_context(validated)

    cohort_fingerprint = cohort_identity_fingerprint(
        identity
        for _, _, _, fundamental_identity, technical_identity in validated
        for identity in (fundamental_identity, technical_identity)
    )
    decisions_with_identity: list[tuple[CombinedDecision, CalculationIdentity]] = []
    for row, fundamental, technical, fundamental_identity, technical_identity in validated:
        decision = combine_row_decision(
            row,
            fundamental,
            technical,
            config=config,
            today=technical_identity.temporal.as_of_session.value,
        )
        output_identity = build_combined_result_identity(
            fundamental_identity=fundamental_identity,
            technical_identity=technical_identity,
            fundamental_score=fundamental,
            technical_score=technical,
            config=config,
            calculation_version=COMBINED_DECISION_CALCULATION_VERSION,
            cohort_fingerprint=cohort_fingerprint,
        )
        decisions_with_identity.append((decision, output_identity))
    decisions_with_identity.sort(key=lambda item: cockpit_sort_key(item[0]))

    results = [
        _to_model(
            run_id=run_id,
            final_rank=index,
            decision=decision,
            calculation_identity=identity,
        )
        for index, (decision, identity) in enumerate(decisions_with_identity, start=1)
    ]
    db.execute(
        update(WinnerPredictionSnapshot)
        .where(WinnerPredictionSnapshot.run_id == run_id)
        .where(WinnerPredictionSnapshot.combined_result_id.is_not(None))
        .values(combined_result_id=None)
    )
    db.execute(delete(CombinedResult).where(CombinedResult.run_id == run_id))
    db.add_all(results)
    db.flush()
    if isinstance(db, Session):
        for result in results:
            persist_core_evidence(
                db,
                kind=CoreEvidenceKind.COMBINED,
                current_row=result,
                sources={
                    "fundamental": fundamentals[result.ticker.upper()],
                    "technical": technicals[result.ticker.upper()],
                },
            )
    return results


def combine_row_decision(
    row: RawCompanyRow,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    config: dict[str, Any] | None = None,
    today: date | None = None,
) -> CombinedDecision:
    config = config or _load_scoring_config()
    weights = config["combined_score"]
    penalties = config["penalties"]
    labels = config["labels"]

    diagnostic_technical = technical
    technical, eligibility = technical_decision_input(technical, TECHNICAL_TO_COMBINED)

    fundamental_score = _float_or_none(fundamental.fundamental_score if fundamental else None)
    dual_score = _float_or_none(technical.dual_score if technical else None)
    technical_classification = technical.classification if technical else None
    fundamental_label = fundamental.fundamental_label if fundamental else None

    final_score = _weighted_available_score(
        fundamental_score=fundamental_score,
        dual_score=dual_score,
        fundamental_weight=float(weights["fundamental_score"]),
        dual_weight=float(weights["dual_score"]),
    )
    weighted_score_before_penalties = final_score
    penalty_breakdown: dict[str, float] = {}
    notes: list[str] = []

    if fundamental_score is None:
        penalty = float(penalties["missing_data"])
        final_score -= penalty
        penalty_breakdown["missing_fundamental"] = penalty
        notes.append("fundamental missing")
    if dual_score is None:
        penalty = float(penalties["missing_data"])
        final_score -= penalty
        penalty_breakdown["missing_technical"] = penalty
        notes.append("technical missing")

    if technical_classification in DANGER_CLASSIFICATIONS:
        penalty = float(penalties["danger_classification"])
        final_score -= penalty
        penalty_breakdown["danger_classification"] = penalty
        notes.append(technical_classification.lower())
    elif technical_classification == "Overheated momentum":
        penalty = float(penalties["overheated_momentum"])
        final_score -= penalty
        penalty_breakdown["overheated_momentum"] = penalty
        notes.append("overheated")

    if fundamental_label == "Value trap risk":
        penalty = float(penalties["value_trap_risk"])
        final_score -= penalty
        penalty_breakdown["value_trap_risk"] = penalty
        notes.append("value trap")
    elif fundamental_label == "Growth trap risk":
        penalty = float(penalties["growth_trap_risk"])
        final_score -= penalty
        penalty_breakdown["growth_trap_risk"] = penalty
        notes.append("growth trap")
    elif fundamental_label == "Quality risk":
        penalty = float(penalties["quality_risk"])
        final_score -= penalty
        penalty_breakdown["quality_risk"] = penalty
        notes.append("quality risk")

    if technical and _liquidity_warning(technical.debug_json):
        penalty = float(penalties["liquidity_warning"])
        final_score -= penalty
        penalty_breakdown["liquidity_warning"] = penalty
        notes.append("liquidity warning")

    earnings_risk = _calculate_row_earnings_risk(row, config, today)
    final_score -= earnings_risk.penalty
    if earnings_risk.penalty:
        penalty_breakdown[f"earnings_{earnings_risk.risk_level or 'unknown'}"] = (
            earnings_risk.penalty
        )
    if earnings_risk.warning_flags:
        notes.append(earnings_risk.message)

    final_score = _clamp(final_score)
    decision = _decision_label(
        final_score=final_score,
        labels=labels,
        has_fundamental=fundamental_score is not None,
        has_technical=dual_score is not None,
        technical_classification=technical_classification,
        fundamental_label=fundamental_label,
    )
    if earnings_risk.decision_blocked and config.get("earnings_risk_gate", {}).get(
        "block_new_entries", True
    ):
        decision = "Blocked by earnings gate"

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
    warning_flags = _merge_warning_flags(warnings.flags, earnings_risk.warning_flags)
    if technical is None and diagnostic_technical is not None:
        warning_flags = _merge_warning_flags(
            warning_flags, tuple(warning_flags_for_row(None, diagnostic_technical))
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
        upcoming_earnings_date=earnings_risk.upcoming_earnings_date,
        days_until_earnings=earnings_risk.days_until_earnings,
        earnings_risk_level=earnings_risk.risk_level,
        earnings_warning_flags=list(earnings_risk.warning_flags),
        notes=", ".join(notes) if notes else "aligned",
        warning_flags=warning_flags,
        is_complete=warnings.is_complete,
        has_warning=bool(warning_flags),
        has_fundamental=warnings.has_fundamental,
        has_technical=warnings.has_technical,
        sort_bucket=warnings.sort_bucket,
        debug_evidence={**_combined_debug_evidence(
            row=row,
            fundamental=fundamental,
            technical=technical,
            config=config,
            weighted_score_before_penalties=weighted_score_before_penalties,
            penalty_breakdown=penalty_breakdown,
            earnings_risk=earnings_risk,
            final_score=final_score,
            decision=decision,
            position_size=position_size,
            warning_flags=warning_flags,
            sort_bucket=warnings.sort_bucket,
        ), TECHNICAL_ELIGIBILITY_KEY: eligibility,
            "diagnostic_technical_score_id": getattr(diagnostic_technical, "id", None)},
    )


def reconstruct_combined_score_from_debug(debug_json: dict[str, Any]) -> float:
    score = float(debug_json["weighted_score_before_penalties"])
    penalties = debug_json.get("penalty_breakdown") or {}
    score -= sum(float(value) for value in penalties.values())
    return _clamp(score)


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
    decision = "Avoid"
    if final_score >= float(labels["strong_candidate_min_score"]):
        decision = "Strong candidate"
    elif final_score >= float(labels["candidate_min_score"]):
        decision = "Candidate"
    elif final_score >= float(labels["watch_min_score"]):
        decision = "Watchlist"
    return _cap_decision_for_fundamental_label(decision, fundamental_label)


def _cap_decision_for_fundamental_label(decision: str, fundamental_label: str | None) -> str:
    max_decision = FUNDAMENTAL_RISK_LABEL_CAPS.get(str(fundamental_label))
    if max_decision is None:
        return decision
    if DECISION_ORDER.get(decision, 0) <= DECISION_ORDER[max_decision]:
        return decision
    return max_decision


def _position_size_hint(
    decision: str,
    technical_classification: str | None,
    technical: TechnicalScore | None,
) -> str:
    risk_score = _float_or_none(technical.risk_score if technical else None)
    if decision == "Blocked by earnings gate":
        return "No new entry"
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
    calculation_identity: CalculationIdentity | None = None,
) -> CombinedResult:
    debug_json = decision.debug_evidence
    if calculation_identity is not None:
        debug_json = embed_calculation_identity(
            debug_json,
            calculation_identity,
            policy=COMBINED_INPUT_COMPATIBILITY.name,
        )
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
        upcoming_earnings_date=decision.upcoming_earnings_date,
        days_until_earnings=decision.days_until_earnings,
        earnings_risk_level=decision.earnings_risk_level,
        earnings_warning_flags_json=decision.earnings_warning_flags,
        notes=decision.notes,
        warning_flags_json=decision.warning_flags,
        is_complete=decision.is_complete,
        has_fundamental=decision.has_fundamental,
        has_technical=decision.has_technical,
        has_warning=decision.has_warning,
        sort_bucket=decision.sort_bucket,
        calculation_version=COMBINED_DECISION_CALCULATION_VERSION,
        config_hash=decision.debug_evidence["config_hash"],
        debug_json=debug_json,
    )


def _validate_explicit_pipeline_context(
    *,
    source_identity: CalculationIdentity,
    run_id: int,
    ticker: str,
    market_cutoff: MarketCalculationCutoff | None,
    pipeline_run_id: int | None,
) -> None:
    if market_cutoff is None and pipeline_run_id is None:
        return
    if market_cutoff is None or pipeline_run_id is None:
        raise ValueError(
            "Combined identity validation requires market_cutoff and pipeline_run_id together"
        )
    expected = calculation_identity_from_market_context(
        market_cutoff,
        run_id=run_id,
        pipeline_id=pipeline_run_id,
        ticker=ticker,
    )
    compatibility = CalculationIdentityCompatibilityValidator.compare(
        expected,
        source_identity,
        policy=PIPELINE_CONTEXT_COMPATIBILITY,
    )
    if not compatibility.accepted:
        raise ValueError(
            "CALCULATION_IDENTITY_REJECTED: consumer=CombinedResult "
            f"producer=PipelineContext {compatibility.diagnostic()}"
        )


def _require_single_combined_context(
    validated: list[
        tuple[
            RawCompanyRow,
            FundamentalScore,
            TechnicalScore,
            CalculationIdentity,
            CalculationIdentity,
        ]
    ],
) -> None:
    if not validated:
        return
    expected = validated[0][4]
    for row, _, _, _, actual in validated[1:]:
        compatibility = CalculationIdentityCompatibilityValidator.compare(
            expected,
            actual,
            policy=PIPELINE_CONTEXT_COMPATIBILITY,
        )
        if not compatibility.accepted:
            raise ValueError(
                "CALCULATION_IDENTITY_REJECTED: consumer=CombinedResult "
                f"producer=TechnicalScore ticker={row.ticker.upper()} "
                f"{compatibility.diagnostic()}"
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
    return list(db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id)))


def _technicals_for_run(db: Session, run_id: int) -> list[TechnicalScore]:
    return list(db.scalars(select(TechnicalScore).options(
        selectinload(TechnicalScore.calculation_evidence)
    ).where(TechnicalScore.run_id == run_id)))


def _load_scoring_config(path: Path = Path("config/scoring_weights.yaml")) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _combined_debug_evidence(
    *,
    row: RawCompanyRow,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    config: dict[str, Any],
    weighted_score_before_penalties: float,
    penalty_breakdown: dict[str, float],
    earnings_risk: EarningsRiskResult,
    final_score: float,
    decision: str,
    position_size: str,
    warning_flags: list[str],
    sort_bucket: int,
) -> dict[str, Any]:
    return {
        "calculation_version": COMBINED_DECISION_CALCULATION_VERSION,
        "config_hash": _stable_hash(config),
        "config_snapshot": config,
        "source_ids": {
            "raw_row_id": getattr(row, "id", None),
            "fundamental_score_id": getattr(fundamental, "id", None),
            "technical_score_id": getattr(technical, "id", None),
        },
        "input_scores": {
            "fundamental_score": _float_or_none(
                fundamental.fundamental_score if fundamental else None
            ),
            "dual_score": _float_or_none(technical.dual_score if technical else None),
            "technical_classification": technical.classification if technical else None,
            "fundamental_label": fundamental.fundamental_label if fundamental else None,
        },
        "weights": dict(config.get("combined_score") or {}),
        "label_thresholds": dict(config.get("labels") or {}),
        "weighted_score_before_penalties": round(weighted_score_before_penalties, 6),
        "penalty_breakdown": {key: round(value, 6) for key, value in penalty_breakdown.items()},
        "earnings": {
            "upcoming_earnings_date": (
                earnings_risk.upcoming_earnings_date.isoformat()
                if earnings_risk.upcoming_earnings_date
                else None
            ),
            "days_until_earnings": earnings_risk.days_until_earnings,
            "risk_level": earnings_risk.risk_level,
            "warning_flags": list(earnings_risk.warning_flags),
            "decision_blocked": earnings_risk.decision_blocked,
            "penalty": earnings_risk.penalty,
        },
        "final_score": final_score,
        "combined_decision": decision,
        "position_size_hint": position_size,
        "warning_flags": list(warning_flags),
        "sort_bucket": sort_bucket,
    }


def _calculate_row_earnings_risk(
    row: RawCompanyRow,
    config: dict[str, Any],
    today: date | None,
) -> EarningsRiskResult:
    gate_config = config.get("earnings_risk_gate")
    if gate_config is None:
        gate_config = {"enabled": False}

    return calculate_earnings_risk(
        upcoming_earnings_date=row.upcoming_earnings_date,
        raw_value_present=_raw_earnings_value_present(row.raw_json),
        today=today or current_local_date(),
        config=gate_config,
    )


def _raw_earnings_value_present(raw_json: dict[str, Any]) -> bool:
    for key, value in raw_json.items():
        if str(key).strip().casefold() not in EARNINGS_DATE_RAW_KEYS:
            continue
        if value is None:
            return False
        text = str(value).strip()
        return text.casefold() not in MISSING_EARNINGS_DATE_VALUES
    return False


def _merge_warning_flags(existing: list[str], extra: tuple[str, ...]) -> list[str]:
    flags = list(existing)
    seen = set(flags)
    for flag in extra:
        if flag not in seen:
            flags.append(flag)
            seen.add(flag)
    return flags


def _stable_hash(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


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
