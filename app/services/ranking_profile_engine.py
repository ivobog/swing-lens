from dataclasses import dataclass, replace
from datetime import date
from typing import Any

from app.models.tables import FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.combined_decision import (
    BUYABLE_CLASSIFICATIONS,
    _calculate_row_earnings_risk,
)
from app.services.confidence_service import build_combined_warning_flags
from app.services.ranking_profile_components import (
    calculate_technical_profile_score,
    extract_technical_components,
)
from app.services.ranking_profile_config import RankingProfileConfig
from app.services.ranking_profile_gates import apply_profile_gates
from app.services.ranking_profile_penalties import calculate_profile_penalties

RANKING_ENGINE_VERSION = "1.1.0"


@dataclass(frozen=True)
class RankingProfileDecision:
    ticker: str
    raw_row_id: int | None
    company_name: str | None
    sector: str | None
    ranking_profile: str
    ranking_label: str
    profile_rank: int
    profile_score: float
    technical_profile_score: float | None
    fundamental_score: float | None
    base_technical_score: float | None
    technical_classification: str | None
    fundamental_label: str | None
    decision_label: str
    position_size_hint: str | None
    notes: list[str]
    warning_flags: list[str]
    penalties: dict[str, float]
    gates: dict[str, Any]
    component_scores: dict[str, float]
    debug: dict[str, Any]
    upcoming_earnings_date: date | None
    days_until_earnings: int | None
    earnings_risk_level: str | None
    is_complete: bool
    has_warning: bool
    has_fundamental: bool
    has_technical: bool
    sort_bucket: int


def rank_profile(
    *,
    profile: RankingProfileConfig,
    rows: list[RawCompanyRow],
    fundamentals: dict[str, FundamentalScore],
    technicals: dict[str, TechnicalScore],
    config: dict[str, Any],
    today: date | None = None,
    liquidity_features: dict[str, Any] | None = None,
) -> list[RankingProfileDecision]:
    liquidity_features = liquidity_features or {}
    decisions = [
        rank_single_row(
            profile=profile,
            row=row,
            fundamental=fundamentals.get(row.ticker.upper()),
            technical=technicals.get(row.ticker.upper()),
            config=config,
            today=today,
            liquidity_feature=liquidity_features.get(row.ticker.upper()),
        )
        for row in _unique_rows(rows)
    ]
    ranked = sorted(decisions, key=ranking_sort_key)
    return [replace(decision, profile_rank=index) for index, decision in enumerate(ranked, start=1)]


