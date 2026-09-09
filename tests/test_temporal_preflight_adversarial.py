from __future__ import annotations

import random
from dataclasses import replace
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from app.services.canonical_evidence import CanonicalEvidenceSerializer
from app.services.market_clock_service import MarketClockService
from app.services.setup_lifecycle.transition_candidate_service import (
    TransitionCandidateResult,
    aggregate_evidence_fingerprint,
)
from app.services.transition_preflight_plan_service import (
    PREFLIGHT_STATE_TRANSITIONS,
    TransitionPreflightPlanStatus,
)


def test_preflight_state_machine_has_only_explicit_terminal_transitions() -> None:
    assert PREFLIGHT_STATE_TRANSITIONS == {
        (TransitionPreflightPlanStatus.RESERVED, "CONSUME"): (
            TransitionPreflightPlanStatus.CONSUMED
        ),
        (TransitionPreflightPlanStatus.RESERVED, "CANCEL"): (
            TransitionPreflightPlanStatus.CANCELLED
        ),
        (TransitionPreflightPlanStatus.RESERVED, "EXPIRE"): (TransitionPreflightPlanStatus.EXPIRED),
        (TransitionPreflightPlanStatus.RESERVED, "INVALIDATE"): (
            TransitionPreflightPlanStatus.STALE
        ),
    }
    terminal = {
        TransitionPreflightPlanStatus.CONSUMED,
        TransitionPreflightPlanStatus.CANCELLED,
        TransitionPreflightPlanStatus.EXPIRED,
        TransitionPreflightPlanStatus.STALE,
    }
    assert all(source not in terminal for source, _event in PREFLIGHT_STATE_TRANSITIONS)


def test_temporal_boundary_matrix_is_timezone_representation_invariant() -> None:
    instants = (
        datetime(2026, 3, 8, 7, 0, tzinfo=UTC),  # New York DST start
        datetime(2026, 3, 29, 1, 0, tzinfo=UTC),  # Zurich DST start
        datetime(2026, 10, 25, 1, 0, tzinfo=UTC),  # Zurich DST end
        datetime(2026, 11, 1, 6, 0, tzinfo=UTC),  # New York DST end
        datetime(2026, 9, 9, 13, 30, tzinfo=UTC),  # market open
        datetime(2026, 9, 9, 20, 0, tzinfo=UTC),  # market close
        datetime(2026, 9, 9, 20, 15, tzinfo=UTC),  # daily bar ready
        datetime(2026, 9, 10, 0, 0, tzinfo=UTC),  # UTC midnight
        datetime(2026, 9, 10, 4, 0, tzinfo=UTC),  # New York midnight
        datetime(2026, 9, 9, 22, 0, tzinfo=UTC),  # Zurich midnight
        datetime(2026, 9, 12, 16, 0, tzinfo=UTC),  # weekend
        datetime(2026, 7, 4, 16, 0, tzinfo=UTC),  # holiday weekend
    )
    zones = tuple(
        ZoneInfo(name) for name in ("UTC", "Europe/Zurich", "America/New_York", "Asia/Tokyo")
    )
    clock = MarketClockService()
    for instant in instants:
        contexts = [
            clock.cutoff_for(instant.astimezone(zone), reason="BOUNDARY_MATRIX") for zone in zones
        ]
        assert {context.cutoff_at for context in contexts} == {instant}
        assert len({context.latest_completed_session for context in contexts}) == 1
        assert len({context.daily_bar_ready_at for context in contexts}) == 1


def test_randomized_candidate_evidence_order_and_timezone_metamorphism() -> None:
    generator = random.Random(20260909)
    base = datetime.fromisoformat("2026-09-09T19:55:33.682959+00:00")
    zones = tuple(
        ZoneInfo(name) for name in ("UTC", "Europe/Zurich", "America/New_York", "Asia/Tokyo")
    )
    for scenario in range(512):
        tickers = [f"T{index:04d}" for index in range(generator.randrange(1, 20))]
        revision = generator.randrange(1, 100)
        results = [
            _candidate(
                ticker,
                base.astimezone(generator.choice(zones)),
                revision,
                scenario,
            )
            for ticker in tickers
        ]
        expected = aggregate_evidence_fingerprint(results)
        generator.shuffle(results)
        shifted = [
            replace(
                row,
                prospective_cutoff=row.prospective_cutoff.astimezone(generator.choice(zones)),
            )
            for row in results
        ]
        assert aggregate_evidence_fingerprint(shifted) == expected

        changed = list(shifted)
        changed[0] = replace(
            changed[0],
            evidence_fingerprint=CanonicalEvidenceSerializer.fingerprint(
                {"scenario": scenario, "revision": revision + 1}
            ),
        )
        assert aggregate_evidence_fingerprint(changed) != expected


def _candidate(
    ticker: str,
    cutoff: datetime,
    revision: int,
    scenario: int,
) -> TransitionCandidateResult:
    session = date(2026, 9, 8)
    return TransitionCandidateResult(
        ticker=ticker,
        prospective_cutoff=cutoff,
        prospective_latest_completed_session=session,
        latest_reconstructable_session=session,
        current_pointer_key=f"{ticker}/1d/{session.isoformat()}",
        current_pointer_snapshot_id=scenario + 1,
        current_pointer_target_session=session,
        prospective_new_key=f"{ticker}/1d/{session.isoformat()}",
        can_compete_with_existing_pointer=True,
        would_initialize_new_key=False,
        predicted_pointer_advance=True,
        predicted_current_state_advance=False,
        reason="EXISTING_POINTER_ADVANCE_DETERMINISTIC_UNDER_FROZEN_CONTEXT",
        confidence="HIGH",
        expected_latest_pointer_revision=revision,
        expected_exact_pointer_revision=revision,
        technical_reconstruction_fingerprint=CanonicalEvidenceSerializer.fingerprint(
            {"ticker": ticker, "cutoff_at": cutoff, "scenario": scenario}
        ),
        evidence_fingerprint=CanonicalEvidenceSerializer.fingerprint(
            {
                "ticker": ticker,
                "cutoff_at": cutoff,
                "revision": revision,
                "scenario": scenario,
            }
        ),
    )
