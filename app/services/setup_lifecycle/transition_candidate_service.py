from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tables import (
    SetupSignalSnapshot,
    SetupSignalSnapshotCurrentSelection,
    TechnicalScore,
)
from app.services.canonical_evidence import CanonicalEvidenceSerializer
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
from app.services.technical_score_service import preview_run_technicals


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
    predicted_current_state_advance: bool
    reason: str
    confidence: str
    market_calculation_context_id: int | None = None
    expected_exact_pointer_snapshot_id: int | None = None
    expected_exact_pointer_revision: int | None = None
    expected_latest_pointer_revision: int | None = None
    technical_reconstruction_fingerprint: str = ""
    evidence_fingerprint: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "prospective_cutoff": CanonicalEvidenceSerializer.canonicalize(self.prospective_cutoff),
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
            "predicted_current_state_advance": self.predicted_current_state_advance,
            "reason": self.reason,
            "confidence": self.confidence,
            "market_calculation_context_id": self.market_calculation_context_id,
            "expected_exact_pointer_snapshot_id": self.expected_exact_pointer_snapshot_id,
            "expected_exact_pointer_revision": self.expected_exact_pointer_revision,
            "expected_latest_pointer_revision": self.expected_latest_pointer_revision,
            "technical_reconstruction_fingerprint": (self.technical_reconstruction_fingerprint),
            "evidence_fingerprint": self.evidence_fingerprint,
        }