def rank_single_row(
    *,
    profile: RankingProfileConfig,
    row: RawCompanyRow,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    config: dict[str, Any],
    today: date | None = None,
    liquidity_feature: Any | None = None,
) -> RankingProfileDecision:
    fundamental_score = _float_or_none(fundamental.fundamental_score if fundamental else None)
    base_technical_score = _float_or_none(technical.dual_score if technical else None)
    component_scores = extract_technical_components(technical)
    technical_profile_score = calculate_technical_profile_score(
        component_scores,
        profile.technical_components,
    )
    weighted_score = calculate_profile_score(
        technical_profile_score=technical_profile_score,
        fundamental_score=fundamental_score,
        profile=profile,
    )
    earnings_risk = _calculate_row_earnings_risk(row, config, today)
    penalty_result = calculate_profile_penalties(
        profile=profile,
        row=row,
        fundamental=fundamental,
        technical=technical,
        component_scores=component_scores,
        earnings_risk=earnings_risk,
    )
    tradeability_penalty, tradeability_grade, tradeability_warning = _tradeability_penalty(
        profile, liquidity_feature
    )
    penalties = dict(penalty_result.penalties)
    if tradeability_penalty > 0:
        penalties["ibkr_tradeability"] = tradeability_penalty
    total_penalty = penalty_result.total_penalty + tradeability_penalty
    profile_score = _clamp(weighted_score - total_penalty)
    decision = decision_from_score(profile_score, profile)
    gate_result = apply_profile_gates(
        profile=profile,
        score=profile_score,
        decision=decision,
        fundamental=fundamental,
        technical=technical,
        earnings_risk=earnings_risk,
        component_scores=component_scores,
    )
    warning_summary = build_combined_warning_flags(
        fundamental=fundamental,
        technical=technical,
        decision=gate_result.decision,
    )
    warning_flags = _merge_unique(
        warning_summary.flags,
        list(earnings_risk.warning_flags),
        penalty_result.warning_flags,
        [tradeability_warning] if tradeability_warning else [],
        gate_result.warning_flags,
    )
    tradeability_notes = (
        [f"IBKR tradeability overlay applied ({tradeability_grade})"]
        if tradeability_penalty > 0
        else []
    )
    notes = _merge_unique_text(
        penalty_result.notes, tradeability_notes, gate_result.notes
    ) or ["aligned"]
    is_complete = fundamental_score is not None and technical_profile_score is not None

    return RankingProfileDecision(
        ticker=row.ticker.upper(),
        raw_row_id=getattr(row, "id", None),
        company_name=row.company_name,
        sector=row.sector,
        ranking_profile=profile.name,
        ranking_label=profile.label,
        profile_rank=0,
        profile_score=profile_score,
        technical_profile_score=technical_profile_score,
        fundamental_score=fundamental_score,
        base_technical_score=base_technical_score,
        technical_classification=technical.classification if technical else None,
        fundamental_label=fundamental.fundamental_label if fundamental else None,
        decision_label=gate_result.decision,
        position_size_hint=_position_size_hint(gate_result.decision, technical),
        notes=notes,
        warning_flags=warning_flags,
        penalties=penalties,
        gates=gate_result.gates,
        component_scores=component_scores,
        debug=_debug_payload(
            profile=profile,
            fundamental_score=fundamental_score,
            base_technical_score=base_technical_score,
            technical=technical,
            fundamental=fundamental,
            component_scores=component_scores,
            penalties=penalties,
            gates=gate_result.gates,
            technical_profile_score=technical_profile_score,
            weighted_score=weighted_score,
            total_penalty=total_penalty,
            profile_score=profile_score,
            liquidity_feature=liquidity_feature,
            tradeability_grade=tradeability_grade,
        ),
        upcoming_earnings_date=earnings_risk.upcoming_earnings_date,
        days_until_earnings=earnings_risk.days_until_earnings,
        earnings_risk_level=earnings_risk.risk_level,
        is_complete=is_complete,
        has_warning=bool(warning_flags),
        has_fundamental=fundamental_score is not None,
        has_technical=technical_profile_score is not None,
        sort_bucket=_sort_bucket(gate_result.decision, is_complete, bool(warning_flags)),
    )


def calculate_profile_score(
    *,
    technical_profile_score: float | None,
    fundamental_score: float | None,
    profile: RankingProfileConfig,
) -> float:
    total = 0.0
    weight = 0.0
    if technical_profile_score is not None:
        total += technical_profile_score * profile.technical_weight
        weight += profile.technical_weight
    if fundamental_score is not None:
        total += fundamental_score * profile.fundamental_weight
        weight += profile.fundamental_weight
    if weight == 0:
        return 0.0
    if profile.missing_data_policy.rescale_available:
        return _clamp(total / weight)
    return _clamp(total)


def decision_from_score(score: float, profile: RankingProfileConfig) -> str:
    thresholds = profile.thresholds
    if score >= thresholds.strong_candidate_min_score:
        return "Strong candidate"
    if score >= thresholds.candidate_min_score:
        return "Candidate"
    if score >= thresholds.watch_min_score:
        return "Watchlist"
    return "Avoid"


def ranking_sort_key(decision: RankingProfileDecision) -> tuple[int, float, float, float, str]:
    return (
        decision.sort_bucket,
        -(decision.profile_score or -1.0),
        -(decision.fundamental_score if decision.fundamental_score is not None else -1.0),
        -(
            decision.technical_profile_score
            if decision.technical_profile_score is not None
            else -1.0
        ),
        decision.ticker,
    )


def _position_size_hint(decision: str, technical: TechnicalScore | None) -> str:
    if decision == "Blocked by earnings gate":
        return "No new entry"
    if decision == "Avoid":
        return "Avoid"
    if decision in {"Low confidence", "Speculative watch"}:
        return "Small probe"
    if decision in {"Watch", "Watchlist"}:
        return "Small probe"
    risk_score = _float_or_none(technical.risk_score if technical else None)
    if (
        decision == "Strong candidate"
        and technical
        and technical.classification in BUYABLE_CLASSIFICATIONS
        and (risk_score is None or risk_score <= 3.5)
    ):
        return "Full starter"
    if decision in {"Strong candidate", "Candidate"}:
        return "Half starter"
    return "Wait"


