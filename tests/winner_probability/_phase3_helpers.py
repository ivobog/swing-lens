from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    MarketRegimeSnapshot,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    TechnicalScore,
    UploadRun,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionEpisode,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
    WinnerTemporalValidityDecision,
)
from app.services.winner_probability.repository import RunCaptureContext, TickerCaptureContext


class FakeWinnerRepository:
    def __init__(self, context: RunCaptureContext | None = None) -> None:
        self.context = context
        self.predictions: list[WinnerPredictionSnapshot] = []
        self.episodes: list[WinnerPredictionEpisode] = []
        self.outcome_definitions: list[WinnerOutcomeDefinition] = []
        self.forward_outcomes: list[WinnerForwardOutcome] = []
        self.target_stop_outcomes: list[WinnerTargetStopOutcome] = []
        self.estimates: list[WinnerProbabilityEstimate] = []
        self.temporal_decisions: list[WinnerTemporalValidityDecision] = []
        self._ids: defaultdict[type, int] = defaultdict(lambda: 1)

    def load_run_context(self, _db, _run_id: int) -> RunCaptureContext:
        if self.context is None:
            raise ValueError("missing context")
        return self.context

    def get_active_prediction(self, _db, **kwargs) -> WinnerPredictionSnapshot | None:
        for prediction in self.predictions:
            if (
                prediction.run_id == kwargs["run_id"]
                and prediction.ticker == kwargs["ticker"]
                and prediction.prediction_as_of_date == kwargs["prediction_as_of_date"]
                and prediction.feature_schema_version == kwargs["feature_schema_version"]
                and prediction.superseded_at is None
            ):
                return prediction
        return None

    def get_episode_by_key(self, _db, episode_key: str) -> WinnerPredictionEpisode | None:
        return next(
            (episode for episode in self.episodes if episode.episode_key == episode_key), None
        )

    def get_active_episode(
        self,
        _db,
        *,
        dependency_group_hash: str,
        signal_date: date,
    ) -> WinnerPredictionEpisode | None:
        for episode in self.episodes:
            if (
                episode.dependency_group_hash == dependency_group_hash
                and episode.starts_on <= signal_date <= episode.ends_on
            ):
                return episode
        return None

    def get_outcome_definition(self, _db, **kwargs) -> WinnerOutcomeDefinition | None:
        return next(
            (
                definition
                for definition in self.outcome_definitions
                if definition.definition_id == kwargs["definition_id"]
                and definition.calculation_version == kwargs["calculation_version"]
            ),
            None,
        )

    def get_forward_outcome(self, _db, **kwargs) -> WinnerForwardOutcome | None:
        return next(
            (
                outcome
                for outcome in self.forward_outcomes
                if outcome.prediction_id == kwargs["prediction_id"]
                and outcome.entry_model == kwargs["entry_model"]
                and outcome.horizon_sessions == kwargs["horizon_sessions"]
                and outcome.is_current_revision
            ),
            None,
        )

    def get_target_stop_outcome(self, _db, **kwargs) -> WinnerTargetStopOutcome | None:
        return next(
            (
                outcome
                for outcome in self.target_stop_outcomes
                if outcome.prediction_id == kwargs["prediction_id"]
                and outcome.outcome_definition_id == kwargs["outcome_definition_id"]
                and outcome.is_current_revision
            ),
            None,
        )

    def get_current_temporal_decision(
        self, _db, prediction_id: int
    ) -> WinnerTemporalValidityDecision | None:
        rows = [row for row in self.temporal_decisions if row.prediction_id == prediction_id]
        return max(rows, key=lambda row: row.validation_sequence, default=None)

    def get_decision_time_estimate(self, _db, **kwargs) -> WinnerProbabilityEstimate | None:
        return next(
            (
                estimate
                for estimate in self.estimates
                if estimate.prediction_id == kwargs["prediction_id"]
                and estimate.outcome_definition_id == kwargs["outcome_definition_id"]
                and estimate.source_version == kwargs["source_version"]
                and estimate.training_cutoff_at == kwargs["training_cutoff_at"]
            ),
            None,
        )

    def add(self, _db, row: Any):
        if getattr(row, "id", None) is None:
            row.id = self._ids[type(row)]
            self._ids[type(row)] += 1
        if isinstance(row, WinnerPredictionSnapshot) and row not in self.predictions:
            self.predictions.append(row)
        elif isinstance(row, WinnerPredictionEpisode) and row not in self.episodes:
            self.episodes.append(row)
        elif isinstance(row, WinnerOutcomeDefinition) and row not in self.outcome_definitions:
            self.outcome_definitions.append(row)
        elif isinstance(row, WinnerForwardOutcome) and row not in self.forward_outcomes:
            self.forward_outcomes.append(row)
        elif isinstance(row, WinnerTargetStopOutcome) and row not in self.target_stop_outcomes:
            self.target_stop_outcomes.append(row)
        elif isinstance(row, WinnerProbabilityEstimate) and row not in self.estimates:
            self.estimates.append(row)
        elif isinstance(row, WinnerTemporalValidityDecision) and row not in self.temporal_decisions:
            self.temporal_decisions.append(row)
        return row


