"""Winner acquisition permission using the shared Phase-3 contracts."""

from dataclasses import replace
from typing import Any

from app.services.contextual_consumer_eligibility import (
    ContextualConsumerPolicy,
    contextual_decision_input,
    frozen_sector_row,
)
from app.services.producer_readiness import NativeReadinessMetrics
from app.services.technical_consumer_eligibility import (
    TechnicalConsumerPolicy,
    technical_decision_input,
)

WINNER_ELIGIBILITY_KEY = "winner_consumer_eligibility"
TECHNICAL_TO_WINNER = TechnicalConsumerPolicy("WINNER", "technical-to-winner-v1")
RANKING_TO_WINNER = ContextualConsumerPolicy(
    "RANKING", "WINNER", "ranking-to-winner-v2", required_readiness_version="ranking-readiness-v2"
)
REGIME_TO_WINNER = ContextualConsumerPolicy(
    "REGIME", "WINNER", "regime-to-winner-v2", required_readiness_version="regime-readiness-v2"
)
SECTOR_TO_WINNER = ContextualConsumerPolicy(
    "SECTOR", "WINNER", "sector-to-winner-v2", required_readiness_version="sector-readiness-v2"
)
FUNDAMENTAL_TO_WINNER = ContextualConsumerPolicy(
    "FUNDAMENTAL", "WINNER", "fundamental-to-winner-v1"
)
COMBINED_TO_WINNER = ContextualConsumerPolicy(
    "COMBINED",
    "WINNER",
    "combined-to-winner-v2",
    required_readiness_version="combined-readiness-v2",
)


class WinnerSourceEligibilityError(ValueError):
    """Required source rejection before a feature vector or prediction is written."""

    def __init__(self, decisions: NativeReadinessMetrics):
        self.decisions = decisions
        payload = decisions.canonical_payload()
        self.sources = tuple(
            key
            for key, item in payload.items()
            if item["requirement"] == "MANDATORY" and not item["included"]
        )
        technical = payload["technical"]["producer_readiness"]
        if "TECH_INSUFFICIENT_HISTORY" in technical["blocking_reasons"]:
            self.reason = "insufficient_completed_bars"
        elif any(payload[key]["decision"]["status"] == "INELIGIBLE" for key in self.sources):
            self.reason = "winner_source_ineligible"
        else:
            self.reason = "winner_readiness_undecided"
        super().__init__(f"{self.reason}: sources={','.join(self.sources)}")

    def metadata(self, ticker: str) -> dict[str, Any]:
        return {
            "ticker": ticker,
            "reason": self.reason,
            "sources": list(self.sources),
            WINNER_ELIGIBILITY_KEY: self.decisions.canonical_payload(),
        }


def winner_decision_inputs(run_context: Any, ticker_context: Any):
    """Called only after exact source identity/handoff acquisition, without reselection.

    Technical, Ranking and Combined are mandatory. Fundamental, Regime and
    Sector have native nullable representations. Compute every decision first.
    """
    if getattr(ticker_context, "setup_lifecycle_features", None):
        raise ValueError("Winner acquisition does not accept Setup/Lifecycle features")
    ranking = ticker_context.ranking_results[0] if ticker_context.ranking_results else None
    technical, tech = technical_decision_input(ticker_context.technical_score, TECHNICAL_TO_WINNER)
    ranking_input, rank = contextual_decision_input(ranking, RANKING_TO_WINNER)
    fundamental, fund = contextual_decision_input(
        ticker_context.fundamental_score, FUNDAMENTAL_TO_WINNER
    )
    combined, combination = contextual_decision_input(
        ticker_context.combined_result, COMBINED_TO_WINNER
    )
    regime, market = contextual_decision_input(run_context.market_regime_snapshot, REGIME_TO_WINNER)
    sector, rotation = contextual_decision_input(
        run_context.sector_rotation_snapshot, SECTOR_TO_WINNER
    )
    decisions = {}
    for name, row, permission, requirement in (
        ("technical", ticker_context.technical_score, tech, "MANDATORY"),
        ("ranking", ranking, rank, "MANDATORY"),
        ("combined", ticker_context.combined_result, combination, "MANDATORY"),
        ("fundamental", ticker_context.fundamental_score, fund, "OPTIONAL"),
        ("regime", run_context.market_regime_snapshot, market, "OPTIONAL"),
        ("sector", run_context.sector_rotation_snapshot, rotation, "OPTIONAL"),
    ):
        decisions[name] = {
            **permission,
            "source_id": getattr(row, "id", None),
            "requirement": requirement,
            "included": permission["decision"]["status"] == "ELIGIBLE",
        }
    frozen = NativeReadinessMetrics.freeze(decisions)
    if any(
        item["requirement"] == "MANDATORY" and not item["included"] for item in decisions.values()
    ):
        raise WinnerSourceEligibilityError(frozen)
    for projected, original in (
        (technical, ticker_context.technical_score),
        (ranking_input, ranking),
        (fundamental, ticker_context.fundamental_score),
        (combined, ticker_context.combined_result),
        (regime, run_context.market_regime_snapshot),
        (sector, run_context.sector_rotation_snapshot),
    ):
        if projected is not None:
            # Core business payloads exclude operational clocks. Preserve the exact
            # selected row's clocks for Winner's separate existing PIT audit.
            projected.created_at = original.created_at
            projected.updated_at = getattr(original, "updated_at", None)
    return (
        replace(run_context, market_regime_snapshot=regime, sector_rotation_snapshot=sector),
        replace(
            ticker_context,
            technical_score=technical,
            fundamental_score=fundamental,
            combined_result=combined,
            ranking_results=(ranking_input, *ticker_context.ranking_results[1:]),
            sector_row=frozen_sector_row(sector, ticker_context.sector_row),
        ),
        frozen,
    )
