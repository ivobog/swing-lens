from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
)
from app.services.market_calculation_context_service import (
    prospective_pipeline_market_context,
)
from app.services.market_clock_service import MarketCalculationCutoff
from app.services.setup_lifecycle.canonicalization import select_canonical_snapshot
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import (
    BuiltSnapshot,
    SetupLifecycleSnapshotBuilder,
)
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader


@dataclass(frozen=True)
class TransitionCandidateResult:
    ticker: str
    prospective_cutoff: datetime
    prospective_latest_completed_session: object
    latest_reconstructable_session: object
    current_pointer_key: str | None
    current_pointer_snapshot_id: int | None
    current_pointer_target_session: object | None
    prospective_new_key: str
    can_compete_with_existing_pointer: bool
    would_initialize_new_key: bool
    predicted_pointer_advance: bool
    reason: str
    confidence: str

    def as_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "prospective_cutoff": self.prospective_cutoff.isoformat(),
            "prospective_latest_completed_session": str(self.prospective_latest_completed_session),
            "latest_reconstructable_session": str(self.latest_reconstructable_session),
            "current_pointer_key": self.current_pointer_key,
            "current_pointer_snapshot_id": self.current_pointer_snapshot_id,
            "current_pointer_target_session": (
                str(self.current_pointer_target_session)
                if self.current_pointer_target_session is not None
                else None
            ),
            "prospective_new_key": self.prospective_new_key,
            "can_compete_with_existing_pointer": self.can_compete_with_existing_pointer,
            "would_initialize_new_key": self.would_initialize_new_key,
            "predicted_pointer_advance": self.predicted_pointer_advance,
            "reason": self.reason,
            "confidence": self.confidence,
        }


class TransitionCandidateDiscoveryService:
    """Read-only prediction of lifecycle current-selection pointer advances."""

    def __init__(
        self,
        *,
        source_loader: SetupLifecycleSourceLoader | None = None,
        snapshot_builder: SetupLifecycleSnapshotBuilder | None = None,
        repository: SetupLifecycleRepository | None = None,
    ) -> None:
        self.source_loader = source_loader or SetupLifecycleSourceLoader()
        self.snapshot_builder = snapshot_builder or SetupLifecycleSnapshotBuilder()
        self.repository = repository or SetupLifecycleRepository()

    def discover_for_run(
        self,
        db: Session,
        run_id: int,
        *,
        cutoff_at: datetime | None = None,
        market_cutoff: MarketCalculationCutoff | None = None,
        tickers: set[str] | None = None,
    ) -> list[TransitionCandidateResult]:
        prospective = market_cutoff or prospective_pipeline_market_context(cutoff_at=cutoff_at)
        context = self.source_loader.load_run_context(
            db,
            run_id,
            market_cutoff=prospective,
        )
        requested = {ticker.upper() for ticker in tickers} if tickers else None
        results: list[TransitionCandidateResult] = []
        for ticker_context in context.tickers:
            if requested is not None and ticker_context.ticker not in requested:
                continue
            built = self.snapshot_builder.build(ticker_context)
            latest_pointer, exact_pointer = self._pointers(
                db,
                ticker=built.dto.ticker,
                timeframe=built.dto.timeframe,
                data_as_of_date=built.dto.data_as_of_date,
                cutoff_at=prospective.cutoff_at,
            )
            results.append(
                self.assess(
                    built,
                    prospective=prospective,
                    latest_pointer=latest_pointer,
                    exact_pointer=exact_pointer,
                    all_required_pit_inputs=_prospective_inputs_are_complete(
                        ticker_context,
                        built,
                        prospective,
                    ),
                )
            )
        return results

    def assess(
        self,
        built: BuiltSnapshot,
        *,
        prospective: MarketCalculationCutoff,
        latest_pointer: SetupSignalSnapshot | None,
        exact_pointer: SetupSignalSnapshot | None,
        all_required_pit_inputs: bool,
        nondeterministic_factor: bool = False,
    ) -> TransitionCandidateResult:
        candidate = _transient_snapshot(self.repository, built)
        new_key = _selection_key(candidate)
        current_key = _selection_key(latest_pointer) if latest_pointer is not None else None
        can_compete = exact_pointer is not None
        initializes = not can_compete
        predicted = False
        if exact_pointer is not None:
            candidate.id = (exact_pointer.id or 0) + 1
            selected = select_canonical_snapshot([exact_pointer, candidate])
            predicted = selected is candidate

        if initializes:
            reason = "NEW_KEY_INITIALIZATION_NOT_TRANSITION_COVERAGE"
            confidence = "LOW"
        elif not all_required_pit_inputs:
            reason = "PIT_INPUTS_INCOMPLETE_OR_NOT_PROSPECTIVELY_RECONSTRUCTED"
            confidence = "LOW"
            predicted = False
        elif not predicted:
            reason = "PROSPECTIVE_SNAPSHOT_DOES_NOT_DISPLACE_CURRENT_SELECTION"
            confidence = "LOW"
        elif nondeterministic_factor:
            reason = "POINTER_ADVANCE_DEPENDS_ON_ONE_NONDETERMINISTIC_FACTOR"
            confidence = "MEDIUM"
        else:
            reason = "EXISTING_POINTER_ADVANCE_DETERMINISTIC_UNDER_FROZEN_CONTEXT"
            confidence = "HIGH"

        return TransitionCandidateResult(
            ticker=candidate.ticker,
            prospective_cutoff=prospective.cutoff_at,
            prospective_latest_completed_session=prospective.latest_completed_session,
            latest_reconstructable_session=candidate.data_as_of_date,
            current_pointer_key=current_key,
            current_pointer_snapshot_id=latest_pointer.id if latest_pointer is not None else None,
            current_pointer_target_session=(
                latest_pointer.data_as_of_date if latest_pointer is not None else None
            ),
            prospective_new_key=new_key,
            can_compete_with_existing_pointer=can_compete,
            would_initialize_new_key=initializes,
            predicted_pointer_advance=predicted,
            reason=reason,
            confidence=confidence,
        )

    @staticmethod
    def _pointers(
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        data_as_of_date,
        cutoff_at: datetime,
    ) -> tuple[SetupSignalSnapshot | None, SetupSignalSnapshot | None]:
        rows = list(
            db.scalars(
                select(SetupSignalSnapshot)
                .join(
                    SetupSignalSnapshotCurrentSelection,
                    SetupSignalSnapshotCurrentSelection.selected_snapshot_id
                    == SetupSignalSnapshot.id,
                )
                .where(SetupSignalSnapshot.ticker == ticker.upper())
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .where(SetupSignalSnapshotCurrentSelection.created_at <= cutoff_at)
                .order_by(
                    SetupSignalSnapshot.data_as_of_date.desc(),
                    SetupSignalSnapshot.id.desc(),
                )
            )
        )
        latest = rows[0] if rows else None
        exact = next((row for row in rows if row.data_as_of_date == data_as_of_date), None)
        return latest, exact


