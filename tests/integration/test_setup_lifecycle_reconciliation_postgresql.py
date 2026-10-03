"""PostgreSQL proof for cross-context lifecycle reconciliation."""

from datetime import UTC, datetime

import pytest
import test_contextual_configuration_adoption_postgresql as contextual
from native_mutation_support import seed_native_core
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.tables import (
    SetupLifecycleEpisode,
    SetupLifecycleEvaluationEvidence,
    SetupLifecycleEvent,
)
from app.services.decision_effective_configuration import (
    resolve_lifecycle_configuration,
    resolve_setup_configuration,
)
from app.services.setup_lifecycle.episode_service import SetupLifecycleEpisodeService
from app.services.setup_lifecycle.errors import SetupLifecycleReconciliationError
from app.services.setup_lifecycle.repository import SetupLifecycleRepository
from app.services.setup_lifecycle.snapshot_builder import SetupLifecycleSnapshotBuilder
from app.services.setup_lifecycle.source_loader import SetupLifecycleSourceLoader

contextual_engine = contextual.contextual_engine

pytestmark = [pytest.mark.integration, pytest.mark.destructive]


def _capture(db, *, run_id, cutoff, setup):
    context = SetupLifecycleSourceLoader().load_run_context(db, run_id, market_cutoff=cutoff)
    dto = SetupLifecycleSnapshotBuilder(setup.setup_config()).build(context.tickers[0]).dto
    return SetupLifecycleRepository(setup.setup_config()).upsert_snapshot(db, dto)


def test_cross_context_current_evaluation_precedes_primary_and_is_idempotent(
    contextual_engine,
):
    setup = resolve_setup_configuration()
    lifecycle = resolve_lifecycle_configuration()
    service = SetupLifecycleEpisodeService(config=lifecycle.setup_config())
    repository = SetupLifecycleRepository(setup.setup_config())

    with Session(contextual_engine) as db:
        cutoff_one, _, _ = seed_native_core(
            db,
            run_id=7,
            ticker="ACMR",
            cutoff_at=datetime(2026, 9, 16, 21, tzinfo=UTC),
            extra_configurations=(setup, lifecycle),
        )
        first_snapshot = _capture(db, run_id=7, cutoff=cutoff_one, setup=setup)
        first = service.apply_snapshot(db, first_snapshot)
        assert first.episode is not None
        original_episode_id = first.episode.id
        original_evaluation_id = first.episode.latest_evaluation_evidence_id
        db.commit()

        cutoff_two, _, _ = seed_native_core(
            db,
            run_id=8,
            ticker="ACMR",
            cutoff_at=datetime(2026, 9, 18, 21, tzinfo=UTC),
            extra_configurations=(setup, lifecycle),
        )
        second_snapshot = _capture(db, run_id=8, cutoff=cutoff_two, setup=setup)
        db.commit()

        original = db.get(SetupLifecycleEpisode, original_episode_id)
        with pytest.raises(
            SetupLifecycleReconciliationError,
            match="MUTATION_LIFECYCLE_PRIMARY_EXECUTION_SCOPE_MISMATCH",
        ):
            service.refresh_primary_status(
                db,
                ticker="ACMR",
                timeframe="1d",
                market_cutoff=cutoff_two,
            )
        db.commit()
        original = db.get(SetupLifecycleEpisode, original_episode_id)
        assert original.status == "ACTIVE"
        assert original.latest_evaluation_evidence_id == original_evaluation_id

        episodes = tuple(
            repository.lifecycle_episodes_for_keys(db, {("ACMR", "1d")})[("ACMR", "1d")]
        )
        plan = service.plan_reconciliation(db, second_snapshot, preloaded_episodes=episodes)
        assert plan.current_family == original.setup_family
        assert plan.displaced_episode_ids == ()
        result = service.apply_snapshot(
            db,
            second_snapshot,
            preloaded_episodes=episodes,
            reconciliation_plan=plan,
            refresh_primary=False,
        )
        service.refresh_primary_status(db, ticker="ACMR", timeframe="1d", market_cutoff=cutoff_two)
        db.commit()

        original = db.get(SetupLifecycleEpisode, original_episode_id)
        assert original.status == "ACTIVE"
        assert original.current_snapshot_id == second_snapshot.id
        assert result.reconciliation_results == ()
        active = list(
            db.scalars(
                select(SetupLifecycleEpisode).where(
                    SetupLifecycleEpisode.ticker == "ACMR",
                    SetupLifecycleEpisode.timeframe == "1d",
                    SetupLifecycleEpisode.status == "ACTIVE",
                )
            )
        )
        assert all(row.is_primary for row in active[:1])
        assert all(
            db.get(
                SetupLifecycleEvaluationEvidence, row.latest_evaluation_evidence_id
            ).calculation_cutoff_at
            == cutoff_two.cutoff_at
            for row in active
        )

        counts_before = (
            db.scalar(select(func.count()).select_from(SetupLifecycleEvaluationEvidence)),
            db.scalar(select(func.count()).select_from(SetupLifecycleEvent)),
        )
        episodes = tuple(
            repository.lifecycle_episodes_for_keys(db, {("ACMR", "1d")})[("ACMR", "1d")]
        )
        retry_plan = service.plan_reconciliation(db, second_snapshot, preloaded_episodes=episodes)
        retry = service.apply_snapshot(
            db,
            second_snapshot,
            preloaded_episodes=episodes,
            reconciliation_plan=retry_plan,
            refresh_primary=False,
        )
        service.refresh_primary_status(db, ticker="ACMR", timeframe="1d", market_cutoff=cutoff_two)
        db.commit()
        assert retry.reconciliation_results == ()
        assert counts_before == (
            db.scalar(select(func.count()).select_from(SetupLifecycleEvaluationEvidence)),
            db.scalar(select(func.count()).select_from(SetupLifecycleEvent)),
        )
