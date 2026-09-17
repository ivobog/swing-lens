from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.tables import (
    CombinedResult,
    FundamentalScore,
    MarketRegimeSnapshot,
    RankingResult,
    RawCompanyRow,
    SectorRotationRow,
    SectorRotationSnapshot,
    TechnicalScore,
    TransitionDecisionHandoffManifest,
    UploadRun,
    WinnerForwardOutcome,
    WinnerOutcomeDefinition,
    WinnerPredictionEpisode,
    WinnerPredictionSnapshot,
    WinnerProbabilityEstimate,
    WinnerTargetStopOutcome,
    WinnerTemporalValidityDecision,
)
from app.services.market_clock_service import MarketCalculationCutoff


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
    decision_handoff_manifest: TransitionDecisionHandoffManifest | None = None
    market_regime_candidates: tuple[MarketRegimeSnapshot, ...] = field(default_factory=tuple)
    sector_rotation_candidates: tuple[SectorRotationSnapshot, ...] = field(default_factory=tuple)
    sector_rows_by_snapshot: dict[int, dict[str, SectorRotationRow]] = field(default_factory=dict)
    tickers: tuple[TickerCaptureContext, ...] = field(default_factory=tuple)


class WinnerProbabilityRepository:
    def load_run_context(
        self,
        db: Session,
        run_id: int,
        *,
        market_cutoff: MarketCalculationCutoff | None = None,
        decision_handoff_manifest_id: int | None = None,
    ) -> RunCaptureContext:
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
            db.scalars(
                select(FundamentalScore)
                .options(
                    selectinload(FundamentalScore.calculation_evidence),
                )
                .where(FundamentalScore.run_id == run_id)
            )
        )
        technicals = _by_ticker(
            db.scalars(
                select(TechnicalScore)
                .options(
                    selectinload(TechnicalScore.calculation_evidence),
                )
                .where(TechnicalScore.run_id == run_id)
            )
        )
        combined = _by_ticker(
            db.scalars(
                select(CombinedResult)
                .options(
                    selectinload(CombinedResult.calculation_evidence),
                )
                .where(CombinedResult.run_id == run_id)
            )
        )
        rankings = _rankings_by_ticker(
            db.scalars(
                select(RankingResult)
                .options(
                    selectinload(RankingResult.calculation_evidence),
                )
                .where(RankingResult.run_id == run_id)
            )
        )
        market_statement = (
            select(MarketRegimeSnapshot)
            .options(
                selectinload(MarketRegimeSnapshot.calculation_evidence),
            )
            .where(MarketRegimeSnapshot.is_current_revision.is_(True))
        )
        sector_statement = (
            select(SectorRotationSnapshot)
            .options(
                selectinload(SectorRotationSnapshot.calculation_evidence),
            )
            .where(SectorRotationSnapshot.is_current_revision.is_(True))
        )
        if market_cutoff is not None:
            market_statement = market_statement.where(
                MarketRegimeSnapshot.as_of_date == market_cutoff.latest_completed_session
            )
            sector_statement = sector_statement.where(
                SectorRotationSnapshot.as_of_date == market_cutoff.latest_completed_session
            )
        market_candidates = tuple(
            db.scalars(
                market_statement.order_by(
                    MarketRegimeSnapshot.as_of_date.desc(),
                    MarketRegimeSnapshot.created_at.desc(),
                    MarketRegimeSnapshot.id.desc(),
                )
            )
        )
        sector_candidates = tuple(
            db.scalars(
                sector_statement.order_by(
                    SectorRotationSnapshot.as_of_date.desc(),
                    SectorRotationSnapshot.created_at.desc(),
                    SectorRotationSnapshot.id.desc(),
                )
            )
        )
        market_snapshot = market_candidates[0] if market_candidates else None
        sector_snapshot = sector_candidates[0] if sector_candidates else None
        sector_rows_by_snapshot: dict[int, dict[str, SectorRotationRow]] = defaultdict(dict)
        snapshot_ids = [row.id for row in sector_candidates if row.id is not None]
        if snapshot_ids:
            for row in db.scalars(
                select(SectorRotationRow).where(SectorRotationRow.snapshot_id.in_(snapshot_ids))
            ):
                sector_rows_by_snapshot[row.snapshot_id][row.sector] = row
        sector_rows = sector_rows_by_snapshot.get(getattr(sector_snapshot, "id", None), {})

        handoff_statement = select(TransitionDecisionHandoffManifest).where(
            TransitionDecisionHandoffManifest.upload_run_id == run_id
        )
        if market_cutoff is not None:
            handoff_statement = handoff_statement.where(
                TransitionDecisionHandoffManifest.market_calculation_context_id
                == market_cutoff.context_id
            )
        if decision_handoff_manifest_id is not None:
            handoff_statement = handoff_statement.where(
                TransitionDecisionHandoffManifest.id == decision_handoff_manifest_id
            )
        handoff_manifest = db.scalar(
            handoff_statement.order_by(TransitionDecisionHandoffManifest.id.desc()).limit(1)
        )

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
            decision_handoff_manifest=handoff_manifest,
            market_regime_candidates=market_candidates,
            sector_rotation_candidates=sector_candidates,
            sector_rows_by_snapshot=dict(sector_rows_by_snapshot),
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

    def get_current_temporal_decision(
        self,
        db: Session,
        prediction_id: int,
    ) -> WinnerTemporalValidityDecision | None:
        return db.scalar(
            select(WinnerTemporalValidityDecision)
            .where(WinnerTemporalValidityDecision.prediction_id == prediction_id)
            .order_by(WinnerTemporalValidityDecision.validation_sequence.desc())
            .limit(1)
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
