from dataclasses import dataclass
from typing import Any

from app.services.technical_indicators import TechnicalFeatureResult

CONFIDENCE_ERROR = "error"
CONFIDENCE_LOW = "low"
CONFIDENCE_NORMAL = "normal"
CONFIDENCE_HIGH = "high"


@dataclass(frozen=True)
class TechnicalDataReadiness:
    has_price_data: bool
    has_volume_data: bool
    has_sufficient_history: bool
    has_benchmark_data: bool
    has_market_data: bool
    has_htf_data: bool
    has_relative_strength_data: bool
    missing_reasons: list[str]
    data_quality_score: float
    confidence: str

    def missing_flags(self) -> dict[str, bool]:
        return {
            "has_price_data": self.has_price_data,
            "has_volume_data": self.has_volume_data,
            "has_sufficient_history": self.has_sufficient_history,
            "has_benchmark_data": self.has_benchmark_data,
            "has_market_data": self.has_market_data,
            "has_htf_data": self.has_htf_data,
            "has_relative_strength_data": self.has_relative_strength_data,
            "missing_price_data": not self.has_price_data,
            "missing_volume_data": "missing_volume_data" in self.missing_reasons,
            "missing_benchmark_data": "missing_benchmark_data" in self.missing_reasons,
            "missing_market_data": "missing_market_data" in self.missing_reasons,
            "missing_htf_data": "missing_htf_data" in self.missing_reasons,
        }


def build_data_readiness(
    feature_result: TechnicalFeatureResult,
    htf_features: dict[str, Any] | None,
    relative_strength_features: dict[str, Any] | None,
    market_features: dict[str, Any] | None,
    params: dict[str, Any],
) -> TechnicalDataReadiness:
    latest = feature_result.latest or {}
    missing_data = feature_result.missing_data or {}

    has_price_data = bool(latest)
    has_volume_data = _num(latest.get("volume")) > 0
    has_sufficient_history = not bool(
        feature_result.insufficient_data or missing_data.get("insufficient_history")
    )
    has_benchmark_data = _has_benchmark_features(relative_strength_features)
    has_market_data = bool(market_features)
    has_htf_data = _has_htf_features(htf_features)
    has_relative_strength_data = has_benchmark_data

    missing_reasons: list[str] = []
    if not has_price_data:
        missing_reasons.append("missing_price_data")
    if not has_volume_data:
        missing_reasons.append("missing_volume_data")
    if not has_sufficient_history:
        missing_reasons.append("insufficient_history")
    if _requires_benchmark(params) and not has_benchmark_data:
        missing_reasons.append("missing_benchmark_data")
    if _requires_market(params) and not has_market_data:
        missing_reasons.append("missing_market_data")
    if _requires_htf(params) and not has_htf_data:
        missing_reasons.append("missing_htf_data")

    data_quality_score = _data_quality_score(
        has_price_data=has_price_data,
        has_volume_data=has_volume_data,
        has_sufficient_history=has_sufficient_history,
        missing_required_context=any(
            reason
            in {
                "missing_benchmark_data",
                "missing_market_data",
                "missing_htf_data",
            }
            for reason in missing_reasons
        ),
    )
    confidence = _confidence(
        has_price_data=has_price_data,
        missing_reasons=missing_reasons,
        data_quality_score=data_quality_score,
    )

    return TechnicalDataReadiness(
        has_price_data=has_price_data,
        has_volume_data=has_volume_data,
        has_sufficient_history=has_sufficient_history,
        has_benchmark_data=has_benchmark_data,
        has_market_data=has_market_data,
        has_htf_data=has_htf_data,
        has_relative_strength_data=has_relative_strength_data,
        missing_reasons=missing_reasons,
        data_quality_score=data_quality_score,
        confidence=confidence,
    )


def _requires_benchmark(params: dict[str, Any]) -> bool:
    return bool(params.get("market_rs", {}).get("useRelativeStrengthFilter", False))


def _requires_market(params: dict[str, Any]) -> bool:
    return bool(params.get("market_rs", {}).get("useMarketFilter", False))


def _requires_htf(params: dict[str, Any]) -> bool:
    if params.get("scoring", {}).get("scoringMode") == "Early Rocket":
        return False
    return bool(params.get("htf", {}).get("useHtfTrendFilter", False))


def _has_benchmark_features(features: dict[str, Any] | None) -> bool:
    if not features:
        return False
    return features.get("benchmark_rs_line") is not None


def _has_htf_features(features: dict[str, Any] | None) -> bool:
    if not features:
        return False
    return features.get("htf_sma_slow") is not None


def _data_quality_score(
    has_price_data: bool,
    has_volume_data: bool,
    has_sufficient_history: bool,
    missing_required_context: bool,
) -> float:
    if not has_price_data:
        return 0.0
    score = 10.0
    if not has_volume_data:
        score -= 1.0
    if not has_sufficient_history:
        score -= 3.0
    if missing_required_context:
        score -= 3.0
    return max(0.0, min(10.0, round(score, 4)))


def _confidence(
    has_price_data: bool,
    missing_reasons: list[str],
    data_quality_score: float,
) -> str:
    if not has_price_data:
        return CONFIDENCE_ERROR
    if missing_reasons:
        return CONFIDENCE_LOW
    if data_quality_score >= 9.5:
        return CONFIDENCE_HIGH
    return CONFIDENCE_NORMAL


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