def build_run_context(
    *,
    ticker: str = "MSFT",
    as_of_date: date = date(2026, 7, 31),
    include_market: bool = True,
    include_sector: bool = True,
) -> RunCaptureContext:
    created_at = datetime(2026, 7, 31, 15, 0, tzinfo=UTC)
    upload = UploadRun(
        id=7,
        filename="swinglens_screen.csv",
        uploaded_at=created_at,
        processed_at=created_at,
        status="COMPLETED",
        notes="growth_screen_v1",
    )
    raw = RawCompanyRow(
        id=11,
        run_id=7,
        row_number=1,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        sector_canonical="Technology",
        raw_json={"Symbol": ticker},
        created_at=created_at,
    )
    fundamental = FundamentalScore(
        id=21,
        run_id=7,
        ticker=ticker,
        fundamental_score=Decimal("8.2"),
        data_coverage_score=Decimal("0.92"),
        created_at=created_at,
    )
    technical = TechnicalScore(
        id=31,
        run_id=7,
        ticker=ticker,
        dual_score=Decimal("8.4"),
        classification="Clean bull pullback",
        action_bias="pullback",
        suggested_stop=Decimal("2.0"),
        suggested_target=Decimal("2.5"),
        reward_risk=Decimal("2.1"),
        technical_confidence="ok",
        insufficient_data=False,
        created_at=created_at,
    )
    combined = CombinedResult(
        id=41,
        run_id=7,
        ticker=ticker,
        company_name=f"{ticker} Corp",
        sector="Technology",
        final_rank=1,
        final_score=Decimal("8.5"),
        fundamental_score=Decimal("8.2"),
        technical_classification="Clean bull pullback",
        dual_score=Decimal("8.4"),
        combined_decision="Strong candidate",
        earnings_risk_level="low",
        is_complete=True,
        has_warning=False,
        created_at=created_at,
    )
    ranking = RankingResult(
        id=51,
        run_id=7,
        ticker=ticker,
        ranking_profile="momentum_swing",
        ranking_label="Momentum Swing",
        profile_rank=1,
        profile_score=Decimal("8.6"),
        decision_label="Strong candidate",
        is_complete=True,
        created_at=created_at,
    )
    market = (
        MarketRegimeSnapshot(
            id=61,
            run_id=7,
            as_of_date=as_of_date,
            calculation_version="mrcc-1.0.0",
            regime="Confirmed Uptrend",
            risk_state="Green",
            score=8.0,
            confidence="normal",
            action_summary="constructive",
            created_at=created_at,
        )
        if include_market
        else None
    )
    sector_snapshot = (
        SectorRotationSnapshot(
            id=71,
            run_id=7,
            as_of_date=as_of_date,
            calculation_version="sector-rotation-1.0.0",
            config_hash="sector-hash",
            mode="universe_only",
            created_at=created_at,
        )
        if include_sector
        else None
    )
    sector_row = (
        SectorRotationRow(
            id=81,
            snapshot_id=71,
            sector="Technology",
            sector_slug="technology",
            rotation_state="Leading",
            sector_permission="full_allowed",
            confidence="high",
            current_rank=1,
        )
        if include_sector
        else None
    )
    return RunCaptureContext(
        upload_run=upload,
        market_regime_snapshot=market,
        sector_rotation_snapshot=sector_snapshot,
        tickers=(
            TickerCaptureContext(
                raw_row=raw,
                fundamental_score=fundamental,
                technical_score=technical,
                combined_result=combined,
                ranking_results=(ranking,),
                sector_row=sector_row,
            ),
        ),
    )