def _debug_payload(
    *,
    profile: RankingProfileConfig,
    fundamental_score: float | None,
    base_technical_score: float | None,
    technical: TechnicalScore | None,
    fundamental: FundamentalScore | None,
    component_scores: dict[str, float],
    penalties: dict[str, float],
    gates: dict[str, Any],
    technical_profile_score: float | None,
    weighted_score: float,
    total_penalty: float,
    profile_score: float,
    liquidity_feature: Any | None,
    tradeability_grade: str | None,
) -> dict[str, Any]:
    return {
        "ranking_engine_version": RANKING_ENGINE_VERSION,
        "profile": {
            "name": profile.name,
            "label": profile.label,
            "weights": {
                "technical": profile.technical_weight,
                "fundamental": profile.fundamental_weight,
            },
            "technical_components": profile.technical_components,
            "tradeability_overlay": {
                "enabled": profile.tradeability_overlay.enabled,
                "poor_penalty": profile.tradeability_overlay.poor_penalty,
                "very_poor_penalty": profile.tradeability_overlay.very_poor_penalty,
                "maximum_penalty": profile.tradeability_overlay.maximum_penalty,
                "minimum_dollar_volume": profile.tradeability_overlay.minimum_dollar_volume,
            },
        },
        "inputs": {
            "fundamental_score": fundamental_score,
            "base_technical_score": base_technical_score,
            "technical_classification": technical.classification if technical else None,
            "fundamental_label": fundamental.fundamental_label if fundamental else None,
            "ibkr_liquidity_coverage": _feature_field(liquidity_feature, "coverage_status"),
            "ibkr_liquidity_classification": _feature_field(
                liquidity_feature, "classification"
            ),
            "ibkr_tradeability_grade": tradeability_grade,
        },
        "component_scores": component_scores,
        "penalties": penalties,
        "gates": gates,
        "calculation": {
            "technical_profile_score": technical_profile_score,
            "weighted_score_before_penalty": weighted_score,
            "total_penalty": total_penalty,
            "profile_score": profile_score,
        },
    }


def _tradeability_penalty(
    profile: RankingProfileConfig,
    feature: Any | None,
) -> tuple[float, str | None, str | None]:
    overlay = profile.tradeability_overlay
    if not overlay.enabled or feature is None:
        return 0.0, None, None
    if str(_feature_field(feature, "coverage_status") or "").upper() != "AVAILABLE":
        return 0.0, None, None
    grade = str(_feature_field(feature, "classification") or "").upper()
    components = _feature_field(feature, "components_json") or _feature_field(
        feature, "components"
    ) or {}
    dollar_volume = _float_or_none(components.get("dollar_volume"))
    below_profile_floor = (
        overlay.minimum_dollar_volume is not None
        and dollar_volume is not None
        and dollar_volume < overlay.minimum_dollar_volume
    )
    if grade == "VERY_POOR":
        configured = overlay.very_poor_penalty
    elif grade == "POOR" or below_profile_floor:
        grade = "BELOW_PROFILE_DOLLAR_VOLUME" if below_profile_floor else grade
        configured = overlay.poor_penalty
    else:
        return 0.0, grade or None, None
    penalty = round(min(configured, overlay.maximum_penalty), 4)
    return penalty, grade, "IBKR_TRADEABILITY_PENALTY" if penalty > 0 else None


def _feature_field(feature: Any | None, name: str) -> Any:
    if feature is None:
        return None
    if isinstance(feature, dict):
        return feature.get(name)
    return getattr(feature, name, None)


def _unique_rows(rows: list[RawCompanyRow]) -> list[RawCompanyRow]:
    seen: set[str] = set()
    unique: list[RawCompanyRow] = []
    for row in rows:
        ticker = row.ticker.upper()
        if ticker not in seen:
            seen.add(ticker)
            unique.append(row)
    return unique


def _sort_bucket(decision: str, is_complete: bool, has_warning: bool) -> int:
    if decision in {"Blocked by earnings gate", "Avoid"}:
        return 3
    if not is_complete:
        return 2
    if has_warning:
        return 1
    return 0


def _merge_unique(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    merged: list[str] = []
    for group in groups:
        for value in group:
            if value not in seen:
                merged.append(value)
                seen.add(value)
    return merged


def _merge_unique_text(*groups: list[str]) -> list[str]:
    return _merge_unique(*groups)


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _clamp(value: float) -> float:
    return max(0.0, min(10.0, round(float(value), 4)))
