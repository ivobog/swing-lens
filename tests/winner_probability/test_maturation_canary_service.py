from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.services.winner_probability.maturation_canary_service import (
    CANARY_SCHEMA,
    CanaryApprovalError,
    canonical_canary_hash,
    independent_forward_metrics,
    independent_target_stop,
    verify_canary_approval,
)


def _bar(day: int, *, high: str, low: str, close: str) -> SimpleNamespace:
    return SimpleNamespace(
        bar_date=date(2026, 8, day),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
    )


def test_independent_forward_metrics_use_exact_decimal_precision() -> None:
    bars = [
        _bar(20, high="103", low="99", close="101"),
        _bar(21, high="104", low="97", close="102"),
        _bar(24, high="108", low="98", close="106"),
        _bar(25, high="105", low="96", close="99"),
        _bar(26, high="107", low="95", close="105"),
    ]

    result = independent_forward_metrics(bars)

    assert result == {
        "entry_price": Decimal("100"),
        "exit_price": Decimal("105"),
        "close_return_pct": Decimal("5.000000"),
        "positive_return": True,
        "mfe_pct": Decimal("8.000000"),
        "mae_pct": Decimal("-5.000000"),
        "sessions_to_mfe": 3,
        "sessions_to_mae": 5,
    }


def test_independent_target_stop_is_conservative_on_same_bar_conflict() -> None:
    bars = [_bar(20, high="106", low="94", close="101")]

    result = independent_target_stop(
        bars,
        entry_price=Decimal("100"),
        target_pct=Decimal("5"),
        stop_pct=Decimal("5"),
        same_bar_conflict_policy="CONSERVATIVE_STOP_FIRST",
    )

    assert result == {
        "target_hit": True,
        "stop_hit": True,
        "first_event": "SAME_BAR_CONFLICT",
        "event_session": date(2026, 8, 20),
        "same_bar_conflict": True,
        "primary_winner": False,
        "optimistic_winner": True,
        "conservative_winner": False,
    }


def test_manifest_hash_is_deterministic_and_approval_is_fail_closed() -> None:
    touch_set = {"forward_outcomes": [], "target_stop_outcomes": []}
    negative_scope: list[dict[str, object]] = []
    first = {
        "schema": CANARY_SCHEMA,
        "touch_set": touch_set,
        "touch_set_hash": canonical_canary_hash(touch_set),
        "negative_scope": negative_scope,
        "negative_scope_hash": canonical_canary_hash(negative_scope),
        "outcomes": [{"outcome_id": 2}, {"outcome_id": 7}],
    }
    second = {
        "outcomes": [{"outcome_id": 2}, {"outcome_id": 7}],
        "negative_scope_hash": canonical_canary_hash(negative_scope),
        "negative_scope": negative_scope,
        "touch_set_hash": canonical_canary_hash(touch_set),
        "touch_set": touch_set,
        "schema": CANARY_SCHEMA,
    }
    reviewed_hash = canonical_canary_hash(first)

    assert reviewed_hash == canonical_canary_hash(second)
    verify_canary_approval(
        first,
        reviewed_manifest_hash=reviewed_hash,
        approve_write=True,
        actor="pytest",
        request_key="winner-canary-test",
    )

    with pytest.raises(PermissionError, match="approve_write"):
        verify_canary_approval(
            first,
            reviewed_manifest_hash=reviewed_hash,
            approve_write=False,
            actor="pytest",
            request_key="winner-canary-test",
        )
    with pytest.raises(CanaryApprovalError, match="hash"):
        verify_canary_approval(
            first,
            reviewed_manifest_hash="0" * 64,
            approve_write=True,
            actor="pytest",
            request_key="winner-canary-test",
        )
    with pytest.raises(ValueError, match="actor"):
        verify_canary_approval(
            first,
            reviewed_manifest_hash=reviewed_hash,
            approve_write=True,
            actor="",
            request_key="winner-canary-test",
        )


def test_manifest_hash_normalizes_timezone_aware_values() -> None:
    payload = {
        "at": datetime(2026, 8, 20, 12, 0, tzinfo=UTC),
        "amount": Decimal("1.2300"),
        "day": date(2026, 8, 20),
    }

    assert canonical_canary_hash(payload) == canonical_canary_hash(payload)


def test_reviewed_batch_approval_accepts_100_and_rejects_more_than_250() -> None:
    touch_set = {"forward_outcomes": [], "target_stop_outcomes": []}
    negative_scope: list[dict[str, object]] = []

    def manifest(count: int) -> dict[str, object]:
        return {
            "schema": CANARY_SCHEMA,
            "touch_set": touch_set,
            "touch_set_hash": canonical_canary_hash(touch_set),
            "negative_scope": negative_scope,
            "negative_scope_hash": canonical_canary_hash(negative_scope),
            "outcomes": [{"outcome_id": value} for value in range(1, count + 1)],
        }

    reviewed = manifest(100)
    verify_canary_approval(
        reviewed,
        reviewed_manifest_hash=canonical_canary_hash(reviewed),
        approve_write=True,
        actor="pytest",
        request_key="winner-batch-100",
    )

    oversized = manifest(251)
    with pytest.raises(CanaryApprovalError, match="250"):
        verify_canary_approval(
            oversized,
            reviewed_manifest_hash=canonical_canary_hash(oversized),
            approve_write=True,
            actor="pytest",
            request_key="winner-batch-251",
        )


def test_reviewed_batch_approval_rejects_negative_scope_hash_drift() -> None:
    touch_set = {"forward_outcomes": [], "target_stop_outcomes": []}
    manifest = {
        "schema": CANARY_SCHEMA,
        "touch_set": touch_set,
        "touch_set_hash": canonical_canary_hash(touch_set),
        "negative_scope": [{"target_stop_outcome_id": 17, "state_hash": "reviewed"}],
        "negative_scope_hash": "0" * 64,
        "outcomes": [{"outcome_id": 1}],
    }

    with pytest.raises(CanaryApprovalError, match="negative-scope"):
        verify_canary_approval(
            manifest,
            reviewed_manifest_hash=canonical_canary_hash(manifest),
            approve_write=True,
            actor="pytest",
            request_key="winner-negative-scope-drift",
        )
