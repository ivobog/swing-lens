from dataclasses import dataclass
from typing import Any

from app.services.pine_replica_engine import PineReplicaScore


@dataclass(frozen=True)
class TechnicalScoreV4:
    base_score: PineReplicaScore
    engine_version: str
    data_readiness: dict[str, Any]
    adaptive: dict[str, Any]
    contraction: dict[str, Any]
    box: dict[str, Any]
    stage: dict[str, Any]
    regime: dict[str, Any]
    leadership: dict[str, Any] | None
    climax: dict[str, Any]
    feature_flags: list[str]
    warning_flags: list[str]
    sub_tags: list[str]
    final_v4_score: float
    final_v4_classification: str
    final_v4_action: str
    debug: dict[str, Any]

    @property
    def ticker(self) -> str:
        return self.base_score.ticker


def technical_score_v4_from_base_score(
    base_score: PineReplicaScore,
    v4_params: dict[str, Any] | None = None,
) -> TechnicalScoreV4:
    debug = {**(base_score.debug or {})}
    explainability = _dict(debug.get("explainability"))
    engine_config = (v4_params or {}).get("engine", {})
    engine_version = str(
        explainability.get("engine_version")
        or engine_config.get("version")
        or "4.0.0"
    )
    feature_flags = _list(explainability.get("feature_flags"))
    warning_flags = _list(explainability.get("warning_flags")) or list(
        base_score.warning_flags
    )
    sub_tags = _list(explainability.get("sub_tags"))
    final_v4_score = _num(explainability.get("final_v4_score"), base_score.dual_score)
    final_v4_classification = str(
        explainability.get("final_v4_classification")
        or base_score.classification
    )
    final_v4_action = str(
        explainability.get("final_v4_action")
        or base_score.action_bias
    )

    explainability = {
        **explainability,
        "engine_version": engine_version,
        "data_readiness": _dict(explainability.get("data_readiness")),
        "adaptive": _dict(explainability.get("adaptive")),
        "contraction": _dict(explainability.get("contraction")),
        "box": _dict(explainability.get("box")),
        "stage": _dict(explainability.get("stage")),
        "regime": _dict(explainability.get("regime")),
        "leadership": _optional_dict(explainability.get("leadership")),
        "climax": _dict(explainability.get("climax")),
        "feature_flags": feature_flags,
        "warning_flags": warning_flags,
        "sub_tags": sub_tags,
        "final_v4_score": final_v4_score,
        "final_v4_classification": final_v4_classification,
        "final_v4_action": final_v4_action,
    }
    debug["explainability"] = explainability

    return TechnicalScoreV4(
        base_score=base_score,
        engine_version=engine_version,
        data_readiness=explainability["data_readiness"],
        adaptive=explainability["adaptive"],
        contraction=explainability["contraction"],
        box=explainability["box"],
        stage=explainability["stage"],
        regime=explainability["regime"],
        leadership=explainability["leadership"],
        climax=explainability["climax"],
        feature_flags=feature_flags,
        warning_flags=warning_flags,
        sub_tags=sub_tags,
        final_v4_score=final_v4_score,
        final_v4_classification=final_v4_classification,
        final_v4_action=final_v4_action,
        debug=debug,
    )


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _optional_dict(value: Any) -> dict[str, Any] | None:
    return value if isinstance(value, dict) else None


def _list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _num(value: Any, default: float) -> float:
    if value is None:
        return round(float(default), 4)
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return round(float(default), 4)
