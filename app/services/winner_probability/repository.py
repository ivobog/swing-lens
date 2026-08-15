from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

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
)


@dataclass(frozen=True)
class TickerCaptureContext:
    raw_row: RawCompanyRow
    fundamental_score: FundamentalScore | None = None
    technical_score: TechnicalScore | None = None
    combined_result: CombinedResult | None = None
    ranking_results: tuple[RankingResult, ...] = ()
    sector_row: SectorRotationRow | None = None
    setup_lifecycle_features: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunCaptureContext:
    upload_run: UploadRun
    market_regime_snapshot: MarketRegimeSnapshot | None
    sector_rotation_snapshot: SectorRotationSnapshot | None
    tickers: tuple[TickerCaptureContext, ...] = field(default_factory=tuple)


class WinnerProbabilityRepository:
    def load_run_context(self, db: Session, run_id: int) -> RunCaptureContext:
        upload_run = db.get(UploadRun, run_id)
        if upload_run is None:
            raise ValueError(f"Upload run {run_id} was not found.")

        raw_rows = list(
            db.scalars(
                select(RawCompanyRow)
                .where(RawCompanyRow.run_id == run_id)
                .order_by(RawCompanyRow.row_number)
            )
        )
        fundamentals = _by_ticker(
            db.scalars(select(FundamentalScore).where(FundamentalScore.run_id == run_id))
        )
        technicals = _by_ticker(
            db.scalars(select(TechnicalScore).where(TechnicalScore.run_id == run_id))
        )
        combined = _by_ticker(
            db.scalars(select(CombinedResult).where(CombinedResult.run_id == run_id))
        )
        rankings = _rankings_by_ticker(
            db.scalars(select(RankingResult).where(RankingResult.run_id == run_id))
        )
        market_snapshot = db.scalar(
            select(MarketRegimeSnapshot)
            .where(MarketRegimeSnapshot.run_id == run_id)
            .order_by(MarketRegimeSnapshot.as_of_date.desc(), MarketRegimeSnapshot.id.desc())
            .limit(1)
        )
        sector_snapshot = db.scalar(
            select(SectorRotationSnapshot)
            .where(SectorRotationSnapshot.run_id == run_id)
            .order_by(
                SectorRotationSnapshot.as_of_date.desc(),
                SectorRotationSnapshot.id.desc(),
            )
            .limit(1)
        )
        sector_rows = {}
        if sector_snapshot is not None:
            sector_rows = {
                row.sector: row
                for row in db.scalars(
                    select(SectorRotationRow).where(
                        SectorRotationRow.snapshot_id == sector_snapshot.id
                    )
                )
            }

        ticker_contexts = tuple(
            TickerCaptureContext(
                raw_row=row,
                fundamental_score=fundamentals.get(_ticker(row)),
                technical_score=technicals.get(_ticker(row)),
                combined_result=combined.get(_ticker(row)),
                ranking_results=tuple(rankings.get(_ticker(row), ())),
                sector_row=sector_rows.get(row.sector_canonical or row.sector),
            )
            for row in raw_rows
        )
        return RunCaptureContext(
            upload_run=upload_run,
            market_regime_snapshot=market_snapshot,
            sector_rotation_snapshot=sector_snapshot,
            tickers=ticker_contexts,
        )

    def get_active_prediction(
        self,
        db: Session,
        *,
        run_id: int,
        ticker: str,
        prediction_as_of_date,
        feature_schema_version: str,
    ) -> WinnerPredictionSnapshot | None:
        return db.scalar(
            select(WinnerPredictionSnapshot)
            .where(WinnerPredictionSnapshot.run_id == run_id)
            .where(WinnerPredictionSnapshot.ticker == ticker)
            .where(WinnerPredictionSnapshot.prediction_as_of_date == prediction_as_of_date)
            .where(WinnerPredictionSnapshot.feature_schema_version == feature_schema_version)
            .where(WinnerPredictionSnapshot.superseded_at.is_(None))
        )

    def get_episode_by_key(
        self,
        db: Session,
        episode_key: str,
    ) -> WinnerPredictionEpisode | None:
        return db.scalar(
            select(WinnerPredictionEpisode).where(
                WinnerPredictionEpisode.episode_key == episode_key
            )
        )

    def get_active_episode(
        self,
        db: Session,
        *,
        dependency_group_hash: str,
        signal_date,
    ) -> WinnerPredictionEpisode | None:
        return db.scalar(
            select(WinnerPredictionEpisode)
            .where(WinnerPredictionEpisode.dependency_group_hash == dependency_group_hash)
            .where(WinnerPredictionEpisode.starts_on <= signal_date)
            .where(WinnerPredictionEpisode.ends_on >= signal_date)
            .order_by(WinnerPredictionEpisode.starts_on.desc())
            .limit(1)
        )

    def get_outcome_definition(
        self,
        db: Session,
        *,
        definition_id: str,
        calculation_version: str,
    ) -> WinnerOutcomeDefinition | None:
        return db.scalar(
            select(WinnerOutcomeDefinition)
            .where(WinnerOutcomeDefinition.definition_id == definition_id)
            .where(WinnerOutcomeDefinition.calculation_version == calculation_version)
        )

    def get_active_outcome_definition(
        self,
        db: Session,
        *,
        definition_id: str,
    ) -> WinnerOutcomeDefinition | None:
        return db.scalar(
            select(WinnerOutcomeDefinition)
            .where(WinnerOutcomeDefinition.definition_id == definition_id)
            .where(WinnerOutcomeDefinition.is_active.is_(True))
        )

    def get_forward_outcome(
        self,
        db: Session,
        *,
        prediction_id: int,
        entry_model: str,
        horizon_sessions: int,
    ) -> WinnerForwardOutcome | None:
        return db.scalar(
            select(WinnerForwardOutcome)
            .where(WinnerForwardOutcome.prediction_id == prediction_id)
            .where(WinnerForwardOutcome.entry_model == entry_model)
            .where(WinnerForwardOutcome.horizon_sessions == horizon_sessions)
            .where(WinnerForwardOutcome.is_current_revision.is_(True))
        )

    def get_target_stop_outcome(
        self,
        db: Session,
        *,
        prediction_id: int,
        outcome_definition_id: int,
    ) -> WinnerTargetStopOutcome | None:
        return db.scalar(
            select(WinnerTargetStopOutcome)
            .where(WinnerTargetStopOutcome.prediction_id == prediction_id)
            .where(WinnerTargetStopOutcome.outcome_definition_id == outcome_definition_id)
            .where(WinnerTargetStopOutcome.is_current_revision.is_(True))
        )

    def get_decision_time_estimate(
        self,
        db: Session,
        *,
        prediction_id: int,
        outcome_definition_id: int,
        estimate_kind: str = "DECISION_TIME",
        source_version: str,
        training_cutoff_at,
    ) -> WinnerProbabilityEstimate | None:
        return db.scalar(
            select(WinnerProbabilityEstimate)
            .where(WinnerProbabilityEstimate.prediction_id == prediction_id)
            .where(WinnerProbabilityEstimate.outcome_definition_id == outcome_definition_id)
            .where(WinnerProbabilityEstimate.estimate_kind == estimate_kind)
            .where(WinnerProbabilityEstimate.source_version == source_version)
            .where(WinnerProbabilityEstimate.training_cutoff_at == training_cutoff_at)
        )

    def add(self, db: Session, row: Any) -> Any:
        db.add(row)
        db.flush()
        return row


def _by_ticker(rows) -> dict[str, Any]:
    return {_ticker(row): row for row in rows}


def _rankings_by_ticker(rows) -> dict[str, list[RankingResult]]:
    grouped: dict[str, list[RankingResult]] = defaultdict(list)
    for row in rows:
        grouped[_ticker(row)].append(row)
    for ticker in grouped:
        grouped[ticker].sort(key=lambda row: (row.profile_rank, row.ranking_profile))
    return grouped


def _ticker(row: Any) -> str:
    return str(row.ticker).strip().upper()