class TransitionCandidateDiscoveryService:
    """Read-only prediction of lifecycle current-selection pointer advances."""

    def __init__(
        self,
        *,
        source_loader: SetupLifecycleSourceLoader | None = None,
        snapshot_builder: SetupLifecycleSnapshotBuilder | None = None,
        repository: SetupLifecycleRepository | None = None,
        technical_previewer: Callable[..., list[TechnicalScore]] | None = None,
        full_universe_technical_preview: bool = True,
    ) -> None:
        self.source_loader = source_loader or SetupLifecycleSourceLoader()
        self.snapshot_builder = snapshot_builder or SetupLifecycleSnapshotBuilder()
        self.repository = repository or SetupLifecycleRepository()
        self.technical_previewer = technical_previewer or preview_run_technicals
        self.full_universe_technical_preview = full_universe_technical_preview

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
            tickers=tickers,
        )
        requested = {ticker.upper() for ticker in tickers} if tickers else None
        preview_scores = self.technical_previewer(
            db,
            run_id,
            tickers=(
                None
                if self.full_universe_technical_preview or requested is None
                else sorted(requested)
            ),
            market_cutoff=prospective,
        )
        preview_by_ticker = {row.ticker.upper(): row for row in preview_scores}
        results: list[TransitionCandidateResult] = []
        for ticker_context in context.tickers:
            if requested is not None and ticker_context.ticker not in requested:
                continue
            ticker_context = replace(
                ticker_context,
                technical_score=preview_by_ticker.get(ticker_context.ticker),
            )
            built = self.snapshot_builder.build(ticker_context)
            latest_pointer, exact_pointer, latest_revision, exact_revision = self._pointers(
                db,
                ticker=built.dto.ticker,
                timeframe=built.dto.timeframe,
                data_as_of_date=built.dto.data_as_of_date,
            )
            assessed = self.assess(
                built,
                prospective=prospective,
                latest_pointer=latest_pointer,
                exact_pointer=exact_pointer,
                all_required_pit_inputs=_prospective_inputs_are_complete(
                    ticker_context,
                    built,
                    prospective,
                ),
                latest_pointer_revision=latest_revision,
                exact_pointer_revision=exact_revision,
            )
            technical_fingerprint = _technical_reconstruction_fingerprint(
                ticker_context.technical_score
            )
            results.append(
                replace(
                    assessed,
                    technical_reconstruction_fingerprint=technical_fingerprint,
                    evidence_fingerprint=_candidate_evidence_fingerprint(
                        assessed,
                        built=built,
                        context=ticker_context,
                        prospective=prospective,
                        technical_fingerprint=technical_fingerprint,
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
        latest_pointer_revision: int | None = None,
        exact_pointer_revision: int | None = None,
    ) -> TransitionCandidateResult:
        candidate = _transient_snapshot(self.repository, built)
        new_key = _selection_key(candidate)
        current_key = _selection_key(latest_pointer) if latest_pointer is not None else None
        can_compete = exact_pointer is not None
        initializes = not can_compete
        predicted = False
        current_state_advance = bool(
            initializes
            and latest_pointer is not None
            and candidate.data_as_of_date > latest_pointer.data_as_of_date
        )
        if exact_pointer is not None:
            candidate.id = (exact_pointer.id or 0) + 1
            selected = select_canonical_snapshot([exact_pointer, candidate])
            predicted = selected is candidate

        if not all_required_pit_inputs:
            reason = "PIT_INPUTS_INCOMPLETE_OR_NOT_PROSPECTIVELY_RECONSTRUCTED"
            confidence = "LOW"
            predicted = False
            current_state_advance = False
        elif initializes and current_state_advance:
            reason = "NEW_SESSION_CANONICAL_INITIALIZATION_ADVANCES_CURRENT_STATE"
            confidence = "HIGH" if not nondeterministic_factor else "MEDIUM"
        elif initializes:
            reason = "NEW_KEY_INITIALIZATION_DOES_NOT_ADVANCE_CURRENT_STATE"
            confidence = "LOW"
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
            predicted_current_state_advance=current_state_advance,
            reason=reason,
            confidence=confidence,
            market_calculation_context_id=prospective.context_id,
            expected_exact_pointer_snapshot_id=(
                exact_pointer.id if exact_pointer is not None else None
            ),
            expected_exact_pointer_revision=exact_pointer_revision,
            expected_latest_pointer_revision=latest_pointer_revision,
        )

    @staticmethod
    def _pointers(
        db: Session,
        *,
        ticker: str,
        timeframe: str,
        data_as_of_date,
    ) -> tuple[
        SetupSignalSnapshot | None,
        SetupSignalSnapshot | None,
        int | None,
        int | None,
    ]:
        rows = list(
            db.execute(
                select(SetupSignalSnapshot, SetupSignalSnapshotCurrentSelection.revision)
                .join(
                    SetupSignalSnapshotCurrentSelection,
                    SetupSignalSnapshotCurrentSelection.selected_snapshot_id
                    == SetupSignalSnapshot.id,
                )
                .where(SetupSignalSnapshot.ticker == ticker.upper())
                .where(SetupSignalSnapshot.timeframe == timeframe)
                .order_by(
                    SetupSignalSnapshot.data_as_of_date.desc(),
                    SetupSignalSnapshot.id.desc(),
                )
            )
        )
        latest_pair = rows[0] if rows else None
        exact_pair = next((row for row in rows if row[0].data_as_of_date == data_as_of_date), None)
        return (
            latest_pair[0] if latest_pair is not None else None,
            exact_pair[0] if exact_pair is not None else None,
            int(latest_pair[1]) if latest_pair is not None else None,
            int(exact_pair[1]) if exact_pair is not None else None,
        )


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


def _technical_reconstruction_fingerprint(technical: TechnicalScore | None) -> str:
    if technical is None:
        return CanonicalEvidenceSerializer.fingerprint({"technical": None})
    excluded = {"id", "run_id", "created_at"}
    payload = {
        column.name: getattr(technical, column.name, None)
        for column in TechnicalScore.__table__.columns
        if column.name not in excluded
    }
    return _stable_hash(payload)


def _candidate_evidence_fingerprint(
    result: TransitionCandidateResult,
    *,
    built: BuiltSnapshot,
    context,
    prospective: MarketCalculationCutoff,
    technical_fingerprint: str,
) -> str:
    bars = sorted(
        [
            {
                "id": row.id,
                "session": row.bar_date,
                "what_to_show": row.what_to_show,
                "first_seen_at": row.first_seen_at,
                "revised_at": row.revised_at,
                "revision_count": row.revision_count,
            }
            for row in context.price_bars
        ],
        key=lambda row: (
            str(row["session"]),
            str(row["what_to_show"]),
            int(row["id"] or 0),
        ),
    )
    return _stable_hash(
        {
            "context": {
                "id": prospective.context_id,
                "cutoff_at": prospective.cutoff_at,
                "latest_completed_session": prospective.latest_completed_session,
                "calendar_version": prospective.calendar_version,
                "bar_readiness_version": prospective.bar_readiness_version,
            },
            "ticker": result.ticker,
            "selection_key": result.prospective_new_key,
            "expected_latest_pointer_snapshot_id": result.current_pointer_snapshot_id,
            "expected_latest_pointer_revision": result.expected_latest_pointer_revision,
            "expected_exact_pointer_snapshot_id": result.expected_exact_pointer_snapshot_id,
            "expected_exact_pointer_revision": result.expected_exact_pointer_revision,
            "predicted_snapshot_source_hash": built.source_data_hash,
            "technical_reconstruction_fingerprint": technical_fingerprint,
            "raw_row_id": getattr(context.raw_row, "id", None),
            "price_bars": bars,
            "candidate_classification": result.confidence,
            "candidate_reason": result.reason,
        }
    )


def aggregate_evidence_fingerprint(results: list[TransitionCandidateResult]) -> str:
    return _stable_hash(
        [
            {
                "ticker": row.ticker,
                "selection_key": row.prospective_new_key,
                "evidence_fingerprint": row.evidence_fingerprint,
            }
            for row in sorted(results, key=lambda item: (item.ticker, item.prospective_new_key))
        ]
    )


def _stable_hash(value: object) -> str:
    return CanonicalEvidenceSerializer.fingerprint(value)
