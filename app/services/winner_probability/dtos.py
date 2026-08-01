from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

EVIDENCE_GRADES = frozenset({"High", "Medium", "Low", "Insufficient"})
EARNINGS_RISK_VALUES = frozenset({"none", "low", "medium", "high", "unknown"})
DATA_QUALITY_VALUES = frozenset({"ok", "warning", "insufficient", "unknown"})
ESTIMATE_VIEW_VALUES = frozenset({"DECISION_TIME", "LATEST", "LATEST_RESCORE"})
OUTCOME_REVISION_VIEW_VALUES = frozenset({"AS_OF", "CURRENT"})


class WinnerProbabilityFilterError(ValueError):
    pass


@dataclass(frozen=True)
class WinnerProbabilityFilters:
    probability_min: float | None = None
    probability_max: float | None = None
    lower_bound_min: float | None = None
    lower_bound_max: float | None = None
    interval_width_min: float | None = None
    interval_width_max: float | None = None
    expected_return_min: float | None = None
    expected_return_max: float | None = None
    median_return_min: float | None = None
    median_return_max: float | None = None
    mfe_min: float | None = None
    mfe_max: float | None = None
    mae_min: float | None = None
    mae_max: float | None = None
    target_first_rate_min: float | None = None
    target_first_rate_max: float | None = None
    evidence_grade: str | None = None
    effective_sample_size_min: int | None = None
    sample_size_min: int | None = None
    earnings_risk: str | None = None
    data_quality: str | None = None
    setup_classification: str | None = None
    setup_family: str | None = None
    ranking_profile: str | None = None
    market_regime: str | None = None
    market_risk_state: str | None = None
    sector_state: str | None = None
    sector_rank_min: int | None = None
    sector_rank_max: int | None = None
    eligibility_status: str | None = None

    def __post_init__(self) -> None:
        _optional_ratio(self.probability_min, "probability_min")
        _optional_ratio(self.probability_max, "probability_max")
        _optional_ratio(self.lower_bound_min, "lower_bound_min")
        _optional_ratio(self.lower_bound_max, "lower_bound_max")
        _optional_ratio(self.interval_width_min, "interval_width_min")
        _optional_ratio(self.interval_width_max, "interval_width_max")
        _optional_ratio(self.target_first_rate_min, "target_first_rate_min")
        _optional_ratio(self.target_first_rate_max, "target_first_rate_max")
        _require_order("probability", self.probability_min, self.probability_max)
        _require_order("lower_bound", self.lower_bound_min, self.lower_bound_max)
        _require_order("interval_width", self.interval_width_min, self.interval_width_max)
        _require_order(
            "target_first_rate",
            self.target_first_rate_min,
            self.target_first_rate_max,
        )
        _require_order("expected_return", self.expected_return_min, self.expected_return_max)
        _require_order("median_return", self.median_return_min, self.median_return_max)
        _require_order("mfe", self.mfe_min, self.mfe_max)
        _require_order("mae", self.mae_min, self.mae_max)
        if self.effective_sample_size_min is not None and self.effective_sample_size_min < 0:
            raise WinnerProbabilityFilterError(
                "effective_sample_size_min must be non-negative"
            )
        if self.sample_size_min is not None and self.sample_size_min < 0:
            raise WinnerProbabilityFilterError("sample_size_min must be non-negative")
        if self.sector_rank_min is not None and self.sector_rank_min < 0:
            raise WinnerProbabilityFilterError("sector_rank_min must be non-negative")
        if self.sector_rank_max is not None and self.sector_rank_max < 0:
            raise WinnerProbabilityFilterError("sector_rank_max must be non-negative")
        _require_order("sector_rank", self.sector_rank_min, self.sector_rank_max)
        _optional_choice(self.evidence_grade, EVIDENCE_GRADES, "evidence_grade")
        _optional_choice(self.earnings_risk, EARNINGS_RISK_VALUES, "earnings_risk")
        _optional_choice(self.data_quality, DATA_QUALITY_VALUES, "data_quality")

    def as_query_params(self) -> dict[str, float | int | str]:
        return {
            key: value
            for key, value in self.__dict__.items()
            if value is not None
        }


@dataclass(frozen=True)
class WinnerProbabilityApiQuery:
    outcome_definition_id: str | None = None
    entry_model: str | None = None
    horizon_sessions: int | None = None
    as_of_date: date | None = None
    training_cutoff: date | None = None
    estimate_view: str = "DECISION_TIME"
    outcome_revision_view: str = "CURRENT"
    sort: str = "ticker"
    direction: str = "asc"
    cursor: str | None = None
    page_size: int = 100
    filters: WinnerProbabilityFilters = field(default_factory=WinnerProbabilityFilters)

    def __post_init__(self) -> None:
        if self.horizon_sessions is not None and self.horizon_sessions <= 0:
            raise WinnerProbabilityFilterError("horizon_sessions must be positive")
        _optional_choice(self.estimate_view, ESTIMATE_VIEW_VALUES, "estimate_view")
        _optional_choice(
            self.outcome_revision_view,
            OUTCOME_REVISION_VIEW_VALUES,
            "outcome_revision_view",
        )
        if self.direction not in {"asc", "desc"}:
            raise WinnerProbabilityFilterError("direction must be asc or desc")
        if self.page_size <= 0:
            raise WinnerProbabilityFilterError("page_size must be positive")


def _optional_ratio(value: float | None, field_name: str) -> None:
    if value is None:
        return
    if value < 0 or value > 1:
        raise WinnerProbabilityFilterError(f"{field_name} must be between 0 and 1")


def _optional_choice(value: str | None, allowed: frozenset[str], field_name: str) -> None:
    if value is None:
        return
    if value not in allowed:
        raise WinnerProbabilityFilterError(
            f"{field_name} must be one of {', '.join(sorted(allowed))}"
        )


def _require_order(
    label: str,
    minimum: float | None,
    maximum: float | None,
) -> None:
    if minimum is not None and maximum is not None and minimum > maximum:
        raise WinnerProbabilityFilterError(f"{label} minimum must be <= maximum")
