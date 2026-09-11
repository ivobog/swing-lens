from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.models.tables import TechnicalScore
from app.services.market_calculation_context_service import (
    prospective_pipeline_market_context,
)
from app.services.technical_score_service import (
    TechnicalV5RunContext,
    finalize_technical_scores,
)
from app.settings import Settings


def test_independent_preflight_and_enqueue_contexts_diverge_at_bar_ready_boundary() -> None:
    preflight = prospective_pipeline_market_context(
        cutoff_at=datetime(2026, 9, 8, 20, 14, tzinfo=UTC)
    )
    enqueue = prospective_pipeline_market_context(
        cutoff_at=datetime(2026, 9, 8, 20, 16, tzinfo=UTC)
    )

    assert preflight.latest_completed_session == date(2026, 9, 4)
    assert enqueue.latest_completed_session == date(2026, 9, 8)
    assert preflight.cutoff_at != enqueue.cutoff_at


def test_technical_finalize_preview_does_not_issue_database_writes() -> None:
    score = TechnicalScore(
        run_id=7,
        ticker="MSFT",
        classification="No trade",
        action_bias="No data",
        technical_confidence="error",
        warning_flags_json=["technical_error"],
        missing_data_json={"unavailable": True},
        debug_json={},
    )
    db = WriteRejectingDb()
    settings = Settings(
        technical_v5_enabled=False,
        technical_v5_shadow_compare_enabled=False,
    )

    result = finalize_technical_scores(
        db,
        7,
        [score],
        symbols=["MSFT"],
        settings=settings,
        v5_context=TechnicalV5RunContext(resolutions={}, sector_features={}),
        persist=False,
    )

    assert result == [score]
    assert db.write_attempts == 0


class WriteRejectingDb:
    write_attempts = 0

    def execute(self, *_args, **_kwargs):
        self.write_attempts += 1
        pytest.fail("read-only technical reconstruction attempted a database write")

    def add_all(self, *_args, **_kwargs):
        self.write_attempts += 1
        pytest.fail("read-only technical reconstruction attempted an ORM write")

    def flush(self, *_args, **_kwargs):
        self.write_attempts += 1
        pytest.fail("read-only technical reconstruction attempted an ORM flush")
