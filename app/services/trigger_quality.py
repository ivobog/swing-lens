from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerQualityResult:
    quality: float
    state: str
    trigger_price: float | None
    invalidation_price: float | None
    distance_atr: float | None
    reason: str


def calculate_trigger_quality(
    *,
    close: float | None,
    atr14: float | None,
    trigger_price: float | None,
    invalidation_price: float | None,
    invalidated: bool,
    config: dict,
) -> TriggerQualityResult:
    scores = config["scores"]
    if invalidated or (
        close is not None and invalidation_price is not None and close < invalidation_price
    ):
        return _result(
            scores["invalidated"], "invalidated", trigger_price, invalidation_price, None
        )
    if close is None or atr14 is None or atr14 <= 0 or trigger_price is None:
        return _result(
            scores["not_applicable"], "not_applicable", trigger_price, invalidation_price, None
        )

    distance = round((float(trigger_price) - float(close)) / float(atr14), 4)
    if distance > float(config["far_below_atr"]):
        state = "too_far_below"
    elif distance > float(config["approaching_atr"]):
        state = "approaching"
    elif distance > float(config["near_atr"]):
        state = "near"
    elif distance >= 0:
        state = "at_trigger"
    elif distance >= float(config["freshly_triggered_atr"]):
        state = "freshly_triggered"
    elif distance >= float(config["extended_atr"]):
        state = "beyond_trigger"
    else:
        state = "extended_beyond_trigger"
    return _result(scores[state], state, trigger_price, invalidation_price, distance)


def _result(
    quality: float,
    state: str,
    trigger_price: float | None,
    invalidation_price: float | None,
    distance: float | None,
) -> TriggerQualityResult:
    return TriggerQualityResult(
        quality=max(0.0, min(10.0, round(float(quality), 4))),
        state=state,
        trigger_price=trigger_price,
        invalidation_price=invalidation_price,
        distance_atr=distance,
        reason=f"trigger_state:{state}",
    )