def _transient_snapshot(
    repository: SetupLifecycleRepository, built: BuiltSnapshot
) -> SetupSignalSnapshot:
    dto = built.dto
    snapshot = SetupSignalSnapshot(
        ticker=dto.ticker,
        timeframe=dto.timeframe,
        data_as_of_date=dto.data_as_of_date,
        calculated_at=dto.calculated_at,
        origin_type=dto.origin_type,
        engine_version=dto.engine_version,
        config_version=dto.config_version,
        config_hash=dto.config_hash,
        source_data_hash=dto.source_data_hash,
        schema_version=dto.schema_version,
        data_quality_label=dto.data_quality_label,
    )
    repository._apply_snapshot_fields(snapshot, dto)
    return snapshot


def _selection_key(snapshot: SetupSignalSnapshot | None) -> str | None:
    if snapshot is None:
        return None
    return f"{snapshot.ticker}/{snapshot.timeframe}/{snapshot.data_as_of_date.isoformat()}"


def _prospective_inputs_are_complete(context, built, prospective) -> bool:
    technical = context.technical_score
    temporal_match = bool(
        technical is not None
        and technical.calculation_cutoff_at == prospective.cutoff_at
        and technical.input_as_of_session == prospective.latest_completed_session
    )
    fatal = {
        "MISSING_REQUIRED_TECHNICAL_SCORE",
        "MISSING_REQUIRED_SETUP_SCORE",
        "MISSING_REQUIRED_CLASSIFICATION",
        "MISSING_REQUIRED_CLOSE_PRICE",
        "NO_COMPLETED_DAILY_BAR",
        "FUTURE_DATED_SOURCE_CONTEXT",
    }
    return (
        temporal_match
        and built.required_feature_coverage == 1.0
        and not fatal.intersection(built.warnings)
    )
