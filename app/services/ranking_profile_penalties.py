from dataclasses import dataclass

from app.models.tables import FundamentalScore, RawCompanyRow, TechnicalScore
from app.services.combined_decision import DANGER_CLASSIFICATIONS
from app.services.earnings_risk_service import EarningsRiskResult
from app.services.ranking_profile_config import RankingProfileConfig


@dataclass(frozen=True)
class ProfilePenaltyResult:
    total_penalty: float
    penalties: dict[str, float]
    notes: list[str]
    warning_flags: list[str]


def calculate_profile_penalties(
    *,
    profile: RankingProfileConfig,
    row: RawCompanyRow,
    fundamental: FundamentalScore | None,
    technical: TechnicalScore | None,
    component_scores: dict[str, float],
    earnings_risk: EarningsRiskResult,
) -> ProfilePenaltyResult:
    penalties: dict[str, float] = {}
    notes: list[str] = []
    warning_flags: list[str] = []

    if fundamental is None or fundamental.fundamental_score is None:
        _add_missing_data_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "fundamental missing",
            "missing_fundamental",
        )
    if technical is None or technical.dual_score is None:
        _add_missing_data_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "technical missing",
            "missing_technical",
        )

    if technical is not None:
        _add_technical_penalties(
            profile,
            technical,
            component_scores,
            penalties,
            notes,
            warning_flags,
        )

    if fundamental is not None:
        _add_fundamental_penalties(profile, fundamental, penalties, notes, warning_flags)

    _add_earnings_penalty(profile, row, earnings_risk, penalties, notes, warning_flags)

    total_penalty = round(sum(penalties.values()), 4)
    return ProfilePenaltyResult(
        total_penalty=total_penalty,
        penalties=penalties,
        notes=notes,
        warning_flags=_unique(warning_flags),
    )


def _add_technical_penalties(
    profile: RankingProfileConfig,
    technical: TechnicalScore,
    component_scores: dict[str, float],
    penalties: dict[str, float],
    notes: list[str],
    warning_flags: list[str],
) -> None:
    classification = technical.classification
    warning_values = set(technical.warning_flags_json or [])

    if classification == "Failed breakout":
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "failed_breakout",
            "failed breakout",
            "failed_breakout",
            fallback_key="danger_classification",
        )
    elif (
        classification == "Climax reversal risk"
        or component_scores.get("momentum_danger", 0.0) >= 7.0
    ):
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "climax_risk",
            "climax risk",
            "climax_risk",
            fallback_key="danger_classification",
        )
    elif classification == "Late-stage extension":
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "late_stage_extension",
            "late-stage extension",
            "late_stage_extension",
            fallback_key="danger_classification",
        )
    elif classification == "Distribution risk" or "heavy_distribution" in warning_values:
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "heavy_distribution",
            "heavy distribution",
            "distribution_risk",
            fallback_key="distribution_risk",
        )
    elif classification in DANGER_CLASSIFICATIONS:
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "danger_classification",
            str(classification).lower(),
            "danger_classification",
        )

    if classification == "Overheated momentum":
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "overheated_momentum",
            "overheated momentum",
            "overheated_momentum",
        )

    if _liquidity_warning(technical):
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "liquidity_warning",
            "liquidity warning",
            "liquidity_warning",
        )


def _add_fundamental_penalties(
    profile: RankingProfileConfig,
    fundamental: FundamentalScore,
    penalties: dict[str, float],
    notes: list[str],
    warning_flags: list[str],
) -> None:
    if fundamental.fundamental_label == "Value trap risk":
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "value_trap_risk",
            "value trap",
            "value_trap",
        )
    if fundamental.fundamental_label == "Growth trap risk":
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "growth_trap_risk",
            "growth trap",
            "growth_trap",
        )
    if fundamental.fundamental_label == "Quality risk":
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "quality_risk",
            "quality risk",
            "quality_risk",
        )

    flags = _fundamental_flags(fundamental)
    if "share_dilution" in flags:
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "extreme_dilution",
            "extreme dilution",
            "extreme_dilution",
        )
    if "negative_free_cash_flow" in flags:
        _add_penalty(
            profile,
            penalties,
            notes,
            warning_flags,
            "negative_fcf",
            "negative free cash flow",
            "negative_fcf",
        )


def _add_earnings_penalty(
    profile: RankingProfileConfig,
    row: RawCompanyRow,
    earnings_risk: EarningsRiskResult,
    penalties: dict[str, float],
    notes: list[str],
    warning_flags: list[str],
) -> None:
    del row
    penalty_key = {
        "blocked": "earnings_blocked",
        "high": "earnings_high_risk",
        "medium": "earnings_medium_risk",
    }.get(earnings_risk.risk_level)
    if penalty_key is None:
        return
    _add_penalty(
        profile,
        penalties,
        notes,
        warning_flags,
        penalty_key,
        earnings_risk.message,
        penalty_key,
    )


def _add_penalty(
    profile: RankingProfileConfig,
    penalties: dict[str, float],
    notes: list[str],
    warning_flags: list[str],
    key: str,
    note: str,
    warning_flag: str,
    fallback_key: str | None = None,
) -> None:
    configured_key = key if key in profile.penalties else fallback_key
    if configured_key is None or configured_key not in profile.penalties:
        return
    value = float(profile.penalties[configured_key])
    if value <= 0:
        return
    penalties[key] = max(penalties.get(key, 0.0), value)
    notes.append(note)
    warning_flags.append(warning_flag)


def _add_missing_data_penalty(
    profile: RankingProfileConfig,
    penalties: dict[str, float],
    notes: list[str],
    warning_flags: list[str],
    note: str,
    warning_flag: str,
) -> None:
    value = float(profile.missing_data_policy.penalty)
    if value <= 0:
        return
    penalties["missing_data"] = max(penalties.get("missing_data", 0.0), value)
    notes.append(note)
    warning_flags.append(warning_flag)


def _liquidity_warning(technical: TechnicalScore) -> bool:
    if "liquidity_warning" in (technical.warning_flags_json or []):
        return True
    debug_json = technical.debug_json or {}
    derived = debug_json.get("derived")
    return isinstance(derived, dict) and bool(derived.get("liquidity_warning"))


def _fundamental_flags(fundamental: FundamentalScore) -> set[str]:
    flags: set[str] = set()
    for source in [fundamental.trap_flags_json, fundamental.v2_warning_flags_json]:
        if not isinstance(source, dict):
            continue
        values = source.get("flags")
        if isinstance(values, list):
            flags.update(str(value) for value in values)
    return flags


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result
