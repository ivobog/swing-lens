from dataclasses import dataclass
from typing import Any

from app.models.tables import FundamentalScore, TechnicalScore
from app.services.combined_decision import DANGER_CLASSIFICATIONS
from app.services.earnings_risk_service import EarningsRiskResult
from app.services.ranking_profile_config import RankingProfileConfig

DECISION_ORDER = {
    "Blocked by earnings gate": 0,
    "Avoid": 1,
    "Low confidence": 2,
    "Speculative watch": 2,
    "Watch": 3,
    "Candidate": 4,
    "Strong candidate": 5,
}


@dataclass(frozen=True)
class GateResult:
    decision: str
    score_cap: float | None
    blocked: bool
    gates: dict[str, Any]
    notes: list[str]
    warning_flags: list[str]


def apply_profile_gates(
    *,
    profile: RankingProfileConfig,
    score: float,
    decision: str,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    earnings_risk: EarningsRiskResult,
    component_scores: dict[str, float],
) -> GateResult:
    del score, component_scores
    gates: dict[str, Any] = {}
    notes: list[str] = []
    warning_flags: list[str] = []
    final_decision = decision
    blocked = False

    if profile.gates.get("earnings_block") is True and earnings_risk.decision_blocked:
        gates["earnings_block"] = True
        notes.append("blocked by earnings gate")
        warning_flags.append("earnings_blocked")
        return GateResult(
            decision="Blocked by earnings gate",
            score_cap=None,
            blocked=True,
            gates=gates,
            notes=notes,
            warning_flags=warning_flags,
        )
    gates["earnings_block"] = False

    if technical and profile.gates.get("danger_blocks_candidate") is True:
        if technical.classification in DANGER_CLASSIFICATIONS:
            final_decision = _cap_decision(final_decision, "Avoid")
            gates["danger_gate"] = True
            notes.append("danger classification gate")
            warning_flags.append("danger_classification")
        else:
            gates["danger_gate"] = False

    floor = profile.gates.get("fundamental_floor")
    if isinstance(floor, dict) and floor.get("enabled") is True:
        fundamental_score = _fundamental_score(fundamental)
        min_score = float(floor.get("min_score", 0.0))
        if fundamental_score is not None and fundamental_score < min_score:
            max_decision = str(floor.get("max_decision", "Watch"))
            final_decision = _cap_decision(final_decision, max_decision)
            gates["fundamental_floor"] = {
                "passed": False,
                "min_score": min_score,
                "max_decision": max_decision,
            }
            notes.append("fundamental floor failed")
            warning_flags.append("fundamental_floor_failed")
        else:
            gates["fundamental_floor"] = {"passed": True, "min_score": min_score}

    if profile.gates.get("liquidity_caps_candidate") is True and _liquidity_warning(technical):
        final_decision = _cap_decision(final_decision, "Watch")
        gates["liquidity_cap"] = True
        notes.append("liquidity cap")
        warning_flags.append("liquidity_warning")
    elif profile.gates.get("liquidity_caps_candidate") is True:
        gates["liquidity_cap"] = False

    if fundamental is None or technical is None or _low_data_quality(technical):
        final_decision = _cap_decision(final_decision, "Low confidence")
        gates["data_quality"] = False
        notes.append("low data quality")
        warning_flags.append("low_data_quality")
    else:
        gates["data_quality"] = True

    return GateResult(
        decision=final_decision,
        score_cap=None,
        blocked=blocked,
        gates=gates,
        notes=notes,
        warning_flags=_unique(warning_flags),
    )


def _cap_decision(decision: str, max_decision: str) -> str:
    if DECISION_ORDER.get(decision, 0) <= DECISION_ORDER.get(max_decision, 0):
        return decision
    return max_decision


def _fundamental_score(fundamental: FundamentalScore | None) -> float | None:
    if fundamental is None or fundamental.fundamental_score is None:
        return None
    return float(fundamental.fundamental_score)


def _liquidity_warning(technical: TechnicalScore | None) -> bool:
    if technical is None:
        return False
    if "liquidity_warning" in (technical.warning_flags_json or []):
        return True
    debug_json = technical.debug_json or {}
    derived = debug_json.get("derived")
    return isinstance(derived, dict) and bool(derived.get("liquidity_warning"))


def _low_data_quality(technical: TechnicalScore | None) -> bool:
    if technical is None:
        return True
    confidence = (technical.technical_confidence or "").casefold()
    if confidence in {"low", "error"}:
        return True
    if technical.insufficient_data:
        return True
    missing_data = technical.missing_data_json or {}
    return bool(missing_data.get("insufficient_history"))


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
